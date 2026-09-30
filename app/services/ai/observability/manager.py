"""Langfuse SDK 生命周期与 OpenTelemetry 导出钩子。

平台把 Langfuse 当作 OTel 的**后端**使用：SDK 在构造时会把自己的 TracerProvider 注册为
全局 provider（仅当当前仍是 ``ProxyTracerProvider``），因此 AgentScope 的
``TracingMiddleware`` 产生的 span 会自动流到 Langfuse。

两个钩子都在**导出阶段**执行，读的都是实时快照，所以开关、采样率、内容脱敏都不需要重建 client：

- ``should_export_span``：放行白名单 scope（``agentscope`` / ``nanzi.llm``）或带 ``gen_ai.*``
  属性的 span，再按 trace 粒度采样。**必须自定义**：Langfuse 默认过滤器只认自家 span、
  GenAI span 与已知 LLM 插桩库，``agentscope`` 不在名单里，不自定义就连根 span 都会被静默丢弃。
- ``mask_otel_spans``：关闭内容采集时删除承载用户内容的属性，保留结构与 token 用量。

连接参数（host / keys / timeout）变化时按指纹重建 client；重建先建新再关旧，
新建失败则继续沿用旧 client，避免把正在工作的链路打断。
"""

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

from app.services.ai.observability import gate, settings, span_attributes

logger = logging.getLogger(__name__)

# 只有这些 instrumentation scope 的 span 会被上报；其余库（若将来启用插桩）不会被顺带收集。
ALLOWED_SCOPE_NAMES = frozenset({"agentscope", "nanzi.llm"})
GENAI_ATTRIBUTE_PREFIX = "gen_ai."

# 连通性探测：与 SDK 的 auth_check 等价，但不构造 client，避免污染全局 TracerProvider。
AUTH_PROBE_PATH = "/api/public/projects"

_client: Optional[Any] = None
_client_fingerprint: Optional[str] = None
_last_error: Optional[str] = None
_lock = threading.Lock()
_listener_registered = False


def current_client() -> Optional[Any]:
    """返回当前 Langfuse client（未启用/初始化失败时为 None）。"""
    with _lock:
        return _client


def current_fingerprint() -> Optional[str]:
    """返回当前 client 对应连接参数的指纹。"""
    with _lock:
        return _client_fingerprint


def last_error() -> Optional[str]:
    """返回最近一次初始化/重建失败原因。"""
    with _lock:
        return _last_error


def _set_error(message: Optional[str]) -> None:
    global _last_error
    with _lock:
        _last_error = message


# --------------------------------------------------------------------------- 导出钩子


def _span_scope_name(span: Any) -> str:
    scope = getattr(span, "instrumentation_scope", None)
    return str(getattr(scope, "name", "") or "")


def _span_attributes(span: Any) -> Dict[str, Any]:
    attributes = getattr(span, "attributes", None)
    return dict(attributes) if attributes else {}


def _is_allowed_scope(scope_name: str, attributes: Dict[str, Any]) -> bool:
    if scope_name in ALLOWED_SCOPE_NAMES:
        return True
    if scope_name.startswith("nanzi.llm"):
        return True
    return any(str(key).startswith(GENAI_ATTRIBUTE_PREFIX) for key in attributes)


def _span_trace_id_hex(span: Any) -> Optional[str]:
    try:
        span_context = span.get_span_context()
        trace_id = getattr(span_context, "trace_id", None)
    except Exception:
        return None
    if not trace_id:
        return None
    return format(int(trace_id), "032x")


def make_should_export_span() -> Callable[[Any], bool]:
    """构造 ``should_export_span`` 钩子（判定逻辑读实时快照）。"""

    def should_export_span(span: Any) -> bool:
        try:
            snapshot = settings.get_snapshot()
            if snapshot.status != settings.STATUS_ENABLED:
                return False
            attributes = _span_attributes(span)
            if not _is_allowed_scope(_span_scope_name(span), attributes):
                return False
            return gate.is_sampled(_span_trace_id_hex(span), snapshot.sample_rate)
        except Exception as exc:  # 钩子抛异常会导致整批丢弃，必须自兜底
            logger.warning("Langfuse should_export_span 判定异常，已丢弃该 span: %s", exc)
            return False

    return should_export_span


