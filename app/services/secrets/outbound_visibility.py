"""请求内的出站日志边界。受保护请求的 HTTP 库日志可能包含 URL 或远端回显。"""

import logging
from contextvars import ContextVar

protected_transport: ContextVar[bool] = ContextVar("protected_credential_transport", default=False)


class _ProtectedTransportFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not protected_transport.get()


for _logger_name in ("httpx", "httpx2", "mcp.client.sse", "mcp.client.streamable_http"):
    logging.getLogger(_logger_name).addFilter(_ProtectedTransportFilter())
