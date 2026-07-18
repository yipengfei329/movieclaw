from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="movieclaw", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")
    reload: bool = Field(default=True, alias="APP_RELOAD")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    access_log_enabled: bool = Field(default=True, alias="APP_ACCESS_LOG_ENABLED")
    # ------------------------------------------------------------------
    # 运行日志落盘（设置页「系统日志」的数据来源）
    # ------------------------------------------------------------------
    # 后端全部运行日志按天写入 log_dir 下的 movieclaw-YYYY-MM-DD.log。
    # 默认与 SQLite 同在 data/ 目录，Docker 部署挂载 data/ 卷即可保证
    # 容器重启 / 升级镜像日志不丢。超过保留天数的旧日志自动删除。
    log_dir: str = Field(default="./data/logs", alias="LOG_DIR")
    log_retention_days: int = Field(default=30, alias="LOG_RETENTION_DAYS")
    api_v1_prefix: str = "/api/v1"

    # ------------------------------------------------------------------
    # 数据库配置
    # ------------------------------------------------------------------
    # 默认使用容器内 data/ 目录下的 SQLite 文件；部署时把 data/ 挂载为 Docker
    # volume 即可实现持久化与备份。异步驱动固定为 aiosqlite。
    # 如需换用其它数据库，直接通过环境变量 DATABASE_URL 覆盖即可（无需改代码）。
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/movieclaw.db",
        alias="DATABASE_URL",
    )
    # 是否打印所有 SQL 语句，调试时开启，生产建议关闭
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # ------------------------------------------------------------------
    # 本地化资源目录（用户上传的图片等）
    # ------------------------------------------------------------------
    # 存放"随部署实例走、需持久化"的用户上传文件，首个用户就是首页背景图。
    # 与 SQLite 同放在 data/ 下（默认 data/uploads），部署时把 data/ 挂成 Docker
    # volume，容器重启/升级镜像都不丢。后续其它本地化设定（自定义图标、封面缓存
    # 等）也归到这个目录，通过环境变量 MEDIA_DIR 可整体改到别处。
    media_dir: str = Field(default="./data/uploads", alias="MEDIA_DIR")

    # ------------------------------------------------------------------
    # 远程图片磁盘缓存
    # ------------------------------------------------------------------
    # 发现页海报（TMDB）、豆瓣剧照、PT 站种子详情图等所有远程图片都经
    # /images/proxy 统一收口并缓存到本地磁盘，二次访问不再回源互联网。
    # 目录与 SQLite 同在 data/ 下，Docker 部署挂载 data 一个卷即可持久化。
    image_cache_dir: str = Field(default="./data/cache/images", alias="IMAGE_CACHE_DIR")
    # 缓存容量上限（MB）。超限后按「最久未访问」自动清理到上限的 90%。
    image_cache_max_mb: int = Field(default=2048, alias="IMAGE_CACHE_MAX_MB")

    # ------------------------------------------------------------------
    # 配置加密主密钥（保护 app_setting / 站点凭据中的敏感字段）
    # ------------------------------------------------------------------
    # 双通道设计（详见 movieclaw_db.crypto.SecretBox）：
    # - 方案 A（高级用户）：设置 MASTER_KEY 环境变量，密钥不落盘、最安全，但须自行
    #   妥善保管——丢失将导致所有密文永久无法恢复。
    # - 方案 B（默认，面向非开发者）：不设 MASTER_KEY 时，首次启动自动在数据目录
    #   生成密钥文件（下方 secret_key_file），全自动、用户无感。
    # ⚠️ 主密钥属于引导层，绝不存进数据库。
    master_key: str | None = Field(default=None, alias="MASTER_KEY")
    # 方案 B 的密钥文件路径。默认与 SQLite 同放 data/ 目录，随 volume 一并持久化、备份。
    secret_key_file: str = Field(default="./data/.secret_key", alias="SECRET_KEY_FILE")

    # ------------------------------------------------------------------
    # Agent 工作区
    # ------------------------------------------------------------------
    # Agent 的 bash / read / write / edit 工具的工作目录与相对路径解析基准。
    # 独立成一个目录（而非整个项目根），把 Agent 的文件操作圈在可控范围内；
    # 与 data/ 同级便于 Docker 一并挂载持久化。
    agent_workspace_dir: str = Field(
        default="./data/agent-workspace", alias="AGENT_WORKSPACE_DIR"
    )
    # Agent 会话转录目录：一个会话一个 JSONL 文件（append-only，事实源）。
    # SQLite 里的 agent_session 表只是可从这里整体重建的查询索引。
    # 与 data/ 下其它持久化目录一样随 Docker volume 一并备份。
    agent_sessions_dir: str = Field(
        default="./data/agent-sessions", alias="AGENT_SESSIONS_DIR"
    )

    # ------------------------------------------------------------------
    # 登录会话 Cookie
    # ------------------------------------------------------------------
    # Secure 标志：开启后 Cookie 仅经 https 传输。自托管用户大量走 LAN 内 http
    # 直连，默认开启会导致登录后立刻掉线，故默认关闭；公网 https 部署时建议置 true。
    session_cookie_secure: bool = Field(default=False, alias="SESSION_COOKIE_SECURE")

    # ------------------------------------------------------------------
    # 订阅投递
    # ------------------------------------------------------------------
    # 模拟投递开关（默认开）：匹配管线走完整状态机（认领→grabbed、活动照记），
    # 但不取种、不碰下载器，只打完整中文日志。用于安全观察订阅管线行为；
    # 确认无误后置 false 切换真实投递，代码路径不变。
    subscription_dispatch_dry_run: bool = Field(
        default=True, alias="SUBSCRIPTION_DISPATCH_DRY_RUN"
    )

    # ------------------------------------------------------------------
    # TMDB 影视元数据（发现页数据源）
    # ------------------------------------------------------------------
    # 支持两种格式，自动识别：v4 API Read Access Token（"eyJ" 开头的长令牌）
    # 或 v3 API Key（32 位十六进制）。
    # 未配置时发现页自动禁用，其余功能不受影响。到 themoviedb.org
    # 免费注册后在「账户设置 → API」页申请，通过 TMDB_API_KEY 环境变量配置。
    tmdb_api_key: str | None = Field(default=None, alias="TMDB_API_KEY")
    # TMDB 接口与图床地址。所在网络无法直连 api.themoviedb.org 时，
    # 可整体切换到自建反代或公共镜像，无需改代码。
    tmdb_api_base_url: str = Field(
        default="https://api.themoviedb.org/3", alias="TMDB_API_BASE_URL"
    )
    tmdb_image_base_url: str = Field(
        default="https://image.tmdb.org/t/p", alias="TMDB_IMAGE_BASE_URL"
    )
    # 元数据语言与地区（影响标题/简介译文与「正在热映/即将上映」的地区口径）
    tmdb_language: str = Field(default="zh-CN", alias="TMDB_LANGUAGE")
    tmdb_region: str = Field(default="CN", alias="TMDB_REGION")
    # 豆瓣视角只读取公开榜单；保留可替换地址，便于部署环境使用自建反代。
    douban_api_base_url: str = Field(
        default="https://m.douban.com/rexxar/api/v2", alias="DOUBAN_API_BASE_URL"
    )

    # ------------------------------------------------------------------
    # 定时任务调度配置
    # ------------------------------------------------------------------
    # 调度总开关：置 false 可让部署者完全关掉定时任务（如临时排障、多实例部署时
    # 只想让其中一个实例跑调度）。
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    # cron 触发所用时区。数据库存 UTC，但用户按本地时间理解「每天几点」，故需明确时区。
    scheduler_timezone: str = Field(default="Asia/Shanghai", alias="SCHEDULER_TIMEZONE")
    # 任务执行历史的保留天数，超期由内置清理任务归档，避免 task_run 无限增长。
    task_run_retention_days: int = Field(default=30, alias="TASK_RUN_RETENTION_DAYS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