def make_mask_otel_spans() -> Callable[..., Optional[Any]]:
    """构造 ``mask_otel_spans`` 钩子：关闭内容采集时删除内容属性。"""

    def mask_otel_spans(*, params: Any) -> Optional[Any]:
        try:
            snapshot = settings.get_snapshot()
            if snapshot.capture_content:
                return None

            from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

            patches: Dict[Any, Any] = {}
            for identifier, span in getattr(params, "spans", {}).items():
                attributes = _span_attributes(span)
                content_keys = tuple(
                    key for key in attributes if span_attributes.is_content_attribute(str(key))
                )
                if content_keys:
                    patches[identifier] = OtelSpanPatch(delete_attributes=content_keys)

            if not patches:
                return None
            return MaskOtelSpansResult(span_patches=patches)
        except Exception as exc:
            # 返回非法结果会让 Langfuse 丢弃整批，因此异常时退回「不改动」而不是抛出去。
            logger.warning("Langfuse mask_otel_spans 执行异常，本次不脱敏: %s", exc)
            return None

    return mask_otel_spans


# --------------------------------------------------------------------------- client 生命周期


def _build_client(snapshot: "settings.LangfuseSnapshot") -> Any:
    from langfuse import Langfuse

    return Langfuse(
        host=snapshot.host,
        public_key=snapshot.public_key,
        secret_key=snapshot.secret_key,
        timeout=snapshot.timeout_seconds,
        environment=snapshot.environment,
        release=snapshot.release,
        should_export_span=make_should_export_span(),
        mask_otel_spans=make_mask_otel_spans(),
    )


def _teardown_client(client: Any) -> None:
    """关停旧 client：先尽力 flush 再 shutdown，异常只记日志。

    已 shutdown 的 ``BatchSpanProcessor`` 会忽略后续 ``emit()``，因此不会重复上报。
    """
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:
        logger.warning("Langfuse flush 失败（忽略）: %s", exc)
    try:
        client.shutdown()
    except Exception as exc:
        logger.warning("Langfuse shutdown 失败（忽略）: %s", exc)


def _teardown_client_in_background(client: Any) -> None:
    """在后台线程里关闭被替换掉的旧 client。

    为什么必须异步——这是个真实踩过的坑：``client.flush()`` 内部是**同步**的
    ``tracer_provider.force_flush()``，它会等导出线程把队列发完。Langfuse 不可达时，
    这一等就是最长一个 ``timeout_seconds``（默认 5s）。而重建 client 的调用链是
    「配置刷新 → 快照监听器 → ``_ensure_client``」，整条链**跑在事件循环线程里**，
    一旦在这里同步 flush，事件循环会被卡住，**所有并发请求（包括正在流式输出的对话）
    都会一起停顿**——这违背了「追踪只是旁路」的前提。

    ``daemon=True`` 的取舍：进程被强杀时这个后台线程可能来不及 flush（丢最后一批
    span）。但正常关闭走的是 :func:`shutdown_observability`，那里会 ``await`` 完成，
    不丢数据。宁可丢几个 span，也不能让对话卡住。
    """
    if client is None:
        return
    try:
        thread = threading.Thread(
            target=_teardown_client,
            args=(client,),
            name="langfuse-teardown",
            daemon=True,
        )
        thread.start()
    except Exception as exc:  # pragma: no cover - 极端情况下退化为同步关闭
        logger.warning("Langfuse 旧 client 后台关闭线程启动失败，改为同步关闭: %s", exc)
        _teardown_client(client)


def _ensure_client() -> None:
    """按当前快照保证 client 与连接参数一致（同步，可被刷新协程与启动路径调用）。"""
    global _client, _client_fingerprint

    snapshot = settings.get_snapshot()

    if snapshot.status != settings.STATUS_ENABLED:
        # 已建好的 client 不主动销毁：导出钩子已按实时快照拦截，销毁反而让「再打开」
        # 需要重建。冷启动时则根本不构造，做到零开销。
        return

    with _lock:
        if _client is not None and _client_fingerprint == snapshot.client_fingerprint:
            return

    try:
        new_client = _build_client(snapshot)
    except Exception as exc:
        _set_error(f"{type(exc).__name__}: {exc}")
        logger.error("Langfuse client 初始化失败，追踪降级为 no-op: %s", exc, exc_info=True)
        return

    with _lock:
        old_client = _client
        _client = new_client
        _client_fingerprint = snapshot.client_fingerprint
        _last_error = None

    if old_client is not None and old_client is not new_client:
        # 必须走后台上线程：这里可能正跑在事件循环里，而关闭旧 client 会同步 flush。
        _teardown_client_in_background(old_client)
    logger.info("Langfuse client 就绪: host=%s", snapshot.host)


def _on_snapshot_refreshed(_snapshot: "settings.LangfuseSnapshot") -> None:
    _ensure_client()


def _register_listener() -> None:
    global _listener_registered
    if _listener_registered:
        return
    settings.add_refresh_listener(_on_snapshot_refreshed)
    _listener_registered = True


