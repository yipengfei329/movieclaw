#!/usr/bin/env bash
# =============================================================================
# movieclaw Docker 镜像构建脚本
#
# 用法：
#   ./scripts/build-image.sh                  # 构建 movieclaw:latest（本机架构）
#   TAG=v0.1.0 ./scripts/build-image.sh       # 指定标签
#   PLATFORM=linux/amd64 ./scripts/build-image.sh   # 交叉构建 NAS 常见的 x86_64
#   CN_MIRROR=1 ./scripts/build-image.sh      # 国内镜像源加速（pip 清华源 + npmmirror）
#
# TMDB Key 读取顺序：环境变量 TMDB_API_KEY > 仓库根目录 .env 中的 TMDB_API_KEY。
# Key 只通过 --build-arg 传入，不进入构建上下文（.env 已被 .dockerignore 排除）。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG="${TAG:-latest}"
IMAGE="movieclaw:${TAG}"

# 读取 TMDB Key
TMDB_KEY="${TMDB_API_KEY:-}"
if [[ -z "$TMDB_KEY" && -f .env ]]; then
    TMDB_KEY="$(grep -E '^TMDB_API_KEY=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true)"
fi
if [[ -z "$TMDB_KEY" ]]; then
    echo "错误：未找到 TMDB_API_KEY（设置环境变量，或写入仓库根目录 .env）" >&2
    exit 1
fi

BUILD_ARGS=(--build-arg "TMDB_API_KEY=$TMDB_KEY")

# 国内网络加速
if [[ "${CN_MIRROR:-0}" == "1" ]]; then
    BUILD_ARGS+=(
        --build-arg "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
        --build-arg "NPM_REGISTRY=https://registry.npmmirror.com"
    )
fi

# 交叉构建（如在 Apple Silicon 上给 x86_64 NAS 出镜像）
if [[ -n "${PLATFORM:-}" ]]; then
    BUILD_ARGS+=(--platform "$PLATFORM")
fi

echo "构建 $IMAGE ……"
docker build "${BUILD_ARGS[@]}" -t "$IMAGE" .
echo "完成：$IMAGE"
