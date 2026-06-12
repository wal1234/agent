# 单机 Linux 云服务器部署指南

> 适用场景：**1 台 Linux 云服务器**（如阿里云 / 腾讯云 / 火山引擎 ECS）部署"生产缺陷神探"智能体。
>
> 部署形态：systemd 托管的 uvicorn 进程 + Nginx 反向代理 + 火山引擎方舟（glm-5.1）。
> 不依赖 Docker、不依赖 PostgreSQL、不依赖容器编排——**最朴素也最容易排错的姿势**。

---

## 0. 部署成果速览

部署完成后你会拥有：

```
公网 → Nginx (80/443) → uvicorn (127.0.0.1:8000) → DefectHunter API
                                                  → 知识库 JSONL（本地磁盘）
                                                  → 火山引擎方舟 glm-5.1
```

- 业务方访问 `https://defect-hunter.your-domain.com/docs` 即看到 Swagger UI
- 业务方上传台账 JSON 即可拿到 4 份 Markdown 报告
- 重启服务器自动拉起服务
- 日志、知识库均存盘可追溯

---

## 1. 服务器准备

### 1.1 推荐配置

| 项 | 最低 | 推荐 |
|----|------|------|
| CPU | 1 核 | 2 核 |
| 内存 | 1 GB | 2 GB |
| 磁盘 | 10 GB | 40 GB（知识库会持续累积） |
| 网络 | 1 Mbps 公网带宽 | 5 Mbps |
| 系统 | Ubuntu 22.04 LTS / Debian 12 / CentOS Stream 9 | Ubuntu 22.04 LTS |
| 公网 | 必需，能出站调用 `ark.cn-beijing.volces.com:443` | 同 |

> ⚠️ **必须能访问 `https://ark.cn-beijing.volces.com`**——这是火山引擎方舟的 API 地址。如果是金融/政务内网环境，提前找网络组开白名单。

### 1.2 基础初始化

以下假设系统是 Ubuntu 22.04 LTS（其他发行版命令略有差异）：

```bash
# 1.2.1 更新系统
sudo apt update && sudo apt upgrade -y

# 1.2.2 安装基础工具
sudo apt install -y git curl wget vim build-essential ca-certificates \
                     python3 python3-venv python3-pip python3-dev \
                     nginx ufw

# 1.2.3 创建专用运行用户（不要用 root 跑业务进程）
sudo useradd -m -s /bin/bash defect
sudo mkdir -p /opt/defect-hunter
sudo chown -R defect:defect /opt/defect-hunter

# 1.2.4 时区（生成的报告时间戳与业务对齐）
sudo timedatectl set-timezone Asia/Shanghai
```

### 1.3 防火墙

```bash
# 仅放行 SSH + HTTP + HTTPS
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

> 不要把 8000（uvicorn）端口开到公网。让 Nginx 反代是唯一入口。

---

## 2. 部署应用

### 2.1 拉取代码

```bash
sudo -u defect bash <<'EOF'
cd /opt/defect-hunter
git clone <你的代码仓库地址> .
# 或者：scp 上传压缩包再解压
EOF
```

### 2.2 创建虚拟环境并安装依赖

```bash
sudo -u defect bash <<'EOF'
cd /opt/defect-hunter
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
EOF
```

### 2.3 配置火山引擎 API Key

把 API Key 单独放在受保护的文件里（不进 git，不进环境全局变量）：

```bash
# 用 root 写入受限的环境变量文件
sudo tee /etc/defect-hunter.env > /dev/null <<'EOF'
ARK_API_KEY=替换为你的火山引擎方舟 API Key
EOF

sudo chmod 600 /etc/defect-hunter.env
sudo chown defect:defect /etc/defect-hunter.env
```

### 2.4 配置应用（可选）

如果默认配置已经够用，**这一步可跳过**。如需自定义模型/温度等：

```bash
sudo -u defect vim /opt/defect-hunter/config/config.yaml
```

关键配置示例：

```yaml
llm:
  provider: "volcengine"
  model: "glm-5.1"               # 或 endpoint ID（如 ep-xxxxxx）
  api_key: "${ARK_API_KEY}"      # 自动读取 systemd 注入的环境变量
  max_tokens: 4096
  temperature: 0.2
