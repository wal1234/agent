#!/bin/bash
# 缺陷神探 - 知识库 + 配置每日备份
#
# 安装：
#   sudo cp backup.sh /usr/local/bin/defect-hunter-backup.sh
#   sudo chmod +x /usr/local/bin/defect-hunter-backup.sh
#   echo "0 3 * * * root /usr/local/bin/defect-hunter-backup.sh" | sudo tee /etc/cron.d/defect-hunter-backup

set -euo pipefail

APP_DIR=/opt/defect-hunter
BACKUP_DIR=/var/backups/defect-hunter
RETENTION_DAYS=30

DATE=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

# 备份知识库 + 配置（不备份代码，代码在 git 里）
tar czf "$BACKUP_DIR/defect-kb-$DATE.tar.gz" \
    -C "$APP_DIR" data/knowledge_base \
    -C "$APP_DIR" config 2>&1 | logger -t defect-hunter-backup

# 清理过期备份
find "$BACKUP_DIR" -name 'defect-kb-*.tar.gz' -mtime "+$RETENTION_DAYS" -delete

# 摘要
SIZE=$(du -h "$BACKUP_DIR/defect-kb-$DATE.tar.gz" | cut -f1)
COUNT=$(ls "$BACKUP_DIR"/defect-kb-*.tar.gz 2>/dev/null | wc -l)
KB_LINES=$(wc -l < "$APP_DIR/data/knowledge_base/defects.jsonl" 2>/dev/null || echo 0)

logger -t defect-hunter-backup \
    "备份完成: $DATE 大小=$SIZE KB条数=$KB_LINES 总备份数=$COUNT"
