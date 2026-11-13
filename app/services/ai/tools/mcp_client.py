import json
import logging
import asyncio
import time
import uuid
from contextlib import AsyncExitStack
from typing import AsyncIterator, Awaitable, Callable, Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse
import httpx
import httpx2
import anyio
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache
from app.services.mcp.mcp_auth_policy import build_mcp_headers, resolve_mcp_auth_headers
from app.services.secrets.injection import apply_mcp_bindings
from app.services.secrets.redact import redact_value
from app.services.mcp.outbound_audit import record_outbound_audit_log
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


class McpCredentialDeniedError(Exception):
    """绑定的凭据用不了（停用 / 过期 / 场景不符 / 域名不在白名单）。

    和「没有绑定」区分开：没绑时退回 ``fixed_token`` 是设计好的默认行为；绑了却
    用不了还退回，就是用户以为在用自己账号、实际在用管理员的账号。

    文案按**拒绝原因**给不同的修复动作。原来是一句话兜底「请检查凭据状态」，
    可域名白名单不对时凭据状态是正常的，按那句话去查永远查不到。
    """

    def __init__(self, server_name: str, denials: dict[str, str | None]):
        from app.services.secrets.injection import denial_hint, denial_text

        self.server_name = server_name
        self.denials = dict(denials)

        parts = [
            f"{slot}（{denial_text(reason)}）" for slot, reason in denials.items()
        ]
        # 同一种原因只提示一次：多个槽位可能卡在同一道闸门上
        hints: list[str] = []
        for reason in denials.values():
            hint = denial_hint(reason)
            if hint and hint not in hints:
                hints.append(hint)

        message = f"MCP 服务 {server_name} 的凭据没有注入：" + "；".join(parts)
        if hints:
            message += "。" + "；".join(hints)
        message += "。也可以解除绑定改用服务级 Token。"
        super().__init__(message)

# tools/list 是分页接口；只取第一页会把后续工具当成“远端已删除”误下线。
# 上限用于防御异常远端返回无限 cursor。
MAX_REMOTE_TOOL_PAGES = 100
MAX_REMOTE_TOOLS = 5000
# 同步时若远端返回空列表而本地已有可用工具，默认拒绝应用（见 sync_tools）。
# 这是防止远端瞬时故障/权限抖动把整批工具误标为删除的最后一道闸门。
_AUDIT_TASKS: set[asyncio.Task] = set()

# anyio 取消宿主 task 时写进 CancelledError 文案的固定片段（见 mcp 2.x
# ``streamable_http_client`` 内的 ``anyio.create_task_group``）。
_ANYIO_SCOPE_CANCEL_MARKER = "cancel scope"


class McpToolCallTimeout(TimeoutError):
    """单次 ``tools/call`` 等不到结果（远端业务执行太慢），**不是**传输断开。

    仍继承 ``TimeoutError``，对外语义不变（调用方按超时处理）；但从其中派生出来
    是为了让 ``_is_transport_error`` 能先把它摘出去——``TimeoutError`` 是 ``OSError``
    的子类，落在「断连重连」的判定里会把一次超时的写操作重发一遍（远端执行两次、
    总耗时翻倍）。
    """


#: 传输层断开的异常类型。mcp 2.x 的客户端传输跑在 **httpx2** + anyio 之上：
#: ``httpx2.HTTPError`` 与 ``httpx.HTTPError`` 没有继承关系（两个独立包），
#: anyio 的资源关闭异常、以及 task group 抛出的 ``ExceptionGroup`` 也都不在
#: ``OSError`` 体系里。漏判的后果是远端重启后自愈失效——每个调用都失败且每次都
#: 刷新会话活跃时间，只要还有流量就一直好不了。
_TRANSPORT_ERROR_TYPES: tuple = (
    ConnectionError,
    EOFError,
    OSError,  # 含内置 TimeoutError（3.10 起是其子类）与 socket.timeout
    httpx.HTTPError,
    httpx2.HTTPError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
)


#: 平台私有 JSON-Schema 扩展前缀。这些字段决定「证据类型 / 证据策略」等调用门禁，
#: 只能由平台自己写；远端服务在 inputSchema 里自带同名键时必须剥掉，否则等于让远端
#: 改写平台的调用策略。
_PLATFORM_SCHEMA_EXTENSION_PREFIX = "x-nanzi-"


def _strip_platform_schema_extensions(value: Any) -> Any:
    """递归移除远端 schema 里的平台私有扩展键（``x-nanzi-*``）。"""
    if isinstance(value, dict):
        return {
            key: _strip_platform_schema_extensions(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.strip().casefold().startswith(_PLATFORM_SCHEMA_EXTENSION_PREFIX))
        }
    if isinstance(value, list):
        return [_strip_platform_schema_extensions(item) for item in value]
    return value


def _is_transport_error(err: BaseException) -> bool:
    """判断异常是否属于「传输断开」，需要重连后重试。

    ``ExceptionGroup``（anyio task group 会把并发子任务的异常打包抛出）按成员递归
    判断：只要有一个成员是传输异常，整体就按传输断开处理。
    """
    if isinstance(err, McpToolCallTimeout):
        return False
    if isinstance(err, _TRANSPORT_ERROR_TYPES):
        return True
    if isinstance(err, BaseExceptionGroup):
        return any(_is_transport_error(item) for item in err.exceptions)
    return False


def _is_anyio_scope_cancellation(err: BaseException) -> bool:
    """区分 anyio 的 CancelScope 取消与调用方取消。

    mcp 2.x 的 ``streamable_http_client`` 内部用 ``anyio.create_task_group``；
    握手阶段连接失败（DNS 不通、端口拒绝）时 task group 会 cancel 宿主 task，
    抛出 ``CancelledError("Cancelled via cancel scope 0x...")``。这是**传输层
    失败**的一种表现形式，不是调用方要求取消——后续必须继续走 SSE / Direct
    HTTP 降级，否则「MCP 服务暂时不可达」会变成整条接口 500。

    不能用 ``task.cancelling()`` 判断来源：anyio 内部取消与 ``task.cancel()``
    都会让计数变成 1。调用方取消若直接落在预检（``_tcp_endpoint_reachable``）
    等非 anyio 路径上，异常不带该文案，会照常冒泡；一旦取消发生在 anyio
    task group 内部，文案同样会被改写，这就是为什么「不可达」必须提前到预检
    判断——那条路径上不会产生这种无法区分来源的取消。
    """
    if not isinstance(err, asyncio.CancelledError):
        return False
    return _ANYIO_SCOPE_CANCEL_MARKER in str(err).lower()


def _tool_input_schema(tool: Any) -> Any:
    """取 MCP 工具的入参 schema。

    mcp 2.x 把 JSON-RPC 的 ``inputSchema`` 字段改名为 ``input_schema``（与
    ``read_only_hint`` 等注解一起）；但外部服务返回的原始 dict、以及历史缓存里
    仍可能是旧名。这里按「新名 → 旧名 → 平台缓存字段」依次回退，两个大版本都能取到。
    """
    for attr in ("input_schema", "inputSchema"):
        value = getattr(tool, attr, None)
        if value is not None:
            return value
    if isinstance(tool, dict):
        for key in ("input_schema", "inputSchema", "parameter_schema"):
            value = tool.get(key)
            if value is not None:
                return value
    return None