```

### 2.5 准备数据目录

```bash
sudo -u defect bash <<'EOF'
cd /opt/defect-hunter
mkdir -p data/samples data/knowledge_base reports logs
EOF
```

### 2.6 烟测

```bash
sudo -u defect bash <<'EOF'
cd /opt/defect-hunter
. .venv/bin/activate
export ARK_API_KEY="$(grep ARK_API_KEY /etc/defect-hunter.env | cut -d= -f2-)"
# 临时启动一下，确认能跑
python main.py serve --host 127.0.0.1 --port 8000 &
SERVER_PID=$!
sleep 3
curl -s http://127.0.0.1:8000/api/v1/health | python3 -m json.tool
kill $SERVER_PID
EOF
```

期望看到：

```json
{
  "status": "ok",
  "service": "defect-hunter",
  "version": "1.0.0",
  "llm_configured": true,
  "llm_provider": "volcengine",
  "llm_model": "glm-5.1"
}
```

`llm_configured=true` 说明 API Key 已加载成功。

---

## 3. systemd 托管

让服务开机自启 + 异常自动重启 + 日志接入 journald。

### 3.1 创建 service 单元

```bash
sudo tee /etc/systemd/system/defect-hunter.service > /dev/null <<'EOF'
[Unit]
Description=Defect Hunter API Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=defect
Group=defect
WorkingDirectory=/opt/defect-hunter
EnvironmentFile=/etc/defect-hunter.env
ExecStart=/opt/defect-hunter/.venv/bin/uvicorn src.api:app \
            --host 127.0.0.1 \
            --port 8000 \
            --workers 2 \
            --proxy-headers \
            --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5s

# 资源与安全限制
LimitNOFILE=65535
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/defect-hunter/data /opt/defect-hunter/reports /opt/defect-hunter/logs

# 日志输出
StandardOutput=journal
StandardError=journal
SyslogIdentifier=defect-hunter

[Install]
WantedBy=multi-user.target
EOF
```

### 3.2 启动并自启

```bash
sudo systemctl daemon-reload
sudo systemctl enable defect-hunter
sudo systemctl start defect-hunter

# 查看状态
sudo systemctl status defect-hunter

# 查看实时日志
sudo journalctl -u defect-hunter -f
```

期望看到：

```
INFO:     Started server process [12345]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 3.3 验证

```bash
curl -s http://127.0.0.1:8000/api/v1/health | python3 -m json.tool
```

---

## 4. Nginx 反向代理

### 4.1 编写站点配置

假设你的域名是 `defect-hunter.example.com`，没有域名就先用服务器 IP。

```bash
sudo tee /etc/nginx/sites-available/defect-hunter > /dev/null <<'EOF'
upstream defect_hunter_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# HTTP 入口（Let's Encrypt 申请前需要）
server {
    listen 80;
    listen [::]:80;
    server_name defect-hunter.example.com;

    # ACME Challenge（certbot 用）
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # 暂时直接代理（拿到证书后会改成 301 跳 HTTPS）
    location / {
        proxy_pass         http://defect_hunter_backend;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # 大文件上传支持（业务方上传台账可能很大）
        client_max_body_size 50m;

        # LLM 调用 + 编排子图较慢，给 5 分钟超时
        proxy_connect_timeout 30s;
        proxy_send_timeout    300s;
        proxy_read_timeout    300s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/defect-hunter /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 4.2 申请 Let's Encrypt 证书（HTTPS）

> 跳过此步即纯 HTTP 部署。生产环境强烈建议开 HTTPS。

```bash
# 安装 certbot（Ubuntu 22.04+）
sudo apt install -y certbot python3-certbot-nginx

# 自动配置并申请证书
sudo certbot --nginx -d defect-hunter.example.com \
              --agree-tos --email your@email.com \
              --redirect --non-interactive

# certbot 会自动改写 Nginx 配置加 SSL 段，并设置定时续期
sudo systemctl status certbot.timer
```

完成后访问 `https://defect-hunter.example.com/docs` 即可看到 Swagger UI。

