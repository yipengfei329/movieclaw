.PHONY: install api web test test-all lint format check api-types help

.DEFAULT_GOAL := help

install: ## 初始化开发环境（Python + Node.js 依赖）
	uv sync --all-extras
	pnpm install

api: ## 启动后端 API 服务（热重载模式）
	uv run uvicorn movieclaw_api.main:app --factory --reload

web: ## 启动前端开发服务器
	pnpm web:dev

test: ## 运行单元测试（跳过集成测试）
	uv run pytest -m "not integration"

test-all: ## 运行完整测试套件（含集成测试，需配置 .env）
	uv run pytest

lint: ## 检查代码规范（Python + 前端 lint + 类型检查）
	uv run ruff check .
	pnpm web:lint
	pnpm web:typecheck

format: ## 自动修复代码格式（Python）
	uv run ruff format .
	uv run ruff check --fix .

check: lint test ## 运行所有质量检查（lint + test）

api-types: ## 从运行中的后端生成 OpenAPI TypeScript 类型（需先 make api）
	pnpm api:types

help: ## 显示可用命令列表
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "用法: make <命令>"
