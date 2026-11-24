#!/bin/bash
# CEO助手 一键启动/恢复脚本（远端 guest@39.105.85.124）
set -e
cd /home/guest/ceo-assistant/backend
# 1. 确保审计库容器在跑（重启策略 unless-stopped，宕机时用这条兜底）
docker ps --filter name=ceo-audit-postgres --filter status=running --format "{{.Names}}" | grep -q ceo-audit-postgres || docker start ceo-audit-postgres
sleep 3
# 2. 若 8002 已活则直接退出
if ss -ltn | grep -q ":8002 "; then echo "CEO助手已在运行 (8002)"; exit 0; fi
# 3. 后台拉起 uvicorn
nohup .venv/bin/python -m uvicorn app.main_v3:app --host 0.0.0.0 --port 8002 >> uvicorn.log 2>&1 &
sleep 6
# 4. 验证
ss -ltn | grep -q ":8002 " && echo "OK: 8002 listening (pid $(pgrep -f "uvicorn app.main_v3" | head -1))" || { echo "FAILED, log tail:"; tail -15 uvicorn.log; exit 1; }
curl -s -o /dev/null -w "GET / -> %{http_code}\n" http://127.0.0.1:8002/
