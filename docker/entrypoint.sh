#!/bin/bash
# =============================================================================
# movieclaw 容器入口：一个容器同时跑 FastAPI 后端和 Next.js 前端。
#
# 进程模型：
#   - 后端监听容器内 8000（不对外），前端监听 3000（对外唯一端口）
#   - /api/v1 请求由 Next 服务器反代到后端（构建时固化的 rewrite 规则）
#   - 任意一个进程退出，整个容器退出（交给 Docker 的 restart 策略拉起），
#     避免出现"半死"状态：前端活着但后端已挂
# =============================================================================
set -euo pipefail

cd /app

# 数据库迁移由后端启动时自动执行（movieclaw_db/migrations.py），无需在此处理

echo "[entrypoint] 启动后端 (FastAPI, 127.0.0.1:8000)……"
python -m movieclaw_api.main &
API_PID=$!

echo "[entrypoint] 启动前端 (Next.js, 0.0.0.0:3000)……"
PORT=3000 HOSTNAME=0.0.0.0 node /app/web/apps/web/server.js &
WEB_PID=$!

# 收到停止信号时把两个子进程都带走，确保容器干净退出
shutdown() {
    kill "$API_PID" "$WEB_PID" 2>/dev/null || true
}
trap shutdown TERM INT

# 任一进程退出即结束容器（wait -n 等最先退出的那个）
wait -n "$API_PID" "$WEB_PID"
EXIT_CODE=$?
echo "[entrypoint] 有进程退出（exit=$EXIT_CODE），停止容器……"
shutdown
wait || true
exit "$EXIT_CODE"