---

## 5. 验证部署

### 5.1 各层连通性

```bash
# 1. 上游（uvicorn）
curl -s http://127.0.0.1:8000/api/v1/health

# 2. Nginx
curl -s http://defect-hunter.example.com/api/v1/health

# 3. HTTPS（如果开了）
curl -s https://defect-hunter.example.com/api/v1/health
```

### 5.2 业务流程联调

准备一份最小台账 JSON：

```bash
cat > /tmp/min-taizhang.json <<'EOF'
{
  "metadata": {"total": 2},
  "defects": [
    {
      "id": "TEST-001",
      "defect_name": "订单越权查看",
      "module": "安全漏洞",
      "defect_type": "CODE_ISSUE",
      "problem_type": "权限控制",
      "discovery_channel": "安全测试",
      "priority": "MEDIUM",
      "root_cause": "未校验用户身份"
    },
    {
      "id": "TEST-002",
      "defect_name": "复制活动奖品丢失",
      "module": "科技缺陷",
      "defect_type": "CODE_ISSUE",
      "discovery_channel": "测试发现",
      "priority": "MEDIUM",
      "root_cause": "复制链断裂"
    }
  ]
}
EOF

# 一键全套分析
curl -s -X POST https://defect-hunter.example.com/api/v1/analyze/upload \
     -F "file=@/tmp/min-taizhang.json" \
     -F "period=测试" | python3 -m json.tool | head -40
```

期望返回：包含 `summary` + 4 份 Markdown 报告。

---

## 6. 数据持久化与备份

### 6.1 关键数据目录

| 路径 | 内容 | 重要性 |
|------|------|--------|
| `/opt/defect-hunter/data/knowledge_base/` | 知识库 JSONL（业务核心资产） | 🔴 关键 |
| `/opt/defect-hunter/reports/` | 历史生成的报告 | 🟡 一般 |
| `/opt/defect-hunter/config/config.yaml` | 配置 | 🟡 一般 |
| `/etc/defect-hunter.env` | API Key | 🔴 关键 |

### 6.2 每日备份脚本（cron）

```bash
sudo tee /usr/local/bin/defect-hunter-backup.sh > /dev/null <<'EOF'
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/defect-hunter
DATE=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

tar czf "$BACKUP_DIR/defect-kb-$DATE.tar.gz" \
    -C /opt/defect-hunter data/knowledge_base \
    -C /opt/defect-hunter config

# 保留最近 30 天
find "$BACKUP_DIR" -name 'defect-kb-*.tar.gz' -mtime +30 -delete

logger -t defect-hunter-backup "知识库备份完成: $DATE"
EOF

sudo chmod +x /usr/local/bin/defect-hunter-backup.sh

# 每天凌晨 3 点
echo "0 3 * * * root /usr/local/bin/defect-hunter-backup.sh" \
    | sudo tee /etc/cron.d/defect-hunter-backup
```

> 生产环境建议把 `/var/backups/defect-hunter/` 通过 `rsync` / 对象存储再异地备份一份。

---

## 7. 日常运维

### 7.1 常用命令

```bash
# 查看服务状态
sudo systemctl status defect-hunter

# 重启
sudo systemctl restart defect-hunter

# 实时日志
sudo journalctl -u defect-hunter -f

# 最近一小时的错误日志
sudo journalctl -u defect-hunter --since "1 hour ago" -p err

# 查看 Nginx 访问日志
sudo tail -f /var/log/nginx/access.log

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log

# 知识库当前条数
wc -l /opt/defect-hunter/data/knowledge_base/defects.jsonl
```

### 7.2 升级代码

```bash
sudo -u defect bash <<'EOF'
cd /opt/defect-hunter
git fetch origin
git pull origin main
. .venv/bin/activate
pip install -r requirements.txt
EOF

sudo systemctl restart defect-hunter
sudo journalctl -u defect-hunter --since "1 minute ago"
```

### 7.3 切换 LLM Provider

不需要改代码，只改配置：

