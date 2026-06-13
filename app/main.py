import sys
from fastapi import FastAPI, HTTPException, Cookie, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError # Import Validation Error
from contextlib import asynccontextmanager
from typing import Optional
from app.api.portal.api import portal_router
from app.api.v1.api import v1_router
from app.core.config import settings
from app.core import database, redis
from app.core.middleware import AccessLogMiddleware
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.core.errors import ErrorCode
from app.core.openapi import custom_openapi, tags_metadata # Import OpenAPI Logic
from asynch.errors import InterfaceError
from aiomysql import OperationalError
import logging
import datetime
import uuid
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.core.orm import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:     %(message)s'
)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await database.init_db()
    await redis.init_redis()
    await AuditService.start_worker()
    
    # Initialize Oracle Thick Mode if requested
    if os.environ.get("USE_ORACLE_THICK_MODE") == "1":
        try:
            import oracledb
            lib_dir = os.environ.get("ORACLE_LIB_DIR", "/opt/oracle/instantclient")
            oracledb.init_oracle_client(lib_dir=lib_dir)
            logging.info(f"Oracle Thick Mode initialized with lib_dir: {lib_dir}")
        except Exception as e:
            logging.error(f"Failed to initialize Oracle Thick Mode: {e}")

    # Initialize Global HTTP Client
    from app.core.http_client import GlobalHttpClient
    await GlobalHttpClient.get_client()
    
    # Start Task Scheduler
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.start()

    try:
        from app.services.ai.memory_index_service import MemoryIndexService
        await MemoryIndexService.ensure_index()
    except Exception as e:
        logging.warning("Memory RediSearch index init skipped: %s", e)

    try:
        from app.services.ai.metadata_index_service import MetadataIndexService
        await MetadataIndexService.ensure_index()
        await MetadataIndexService.sync_all_datasets()
    except Exception as e:
        logging.warning("Metadata RediSearch index/sync init skipped: %s", e)

    try:
        from app.services.ai.example_index_service import ExampleIndexService
        await ExampleIndexService.ensure_index()
        await ExampleIndexService.sync_all_examples()
    except Exception as e:
        logging.warning("Example RediSearch index/sync init skipped: %s", e)
    
    yield
    # Shutdown
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.stop()
    await GlobalHttpClient.close()
    await AuditService.stop_worker()
    from app.services.pool_manager import DataSourcePoolManager
    await DataSourcePoolManager.close_all_pools()
    await database.close_db()
    await redis.close_redis()

app = FastAPI(
    title="云枢·智能体平台",
    description="""
## 概述

云枢智能体平台提供统一的 AI 智能体服务，支持 ChatBI、知识库问答、自动化工作流等能力。
通过对接云枢数据服务平台，为业务人员提供自然语言交互的取数与分析体验。

## 核心特性

- 🤖 **意图识别**：精准识别用户查询、问答或操作意图，自动路由处理
- 📊 **ChatBI**：自然语言查数，自动生成图表与可视化分析结论
- 📚 **RAG 知识库**：基于 SOP 和运维手册，提供智能化的技术支持问答
- 🔒 **安全管控**：仅传递元数据给大模型，真实数据通过 Data API 闭环，确保数据不泄露
- 📝 **完整审计**：详尽的对话与查询日志，支持溯源与分析

## 认证方式

所有接口（除登录外）需要在请求头中携带 API Key：

```
X-API-Key: your_api_key_here
```

## 响应格式

所有接口统一返回以下格式：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "primary_chart": { ... },
    "answer_markdown": "...",
    "secondary_widgets": [...]
  },
  "timestamp": "2025-01-01T12:00:00+08:00",
  "trace_id": "..."
}
```

## 联系方式

- **技术支持**：support@yovole.com
- **官网**：https://www.yovole.com
    """,
    version="1.0.0",
    contact={
        "name": "云枢智能体团队",
        "email": "support@yovole.com",
        "url": "https://www.yovole.com"
    },
    license_info={
        "name": "商业许可",
        "url": "https://www.yovole.com/license"
    },
    openapi_tags=tags_metadata,
    lifespan=lifespan,
    docs_url=None,       # Disable default Docs
    redoc_url=None,      # Disable default ReDoc
    openapi_url=None     # Disable default OpenAPI schema
)

