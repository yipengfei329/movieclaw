# syntax=docker/dockerfile:1
# =============================================================================
# movieclaw 单容器镜像：Next.js 前端 + FastAPI 后端 + NER 模型，一个容器跑全部。
#
# 设计要点：
#   - 前端 standalone 输出：只带被引用的依赖，不装完整 node_modules
#   - 后端只装运行依赖（从 pyproject 提取），源码按项目布局摆放（不 pip install
#     打包——启动迁移按「源码根目录」定位 alembic.ini，见 movieclaw_db/migrations.py）
#   - NER 模型从 GitHub Release 下载后烧进镜像，开箱即用，无需用户手动放置
#   - TMDB Key 通过构建参数烧进镜像（运行时可用环境变量覆盖）
#   - 对外只暴露前端 3000 端口，/api/v1 由 Next 反代到容器内的后端
#   - 运行期数据全部落在 /app/data，挂载这一个卷即可持久化
#
# 构建（推荐用 scripts/build-image.sh，会自动带上 TMDB Key）：
#   docker build --build-arg TMDB_API_KEY=xxx -t movieclaw:latest .
#
# 国内网络加速（可选）：
#   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
#   --build-arg NER_MODEL_BASE=<GitHub Release 的镜像加速地址>
# =============================================================================

# ---------------------------------------------------------------------------
# 阶段 1：前端构建（含浏览器扩展 zip，供设置页下载）
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web-builder
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install -g pnpm@10
WORKDIR /build

# 源码要在 install 之前就位：extension 的 postinstall（wxt prepare）依赖 entrypoints/ 源码
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json ./
COPY apps ./apps
RUN pnpm config set registry "$NPM_REGISTRY" && pnpm install --frozen-lockfile
# 构建浏览器扩展并放进 web 静态目录（设置页「浏览器插件」提供下载）
RUN pnpm ext:zip \
    && mkdir -p apps/web/public/extension \
    && cp "$(ls -t apps/extension/.output/*-chrome.zip | head -1)" apps/web/public/extension/movieclaw-extension.zip
RUN pnpm web:build

# ---------------------------------------------------------------------------
# 阶段 2：后端运行依赖（只装 pyproject 的 dependencies，不装 dev 工具、不打包源码）
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS py-deps
ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /build
COPY pyproject.toml ./
RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('requirements.txt', 'w').write('\n'.join(deps))" \
    && python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

# ---------------------------------------------------------------------------
# 阶段 3：NER 模型（从 GitHub Release 下载，烧进镜像作为默认模型）
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS ner-model
ARG NER_MODEL_BASE=https://github.com/yipengfei329/movieclaw/releases/download/torrent-ner-v1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /model \
    && cd /model \
    && curl -fSL --retry 3 -O "$NER_MODEL_BASE/model.int8.onnx" \
    && curl -fSL --retry 3 -O "$NER_MODEL_BASE/tokenizer.json" \
    && curl -fSL --retry 3 -O "$NER_MODEL_BASE/labels.json"

# ---------------------------------------------------------------------------
# 阶段 4：运行镜像
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

# onnxruntime / tokenizers 的 manylinux wheel 依赖 libstdc++（slim 基础镜像可能不带）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 运行时：只拷贝 node 二进制（跑 Next standalone server 足够），不装 npm
COPY --from=web-builder /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# 后端：venv + 源码布局（src / alembic / alembic.ini 的相对位置必须保持）
COPY --from=py-deps /venv /venv
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

# 前端：standalone 产物 + 静态资源 + public（standalone 不自动包含后两者）
COPY --from=web-builder /build/apps/web/.next/standalone ./web
COPY --from=web-builder /build/apps/web/.next/static ./web/apps/web/.next/static
COPY --from=web-builder /build/apps/web/public ./web/apps/web/public

# NER 模型：镜像内只读目录，不占用户的 data 卷；MOVIECLAW_NER_DIR 指过来
COPY --from=ner-model /model ./models/torrent-ner

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# TMDB API Key 在构建时烧入镜像（部署者可用同名环境变量覆盖）
ARG TMDB_API_KEY=""
ENV TMDB_API_KEY=${TMDB_API_KEY}

ENV PATH="/venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    # 生产镜像关闭 uvicorn 热重载（本地开发默认值是开）
    APP_RELOAD=false \
    # 发布镜像默认真实投递订阅（代码默认 dry-run 是开发期的保护）
    SUBSCRIPTION_DISPATCH_DRY_RUN=false \
    MOVIECLAW_NER_DIR=/app/models/torrent-ner

# 运行期数据（SQLite、日志、缓存、上传、密钥）全部落在这个目录
VOLUME /app/data

EXPOSE 3000

# 穿透前端反代打后端健康接口：一次验证 Next 进程、反代链路、FastAPI 三者
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/v1/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

ENTRYPOINT ["/entrypoint.sh"]
