# movieclaw

自部署的 PT 影视资源自动化工具：聚合搜索、订阅追更、自动下载，配一个现代化的 Web 控制台。

> ⚠️ 本项目仅供个人学习与技术交流。使用前请阅读并遵守你所在站点的规则，聚合搜索与自动化操作请合理控制频率。

## 功能

- **聚合搜索**：多站点并发搜索。内置 NexusPHP 框架适配，接入新站点只需一份 YAML 配置（选择器声明式描述），特殊站点可用少量 Python 代码定制
- **搜索结果实体化**：内置 NER 模型解析种子标题（片名 / 年份 / 季集 / 分类），搜索结果按影视作品分组展示，而不是一长串原始种子名
- **发现页**：基于 TMDB / 豆瓣的热门影视浏览，海报墙 + 详情页
- **订阅追更**：订阅影视作品，定时在各站点匹配新资源并投递到下载器
- **下载器管理**：支持 qBittorrent 与 Transmission，搜索结果一键推送下载
- **LLM 助手**：对话式 agent，可调用内置工具协助资源管理；预设 OpenAI / DeepSeek / Kimi / GLM / 阿里云百炼等供应商，任何 OpenAI 兼容端点（Ollama、vLLM…）加一份 YAML 即可接入
- **浏览器扩展**：站点 Cookie 一键同步到后端，Cookie 变化后台自动保持最新，无需手动复制
- **Web 控制台**：Next.js + Tailwind CSS，液态玻璃风格，深浅色主题，支持自定义背景
- **开箱即用的基础设施**：SQLite 存储 + 启动时自动迁移、图片缓存代理、系统日志在线查看

## 技术栈

| 部分 | 技术 |
| --- | --- |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy + Alembic / SQLite |
| 前端 | Next.js (App Router) / TypeScript / Tailwind CSS |
| 浏览器扩展 | WXT (Manifest V3) |
| NER 模型 | 自训多任务模型，int8 ONNX，CPU 推理 |

## 快速开始

### 一键启动（推荐）

```bash
./scripts/dev.sh          # 同时启动后端和前端
./scripts/dev.sh api      # 只启动后端
./scripts/dev.sh web      # 只启动前端
```

脚本会自动完成首次环境准备（创建虚拟环境、安装依赖、生成 `.env`、`pnpm install`），
日志带 `[api]` / `[web]` 彩色前缀区分来源，按 `Ctrl-C` 一键停止全部服务。

### 手动安装

```bash
# 后端（Python 3.11+）
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn movieclaw_api.main:app --factory --reload

# 前端（Node.js 20+）
pnpm install
pnpm web:dev
```

启动后访问：

- Web 控制台：`http://127.0.0.1:3000`
- API 文档（Swagger UI）：`http://127.0.0.1:8000/docs`

### 首次使用

1. 打开控制台，首次访问会引导进入初始化页面，创建管理员账号
2. 在「设置 → 站点」配置 PT 站点 Cookie（或安装浏览器扩展自动同步）
3. 在「设置 → 下载器」接入 qBittorrent / Transmission
4. 需要发现页时在 `.env` 配置 `TMDB_API_KEY`（见 [.env.example](.env.example)），需要 LLM 助手时在「设置 → LLM」填入供应商密钥

## 模型文件（种子名结构化抽取）

搜索结果实体化功能依赖一个本地 NER 模型（int8 量化 ONNX，CPU 即可推理）。
模型不随代码仓库分发，请从 [Releases](https://github.com/yipengfei329/movieclaw/releases) 下载
`model.int8.onnx`、`tokenizer.json`、`labels.json` 三个文件，放入部署目录的
`data/models/torrent-ner/` 后重启服务（目录可用环境变量 `MOVIECLAW_NER_DIR` 覆盖）。

未放置模型时服务可正常启动，仅种子名结构化抽取功能不可用，日志中会有明确提示。

## 项目结构

```text
movieclaw/
├── src/                       # Python 后端（按领域拆分为多个包）
│   ├── movieclaw_api/         # FastAPI 应用：路由、配置、统一响应与异常处理
│   ├── movieclaw_tracker/     # PT 站点适配：NexusPHP 框架 + YAML 站点配置
│   ├── movieclaw_enrich/      # 种子标题结构化抽取（NER 模型推理与后处理）
│   ├── movieclaw_matcher/     # 订阅与站点资源的匹配规则
│   ├── movieclaw_downloader/  # 下载器客户端（qBittorrent / Transmission）
│   ├── movieclaw_media/       # 影视元数据（TMDB / 豆瓣）
│   ├── movieclaw_llm/         # LLM 接入层：供应商预设与路由
│   ├── movieclaw_agent/       # 对话式 agent：工具调用与会话事件流
│   ├── movieclaw_scheduler/   # 定时任务调度
│   ├── movieclaw_db/          # 数据模型与持久化
│   └── movieclaw_cache/       # 通用持久缓存（SWR 双 TTL）
├── apps/
│   ├── web/                   # Next.js Web 控制台
│   └── extension/             # 浏览器扩展（Cookie 同步）
├── alembic/                   # 数据库迁移（启动时自动执行）
├── ml/                        # NER 模型的训练管线（训练数据与产物不入库）
├── tests/                     # 后端测试
└── scripts/dev.sh             # 本地开发一键启动
```

## 开发

```bash
pytest                 # 后端测试
ruff check . && ruff format .   # 后端检查与格式化
pnpm web:lint          # 前端 lint
pnpm web:typecheck     # 前端类型检查
pnpm ext:build         # 构建浏览器扩展
```

约定：

- 业务接口成功响应统一 `success/code/message/data`，错误响应统一 `success/code/message/details`
- 运行期数据（SQLite、日志、图片缓存、上传文件、模型）全部落在 `data/` 目录，部署时挂载该目录即可持久化
- 站点接入方式见 [src/movieclaw_tracker/sites/configs/_template.yaml](src/movieclaw_tracker/sites/configs/_template.yaml)

## License

[MIT](LICENSE)