class McpSyncGuardError(RuntimeError):
    """同步结果看起来不可信（例如远端突然返回 0 个工具），需要显式强制。"""


def _create_no_redirect_mcp_http_client(headers=None, timeout=None, auth=None):
    """``sse_client`` 的传输客户端工厂：禁跟随重定向，且必须是 **httpx2** 客户端。

    为什么是 httpx2：mcp 2.x 把客户端传输整体切到了 ``httpx2``（见
    ``mcp/shared/_httpx_utils.py`` 的 ``McpHttpClientFactory``），``sse_client()``
    会把 ``httpx2.Timeout`` 传进本工厂，再用返回的客户端发请求。若这里仍返回
    ``httpx.AsyncClient``，httpx 在 TCP 连接阶段就会把 ``httpx2.Timeout`` 当数字运算，
    抛 ``TypeError: unsupported operand type(s) for +: 'float' and 'Timeout'``；
    而该异常在 ``McpSseSession.connect()`` 里会被吞成一条 warning 并静默降级
    Direct HTTP——对只支持 SSE 的外部 MCP 服务等于工具同步/调用全部失败。
    回归用例见 ``tests/services/mcp/test_mcp_client_sse_transport.py``。

    为什么还要禁重定向：自定义 Header 可能带凭据，跟随到新 host 等于外泄；内网地址
    已按产品要求放行，但至少要保证重定向不能把请求带到未校验的地址。（httpx2 默认
    即不跟随，mcp 2.x 传输层自己也只跟随同源重定向；这里显式写死是为了不依赖默认值。）
    """
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or httpx2.Timeout(30.0),
        auth=auth,
        follow_redirects=False,
    )


#: 未显式传入超时时的兜底秒数。正常链路里由 ``agent_max_toolcall_timeout`` 的
#: 请求期快照（``AgentContext.agent_max_toolcall_timeout_seconds``）覆盖；这里取
#: 与平台默认值一致的 180s，而不是曾经写死的 120s —— 后者会让长工具在进度走到
#: 一半时被静默掐断，也与平台的超时配置各说各话。
DEFAULT_MCP_TOOL_CALL_TIMEOUT = 180.0

#: ``notifications/progress`` 回调签名：``(progress, total, message)``，异步。
McpProgressCallback = Callable[[float, Optional[float], Optional[str]], Awaitable[None]]


