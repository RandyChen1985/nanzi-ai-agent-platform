import logging
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import List, Optional
from sqlalchemy.engine.url import URL

logger = logging.getLogger(__name__)


def resolve_cors_origins(configured: Optional[List[str]]) -> List[str]:
    """解析 CORS 白名单，并把危险配置显式暴露出来。

    服务端启用了 allow_credentials=True，而浏览器不接受
    `Access-Control-Allow-Origin: *` 与凭据共存，因此通配符配置在跨域场景下
    会让带凭据的请求被直接拒绝。这里不擅自收紧（避免打断既有部署），
    只保留原行为并记录告警，便于按真实域名收敛。
    """
    origins = [
        origin.strip()
        for origin in (configured or [])
        if isinstance(origin, str) and origin.strip()
    ]

    if not origins:
        logger.warning(
            "ALLOWED_ORIGINS 未配置，已回退为通配符 '*'。由于服务端启用了 "
            "allow_credentials，跨域携带凭据的请求会被浏览器拒绝，"
            "请在 .env 中配置实际访问域名。"
        )
        return ["*"]

    if "*" in origins:
        logger.warning(
            "ALLOWED_ORIGINS 包含通配符 '*'，与 allow_credentials=True 冲突："
            "跨域携带凭据的请求会被浏览器拒绝。请收敛为实际访问域名。"
        )

    return origins


class Settings(BaseSettings):
    API_SERVICE_ENV: str = "dev"
    API_SERVICE_PORT: int = 8001
    LOG_LEVEL: str = "INFO"  # Aligned with .env
    ALLOWED_ORIGINS: List[str] = ["*"]
    APP_PUBLIC_URL: Optional[str] = None
    # 仅建议在一个节点开启，避免多节点部署重复启动 APScheduler。
    TASK_SCHEDULER_ENABLED: bool = True

    # 门户会话令牌：浏览器只持有不透明随机令牌（sess_...），真实 API Key 不再下发到前端。
    # 令牌写入 auth:api_key 缓存键，现有校验链无需分支即可接受；滑动续期 24h。
    # 置 false 可在 Redis 异常等场景快速回退到"cookie 直存真实 API Key"的旧行为。
    PORTAL_SESSION_TOKEN_ENABLED: bool = True

    # Main database type: mysql (default) / postgresql
    DATABASE_TYPE: str = "mysql"

    # MySQL
    MYSQL_HOST: Optional[str] = None
    MYSQL_PORT: int = 3306
    MYSQL_DB: Optional[str] = None
    MYSQL_USER: Optional[str] = None
    MYSQL_PASSWORD: Optional[str] = None
    MYSQL_POOL_SIZE: int = 20
    MYSQL_MAX_OVERFLOW: int = 50
    MYSQL_POOL_RECYCLE: int = 3600

    # PostgreSQL (used when DATABASE_TYPE=postgresql)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: Optional[str] = None
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None  # 改为 Optional 且默认为 None
    REDIS_ENABLE: bool = True
    # 审计日志走 Redis 共享队列（多节点一致、崩溃不丢、落库失败可重试）。
    # 关闭时回退进程内队列（单机可用）；Redis 连接不可用时也自动回退，不丢审计。
    AUDIT_USE_REDIS_QUEUE: bool = True
    MCP_RATE_LIMIT_CLIENT_PER_MINUTE: int = 120
    MCP_RATE_LIMIT_USER_PER_MINUTE: int = 60

    # Security - API Key Encryption
    # Fernet Key (32 url-safe base64-encoded bytes)
    ENCRYPTION_KEY: str

    # LLM Gateway
    LLM_BASE_URL: Optional[str] = None
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL_NAME: Optional[str] = None
    LLM_TEMPERATURE: Optional[float] = None

    # External SQL API
    EXTERNAL_SQL_API_URL: Optional[str] = None
    EXTERNAL_SQL_API_KEY: Optional[str] = None

    # Metadata & RAG
    METADATA_PROVIDER: str = "local" # local / ragflow
    RAGFLOW_API_URL: Optional[str] = None
    RAGFLOW_API_KEY: Optional[str] = None

    # Ebbinghaus Memory Configs
    MEMORY_BASE_HALF_LIFE: float = 7.0
    MEMORY_CONSOLIDATION_THRESHOLD: float = 0.82

    # SSO Configuration
    SSO_API_URL: str = "https://yovole.net/api/v1/user/check/login"
    SSO_ACCESS_TOKEN: str = "laplace"
    SSO_REQUEST_SYSTEM: str = "NANZI_AI_AGENT_PLATFORM"
    SSO_REQUEST_BUSINESS: str = "USER-LOGIN"
    SSO_TIMEOUT: int = 30

    @property
    def SKILLS_DIR(self) -> str:
        container_path = "/app/data/skills"
        if os.path.exists(container_path):
            return container_path
        host_path = os.path.expanduser("~/.agents/skills")
        os.makedirs(host_path, exist_ok=True)
        return host_path

    def build_mysql_url(self, driver: str = "mysql+aiomysql") -> URL:
        """构建 SQLAlchemy MySQL URL（自动对 user/password 中的 @:#/ 等特殊字符做编码）。"""
        missing = [
            name for name, value in (
                ("MYSQL_HOST", self.MYSQL_HOST),
                ("MYSQL_DB", self.MYSQL_DB),
                ("MYSQL_USER", self.MYSQL_USER),
                ("MYSQL_PASSWORD", self.MYSQL_PASSWORD),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "MySQL database configuration is incomplete; "
                f"missing: {', '.join(missing)}"
            )
        return URL.create(
            drivername=driver,
            username=self.MYSQL_USER,
            password=self.MYSQL_PASSWORD,
            host=self.MYSQL_HOST,
            port=self.MYSQL_PORT,
            database=self.MYSQL_DB,
        )

    def build_postgresql_url(self, driver: str = "postgresql+psycopg") -> URL:
        """构建 SQLAlchemy PostgreSQL URL，并对凭据中的特殊字符做编码。"""
        missing = [
            name for name, value in (
                ("POSTGRES_DB", self.POSTGRES_DB),
                ("POSTGRES_USER", self.POSTGRES_USER),
                ("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "PostgreSQL database configuration is incomplete; "
                f"missing: {', '.join(missing)}"
            )
        return URL.create(
            drivername=driver,
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )

    @property
    def normalized_database_type(self) -> str:
        database_type = self.DATABASE_TYPE.strip().lower()
        if database_type == "mysql":
            return "mysql"
        if database_type in {"postgres", "postgresql", "pg"}:
            return "postgresql"
        raise ValueError(f"Unsupported DATABASE_TYPE: {self.DATABASE_TYPE}")

    @property
    def DATABASE_ASYNC_URL(self) -> URL:
        if self.normalized_database_type == "postgresql":
            return self.build_postgresql_url("postgresql+psycopg")
        return self.MYSQL_ASYNC_URL

    @property
    def DATABASE_SYNC_URL(self) -> str:
        if self.normalized_database_type == "postgresql":
            return self.build_postgresql_url("postgresql+psycopg").render_as_string(hide_password=False)
        return self.MYSQL_SYNC_URL

    @property
    def MYSQL_ASYNC_URL(self) -> URL:
        return self.build_mysql_url("mysql+aiomysql")

    @property
    def MYSQL_SYNC_URL(self) -> str:
        # APScheduler 等同步组件需要字符串 URL；render 时保留已编码的密码
        return self.build_mysql_url("mysql+pymysql").render_as_string(hide_password=False)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
