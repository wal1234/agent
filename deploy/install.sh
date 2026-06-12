#!/bin/bash
# 缺陷神探 - 一键部署脚本（单机 Linux）
#
# 用法：
#   export ARK_API_KEY="你的火山引擎方舟 API Key"
#   ./install.sh <git 仓库地址>
#
# 此脚本完成：基础依赖 / 创建用户 / 拉代码 / venv / 写 env / 安装 systemd 服务
# 不包含：Nginx 站点配置（请参考 nginx-defect-hunter.conf 手动配置）
#         HTTPS 证书申请（部署完后单独跑 certbot）

set -euo pipefail

# ==================== 参数 ====================
REPO_URL="${1:-}"
APP_DIR="/opt/defect-hunter"
APP_USER="defect"
ENV_FILE="/etc/defect-hunter.env"
PORT="${DEFECT_HUNTER_PORT:-8000}"
WORKERS="${DEFECT_HUNTER_WORKERS:-2}"

if [[ -z "$REPO_URL" ]]; then
    echo "❌ 用法: $0 <git 仓库地址>"
    exit 1
fi
if [[ -z "${ARK_API_KEY:-}" ]]; then
    echo "❌ 请先 export ARK_API_KEY=<你的火山引擎 API Key>"
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo "❌ 请用 root 或 sudo 运行此脚本"
    exit 1
fi

# ==================== 1. 基础依赖 ====================
echo "[1/6] 安装基础依赖..."
if command -v apt-get >/dev/null; then
    apt-get update
    apt-get install -y git curl python3 python3-venv python3-pip python3-dev \
                       nginx ufw build-essential ca-certificates
elif command -v dnf >/dev/null; then
    dnf install -y git curl python3 python3-pip python3-devel nginx \
                   gcc gcc-c++ make
elif command -v yum >/dev/null; then
    yum install -y git curl python3 python3-pip python3-devel nginx \
                   gcc gcc-c++ make
else
    echo "❌ 未识别的包管理器（仅支持 apt/dnf/yum）"
    exit 1
fi

# ==================== 2. 创建用户 + 目录 ====================
echo "[2/6] 创建专用用户与目录..."
id "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ==================== 3. 拉取代码 ====================
echo "[3/6] 拉取代码..."
if [[ -d "$APP_DIR/.git" ]]; then
    sudo -u "$APP_USER" -H bash -c "cd $APP_DIR && git pull"
else
    sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
fi

# ==================== 4. 虚拟环境 + 依赖 ====================
echo "[4/6] 安装 Python 依赖..."
sudo -u "$APP_USER" -H bash -c "
    cd $APP_DIR
    python3 -m venv .venv
    . .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    mkdir -p data/samples data/knowledge_base reports logs
"

# ==================== 5. 写入受保护的环境文件 ====================
echo "[5/6] 写入 API Key 至 $ENV_FILE ..."
cat > "$ENV_FILE" <<EOF
ARK_API_KEY=$ARK_API_KEY
EOF
chmod 600 "$ENV_FILE"
chown "$APP_USER:$APP_USER" "$ENV_FILE"

# ==================== 6. systemd 服务 ====================
echo "[6/6] 安装 systemd 服务..."
SERVICE_FILE="$APP_DIR/deploy/defect-hunter.service"
if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "❌ 缺失 systemd 模板: $SERVICE_FILE"
    exit 1
fi

# 替换 worker / port（如果用户自定义了）
sed -e "s|--port 8000|--port $PORT|" \
    -e "s|--workers 2|--workers $WORKERS|" \
    "$SERVICE_FILE" > /etc/systemd/system/defect-hunter.service

systemctl daemon-reload
systemctl enable defect-hunter
systemctl restart defect-hunter

sleep 3
echo ""
echo "==================================================="
echo "  健康检查"
echo "==================================================="
if curl -sf "http://127.0.0.1:$PORT/api/v1/health" | python3 -m json.tool; then
    echo ""
    echo "✅ 服务已启动并响应正常"
else
    echo "❌ 健康检查失败，查看日志: journalctl -u defect-hunter -n 50"
    exit 1
fi

echo ""
echo "==================================================="
echo "  下一步"
echo "==================================================="
echo "1. 配置 Nginx 反向代理（推荐）："
echo "   sudo cp $APP_DIR/deploy/nginx-defect-hunter.conf /etc/nginx/sites-available/"
echo "   sudo ln -sf /etc/nginx/sites-available/nginx-defect-hunter.conf /etc/nginx/sites-enabled/"
echo "   sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "2. 申请 HTTPS 证书（拿到域名后）："
echo "   sudo certbot --nginx -d defect-hunter.your-domain.com"
echo ""
echo "3. 配置每日备份："
echo "   sudo cp $APP_DIR/deploy/backup.sh /usr/local/bin/defect-hunter-backup.sh"
echo "   sudo chmod +x /usr/local/bin/defect-hunter-backup.sh"
echo "   echo '0 3 * * * root /usr/local/bin/defect-hunter-backup.sh' | sudo tee /etc/cron.d/defect-hunter-backup"
echo ""
echo "完整文档: $APP_DIR/deploy/DEPLOY-LINUX.md"