async def _get_execution_mode() -> str:
    import os
    from app.services.config_service import ConfigService
    env_mode = os.environ.get("SQL_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("local", "remote"):
        return env_mode
    try:
        execution_mode = await ConfigService.get("sql_execution_mode", default="remote")
        execution_mode = execution_mode.strip().lower()
        if execution_mode in ("local", "remote"):
            return execution_mode
    except Exception:
        pass
    return "remote"

# Global Exception Handlers for Resilience
@app.exception_handler(InterfaceError)
@app.exception_handler(OperationalError)
async def database_connection_exception_handler(request: Request, exc: Exception):
    """
    Handle Database Connection Errors.
    Return 503 Service Unavailable with specific business code.
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    execution_mode = await _get_execution_mode()
    return JSONResponse(
        status_code=503,
        content={
            "code": ErrorCode.DATABASE_CONNECTION_FAILED,
            "message": "数据库连接失败",
            "detail": str(exc) if settings.API_SERVICE_ENV != "production" else "数据库暂时不可用，请稍后重试。",
            "data": None,
            "timestamp": datetime.datetime.now().isoformat(),
            "trace_id": trace_id,
            "execution_mode": execution_mode
        },
        headers={"Retry-After": "30"}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Standardize Request Validation Errors (400).
    Convert Pydantic validation errors to standard ErrorResponse format.
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    execution_mode = await _get_execution_mode()
    return JSONResponse(
        status_code=400,
        content={
            "code": ErrorCode.INVALID_PARAMETER,
            "message": "参数校验失败",
            "detail": str(exc.errors()), # Or format properly as a list of errors
            "data": None,
            "timestamp": datetime.datetime.now().isoformat(),
            "trace_id": trace_id,
            "execution_mode": execution_mode
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Standardize HTTPException responses.
    Map status_code to business code if applicable.
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    # Determine business code from status code if not already set
    code = exc.status_code
    if exc.status_code == 401: code = ErrorCode.UNAUTHORIZED
    elif exc.status_code == 403: code = ErrorCode.ACCESS_DENIED
    elif exc.status_code == 404: code = ErrorCode.RESOURCE_NOT_FOUND
    elif exc.status_code == 429: code = ErrorCode.TOO_MANY_REQUESTS
    
    execution_mode = await _get_execution_mode()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": code,
            "message": str(exc.detail),
            "detail": None,
            "data": None,
            "timestamp": datetime.datetime.now().isoformat(),
            "trace_id": trace_id,
            "execution_mode": execution_mode
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    Standardize all other unhandled exceptions (500).
    """
    logging.error(f"Unhandled exception: {exc}", exc_info=True)
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    execution_mode = await _get_execution_mode()
    return JSONResponse(
        status_code=500,
        content={
            "code": ErrorCode.INTERNAL_SERVER_ERROR,
            "message": "服务器内部错误",
            "detail": str(exc) if settings.API_SERVICE_ENV != "production" else "系统遇到未预期错误，请联系技术支持。",
            "data": None,
            "timestamp": datetime.datetime.now().isoformat(),
            "trace_id": trace_id,
            "execution_mode": execution_mode
        }
    )

# Middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS if settings.ALLOWED_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "Accept", "Origin", "User-Agent", "DNT", "Cache-Control", "X-Requested-With"],
    expose_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Routers
# 挂载 API V1 路由 (外部集成接口)
app.include_router(v1_router, prefix="/api/v1")

# 挂载流程控制门户路由 (管理控制台)
app.include_router(portal_router, prefix="/api/portal")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# --- Documentation Security ---

async def get_current_user_from_cookie(
    admin_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db_session)
):
    """Dependency to verify user access via Cookie for Docs (supports both admin and regular users)"""
    if not admin_token:
        # Redirect to login page if no token
        return None
    
    user = await AuthService.verify_api_key(admin_token, db)
    if not user:
        return None
        
    return user

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(user: Optional[dict] = Depends(get_current_user_from_cookie)):
    if not user:
        return RedirectResponse("/login?next=/docs")
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_ui_parameters={"persistAuthorization": True}
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_html(user: Optional[dict] = Depends(get_current_user_from_cookie)):
    if not user:
        return RedirectResponse("/login?next=/redoc")
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=app.title + " - ReDoc"
    )

@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(user: Optional[dict] = Depends(get_current_user_from_cookie)):
    if not user:
         # For JSON endpoint, returning 401/403 is better than redirect usually, 
         # but to force browser login flow, redirect is okay or let Swagger UI handle it.
         # Actually Swagger UI calls this via AJAX. Redirect might fail CORS or opaque.
         # Let's return 401 if unauthorized for JSON.
         raise HTTPException(status_code=401, detail="Unauthorized")
    return await custom_openapi(app)

# Mount frontend static assets if they exist
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
frontend_dist = os.path.join(base_dir, "frontend", "dist")
assets_path = os.path.join(frontend_dist, "assets")

if os.path.exists(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

# 确保 data/uploads 持久化文件上传目录存在并挂载静态托管
uploads_dir = os.path.join(base_dir, "data", "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=uploads_dir), name="uploads")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Skip API routes (handled above)
    if full_path.startswith("api"):
         raise HTTPException(status_code=404, detail="API Not Found")

    # Check if file exists in frontend_dist (for favicon.ico, favicon.png, etc.)
    file_path = os.path.join(frontend_dist, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)

    # Serve index.html for all other routes (SPA)
    index_file = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_file):
        return FileResponse(
            index_file, 
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    
    return {"message": "Admin Portal is being built. Please run `cd frontend && npm run build`."}