```bash
# 切换到 OpenAI
sudo -u defect vim /opt/defect-hunter/config/config.yaml
# 把 llm.provider 改为 "openai"，model 改为 "gpt-4o-mini"
# 把 api_key 改为 "${OPENAI_API_KEY}"

# 在环境文件追加 key
echo 'OPENAI_API_KEY=sk-xxx' | sudo tee -a /etc/defect-hunter.env
sudo systemctl restart defect-hunter
```

### 7.4 调整 worker 数

修改 `/etc/systemd/system/defect-hunter.service` 里的 `--workers 2`：

- 1 核机器：`--workers 1`
- 2 核机器：`--workers 2`（默认）
- 4 核机器：`--workers 3`（留 1 核给系统/Nginx）
- 8 核及以上：`--workers $((CPU - 2))`

```bash
sudo systemctl daemon-reload
sudo systemctl restart defect-hunter
```

---

## 8. 故障排查

### 8.1 服务起不来

```bash
sudo journalctl -u defect-hunter -n 100 --no-pager
```

常见原因：

| 报错关键字 | 处理 |
|-----------|------|
| `ModuleNotFoundError: No module named 'xxx'` | 在虚拟环境中 `pip install -r requirements.txt` |
| `Address already in use` | 8000 端口被占用，`sudo ss -tlnp \| grep 8000` 找进程杀掉 |
| `Permission denied: /opt/defect-hunter/...` | 目录权限：`sudo chown -R defect:defect /opt/defect-hunter` |
| `KeyError: 'ARK_API_KEY'` | `/etc/defect-hunter.env` 没写或写错 |

### 8.2 Health Check 显示 `llm_configured: false`

```bash
# 1. 确认环境文件存在且可读
sudo -u defect cat /etc/defect-hunter.env

# 2. 确认 systemd 加载了
sudo systemctl show defect-hunter | grep -i environment

# 3. 重启使生效
sudo systemctl restart defect-hunter
```

### 8.3 调用 LLM 报错

```bash
# 测试网络出站
sudo -u defect curl -I https://ark.cn-beijing.volces.com/api/v3/chat/completions
# 应该返回 4xx（未带 token），但不应是连接失败
```

如果是 `Could not resolve host`：DNS 问题，检查 `/etc/resolv.conf`。
如果是 `Connection timed out`：服务商安全组没放行出站。

### 8.4 上传大台账文件报 413

修改 Nginx 的 `client_max_body_size`（已默认 `50m`）：

```bash
sudo vim /etc/nginx/sites-available/defect-hunter
# 把 50m 改大，如 200m
sudo nginx -t && sudo systemctl reload nginx
```

### 8.5 LLM 响应慢导致 Nginx 504

调大 Nginx 超时：

```nginx
proxy_read_timeout 600s;   # 改成 10 分钟
```

或检查火山引擎方舟控制台的限流配额。

### 8.6 磁盘占满

```bash
# 查看哪里大
sudo du -sh /opt/defect-hunter/* /var/log/* | sort -hr | head

# 知识库累积太多 → 归档老旧记录
# Nginx 日志 → 已默认 logrotate

# journald 日志 → 限制大小
sudo journalctl --vacuum-size=500M
```

---

## 9. 安全加固（可选但推荐）

### 9.1 SSH 加固

```bash
sudo vim /etc/ssh/sshd_config
# PermitRootLogin no
# PasswordAuthentication no   # 仅密钥登录
# Port 22                      # 改为非标端口减少扫描
sudo systemctl restart ssh
```

### 9.2 限制 API 访问 IP

如果业务方都在公司内网/堡垒机，在 Nginx 加白名单：

```nginx
location /api/v1/ {
    allow 203.0.113.0/24;       # 公司出口 IP
    allow 198.51.100.42;        # 堡垒机
    deny  all;

    proxy_pass http://defect_hunter_backend;
    # ... 其他 proxy 配置
}
```

### 9.3 给 API 加 token 鉴权（最小改动）

在 Nginx 层加 header 校验（适合内部使用）：

```nginx
location /api/v1/ {
    if ($http_x_api_token != "your-secret-token-here") {
        return 401;
    }
    proxy_pass http://defect_hunter_backend;
    # ...
}
```

