"""网页预览内嵌可行性实测服务。

**为什么判定必须在服务端做**：``X-Frame-Options`` 与 CSP ``frame-ancestors`` 都是
**响应头**。跨域 iframe 里前端既读不到响应头，也无法可靠自证「被拦了」——被拦截时
``onload`` 仍会触发、``onerror`` 不一定触发，父页面拿不到任何可信信号。于是前端只能
退化成「按域名猜」，这正是 ``www.baidu.com`` 被误拦、用户点「仍尝试加载」又正常打开
的直接原因（百度首页实测并不下发任何防嵌套头）。

**安全边界**（复用浏览器自动化同一套 SSRF 口径）：

1. 复用 :func:`validate_public_navigation`：只允许 http/https，拒绝环回、私网、
   链路本地、保留网段与云元数据地址，且先做 DNS 解析再判断（防域名指向内网）。
2. 手动逐跳处理重定向并**每跳重新校验**——若交给 httpx 自动跟随，
   ``https://evil.example`` 可以 302 到 ``http://169.254.169.254/`` 绕过校验。
3. 用 streaming GET：只取响应头就立即断开，不下载正文，避免被当成下载器或流量放大器。
4. 探测不出结论时一律返回「未知」（``embeddable=None``）交由前端降级，
   **绝不默认判成受限**，否则又会退回到本次要修的误拦行为。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.services.ai.browser.browser_policy import (
    BrowserUrlBlocked,
    validate_public_navigation,
)

logger = logging.getLogger(__name__)

#: 探测超时：这是给用户点开预览时的同步等待，必须短。
PROBE_TIMEOUT_SECONDS = 6.0
#: 最多跟随的重定向跳数（每跳都会重新过 SSRF 校验）。
MAX_REDIRECTS = 5
#: 以浏览器身份探测：部分站点对非浏览器 UA 下发不同的响应头，
#: 而我们要回答的问题正是「浏览器里能不能嵌」，不是「能不能抓取正文」。
PROBE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_BLOCKING_XFO_TOKENS = frozenset({"deny", "sameorigin"})


@dataclass(frozen=True)
class EmbedPolicyVerdict:
    """纯响应头判定结果。``embeddable`` 为 ``None`` 表示未能得出结论。"""

    embeddable: Optional[bool]
    reason: str
    signals: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbedProbeResult:
    """一次实测的完整结果，直接作为接口响应体。"""

    embeddable: Optional[bool]
    reason: str
    signals: Dict[str, str] = field(default_factory=dict)
    final_url: str = ""


# --------------------------------------------------------------------------
# 响应头判定（纯函数，无 IO，可单测）
# --------------------------------------------------------------------------


def _extract_frame_ancestors(policy: str) -> Optional[str]:
    """取出 CSP 里的 ``frame-ancestors`` 指令值；没有该指令则返回 None。

    ``default-src 'none'`` 之类的指令**不限制嵌入**，只有显式 ``frame-ancestors`` 才会。
    """
    for directive in str(policy or "").split(";"):
        parts = directive.strip().split(None, 1)
        if not parts:
            continue
        if parts[0].lower() == "frame-ancestors":
            # ``frame-ancestors;`` 这种空源列表等价于拒绝全部嵌入。
            return parts[1].strip() if len(parts) > 1 else ""
    return None


def _host_source_matches(source: str, platform_origins: Sequence[str]) -> bool:
    """判断 CSP 源表达式是否是平台自身（支持 ``https://*.example.com`` 通配）。"""
    raw = str(source or "").strip().rstrip("/")
    if not raw or raw.startswith(("'", '"')):
        return False

    parsed_source = urlparse(raw if "//" in raw else f"//{raw}")
    source_host = (parsed_source.hostname or "").lower()
    if not source_host:
        return False
    source_scheme = parsed_source.scheme.lower()

    for origin in platform_origins:
        candidate = str(origin or "").strip()
        if not candidate:
            continue
        parsed_origin = urlparse(
            candidate if "//" in candidate else f"https://{candidate}"
        )
        origin_host = (parsed_origin.hostname or "").lower()
        if not origin_host:
            continue
        origin_scheme = parsed_origin.scheme.lower()
        if source_scheme and origin_scheme and source_scheme != origin_scheme:
            continue

        if source_host.startswith("*."):
            suffix = source_host[2:]
            if origin_host == suffix or origin_host.endswith(f".{suffix}"):
                return True
        elif origin_host == source_host:
            return True
    return False


def _frame_ancestors_allows(sources: str, platform_origins: Sequence[str]) -> bool:
    # CSP 源表达式之间以**空格**分隔（逗号只是容错写法），
    # 例如 ``frame-ancestors 'self' https://portal.example.com``。
    tokens = [token for token in str(sources or "").replace(",", " ").split() if token]
    if not tokens:
        # 空源列表 = 不允许任何祖先。
        return False
    for token in tokens:
        lowered = token.lower()
        if token == "*":
            return True
        if lowered in {"'none'", '"none"', "'self'", '"self"'}:
            # 'none' 不授权任何源；'self' 指的是被嵌页面自身，而预览面板总是异源。
            continue
        if _host_source_matches(token, platform_origins):
            return True
    return False


