#!/usr/bin/env bash
# =============================================================================
# movieclaw 本地开发一键启动脚本
#
# 用法：
#   ./scripts/dev.sh          # 同时启动后端 (FastAPI) 和前端 (Next.js)
#   ./scripts/dev.sh api      # 只启动后端
#   ./scripts/dev.sh web      # 只启动前端
#
# 设计要点：
#   - 首次运行自动完成环境准备：创建 .venv、安装依赖、生成 .env、pnpm install
#   - 后端/前端日志加彩色前缀 [api] / [web]，混合输出也能一眼区分来源
#   - Ctrl-C 一次性停掉所有进程，不留孤儿进程占用端口
#   - 代码改动自动热重载（后端 uvicorn --reload，前端 Next.js 自带）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-all}"
if [[ "$MODE" != "all" && "$MODE" != "api" && "$MODE" != "web" ]]; then
    echo "用法：$0 [all|api|web]（默认 all）" >&2
    exit 1
fi

# 彩色输出（终端不支持时自动降级为纯文本）
if [[ -t 1 ]]; then
    C_API=$'\033[36m'; C_WEB=$'\033[35m'; C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_END=$'\033[0m'
else
    C_API=""; C_WEB=""; C_OK=""; C_ERR=""; C_END=""
fi

info() { echo "${C_OK}[dev]${C_END} $*"; }
fail() { echo "${C_ERR}[dev] 错误：$*${C_END}" >&2; exit 1; }

# 从 .env 读取后端端口（未配置时使用默认值 8000）
read_api_port() {
    local port=""
    if [[ -f .env ]]; then
        port="$(grep -E '^APP_PORT=' .env | tail -1 | cut -d= -f2 | tr -d '[:space:]"'"'" || true)"
    fi
    echo "${port:-8000}"
}

# ---------------------------------------------------------------------------
# 环境准备：缺什么补什么，重复运行不会做多余的事
# ---------------------------------------------------------------------------

prepare_env_file() {
    if [[ ! -f .env ]]; then
        cp .env.example .env
        info "未找到 .env，已从 .env.example 复制生成"
    fi
}

prepare_python() {
    # venv 存在且解释器可用（项目目录移动过会导致软链接失效）则直接复用
    if [[ -x .venv/bin/python ]] && .venv/bin/python --version >/dev/null 2>&1; then
        return
    fi

    info "虚拟环境不存在或已失效，开始重建 .venv ..."
    rm -rf .venv

    # 按新到旧的顺序寻找可用的 Python（项目要求 3.11+）
    local python_bin=""
    for candidate in python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1; then
            python_bin="$candidate"
            break
        fi
    done
    [[ -n "$python_bin" ]] || fail "未找到 Python 3.11+，请先安装（macOS 可执行：brew install python@3.14）"

    info "使用 $("$python_bin" --version) 创建虚拟环境"
    "$python_bin" -m venv .venv

    info "安装后端依赖（pip install -e '.[dev]'）..."
    if ! .venv/bin/pip install --quiet -e ".[dev]"; then
        fail "依赖安装失败。如果是网络问题，可尝试国内镜像：
  .venv/bin/pip install -e '.[dev]' -i https://pypi.tuna.tsinghua.edu.cn/simple"
    fi
    info "后端环境准备完成"
}

prepare_node() {
    command -v pnpm >/dev/null 2>&1 || fail "未找到 pnpm，请先安装（npm install -g pnpm）"
    if [[ ! -d apps/web/node_modules ]]; then
        info "前端依赖未安装，执行 pnpm install ..."
        pnpm install
    fi
}

# 端口被占用时直接报错退出，避免新旧进程混跑导致调试困惑
check_port_free() {
    local port="$1" name="$2"
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        fail "端口 ${port} 已被占用（${name} 无法启动）。查看占用进程：lsof -nP -iTCP:${port} -sTCP:LISTEN"
    fi
}

# ---------------------------------------------------------------------------
# 进程管理：统一前缀输出 + 一键停止
# ---------------------------------------------------------------------------

# 以指定前缀运行命令，将 stdout/stderr 每行加上彩色标签
run_with_prefix() {
    local tag="$1" color="$2"
    shift 2
    "$@" 2>&1 | while IFS= read -r line; do
        printf '%s[%s]%s %s\n' "$color" "$tag" "$C_END" "$line"
    done
}

cleanup() {
    trap - INT TERM EXIT
    echo ""
    info "正在停止所有服务..."
    # 结束整个进程组（包括 uvicorn 热重载的子进程和 Next.js）
    kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# 等待后端健康检查通过后打印访问地址汇总（后台执行，不阻塞日志输出）
wait_and_print_urls() {
    local api_port="$1"
    for _ in $(seq 1 60); do
        if curl -fsS "http://127.0.0.1:${api_port}/api/v1/health" >/dev/null 2>&1; then
            echo ""
            info "后端已就绪 ✔"
            info "  API 文档:  http://127.0.0.1:${api_port}/docs"
            info "  健康检查:  http://127.0.0.1:${api_port}/api/v1/health"
            [[ "$MODE" != "api" ]] && info "  Web 控制台: http://127.0.0.1:3000"
            echo ""
            return
        fi
        sleep 0.5
    done
    info "后端 30 秒内未通过健康检查，请留意上方 [api] 日志中的报错"
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

prepare_env_file
API_PORT="$(read_api_port)"

if [[ "$MODE" != "web" ]]; then
    prepare_python
    check_port_free "$API_PORT" "后端"
fi
if [[ "$MODE" != "api" ]]; then
    prepare_node
    check_port_free 3000 "前端"
fi

if [[ "$MODE" != "web" ]]; then
    # 注意：中文字符串内的变量必须用 ${} 花括号，macOS 自带的 bash 3.2
    # 解析多字节字符有 bug，$VAR 后面直接跟全角符号会把变量名解析错
    info "启动后端 FastAPI（端口 ${API_PORT}，代码改动自动重载）..."
    # movieclaw-api 入口会读取 .env 中的 APP_HOST/APP_PORT/APP_RELOAD 配置
    run_with_prefix api "$C_API" .venv/bin/movieclaw-api &
    wait_and_print_urls "$API_PORT" &
fi

if [[ "$MODE" != "api" ]]; then
    info "启动前端 Next.js（端口 3000）..."
    run_with_prefix web "$C_WEB" pnpm web:dev &
fi

info "全部服务已启动，按 Ctrl-C 一键停止"
wait