后续如需更复杂鉴权（OAuth/JWT），在 FastAPI 层加 `Depends`。

### 9.4 fail2ban（暴力扫描防护）

```bash
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
```

---

## 10. 卸载

```bash
# 停服务
sudo systemctl stop defect-hunter
sudo systemctl disable defect-hunter
sudo rm /etc/systemd/system/defect-hunter.service
sudo systemctl daemon-reload

# 清 Nginx
sudo rm /etc/nginx/sites-enabled/defect-hunter \
        /etc/nginx/sites-available/defect-hunter
sudo systemctl reload nginx

# 备份知识库后再删数据
sudo cp /opt/defect-hunter/data/knowledge_base/defects.jsonl ~/defect-kb-backup.jsonl

sudo rm -rf /opt/defect-hunter
sudo rm /etc/defect-hunter.env
sudo userdel -r defect

# 清 cron
sudo rm /etc/cron.d/defect-hunter-backup
sudo rm /usr/local/bin/defect-hunter-backup.sh
```

---

## 附录 A：一键部署脚本

把 1-3 节的步骤压缩成一个脚本（**先看清楚再用**），保存为 `deploy/install.sh`：

```bash
#!/bin/bash
set -euo pipefail

REPO_URL="${1:-请填仓库地址}"
ARK_API_KEY="${ARK_API_KEY:?请先 export ARK_API_KEY=...}"

echo "[1/6] 安装基础依赖..."
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip nginx ufw

echo "[2/6] 创建用户..."
sudo useradd -m -s /bin/bash defect 2>/dev/null || true
sudo mkdir -p /opt/defect-hunter
sudo chown -R defect:defect /opt/defect-hunter

echo "[3/6] 拉取代码..."
sudo -u defect git clone "$REPO_URL" /opt/defect-hunter

echo "[4/6] 安装依赖..."
sudo -u defect bash -c '
cd /opt/defect-hunter
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
mkdir -p data/samples data/knowledge_base reports logs
'

echo "[5/6] 写入 API Key..."
sudo bash -c "cat > /etc/defect-hunter.env <<EOL
ARK_API_KEY=$ARK_API_KEY
EOL"
sudo chmod 600 /etc/defect-hunter.env
sudo chown defect:defect /etc/defect-hunter.env

echo "[6/6] 安装 systemd 服务..."
sudo cp /opt/defect-hunter/deploy/defect-hunter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now defect-hunter

sleep 2
curl -s http://127.0.0.1:8000/api/v1/health | python3 -m json.tool

echo ""
echo "✅ 部署完成。Nginx 配置请参考 deploy/nginx-defect-hunter.conf"
```

## 附录 B：参考文件

下方文件已提交到仓库 `deploy/` 目录：

- [`deploy/defect-hunter.service`](./defect-hunter.service) - systemd 单元文件
- [`deploy/nginx-defect-hunter.conf`](./nginx-defect-hunter.conf) - Nginx 站点配置
- [`deploy/install.sh`](./install.sh) - 一键部署脚本
- [`deploy/backup.sh`](./backup.sh) - 备份脚本

---

## 附录 C：检查清单（Go-Live 前过一遍）

- [ ] 服务器能 `curl -I https://ark.cn-beijing.volces.com` 成功
- [ ] `/etc/defect-hunter.env` 文件权限是 `600`，所有者是 `defect`
- [ ] `systemctl status defect-hunter` 显示 `active (running)`
- [ ] `curl http://127.0.0.1:8000/api/v1/health` 返回 `llm_configured: true`
- [ ] Nginx 配置 `nginx -t` 通过
- [ ] 公网 `https://your-domain/docs` 能打开 Swagger UI
- [ ] 一键全套分析端点 `/api/v1/analyze/upload` 能正常返回报告
- [ ] `cron` 定时备份已启用
- [ ] 防火墙仅开 22/80/443
- [ ] systemd 设置了 `Restart=on-failure`，杀进程后能自愈
- [ ] 重启服务器后服务自动起来

---

**部署文档版本**：v1.0（2026-06-12）
**适用项目版本**：V1 全部能力 + V2 服务化 MVP + 火山引擎接入