def evaluate_embed_policy(
    headers: Iterable[Tuple[str, str]],
    *,
    platform_origins: Sequence[str] = (),
) -> EmbedPolicyVerdict:
    """根据真实响应头判断浏览器是否会拦截内嵌展示。

    ``report-only`` 的 CSP 只上报不拦截，一律不参与判定；
    多条强制策略中任意一条拒绝嵌入，结论即为不可嵌。
    """
    pairs = [(str(key).lower(), str(value)) for key, value in headers]
    xfo_values = [value for key, value in pairs if key == "x-frame-options"]
    csp_values = [value for key, value in pairs if key == "content-security-policy"]

    signals: Dict[str, str] = {}
    if xfo_values:
        signals["x-frame-options"] = xfo_values[0].strip()

    frame_ancestors_directives: List[str] = []
    for policy in csp_values:
        directive = _extract_frame_ancestors(policy)
        if directive is not None:
            frame_ancestors_directives.append(directive)
    if frame_ancestors_directives:
        signals["content-security-policy"] = " | ".join(
            f"frame-ancestors {directive}".strip()
            for directive in frame_ancestors_directives
        )

    blocking_reasons: List[str] = []

    for value in xfo_values:
        token = value.strip().split(",")[0].strip().lower()
        # ALLOW-FROM 已被现代浏览器废弃，不可能命中平台自身，按拦截处理。
        if token in _BLOCKING_XFO_TOKENS or token.startswith("allow-from"):
            blocking_reasons.append(f"X-Frame-Options: {value.strip()}")
            break

    for directive in frame_ancestors_directives:
        if _frame_ancestors_allows(directive, platform_origins):
            continue
        blocking_reasons.append(f"CSP frame-ancestors {directive}".strip())
        break

    if blocking_reasons:
        return EmbedPolicyVerdict(
            embeddable=False,
            reason="目标站点声明了防嵌套策略（"
            + "；".join(blocking_reasons)
            + "），浏览器会拦截内嵌展示",
            signals=signals,
        )

    return EmbedPolicyVerdict(
        embeddable=True,
        reason="目标站点未声明任何防嵌套策略，可正常内嵌",
        signals=signals,
    )


# --------------------------------------------------------------------------
# 实测探测（含 SSRF 防线）
# --------------------------------------------------------------------------


def _platform_origins() -> Tuple[str, ...]:
    """平台自身的源，用于匹配 CSP ``frame-ancestors`` 白名单。"""
    origins: List[str] = []

    public_url = str(getattr(settings, "APP_PUBLIC_URL", "") or "").strip()
    if public_url:
        origins.append(public_url)

    allowed = getattr(settings, "ALLOWED_ORIGINS", None) or []
    if isinstance(allowed, str):
        allowed = [item.strip() for item in allowed.split(",")]
    origins.extend(str(item).strip() for item in allowed if str(item).strip())

    return tuple(origins)


async def probe_embed_policy(
    url: str,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    max_redirects: int = MAX_REDIRECTS,
    platform_origins: Optional[Sequence[str]] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> EmbedProbeResult:
    """实测目标 URL 的响应头并给出内嵌结论。

    首跳地址非法（内网、非 http(s)）时抛出 :class:`BrowserUrlBlocked`，由调用方转成 400；
    其它一切失败都收敛成 ``embeddable=None`` 的「未知」结论。
    """
    # 首跳校验失败要让调用方明确拒绝（400），而不是伪装成「未知」。
    target = validate_public_navigation(url)

    origins = tuple(platform_origins) if platform_origins is not None else _platform_origins()
    current = target

    client_kwargs = {
        "timeout": timeout,
        "follow_redirects": False,  # 自己跳，才能逐跳校验 SSRF
        "headers": {
            "User-Agent": PROBE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    }
    if transport is not None:
        client_kwargs["transport"] = transport

    async with httpx.AsyncClient(**client_kwargs) as client:
        for _ in range(max_redirects + 1):
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUS:
                        location = response.headers.get("location")
                        if location:
                            next_url = urljoin(str(response.url), location)
                            try:
                                validate_public_navigation(next_url)
                            except BrowserUrlBlocked as exc:
                                logger.warning(
                                    "网页预览探测重定向被 SSRF 防线拦截：%s", exc
                                )
                                return EmbedProbeResult(
                                    embeddable=None,
                                    reason=f"重定向目标被拒绝：{exc}",
                                    final_url=str(response.url),
                                )
                            current = next_url
                            continue

                    verdict = evaluate_embed_policy(
                        response.headers.multi_items(), platform_origins=origins
                    )
                    return EmbedProbeResult(
                        embeddable=verdict.embeddable,
                        reason=verdict.reason,
                        signals=dict(verdict.signals),
                        final_url=str(response.url),
                    )
            except httpx.HTTPError as exc:
                logger.info("网页预览内嵌探测失败：%s：%s", current, exc)
                return EmbedProbeResult(
                    embeddable=None,
                    reason=f"无法访问目标站点完成实测（{type(exc).__name__}），已降级为经验预判",
                    final_url=current,
                )

    return EmbedProbeResult(
        embeddable=None,
        reason="目标站点重定向次数过多，未能取得最终响应头，已降级为经验预判",
        final_url=current,
    )
