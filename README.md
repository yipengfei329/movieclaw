# movieclaw

一个面向 HTTP 接口服务和控制台界面的单仓项目，当前包含：

- `FastAPI` 后端服务
- `Next.js + Tailwind CSS` Web 控制台骨架

重点放在：

- 清晰的分层结构
- 类型安全和配置管理
- 易测试、易扩展
- 本地开发体验友好
- 统一响应、异常处理和访问日志

## 为什么选择 FastAPI

当前这个阶段你需要的是“先把服务框架搭好，再逐步加业务功能”，`FastAPI` 很适合做这个基线：

- 基于类型注解，接口定义、参数校验、响应模型都很自然
- 自带 OpenAPI 文档，后续联调方便
- 异步支持成熟，适合后续接入爬虫、外部 HTTP、队列或数据库
- 社区成熟，和 `Pydantic`、`Uvicorn`、`httpx`、`pytest` 配合顺手

## 项目结构

```text
movieclaw/
├── package.json
├── pnpm-workspace.yaml
├── pyproject.toml
├── README.md
├── .env.example
├── apps/
│   └── web/
│       ├── app/
│       ├── lib/
│       ├── public/
│       └── package.json
├── src/
│   └── movieclaw_api/
│       ├── api/
│       │   ├── router.py
│       │   └── routes/
│       │       └── health.py
│       ├── core/
│       │   ├── config.py
│       │   └── logging.py
│       ├── schemas/
│       │   └── response.py
│       ├── app.py
│       ├── exceptions.py
│       ├── handlers.py
│       └── main.py
└── tests/
    ├── test_api_infra.py
    └── test_health.py
```

## 快速开始

推荐先创建虚拟环境并安装开发依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

建议：

- 本地现在用 Python 3.9+ 就可以跑
- 如果是新项目长期维护，推荐优先使用 Python 3.11
- 前端建议使用 Node.js 20+，当前这套 `Next.js + Tailwind CSS v4` 基线按 Node 20+ 运行

启动服务：

```bash
uvicorn movieclaw_api.main:app --factory --reload
```

或者使用脚本入口：

```bash
movieclaw-api
```

服务启动后可访问：

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- 健康检查: `http://127.0.0.1:8000/api/v1/health`

## Web 控制台

前端位于 `apps/web`，采用：

- `Next.js App Router`
- `TypeScript`
- `Tailwind CSS`

前端默认通过 `NEXT_PUBLIC_API_BASE_URL=/api/v1` 访问后端。  
本地开发时，Next.js 会把 `/api/v1/*` 请求代理到 `http://127.0.0.1:8000`，这样前端仍然可以按同域 API 方式开发。

初始化前端依赖：

```bash
pnpm install
```

启动前端开发服务：

```bash
pnpm web:dev
```

常用前端命令：

```bash
pnpm web:lint
pnpm web:typecheck
pnpm web:build
```

前端默认访问地址：

- Web 首页: `http://127.0.0.1:3000`
- 健康检查页: `http://127.0.0.1:3000/health`

如果本机 Node 环境有问题，也可以用 Docker 中的 Node 镜像执行上述 `pnpm` 命令。

## 开发命令

运行测试：

```bash
pytest
```

检查与格式化：

```bash
ruff check .
ruff format .
```

## 基础约定

- 业务接口成功响应统一使用 `success/code/message/data`
- 错误响应统一使用 `success/code/message/details`
- `/api/v1/health` 保持轻量原生返回
- 访问日志默认开启，可通过 `APP_ACCESS_LOG_ENABLED=false` 关闭
- 日志级别可通过 `APP_LOG_LEVEL` 调整

## 后续推荐演进方向

等你准备进入下一步时，我们可以继续往下加：

1. 统一异常处理和错误码设计
2. 结构化日志与请求追踪
3. 数据库接入（SQLAlchemy / SQLModel）
4. 配置分环境管理
5. Docker / CI / 部署模板