async def _call_tool_with_progress(
    session: ClientSession,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    progress_callback: Optional[McpProgressCallback] = None,
    timeout_seconds: Optional[float] = None,
):
    """统一的单次 ``tools/call`` 入口：透传进度回调 + 平台配置的超时。

    传了 ``progress_callback`` 时，mcp 2.x 会把一个令牌放进请求的
    ``params._meta.progressToken``，随后把远端 ``notifications/progress`` 路由给回调；
    不传则请求与升级前逐字节一致（远端不会被告知要发进度）。

    超时仍用 ``asyncio.wait_for`` 包住，但转换成本模块的 ``McpToolCallTimeout``：
    它不参与「断连重连」的判定，避免把一次超时的写操作当成断线又重发一遍。
    """
    kwargs: Dict[str, Any] = {}
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback

    timeout = (
        timeout_seconds
        if timeout_seconds and timeout_seconds > 0
        else DEFAULT_MCP_TOOL_CALL_TIMEOUT
    )
    try:
        return await asyncio.wait_for(
            session.call_tool(tool_name, arguments, **kwargs),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise McpToolCallTimeout(
            f"MCP tool '{tool_name}' timed out after {timeout:g}s"
        ) from exc


class McpSseSession:
    """Manages an MCP connection, supporting both standard SSE and Direct HTTP Post gateways"""
    def __init__(self, server_id: str, sse_url: str, auth_headers: Optional[Dict] = None):
        self.server_id = server_id
        self.sse_url = sse_url
        self.auth_headers = auth_headers or {}
        self.session: Optional[ClientSession] = None
        self.last_used_at = time.time()
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._exit_stack = None
        self.is_direct_http = False 
        self.mcp_session_id: Optional[str] = None
        self.mcp_protocol_version: Optional[str] = None
        # 传输已确定可用（含无状态 Direct HTTP：那条路径不会有 session/session_id）。
        # 没有这个状态位时 connect() 的幂等短路对无状态网关永远不成立，每次调用都会
        # 重跑「TCP 预检 + Streamable 10s + SSE 探测 5s」全套探测。
        self._transport_ready = False
        # Direct HTTP 的 initialize 握手只做一次；无状态网关不返回 session-id，
        # 原来每次调用都会重发 initialize + notifications/initialized。
        self._direct_initialized = False
        self._rpc_id_counter = 1
        self._http_client: Optional[httpx.AsyncClient] = None
        self._active_requests: int = 0
        self._active_requests_changed = asyncio.Event()
        self._active_requests_changed.set()

    def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    def next_rpc_id(self) -> int:
        self._rpc_id_counter += 1
        return self._rpc_id_counter

    async def _looks_like_sse_endpoint(self) -> bool:
        """Quick probe: skip SSE when the gateway clearly speaks JSON/HTTP."""
        try:
            # 不跟随重定向：自定义 Header 可能包含凭据，跟随到新 host 等于把凭据发过去。
            # 需要重定向的服务请直接填写最终地址。
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(self.sse_url, headers=self.auth_headers)
            content_type = (response.headers.get("content-type") or "").lower()
            if "text/event-stream" in content_type:
                return True
            if "application/json" in content_type or "application/rpc" in content_type:
                logger.info(
                    "[MCP] Endpoint %s reports Content-Type=%s; skipping SSE probe",
                    self.server_id,
                    content_type.split(";")[0].strip() or "unknown",
                )
                return False
            # Unknown type: still try SSE for legacy gateways.
            return True
        except Exception as probe_err:
            logger.info(
                "[MCP] SSE probe skipped for %s due to %s; trying SSE anyway",
                self.server_id,
                type(probe_err).__name__,
            )
            return True

    async def _connect_streamable_http(self) -> None:
        """建立 Streamable HTTP 会话（mcp 2.x 标准传输）。

        与 SSE 的差别只在传输层：``ClientSession`` 之上完全一致，因此
        ``progress_callback`` / ``notifications`` 都能工作。
        """
        http_client = await self._exit_stack.enter_async_context(
            _create_no_redirect_mcp_http_client(headers=self.auth_headers)
        )
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            streamable_http_client(self.sse_url, http_client=http_client)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        self.session = session

    @staticmethod
    def _clear_internal_cancellation() -> None:
        """清掉 anyio 留下的取消计数，否则下一个 await 会立刻再次被取消。"""
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()

    async def _tcp_endpoint_reachable(self, timeout: float = 3.0) -> bool:
        """握手前先确认端点能建立 TCP 连接（只做三次握手，不发 HTTP 请求）。

        为什么需要这一步：mcp 2.x 的 ``streamable_http_client`` 在「连不上」时
        由内部 anyio task group 取消宿主 task，抛出的 CancelledError 带着
        "Cancelled via cancel scope" 文案，与调用方取消无法可靠区分。与其在
        捕获点猜测来源，不如把「网络不可达」判断提前到传输层之外——这里失败
        就直接降级 Direct HTTP，既省掉注定失败的 SSE 探测与 10 秒握手超时，
        也避免把「接口 500」当成「连接失败」。

        只做 TCP 握手，不读不写：仓库里那条「先探测 SSE 会挂成长连接」的教训
        不适用于它（GET 会挂，三次握手不会）。
        """
        parsed = urlparse(self.sse_url or "")
        host = parsed.hostname
        if not host or parsed.scheme not in ("http", "https"):
            return True
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
        except Exception as connect_err:
            logger.info(
                "[MCP] Endpoint %s (%s:%s) unreachable (%s); skipping streamable HTTP",
                self.server_id,
                host,
                port,
                type(connect_err).__name__,
            )
            return False
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except Exception:  # pragma: no cover - 对端先关时的清理噪音
            pass
        return True

    async def _discard_transport(self, label: str) -> None:
        """丢弃半成品传输；清理阶段的异常只告警，不能盖住真正的连接失败原因。

        ``streamable_http_client`` 的 async generator 可能在另一个 task 里退出，
        此时 ``aclose()`` 会抛 ``RuntimeError: Attempted to exit cancel scope in
        a different task than it was entered in``——它与「为什么没连上」无关，
        吞掉并重置栈即可；否则降级分支会被清理异常打断。
        """
        stack, self._exit_stack = self._exit_stack, AsyncExitStack()
        self._transport_ready = False
        self._direct_initialized = False
        if stack is None:
            return
        try:
            await stack.aclose()
        except BaseException as cleanup_err:  # noqa: BLE001 - 清理阶段不挑异常类型
            if isinstance(cleanup_err, asyncio.CancelledError):
                if not _is_anyio_scope_cancellation(cleanup_err):
                    raise
                self._clear_internal_cancellation()
            logger.warning(
                "[MCP] %s cleanup failed for %s (ignored): %s",
                label,
                self.server_id,
                cleanup_err,
            )

    async def connect(self):
        """Initialize a transport; publish SSE state only after its handshake succeeds."""
        async with self._lock:
            if self.session or self.mcp_session_id or self._transport_ready:
                return

            # Debug: Log header keys (redacted)
            header_keys = list(self.auth_headers.keys())
            logger.info(f"[MCP] Connecting to {self.server_id} at {self.sse_url}. Headers keys present: {header_keys}")
            try:
                self._exit_stack = AsyncExitStack()

                # 1. Streamable HTTP 优先：mcp 2.x 服务端（含平台自建 Echo）的默认传输，
                #    也是 HTTP 下**唯一**能收到 notifications/progress 的通道。
                #    顺序不能反：该端点对裸 GET 会挂成长连接，先探测 SSE 会白等
                #    探测超时 + 一次注定失败的 SSE 握手，最后降级成没有进度的
                #    Direct HTTP（实测每次首次调用多花十几秒）。
                #
                #    进入握手前先做 TCP 可达性预检：mcp 2.x 的 streamable 客户端
                #    在「连不上」时会 cancel 宿主 task，抛出的 CancelledError 与
                #    调用方取消无法可靠区分（anyio 会把自己文案写进异常）。把
                #    不可达摘到这条路径之外，既保住降级也保住取消语义。
                if not await self._tcp_endpoint_reachable():
                    # TCP 都建不起来时，SSE 探测与握手必然同样失败，直接降级，
                    # 不再白等 10 秒握手超时。
                    self.is_direct_http = True
                    self._transport_ready = True
                    self.last_used_at = time.time()
                    logger.info(
                        "[MCP] Switched to Direct HTTP mode for %s (endpoint unreachable)",
                        self.server_id,
                    )
                    return

                try:
                    # 用 asyncio.timeout（当前 task 内生效）而不是 create_task + wait_for：
                    # AsyncExitStack 里 enter 的 anyio cancel scope 必须由**同一个 task**
                    # 退出，跨 task 退出会抛
                    # "Attempted to exit cancel scope in a different task than it was entered in"。
                    async with asyncio.timeout(10.0):
                        await self._connect_streamable_http()
                    self.last_used_at = time.time()
                    self.is_direct_http = False
                    self._transport_ready = True
                    logger.info(f"[MCP] Streamable HTTP initialized for {self.server_id}")
                    return
                except BaseException as stream_err:  # noqa: BLE001 - 见下方分类处理
                    if isinstance(stream_err, asyncio.CancelledError):
                        # anyio task group 在握手失败时取消宿主 task：这是传输层
                        # 失败（TLS 中断、对端重置等），不是调用方取消，清掉计数
                        # 后继续降级。调用方取消若在预检阶段发生会直接冒泡，不会
                        # 走到这里。
                        if not _is_anyio_scope_cancellation(stream_err):
                            raise
                        self._clear_internal_cancellation()
                    elif not isinstance(stream_err, Exception):
                        # KeyboardInterrupt / SystemExit 等一律上抛，不参与降级。
                        raise
                    # 不在这里重置 self.session：连接发布前它本来就是 None，
                    # 多余的一次赋值会惊动「握手完成前不得发布会话」的状态观察者
                    # （见 tests/ai/tools/test_mcp_sse_state_guards.py）。
                    # 带上异常文本：只打类型名（RuntimeError）对排查毫无用处，
                    # 而这一步的失败原因直接决定「有没有进度通道」。
                    logger.warning(
                        "[MCP] Streamable HTTP unavailable for %s (%s: %s); trying SSE",
                        self.server_id,
                        type(stream_err).__name__,
                        stream_err,
                    )
                    await self._discard_transport("streamable HTTP")

                try_sse = await self._looks_like_sse_endpoint()

                # 2. Try Standard SSE Connection (only when probe suggests SSE)
                if try_sse:
                    try:
                        async def _connect_sse():
                            read_stream, write_stream = await self._exit_stack.enter_async_context(
                                sse_client(
                                    url=self.sse_url,
                                    headers=self.auth_headers,
                                    httpx_client_factory=_create_no_redirect_mcp_http_client,
                                )
                            )
                            sse_session = await self._exit_stack.enter_async_context(
                                ClientSession(read_stream, write_stream)
                            )
                            await sse_session.initialize()
                            self.session = sse_session

                        # 与上面 Streamable HTTP 同一口径：必须**在当前 task 内**建链。
                        # 之前是 create_task + wait_for，握手在子 task 里把 sse_client 的
                        # anyio cancel scope 压进宿主 self._exit_stack，之后在别的 task 里
                        # aclose() 会抛 "Attempted to exit cancel scope in a different
                        # task than it was entered in"，而 close() 只是把它记成 warning，
                        # 结果底层 sse_client 泄漏。
                        async with asyncio.timeout(10.0):
                            await _connect_sse()

                        self.last_used_at = time.time()
                        self.is_direct_http = False
                        self._transport_ready = True
                        logger.info(f"[MCP] Standard SSE initialized for {self.server_id}")
                        return
                    except Exception as sse_err:
                        # 多数网关（如 ModelScope）返回 JSON/HTTP 而非 SSE；探测失败后降级，不打堆栈。
                        self.session = None
                        self.mcp_session_id = None
                        logger.warning(
                            "[MCP] Standard SSE unavailable for %s (%s: %s); "
                            "falling back to Direct HTTP (no progress channel)",
                            self.server_id,
                            type(sse_err).__name__,
                            sse_err,
                        )
                        await self._discard_transport("SSE")

                # 2. Fallback: Direct HTTP Gateway
                self.is_direct_http = True
                self._transport_ready = True
                logger.info(f"[MCP] Switched to Direct HTTP mode for {self.server_id}")
                self.last_used_at = time.time()
                
            except Exception as e:
                logger.error(f"[MCP] Connection failed for {self.server_id}: {e}", exc_info=True)
                raise

    async def close(self):
        """Closes the connection"""
        async with self._lock:
            exit_stack = self._exit_stack
            self._exit_stack = None
            self.session = None
            self.mcp_session_id = None
            self._transport_ready = False
            self._direct_initialized = False
            http_client = self._http_client
            self._http_client = None
            if http_client and not http_client.is_closed:
                if getattr(self, "_active_requests", 0) > 0:
                    async def _delayed_close(c: httpx.AsyncClient):
                        try:
                            await asyncio.wait_for(
                                self._active_requests_changed.wait(),
                                timeout=120.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[MCP] Timed out waiting for active requests before closing %s",
                                self.server_id,
                            )
                        if not c.is_closed:
                            try:
                                await c.aclose()
                            except Exception:
                                pass
                    asyncio.create_task(_delayed_close(http_client))
                else:
                    try:
                        await http_client.aclose()
                    except Exception as exc:
                        logger.debug("[MCP] HTTP client cleanup failed for %s: %s", self.server_id, exc)
            if exit_stack:
                try:
                    await exit_stack.aclose()
                except Exception as exc:
                    logger.warning(
                        "[MCP] Transport cleanup failed for %s; cached state was cleared: %s",
                        self.server_id,
                        exc,
                    )
            logger.info(f"[MCP] Session closed for {self.server_id}")

    def update_activity(self):
        self.last_used_at = time.time()

class McpClientService:
    _sessions: Dict[str, McpSseSession] = {}
    _sessions_lock = asyncio.Lock()
    _session_creation_locks: Dict[str, asyncio.Lock] = {}
    _cleanup_task: Optional[asyncio.Task] = None

    @classmethod
    async def _load_server(cls, server_id: str) -> McpServer:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(McpServer).where(McpServer.id == server_id))
            server = result.scalar_one_or_none()
            if not server:
                raise ValueError(f"MCP Server {server_id} not found")
            return server

    @classmethod
    async def get_session(
        cls,
        server_id: str,
        *,
        session_key: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> McpSseSession:
        if not cls._cleanup_task:
            cls._cleanup_task = asyncio.create_task(cls._idle_cleanup_loop())

        cache_key = session_key or server_id
        if cache_key not in cls._sessions:
            async with cls._sessions_lock:
                creation_lock = cls._session_creation_locks.setdefault(cache_key, asyncio.Lock())
            try:
                async with creation_lock:
                    if cache_key not in cls._sessions:
                        server = await cls._load_server(server_id)
                        from app.services.mcp.url_policy import (
                            McpUrlPolicyError,
                            validate_mcp_outbound_url,
                        )

                        try:
                            validated_url = validate_mcp_outbound_url(server.sse_url)
                        except McpUrlPolicyError as exc:
                            raise ValueError(f"MCP 服务地址不合法: {exc}") from exc
                        headers = auth_headers if auth_headers is not None else resolve_mcp_auth_headers(server)
                        cls._sessions[cache_key] = McpSseSession(server_id, validated_url, headers)
            finally:
                async with cls._sessions_lock:
                    if (
                        cls._session_creation_locks.get(cache_key) is creation_lock
                        and not creation_lock.locked()
                    ):
                        cls._session_creation_locks.pop(cache_key, None)

        session = cls._sessions[cache_key]
        try:
            await session.connect()
        except Exception:
            try:
                await session.close()
            finally:
                async with cls._sessions_lock:
                    if cls._sessions.get(cache_key) is session:
                        cls._sessions.pop(cache_key, None)
            raise
        session.update_activity()
        return session

    @staticmethod
    def _remote_tool_name(tool: Any) -> Optional[str]:
        if isinstance(tool, str):
            return tool.strip() or None
        name = tool.name if hasattr(tool, "name") else (tool.get("name") if isinstance(tool, dict) else None)
        text = str(name or "").strip()
        return text or None

    @classmethod
    def _dedupe_remote_tools(cls, tools: List[Any]) -> List[Any]:
        """按远端工具名去重并保持顺序；重复项会让同步触发唯一键冲突。"""
        result: List[Any] = []
        seen: set[str] = set()
        for tool in tools:
            name = cls._remote_tool_name(tool)
            if name is None:
                continue
            if name in seen:
                logger.warning("[MCP] Remote tool list contains duplicate name %r; keeping first", name)
                continue
            seen.add(name)
            result.append(tool)
        return result

    @classmethod
    async def _list_sse_tools(cls, session: ClientSession) -> List[Any]:
        tools: List[Any] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_REMOTE_TOOL_PAGES):
            response = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
            tools.extend(list(getattr(response, "tools", None) or []))
            if len(tools) > MAX_REMOTE_TOOLS:
                raise RuntimeError(
                    f"MCP tools/list 返回超过 {MAX_REMOTE_TOOLS} 个工具，已中止同步以避免不完整结果"
                )
            raw_cursor = (
                getattr(response, "nextCursor", None)
                or getattr(response, "next_cursor", None)
            )
            # 只接受字符串 cursor：部分网关/测试替身可能返回非字符串的占位对象，
            # 直接当成“还有下一页”会触发重复 cursor 误判。
            cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise RuntimeError("MCP tools/list 返回了重复的 cursor，已中止同步")
            seen_cursors.add(cursor)
        raise RuntimeError(
            f"MCP tools/list 分页超过 {MAX_REMOTE_TOOL_PAGES} 页，已中止同步以避免不完整结果"
        )

    @classmethod
    async def _list_direct_http_tools(cls, session_mgr: McpSseSession) -> List[Any]:
        await cls._ensure_direct_http_initialized(session_mgr)
        tools: List[Any] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_REMOTE_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            res = await cls._direct_http_rpc(session_mgr, "tools/list", params)
            if isinstance(res, dict):
                tools.extend(list(res.get("tools") or []))
                raw_cursor = res.get("nextCursor") or res.get("next_cursor")
                cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
            elif isinstance(res, list):
                tools.extend(res)
                cursor = None
            else:
                break
            if len(tools) > MAX_REMOTE_TOOLS:
                raise RuntimeError(
                    f"MCP tools/list 返回超过 {MAX_REMOTE_TOOLS} 个工具，已中止同步以避免不完整结果"
                )
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise RuntimeError("MCP tools/list 返回了重复的 cursor，已中止同步")
            seen_cursors.add(cursor)
        raise RuntimeError(
            f"MCP tools/list 分页超过 {MAX_REMOTE_TOOL_PAGES} 页，已中止同步以避免不完整结果"
        )

    @classmethod
    async def list_remote_tools(
        cls,
        server_id: str,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        """拉取远端全部工具（跟随 cursor 分页）。

        ``user_info`` 用于 UserContext 服务：这类服务的 tools/list 也可能要求签名，
        只带静态 Header 会被远端拒绝。签名断言是按用户签发的，必须使用短生命周期
        的临时会话，避免把某个用户的断言复用给其他请求。
        """
        ephemeral_session = False
        session_key: Optional[str] = None
        auth_headers: Optional[Dict[str, str]] = None
        if user_info:
            server = await cls._load_server(server_id)
            if bool(getattr(server, "user_assertion_enabled", False)):
                user_id = str((user_info or {}).get("user_id") or "").strip()
                if not user_id:
                    raise ValueError("MCP UserContext requires an authenticated user_id")
                auth_headers = build_mcp_headers(
                    server,
                    user_info=user_info,
                    agent_info={
                        "agent_id": "mcp-tool-sync",
                        "agent_name": "MCP 工具同步",
                    },
                )
                session_key = f"{server_id}:sync:user:{user_id}:{uuid.uuid4().hex}"
                ephemeral_session = True

        session_mgr = await cls.get_session(
            server_id,
            session_key=session_key,
            auth_headers=auth_headers,
        )
        try:
            if not session_mgr.is_direct_http:
                session = session_mgr.session
                if session is None:
                    raise RuntimeError("MCP SSE session is unavailable")
                try:
                    return cls._dedupe_remote_tools(await cls._list_sse_tools(session))
                except Exception as exc:
                    logger.warning(
                        "[MCP] Tool listing failed for %s; reconnecting once: %s",
                        server_id,
                        exc,
                    )
                    await session_mgr.close()
                    await session_mgr.connect()
                    if session_mgr.is_direct_http:
                        return cls._dedupe_remote_tools(
                            await cls._list_direct_http_tools(session_mgr)
                        )
                    session = session_mgr.session
                    if session is None:
                        raise RuntimeError("MCP SSE session is unavailable after reconnect")
                    return cls._dedupe_remote_tools(await cls._list_sse_tools(session))
            return cls._dedupe_remote_tools(await cls._list_direct_http_tools(session_mgr))
        finally:
            if ephemeral_session:
                await session_mgr.close()
                if session_key:
                    cls._sessions.pop(session_key, None)

    @classmethod
    async def _ensure_direct_http_initialized(cls, session_mgr: McpSseSession) -> None:
        """并发安全的 Direct HTTP 初始化（带双重检查锁）。

        无状态网关不返回 ``mcp-session-id``，原来只认 ``mcp_session_id`` 的短路
        永远不成立，于是**每次调用**都要重发 ``initialize`` +
        ``notifications/initialized``。这里改用独立的握手标记。
        """
        if session_mgr.mcp_session_id or getattr(session_mgr, "_direct_initialized", False):
            return
        async with session_mgr._init_lock:
            if session_mgr.mcp_session_id or getattr(session_mgr, "_direct_initialized", False):
                return
            await cls._initialize_direct_http(session_mgr)
            session_mgr._direct_initialized = True

    @classmethod
    async def _initialize_direct_http(cls, session_mgr: McpSseSession) -> None:
        await cls._direct_http_rpc(session_mgr, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "nanzi-ai-agent", "version": "1.0.0"},
        })
        await cls._direct_http_rpc(
            session_mgr,
            "notifications/initialized",
            {},
            is_notification=True,
        )

    @classmethod
    async def _recover_direct_http_session(
        cls,
        session_mgr: McpSseSession,
        stale_session_id: Optional[str],
    ) -> None:
        """Reinitialize once, while coalescing concurrent stale-session recovery."""
        async with session_mgr._lock:
            if (
                stale_session_id
                and session_mgr.mcp_session_id
                and session_mgr.mcp_session_id != stale_session_id
            ):
                return
            session_mgr.mcp_session_id = None
            # 会话被判失效时要真的重新握手，不能被「只初始化一次」的标记挡住。
            session_mgr._direct_initialized = False
            await cls._initialize_direct_http(session_mgr)
            session_mgr._direct_initialized = True

    @classmethod
    async def call_remote_tool(
        cls,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        user_info: Optional[Dict[str, Any]] = None,
        agent_info: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        private_key: Any = None,
        require_user_context: bool = False,
        progress_callback: Optional[McpProgressCallback] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        session_kwargs: Dict[str, Any] = {}
        ephemeral_session = False
        session_key: Optional[str] = None
        if require_user_context or user_info or agent_info or request_id or private_key:
            server = await cls._load_server(server_id)
            if getattr(server, "enabled_status", 1) != 1:
                raise ValueError("MCP 服务已禁用，无法执行工具")
            signed_user_enabled = bool(getattr(server, "user_assertion_enabled", False))
            if signed_user_enabled and require_user_context:
                user_id = str((user_info or {}).get("user_id") or "").strip()
                if not user_id:
                    raise ValueError("MCP UserContext requires an authenticated user_id")
            auth_headers = build_mcp_headers(
                server,
                user_info=user_info,
                agent_info=agent_info,
                request_id=request_id,
                private_key=private_key,
            )
            # 凭据中心：**绑定优先，会覆盖固定的 Token**。MCP 的 fixed_token 是
            # 服务级单值（全平台共用），属于「没人绑时的默认值」；用户显式绑了
            # 自己的凭据就必须盖过它，否则绑定永远不会生效。
            mcp_denials: dict[str, str] = {}
            mcp_used: dict[str, Any] = {}
            auth_headers = await apply_mcp_bindings(
                server,
                auth_headers,
                user_info=user_info,
                denials=mcp_denials,
                used=mcp_used,
            )
            if mcp_denials:
                # 用户绑了凭据却用不了（停用/过期/场景不符）时**必须失败**，不能
                # 悄悄退回服务级 fixed_token：那样请求会成功，用户以为用的是自己的
                # 账号，实际用的是管理员的——权限边界和审计都错位了。
                raise McpCredentialDeniedError(
                    str(getattr(server, "server_name", "") or server_id), mcp_denials
                )
            session_key = server_id
            if signed_user_enabled:
                user_id = str((user_info or {}).get("user_id") or "").strip()
                if not user_id:
                    raise ValueError("MCP UserContext requires an authenticated user_id")
                # SSE transports capture their headers when connected. A signed
                # assertion is per-call, so never reuse a session carrying an
                # older assertion or jti (for either SSE or direct HTTP).
                session_key = f"{server_id}:user:{user_id}:call:{uuid.uuid4().hex}"
                ephemeral_session = True
            elif mcp_used.get("slots"):
                # 用户绑了自己的凭据：**绝不能跨用户复用连接**。
                # 原因同上面那条，只是触发点不同——用户凭据是每次请求现解析的，
                # 而 SSE 的 Header 在建连那一刻就冻住了，`get_session` 命中缓存
                # 时直接忽略新传入的 auth_headers。共用 session_key 的结果就是
                # 第一个人的凭据被后面所有人用着；而回收只在空闲 300 秒后触发，
                # 只要调用不断，串号根本没有时间上限。
                #
                # 同一用户也按次隔离：他换过凭据值之后，旧连接不能再顶着他的
                # 身份继续发请求。
                bound_user = str(mcp_used.get("user_id") or "").strip() or "unknown"
                session_key = f"{server_id}:user:{bound_user}:bind:{uuid.uuid4().hex}"
                ephemeral_session = True
            session_kwargs = {"session_key": session_key, "auth_headers": auth_headers}

        session_mgr = await cls.get_session(server_id, **session_kwargs)
        start_time = time.perf_counter()
        call_status = "success"
        call_error: Optional[str] = None
        call_result: Any = None
        try:
            if not session_mgr.is_direct_http:
                transport_retry = False
                try:
                    session = session_mgr.session
                    if session is None:
                        # session 已丢失但对象还挂在缓存里：先关闭，下一次调用才会重新
                        # 建连；否则会一直拿到同一个空会话。
                        await session_mgr.close()
                        raise RuntimeError("MCP SSE session is unavailable")
                    response = await _call_tool_with_progress(
                        session,
                        tool_name,
                        arguments,
                        progress_callback=progress_callback,
                        timeout_seconds=timeout_seconds,
                    )
                    return cls._normalize_sse_tool_result(response, tool_name)
                except Exception as e:
                    transport_broken = False
                    if not transport_retry and _is_transport_error(e):
                        transport_retry = True
                        transport_broken = True
                        original_error = e
                        try:
                            await session_mgr.close()
                            await session_mgr.connect()
                            if session_mgr.is_direct_http:
                                # 网关重连后已不再提供 SSE：本次调用改用 Direct HTTP 完成，
                                # 与 list_remote_tools 的降级口径一致，而不是让这次调用白失败。
                                logger.warning(
                                    "[MCP] SSE reconnect for %s switched to Direct HTTP; retrying '%s' over Direct HTTP",
                                    session_mgr.server_id,
                                    tool_name,
                                )
                                call_result = await cls._call_direct_http_tool(
                                    session_mgr,
                                    tool_name,
                                    arguments,
                                    timeout_seconds=timeout_seconds,
                                )
                                return redact_value(call_result, where=f"MCP 工具 {tool_name}")
                            session = session_mgr.session
                            if session is None:
                                await session_mgr.close()
                                raise RuntimeError("MCP SSE session is unavailable after reconnect")
                            response = await _call_tool_with_progress(
                                session,
                                tool_name,
                                arguments,
                                progress_callback=progress_callback,
                                timeout_seconds=timeout_seconds,
                            )
                            return cls._normalize_sse_tool_result(response, tool_name)
                        except Exception as retry_error:
                            e = RuntimeError(
                                f"{original_error}; reconnect retry failed: {retry_error}"
                            )

                    # 远端业务失败（isError / 工具自身抛错）不影响连接；只有 transport
                    # 异常才淘汰按 server_id 共享的会话，否则一次工具报错会拆掉所有并发
                    # 用户的连接并触发重连风暴。
                    if transport_broken and not ephemeral_session:
                        await session_mgr.close()
                    raise RuntimeError(
                        redact_value(
                            f"MCP tool '{tool_name}' failed: {e}",
                            where=f"MCP 工具 {tool_name} 异常",
                        )
                    ) from e
            else:
                call_result = await cls._call_direct_http_tool(
                    session_mgr,
                    tool_name,
                    arguments,
                    timeout_seconds=timeout_seconds,
                )
                return redact_value(call_result, where=f"MCP 工具 {tool_name}")
        except TimeoutError:
            # 超时必须原样往上抛：上层靠 ``isinstance(exc, TimeoutError)`` 把它
            # 判成「工具超时」。换成别的异常类型会被记成普通失败，用户看到的是
            # 「调用异常」而不是「超时」，排查方向直接跑偏。
            call_status = "failed"
            call_error = "MCP tool call timed out"
            raise
        except asyncio.CancelledError:
            call_status = "failed"
            call_error = "MCP tool call cancelled"
            raise
        except Exception as call_exc:
            call_status = "failed"
            call_error = redact_value(str(call_exc), where=f"MCP 工具 {tool_name} 审计")
            # **就地脱敏后再抛**：这条异常会经 ``RuntimeToolError`` 回显给模型、
            # 写进轨迹，还可能被上层日志带堆栈打出来。流式调用里注入的凭据是在
            # 子 Task 的 Context 里登记的，要等父级 finally 才回收——等到那时再
            # 用 ``redact_value`` 已经抓不到原值了，所以必须在抛错现场处理。
            raise RuntimeError(f"MCP tool call failed: {call_error}") from None
        finally:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            try:
                audit_task = asyncio.create_task(
                    record_outbound_audit_log(
                        server_id=server_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_info=user_info,
                        agent_info=agent_info,
                        request_id=request_id,
                        status=call_status,
                        latency_ms=latency_ms,
                        error_message=call_error,
                        tool_output=call_result,
                    )
                )
                # 事件循环对 Task 只持弱引用；不在集合里留强引用，极端情况下任务可能在
                # 执行前被 GC，导致这条审计丢失。
                _AUDIT_TASKS.add(audit_task)
                audit_task.add_done_callback(_AUDIT_TASKS.discard)
            except Exception as audit_dispatch_err:
                logger.warning("[MCP] Failed to dispatch outbound audit log: %s", audit_dispatch_err)

            if ephemeral_session:
                await session_mgr.close()
                if session_key:
                    cls._sessions.pop(session_key, None)

    @staticmethod
    def _parse_sse_payload(text: str) -> Optional[Dict]:
        """从 Streamable HTTP 的 SSE 响应体中解析出 JSON-RPC payload。

        响应体形如：
            event: message
            data: {"jsonrpc":"2.0","id":3,"result":{...}}

        多行 data: 按 SSE 规范拼接；返回最后一条解析成功的 JSON-RPC 对象。
        """
        if not text:
            return None
        data_buffer: list = []
        events: list = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_buffer.append(line[len("data:"):].lstrip())
            elif line == "" and data_buffer:
                events.append("".join(data_buffer))
                data_buffer = []
        if data_buffer:
            events.append("".join(data_buffer))
        for payload in reversed(events):
            payload = payload.strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict) and ("result" in obj or "error" in obj or "method" in obj):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _is_session_expired_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        error = payload.get("error")
        candidates = [
            payload.get("Code"),
            payload.get("code"),
            payload.get("Message"),
            payload.get("message"),
        ]
        if isinstance(error, dict):
            candidates.extend([error.get("code"), error.get("message")])
        normalized = " ".join(str(value).lower() for value in candidates if value is not None)
        return any(marker in normalized for marker in (
            "sessionexpired",
            "session expired",
            "session not found",
            "unknown session",
            "invalid session",
        ))

    @classmethod
    async def _direct_http_rpc(cls, session_mgr: McpSseSession, method: str, params: Optional[Dict], is_notification: bool = False, retry_count: int = 0, timeout: Optional[float] = None) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **session_mgr.auth_headers
        }
        request_session_id = session_mgr.mcp_session_id
        if request_session_id:
            headers["mcp-session-id"] = request_session_id
        
        rpc_id = session_mgr.next_rpc_id() if not is_notification else None
        payload = { "jsonrpc": "2.0", "method": method, "params": params or {} }
        if rpc_id is not None:
            payload["id"] = rpc_id

        logger.debug(f"[MCP-Direct] Request: {method} to {session_mgr.sse_url} | RPC ID: {rpc_id} | Headers keys: {list(headers.keys())}")
        client = session_mgr.get_http_client()
        session_mgr._active_requests = getattr(session_mgr, "_active_requests", 0) + 1
        session_mgr._active_requests_changed.clear()
        try:
            # per-request timeout：共享 client 的默认值不能成为工具超时的第二套口径。
            resp = await client.post(
                session_mgr.sse_url,
                json=payload,
                headers=headers,
                timeout=timeout if timeout and timeout > 0 else None,
            )
            logger.info(f"[MCP-Direct] Response from {method}: HTTP {resp.status_code}")
        except Exception as http_err:
            redacted_err = redact_value(str(http_err), where=f"MCP 服务 {method} 异常")
            logger.error(f"[MCP-Direct] HTTP Request failed for {method}: {redacted_err}")
            raise RuntimeError(
                f"MCP HTTP request failed for {method}: {redacted_err}"
            ) from None
        finally:
            session_mgr._active_requests = max(0, getattr(session_mgr, "_active_requests", 1) - 1)
            if session_mgr._active_requests == 0:
                session_mgr._active_requests_changed.set()

        # Capture Session ID from initialization
        if method == "initialize" and resp.status_code == 200:
            s_id = resp.headers.get("mcp-session-id")
            if not s_id:
                try:
                    try:
                        res_data = resp.json().get("result", {})
                    except json.JSONDecodeError:
                        res_data = (cls._parse_sse_payload(resp.text) or {}).get("result", {})
                    s_id = res_data.get("_experimental", {}).get("session_id") or res_data.get("session_id")
                except Exception:
                    pass
            if s_id:
                session_mgr.mcp_session_id = s_id
                logger.info(f"[MCP-Direct] Captured Session ID: {s_id}")

        # Accept all 2xx codes (200, 201, 202, 204)
        if 200 <= resp.status_code < 300:
            if resp.status_code == 204 or not resp.text:
                return None

            raw_text = resp.text
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = cls._parse_sse_payload(raw_text)
                if data is None:
                    safe_preview = redact_value(
                        raw_text, where=f"MCP 服务 {method} 非 JSON 响应"
                    )[:200]
                    logger.warning(f"[MCP-Direct] Non-JSON success response: {safe_preview}")
                    return None  # Success but not JSON (e.g. 202 Accepted)
                logger.info(f"[MCP-Direct] Parsed SSE-encoded response for {method}")

            if "result" in data:
                return data["result"]
            if is_notification:
                return None
            if "error" in data:
                if (
                    request_session_id
                    and retry_count < 1
                    and not is_notification
                    and method != "initialize"
                    and cls._is_session_expired_payload(data)
                ):
                    logger.warning(
                        "[MCP-Direct] Session invalid for %s; reinitializing before retrying %s",
                        session_mgr.server_id,
                        method,
                    )
                    await cls._recover_direct_http_session(session_mgr, request_session_id)
                    return await cls._direct_http_rpc(
                        session_mgr,
                        method,
                        params,
                        is_notification,
                        retry_count=retry_count + 1,
                    )
                redacted_error = redact_value(
                    data["error"], where=f"MCP 服务 {method} RPC 错误"
                )
                logger.error(f"[MCP-Direct] RPC Error Response: {redacted_error}")
                if isinstance(redacted_error, dict):
                    raise Exception(
                        f"RPC Error {redacted_error.get('code')}: {redacted_error.get('message')}"
                    )
                raise Exception(f"RPC Error: {redacted_error}")
            return data

        error_payload = None
        try:
            error_payload = resp.json()
        except json.JSONDecodeError:
            error_payload = cls._parse_sse_payload(resp.text)
        if (
            request_session_id
            and retry_count < 1
            and not is_notification
            and method != "initialize"
            and resp.status_code in {400, 401, 404, 410}
            and cls._is_session_expired_payload(error_payload)
        ):
            logger.warning(
                "[MCP-Direct] HTTP %s invalidated session for %s; reinitializing before retrying %s",
                resp.status_code,
                session_mgr.server_id,
                method,
            )
            await cls._recover_direct_http_session(session_mgr, request_session_id)
            return await cls._direct_http_rpc(
                session_mgr,
                method,
                params,
                is_notification,
                retry_count=retry_count + 1,
            )

        safe_body = redact_value(resp.text, where=f"MCP 服务 {method} 错误响应")
        logger.error(f"[MCP-Direct] Error Response Body: {safe_body[:500]}")
        # 完整 body 脱敏后抛给上层；日志侧同样保留脱敏前缀，避免明文进日志。
        raise Exception(f"HTTP {resp.status_code}: {safe_body}")

    @classmethod
    async def _call_direct_http_tool(
        cls,
        session_mgr: McpSseSession,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        """Invoke one tool over the Direct HTTP gateway and normalize its result.

        Shared by the native Direct HTTP path and the SSE-to-HTTP fallback, so both
        produce identical payloads and identical error semantics. ``timeout_seconds``
        is the platform per-tool timeout (``agent_max_toolcall_timeout`` snapshot),
        applied per request so it never leaks into the shared client's defaults.
        """
        await cls._ensure_direct_http_initialized(session_mgr)
        rpc_kwargs: Dict[str, Any] = (
            {"timeout": timeout_seconds}
            if timeout_seconds and timeout_seconds > 0
            else {}
        )
        res = await cls._direct_http_rpc(session_mgr, "tools/call", {
            "name": tool_name,
            "arguments": arguments
        }, **rpc_kwargs)
        return cls._normalize_direct_http_tool_result(res, tool_name)

    @staticmethod
    def _normalize_sse_tool_result(response: Any, tool_name: str) -> Any:
        """规整 SSE/SDK tools/call 返回并统一脱敏（首次调用与重连重试共用）。"""
        where = f"MCP 工具 {tool_name}"
        text = "".join(
            getattr(item, "text", "")
            for item in (getattr(response, "content", None) or [])
        )
        if bool(getattr(response, "isError", False) or getattr(response, "is_error", False)):
            # 远端 tools/call 的错误体同样可能回显入参（含凭据槽位），
            # 脱敏后才能进模型上下文。
            raise RuntimeError(
                redact_value(text, where=f"{where} 错误响应")
                or f"MCP tool '{tool_name}' returned an error"
            )
        structured_content = getattr(response, "structuredContent", None)
        if structured_content is None:
            structured_content = getattr(response, "structured_content", None)
        if structured_content is not None:
            return redact_value(
                {
                    "success": True,
                    "content": text,
                    "structured_content": structured_content,
                },
                where=where,
            )
        return redact_value(
            text if text else {"success": True, "content": ""},
            where=where,
        )

    @staticmethod
    def _normalize_direct_http_tool_result(res: Any, tool_name: str) -> Any:
        """把 Direct HTTP 的 tools/call 结果规整成工具返回值。

        抽出来是为了能被单测覆盖：**正常返回才是主路径**，它和错误分支一样必须
        脱敏。远端会把入参（含刚注入的凭据槽位）原样回显在 content 里，
        漏掉这条等于把明文交给模型。
        """
        where = f"MCP 工具 {tool_name}"

        if isinstance(res, dict) and bool(res.get("isError") or res.get("is_error")):
            error_text = "".join(
                c.get("text", "")
                for c in res.get("content") or []
                if isinstance(c, dict) and c.get("type") == "text"
            )
            raise RuntimeError(
                redact_value(
                    error_text or str(res.get("message") or res.get("error") or ""),
                    where=f"{where} 错误响应",
                )
                or f"MCP tool '{tool_name}' returned an error"
            )

        if isinstance(res, dict) and "content" in res:
            text = "".join(
                c.get("text", "")
                for c in (res.get("content") or [])
                if isinstance(c, dict) and c.get("type") == "text"
            )
            structured_content = res.get("structuredContent")
            if structured_content is None:
                structured_content = res.get("structured_content")
            if structured_content is not None:
                return redact_value(
                    {
                        "success": True,
                        "content": text,
                        "structured_content": structured_content,
                    },
                    where=where,
                )
            return redact_value(
                text if text else {"success": True, "content": ""},
                where=where,
            )

        if res is None:
            return {"success": True, "content": ""}

        # 整个响应体串化返回，连字段边界都没有，含凭据时必然整段进模型上下文
        return redact_value(str(res), where=where)

    @classmethod
    async def sync_tools(
        cls,
        server_id: str,
        *,
        user_info: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ):
        """拉取远端工具并同步缓存。

        - `user_info`：UserContext 服务的 tools/list 也需要签名时传入当前操作者；
        - `force`：远端返回 0 个工具而本地仍有可用工具时，默认拒绝应用（防止远端
          瞬时故障/权限抖动把整批工具误标为删除）；显式 force 才允许清空。
        """
        tools = await cls.list_remote_tools(server_id, user_info=user_info)
        async with AsyncSessionLocal() as db:
            # 同一服务的并发同步（创建自动同步 / 管理员手点 / 多 worker）会在插入新
            # 工具时撞 (server_id, tool_name) 唯一键；用行锁把 DB 应用段串行化。
            result = await db.execute(
                select(McpServer).where(McpServer.id == server_id).with_for_update()
            )
            server = result.scalar_one_or_none()
            if not server:
                return
            from datetime import datetime

            cached_result = await db.execute(
                select(McpToolCache).where(McpToolCache.server_id == server_id)
            )
            cached_tools = cached_result.scalars().all()
            cached_by_name = {tool.tool_name: tool for tool in cached_tools}
            cached_available = sum(1 for tool in cached_tools if getattr(tool, "is_available", True))
            remote_tool_names = set()
            stale_unpublished = 0
            remote_deleted_count = 0

            for t in tools:
                t_name = t.name if hasattr(t, "name") else t.get("name")
                if not t_name:
                    continue
                t_desc = t.description if hasattr(t, "description") else t.get("description")
                t_schema = _tool_input_schema(t)
                if hasattr(t_schema, "model_dump"):
                    t_schema = t_schema.model_dump(mode="json")
                schema_payload = (
                    _strip_platform_schema_extensions(t_schema)
                    if isinstance(t_schema, dict)
                    else {}
                )
                annotations = getattr(t, "annotations", None)
                if annotations is None and isinstance(t, dict):
                    annotations = t.get("annotations")
                if hasattr(annotations, "model_dump"):
                    annotations = annotations.model_dump(mode="json")
                if isinstance(annotations, dict) and annotations:
                    schema_payload["x-nanzi-mcp-annotations"] = annotations
                full_name = f"{server.server_name}:{t_name}"
                remote_tool_names.add(full_name)
                existing = cached_by_name.get(full_name)
                if existing:
                    existing.is_available = True
                    existing.tool_description = t_desc
                    existing.parameter_schema = json.dumps(schema_payload)
                else:
                    created = McpToolCache(
                        id=str(uuid.uuid4()),
                        server_id=server_id,
                        tool_name=full_name,
                        tool_description=t_desc,
                        parameter_schema=json.dumps(schema_payload),
                        is_published=False,
                        is_available=True,
                    )
                    db.add(created)
                    # 同一批远端结果即使有重复名（normally 已在 list_remote_tools 去重），
                    # 也不会再插入第二行。
                    cached_by_name[full_name] = created

            if not remote_tool_names and cached_available and not force:
                raise McpSyncGuardError(
                    f"远端返回 0 个工具，但本地仍有 {cached_available} 个可用工具；"
                    "已拒绝本次同步以避免误下线。若确认远端确实已清空，请使用强制同步。"
                )

            server.last_sync_at = datetime.now()
            for cached_tool in cached_tools:
                if cached_tool.tool_name not in remote_tool_names and cached_tool.is_published:
                    cached_tool.is_published = False
                    stale_unpublished += 1
                if cached_tool.tool_name not in remote_tool_names:
                    if getattr(cached_tool, "is_available", True):
                        remote_deleted_count += 1
                    cached_tool.is_available = False
            await db.commit()
            try:
                from app.services.ai.tools.registry import ToolRegistry
                ToolRegistry.clear_db_tool_cache()
            except Exception:
                logger.exception("[MCP] Failed to clear runtime tool cache after sync for %s", server_id)
            return {
                "server_id": server_id,
                "remote_tool_count": len(remote_tool_names),
                "stale_unpublished": stale_unpublished,
                "remote_deleted_count": remote_deleted_count,
            }

    @classmethod
    async def evict_session(cls, server_id: str) -> None:
        """显式关闭并移除特定 server_id 的所有缓存会话"""
        to_evict = []
        for key, session in list(cls._sessions.items()):
            if key == server_id or key.startswith(f"{server_id}:"):
                to_evict.append((key, session))
        for key, session in to_evict:
            try:
                await session.close()
            except Exception as exc:
                logger.warning("[MCP] Failed to close session %s during eviction: %s", key, exc)
            cls._sessions.pop(key, None)

    @classmethod
    async def _idle_cleanup_loop(cls):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired_keys = []
            for sid, session in list(cls._sessions.items()):
                # 不能再用「有没有 session/session_id」当条件：无状态 Direct HTTP
                # 网关两者都为空，那样这条会话永远不会被回收。进行中的请求仍然跳过，
                # 否则会把正在用的传输关掉。
                if getattr(session, "_active_requests", 0) > 0:
                    continue
                if now - session.last_used_at > 300:
                    await session.close()
                    expired_keys.append(sid)
            for sid in expired_keys:
                cls._sessions.pop(sid, None)

    @classmethod
    async def shutdown(cls):
        if cls._cleanup_task: cls._cleanup_task.cancel()
        for session in cls._sessions.values(): await session.close()
