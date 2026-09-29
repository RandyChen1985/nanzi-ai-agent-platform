from enum import IntEnum

class RagFlowError(Exception):
    """RAGFlow 调用失败基类。

    ``status_code`` 供接口层 / 全局异常处理器映射 HTTP 状态码；
    ``retryable`` 表示这次失败是否值得原样重试。
    """

    status_code: int = 502
    retryable: bool = False


class RagFlowServiceError(RagFlowError):
    """RAGFlow 明确返回了业务错误（HTTP 非 200 或 ``code != 0``）。

    这类错误说明 RAGFlow 已经处理完请求并给出了原因（权限、数据集不存在、
    embedding 模型连不上等），重试通常没有意义。
    """

    status_code: int = 502
    retryable: bool = False


class RagFlowUnavailableError(RagFlowError):
    """无法确认 RAGFlow 是否处理了请求：超时、连接中断、服务不可用。

    ``httpx`` 的超时异常 ``str()`` 为空字符串，直接记录会得到没有任何信息的
    日志，因此统一包装成本异常并带上排查线索。
    """

    status_code: int = 503
    retryable: bool = False


class RagFlowTransientError(RagFlowUnavailableError):
    """连接阶段失败或 RAGFlow 返回 5xx，值得重试。"""

    retryable: bool = True


class ErrorCode(IntEnum):
    """
    标准化业务错误码。
    格式说明:
    - 200: 成功响应
    - 400x: 请求错误（客户端侧）
    - 401x: 认证错误
    - 403x: 权限错误
    - 500x: 服务器错误
    """
    SUCCESS = 200  # 成功响应
    
    # 400x: 请求错误
    BAD_REQUEST = 400  # 错误的请求
    INVALID_PARAMETER = 4001  # 无效的参数
    RESOURCE_NOT_FOUND = 4004  # 资源未找到
    RESOURCE_DISABLED = 4005  # 资源已禁用
    
    # 401x: 认证错误
    UNAUTHORIZED = 401  # 未授权
    API_KEY_MISSING = 4011  # API密钥缺失
    API_KEY_INVALID = 4012  # API密钥无效
    TOKEN_EXPIRED = 4013  # Token已过期
    
    # 403x: 权限错误
    FORBIDDEN = 403  # 禁止访问
    ACCESS_DENIED = 4031  # 访问被拒绝
    ADMIN_REQUIRED = 4032  # 需要管理员权限
    
    # 429x: 速率限制
    TOO_MANY_REQUESTS = 429  # 请求过于频繁
    
    # 500x: 服务器错误
    INTERNAL_SERVER_ERROR = 500  # 服务器内部错误
    DATABASE_ERROR = 5001  # 数据库错误
    CLICKHOUSE_ERROR = 5002  # ClickHouse错误
    SERVICE_UNAVAILABLE = 503  # 服务不可用
    DATABASE_CONNECTION_FAILED = 5031  # 数据库连接失败

# 错误码中文描述映射
ERROR_CODE_DESC = {
    ErrorCode.SUCCESS: "成功响应",
    ErrorCode.BAD_REQUEST: "错误的请求",
    ErrorCode.INVALID_PARAMETER: "无效的参数",
    ErrorCode.RESOURCE_NOT_FOUND: "资源未找到",
    ErrorCode.RESOURCE_DISABLED: "资源已禁用",
    ErrorCode.UNAUTHORIZED: "未授权",
    ErrorCode.API_KEY_MISSING: "API密钥缺失",
    ErrorCode.API_KEY_INVALID: "API密钥无效",
    ErrorCode.TOKEN_EXPIRED: "Token已过期",
    ErrorCode.FORBIDDEN: "禁止访问",
    ErrorCode.ACCESS_DENIED: "访问被拒绝",
    ErrorCode.ADMIN_REQUIRED: "需要管理员权限",
    ErrorCode.TOO_MANY_REQUESTS: "请求过于频繁",
    ErrorCode.INTERNAL_SERVER_ERROR: "服务器内部错误",
    ErrorCode.DATABASE_ERROR: "数据库错误",
    ErrorCode.CLICKHOUSE_ERROR: "ClickHouse错误",
    ErrorCode.SERVICE_UNAVAILABLE: "服务不可用",
    ErrorCode.DATABASE_CONNECTION_FAILED: "数据库连接失败",
}

# 响应模型引用
# 注意: 在 openapi.py 中会用到这个映射来生成文档
COMMON_ERROR_RESPONSES = {
    400: {"description": "参数校验失败或请求错误"},
    401: {"description": "未授权或 API Key 无效"},
    403: {"description": "访问被拒绝或无权限"},
    404: {"description": "资源未找到"},
    429: {"description": "请求过于频繁"},
    500: {"description": "服务器内部错误"},
    503: {"description": "数据库连接失败或服务不可用"},
}