async def init_observability() -> None:
    """启动期初始化：读一次配置、注册刷新监听、按需建 client。

    任何失败都只降级，不抛异常、不阻塞启动 —— 配置现在来自数据库，
    冷启动时主库可能尚未就绪，此时由 30s 刷新协程在读到有效配置后自动补建 client。
    """
    try:
        await settings.refresh_snapshot()
    except Exception as exc:  # settings 内部已兜底，这里再保险一层
        logger.warning("Langfuse 配置初次读取失败: %s", exc)

    _register_listener()
    try:
        settings.start_refresh_task()
    except RuntimeError as exc:
        # 无运行中的事件循环（例如纯脚本导入）时跳过定时刷新。
        logger.warning("Langfuse 配置刷新任务未启动: %s", exc)

    _ensure_client()


async def shutdown_observability() -> None:
    """优雅关闭：停掉刷新协程，flush 后关闭 client。"""
    global _client, _client_fingerprint

    try:
        await settings.stop_refresh_task()
    except Exception as exc:
        logger.warning("Langfuse 刷新任务停止失败（忽略）: %s", exc)

    with _lock:
        client = _client
        _client = None
        _client_fingerprint = None

    if client is None:
        return
    # 在导出线程外做一次阻塞 flush，避免进程退出时丢最后一批 span。
    await asyncio.to_thread(_teardown_client, client)


# --------------------------------------------------------------------------- 连通性探测


def _resolve_probe_settings(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把「表单草稿值」叠加到已保存快照上，得到本次探测实际使用的参数。

    这样用户填完即可点「测试连接」，不必先保存；同时未填的字段（尤其是密码框留空
    ＝不修改的 secret_key）回落到已保存配置，不会被空值冲掉。
    """
    snapshot = settings.get_snapshot()
    draft = overrides or {}

    def pick(name: str, saved: Any) -> Any:
        if name not in draft:
            return saved
        value = draft[name]
        if value is None:
            return saved
        if isinstance(value, str) and not value.strip():
            return saved
        return value

    return {
        "enabled": pick("enabled", snapshot.enabled),
        "host": pick("host", snapshot.host),
        "public_key": pick("public_key", snapshot.public_key),
        "secret_key": pick("secret_key", snapshot.secret_key),
        "timeout_seconds": pick("timeout_seconds", snapshot.timeout_seconds),
    }


async def test_connection(overrides: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """用配置做一次真实的连通性与鉴权探测。

    直接请求 ``{host}/api/public/projects``（等价于 SDK 的 ``auth_check``），
    不构造 client，因此不会注册全局 TracerProvider、不会产生任何 span。

    ``overrides`` 为可选的表单草稿值（未保存的输入）。探测**只读**这些值，
    不会写库也不会改动进程内快照。
    """
    probe = _resolve_probe_settings(overrides)

    if not probe["enabled"]:
        return (False, "追踪总开关未启用，请先打开开关")

    missing = [
        name
        for name, value in (
            ("host", probe["host"]),
            ("public_key", probe["public_key"]),
            ("secret_key", probe["secret_key"]),
        )
        if not value
    ]
    if missing:
        return (False, f"配置不完整，缺少: {', '.join(missing)}")

    url = f"{str(probe['host']).strip().rstrip('/')}{AUTH_PROBE_PATH}"
    timeout = max(int(probe["timeout_seconds"] or 5), 1)

    try:
        async with httpx.AsyncClient(
            auth=httpx.BasicAuth(probe["public_key"], probe["secret_key"]),
            timeout=timeout,
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return (False, f"连接 Langfuse 超时（>{timeout}s）：{url}")
    except httpx.ConnectError as exc:
        return (False, f"无法连接 Langfuse（DNS 或网络不可达）：{exc}")
    except Exception as exc:
        return (False, f"连接 Langfuse 失败：{type(exc).__name__}: {exc}")

    status_code = getattr(response, "status_code", 0)
    if status_code == 401 or status_code == 403:
        return (False, f"密钥无效或无权限（HTTP {status_code}），请核对 public/secret key")
    if status_code >= 400:
        return (False, f"Langfuse 返回异常状态码 HTTP {status_code}：{url}")

    try:
        payload = response.json()
        projects = payload.get("data") if isinstance(payload, dict) else None
    except Exception:
        projects = None

    if isinstance(projects, list) and not projects:
        return (False, "鉴权通过但未返回任何项目，请确认密钥所属项目")

    return (True, f"连接成功，Langfuse 可访问：{url}")


# --------------------------------------------------------------------------- 测试辅助


def reset_for_tests() -> None:
    """仅测试使用：清空 client 与错误状态。"""
    global _client, _client_fingerprint, _last_error, _listener_registered
    with _lock:
        _client = None
        _client_fingerprint = None
        _last_error = None
    _listener_registered = False
    settings.clear_refresh_listeners()
