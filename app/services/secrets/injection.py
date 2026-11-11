"""注入接线：把「解析出来的凭据」放进真正发出去的那条请求里。

``resolver.py`` 只负责判定和取值，这一层负责**落在哪个字段上**。目前接了
两个场景：

* ``api_tool`` —— 通用 API 工具的请求头（``generic_api._perform_request``）
* ``mcp``      —— MCP 出站请求头（``mcp_client`` 建会话时）

两个场景都遵守同一条规则：**只填「本来就没有值」的位置**。手填了真实值的
header 一律不动——用户既然手写了，说明他要的就是那个值；想换成凭据中心的值，
先把那格清空再绑定。

脚本 env 与定时任务还没接，原因写在 ``docs/superpowers/specs/`` 的分期里：
脚本能 ``print(os.environ)``，值一旦进子进程就没有任何技术手段能拦住它回显，
必须在「凭据中心已经稳定跑一段时间」之后再开，而且默认关。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping
from urllib.parse import urlparse

from app.services.secrets.policy import (
    CONSUMER_API_TOOL,
    CONSUMER_MCP,
    DENY_BINDING,
    SCENE_API_TOOL,
    SCENE_MCP,
    resolve_api_tool_slot,
)
from app.services.secrets.redact import register_injected
from app.services.secrets.resolver import secret_resolver
from app.services.ai.tools.tool_credentials import CREDENTIAL_PLACEHOLDER_PREFIX

logger = logging.getLogger(__name__)

#: 这些值等于「这里还没填」，值得去解析绑定的凭据来补。
_EMPTY_MARKERS = frozenset({"", "****", "******", "••••••••••••"})

#: 拒绝原因 → 给人看的一句话。
#:
#: 光在日志里记一笔是不够的：调用方拿到的是对方的 401，看不出是自己那条凭据
#: **过期了**还是**被停用了**。所以注入层要把原因带回去，拼进最终错误信息。
DENIAL_REASONS = {
    "disabled": "凭据已停用",
    "expired": "凭据已过期",
    "denied_by_scenario": "这条凭据没有授权给当前场景",
    "denied_by_host": "目标域名不在白名单里",
    "denied_by_grant": "凭据还没有授权给你",
    "denied_by_script_policy": "平台或你的偏好关闭了脚本环境变量注入",
    "denied_by_binding": "没有找到绑定",
    # 规则没匹配上：这不是「没绑」，也不是「凭据坏了」，而是「按这次的值找不到
    # 对应的那条」——文案要说清是哪一步，否则用户只会去翻凭据状态。
    "rule_miss": "匹配规则没有找到对应凭据",
}


#: 每种拒绝对应要做什么。只写「为什么」没用——用户按「检查状态」去查，
#: 而真正要改的是域名白名单或场景勾选，两者根本不在同一个页面。
DENIAL_HINTS = {
    "denied_by_host": "请编辑这条凭据的「域名白名单」放行目标域名（填 * 表示不限）",
    "denied_by_scenario": "请在这条凭据上勾选对应的使用场景",
    "denied_by_grant": "请管理员把这条凭据授权给你（部门或全体）",
    "disabled": "请到凭据中心把它重新启用",
    "expired": "请轮换或重新创建这条凭据",
    "denied_by_script_policy": "请在注入策略里打开脚本环境变量注入",
    "rule_miss": (
        "请检查这条参数的匹配规则：这次入参的值要和某条凭据的 key 一致；"
        "也可以在规则里把「挑不到时」改成回退固定凭据"
    ),
}


def denial_text(reason: str | None) -> str:
    if not reason:
        return "未绑定凭据"
    return DENIAL_REASONS.get(reason, reason)


def denial_hint(reason: str | None) -> str:
    """这条拒绝该怎么修。认不出的原因返回空串，调用方自行兜底。"""
    if not reason:
        return "请先到凭据中心绑定一条凭据"
    return DENIAL_HINTS.get(reason, "")


def _record(denials: dict[str, str] | None, slot: str, reason: str | None) -> None:
    """把「这个槽位为什么没注入」记下来，由调用方决定怎么报。

    **不记「没有绑定」**：那是常态（绝大多数 header 本来就不绑凭据），记下来会
    让调用方误以为出了问题。调用方关心的是「绑了却用不了」——停用、过期、
    场景不符、域名不在白名单。
    """
    if denials is None or reason in (None, "denied_by_binding"):
        return
    # 存**原始 code**：调用方可能想按原因给不同的修复建议（域名白名单 vs 场景勾选），
    # 存成人话就再也分不出来了。展示时用 denial_text()。
    denials[slot] = reason


def _looks_unfilled(value: Any) -> bool:
    """判断一个 header 值是不是「待填」。"""
    if value is None:
        return True
    text = str(value).strip()
    if text in _EMPTY_MARKERS:
        return True
    if text.startswith(CREDENTIAL_PLACEHOLDER_PREFIX):
        return True
    # 全是掩码字符（前端回显的脱敏值）
    return bool(text) and set(text) <= {"*", "•"}


def host_of(url: str | None) -> str | None:
    """从 URL 模板里取出目标域名，供域名白名单校验。"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return parsed.hostname or None


def _current_user(user_info: Mapping[str, Any] | None) -> tuple[str, str]:
    """取 (user_id, dept_code)。显式传入优先，否则按项目既有顺序读上下文。

    读法与 ``user_placeholders.current_user_variables`` 保持一致：先
    ``AgentContext.user_dimensions``（后台执行、定时任务的载体），再
    ``current_user_info``（HTTP 请求的载体）。

    只读一处是不够的：两条链路各自只设其中一个。漏掉的那条不会报错，只会
    拿不到 user_id，于是绑定全部解析失败、工具退回「凭据没填」——症状看起来
    像用户没配凭据，实际是上下文没接上。
    """
    if user_info:
        return (
            str(user_info.get("user_id") or ""),
            str(user_info.get("dept_code") or ""),
        )

    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        dimensions = getattr(context, "user_dimensions", None) if context else None
        if dimensions:
            # dimensions 是展平的：key 直接是 user_id / dept_code，不是嵌套结构
            uid = dimensions.get("user_id")
            if uid in (None, ""):
                uid = dimensions.get("id")
            if uid not in (None, ""):
                return str(uid), str(dimensions.get("dept_code") or "")
    except Exception:  # noqa: BLE001
        logger.debug("[SecretInject] 读取 AgentContext 用户维度失败", exc_info=True)

    try:
        from app.utils.context import current_user_info

        info = current_user_info.get() or {}
        return (
            str(info.get("user_id") or ""),
            str(info.get("dept_code") or ""),
        )
    except Exception:  # noqa: BLE001 - 没有上下文就是没有，不用报错
        return "", ""


def _current_user_attrs(user_info: Mapping[str, Any] | None) -> dict[str, str]:
    """身份字段：身份类键来源（``user.email`` 等）从这里取值。

    只挑规则可能用到的几个，不把整个 ``user_info`` 塞进解析器——那会把 token、
    权限等无关字段一起带进日志上下文。
    """
    info = user_info or {}
    attrs: dict[str, str] = {}
    for key in ("email", "user_name", "username", "real_name", "dept_code", "user_id"):
        value = info.get(key)
        if isinstance(value, str) and value:
            attrs[key] = value
    # user_name 与 username 是历史双写，统一成规则里的名字
    if "user_name" not in attrs and "username" in attrs:
        attrs["user_name"] = attrs["username"]
    return attrs


def _current_trace_id() -> str | None:
    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        return getattr(context, "trace_id", None) if context else None
    except Exception:  # noqa: BLE001
        return None


async def _max_inject_per_call() -> int:
    """单次调用最多注入几条凭据（平台策略 ``secret_policy_max_inject_per_call``）。

    这个值以前只写在系统参数页上、没有任何代码读它——界面上写着「防止一次请求
    把全部凭据一起带走」，实际一条不限。配置项不生效比没有更危险：它给出的是
    虚假的安全感。读失败退回默认 3，绝不放行。
    """
    from app.services.secrets.resolver import DEFAULT_MAX_INJECT, SecretResolver

    try:
        _, _, limit = await SecretResolver._platform_flags()
        return limit
    except Exception:  # noqa: BLE001
        logger.warning("[SecretInject] 读取注入条数上限失败，按默认值处理", exc_info=True)
        return DEFAULT_MAX_INJECT


async def apply_api_tool_bindings(
    config: Any,
    headers: dict[str, str],
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, str]:
    """把绑定的凭据填进 API 工具的请求头。

    只处理「值是空的 / 是 ``${CREDENTIAL:...}`` 占位符」且有绑定记录的 header。
    任何异常都只告警、返回原 headers——凭据中心挂了不能把工具调用一起拖下水，
    它会退回「凭据未填」的既有报错路径，用户能看到明确原因。
    """
    if not headers:
        return headers

    user_id, dept_code = _current_user(user_info)
    user_attrs = _current_user_attrs(user_info)
    if not user_id:
        return headers

    target = host_of(getattr(config, "url_template", None))
    trace_id = _current_trace_id()
    resolved = dict(headers)
    limit = await _max_inject_per_call()
    injected = 0

    for name in list(resolved):
        if injected >= limit:
            logger.warning(
                "[SecretInject] API 工具 %s 命中注入上限 %s 条，其余 header 不再注入",
                getattr(config, "name", "?"),
                limit,
            )
            break
        if not _looks_unfilled(resolved.get(name)):
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_API_TOOL,
                consumer_type=CONSUMER_API_TOOL,
                consumer_id=str(getattr(config, "id", "") or ""),
                slot=f"header:{name}",
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
                user_attrs=user_attrs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretInject] API 工具 %s 的 %s 解析失败: %s", getattr(config, "name", "?"), name, exc)
            continue

        if result.ok and result.value is not None:
            plain = result.value.reveal()
            register_injected(plain)
            resolved[name] = plain
            injected += 1
        else:
            # 「没有绑定」是常态，不必刷日志；其它拒绝原因值得记一笔，
            # 否则用户只会看到「凭据没填」，完全不知道是域名/场景/授权拦下的。
            if result.denial not in (None, "denied_by_binding"):
                logger.info(
                    "[SecretInject] API 工具 %s 的 %s 被拒绝: %s",
                    getattr(config, "name", "?"),
                    name,
                    result.denial,
                )
            # 原因要带回调用层：停用/过期和「没绑」看起来都是「值是空的」，
            # 但用户要做的动作完全不同。
            _record(denials, f"header:{name}", result.denial)

    return resolved


async def apply_api_tool_param_bindings(
    config: Any,
    params: dict[str, Any],
    *,
    method: str,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把「声明为来自凭据」的参数补上值。

    header 是配置里的固定行，绑定天然；参数不行——它的值由模型运行时填，
    平台分不清「模型没填」和「模型编了一个值」。所以只有那些在
    ``x-nanzi-system-params`` 里标了 ``source: credential`` 的参数才走这里，
    它们同时已从模型可见的 properties 里消失，模型既看不到也改不了。

    槽位按 HTTP 语义落成 ``query:名字``（GET）或 ``body:名字``（其它方法），
    和绑定表里存的一致。
    """
    from app.services.ai.tools.system_params import credential_param_names
    from app.services.secrets.locator import choice_field

    names = credential_param_names(getattr(config, "parameter_schema", None))
    if not names:
        return params

    user_id, dept_code = _current_user(user_info)
    user_attrs = _current_user_attrs(user_info)
    if not user_id:
        return params

    url_template = getattr(config, "url_template", None)
    target = host_of(url_template)
    trace_id = _current_trace_id()
    resolved = dict(params)
    limit = await _max_inject_per_call()
    injected = 0

    # 模型可能通过编号字段指定用哪条（「挑不到时=让模型挑」）。编号由
    # ``list_my_credentials`` 给出，两边用同一套排序（secret_key → id）。
    # 只有规则未命中且 ``on_miss=model`` 时解析层才会采纳，规则命中时会被忽略。
    #
    # 这段与 MCP 工具参数那条链路（``apply_mcp_arg_bindings``）逐位一致。少了它，
    # API 工具参数配 ``on_miss=model`` 就是「模型想挑也没有字段可填」，规则一未命中
    # 必然失败——而界面上看起来这个选项是生效的。
    choices = {
        name: str(params.get(choice_field(name)) or "").strip()
        for name in names
        if str(params.get(choice_field(name)) or "").strip()
    }
    preferred = (
        await _resolve_choices(choices, user_id=user_id, dept_code=dept_code)
        if choices
        else {}
    )

    for name in names:
        slot = resolve_api_tool_slot(name=name, method=method, url_template=url_template)
        if injected >= limit:
            logger.warning(
                "[SecretInject] API 工具 %s 命中注入上限 %s 条，参数 %s 未注入",
                getattr(config, "name", "?"), limit, name,
            )
            break
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_API_TOOL,
                consumer_type=CONSUMER_API_TOOL,
                consumer_id=str(getattr(config, "id", "") or ""),
                # 槽位由这里推，不写死前缀：POST /refund/{applicant} 里的
                # applicant 是 path 参数，不是 body 字段。
                slot=slot,
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
                # 参数级注入：模型填的其余参数就是规则的匹配来源。
                # 去掉凭据参数自身——用一条凭据决定另一条会形成依赖环。
                rule_args={k: v for k, v in params.items() if k != name},
                user_attrs=user_attrs,
                preferred_secret_id=preferred.get(name),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SecretInject] API 工具 %s 的参数 %s 解析失败: %s",
                getattr(config, "name", "?"), name, exc,
            )
            continue

        if result.ok and result.value is not None:
            plain = result.value.reveal()
            register_injected(plain)
            resolved[name] = plain
            injected += 1
            if preferred.get(name) and result.secret_id == preferred.get(name):
                # 采纳了模型的选择。留一条日志才会有「这次到底谁选的凭据」的
                # 答案——否则规则未命中、模型补位成功时，从外部完全看不出区别。
                logger.info(
                    "[SecretInject] API 工具 %s 的参数 %s 采纳模型选择的凭据",
                    getattr(config, "name", "?"),
                    name,
                )
        else:
            if result.denial not in (None, "denied_by_binding"):
                logger.info(
                    "[SecretInject] API 工具 %s 的参数 %s 被拒绝: %s",
                    getattr(config, "name", "?"), name, result.denial,
                )
            _record(denials, slot, result.denial)

    return resolved


def _set_case_insensitive(headers: dict[str, str], name: str, value: str) -> None:
    """按大小写不敏感写入：命中已有 key 就覆盖它，否则新增。

    槽位名来自绑定表、Header 名来自服务配置，两者大小写不保证一致
    （``header:x-tenant`` vs ``X-Tenant``）。不一致时若直接 ``headers[name] = value``，
    对端会收到两个名字只差大小写的 Header，值是哪个全看它怎么解析。
    """
    for existing in list(headers):
        if existing.casefold() == name.casefold():
            headers[existing] = value
            return
    headers[name] = value


async def apply_mcp_bindings(
    server: Any,
    headers: dict[str, str],
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
    used: dict[str, Any] | None = None,
) -> dict[str, str]:
    """把绑定的凭据填进 MCP 出站请求头。

    **绑定优先，且无条件覆盖。** MCP 的 ``fixed_token`` 和「其他 Header」的值都
    只是「没人绑时的默认值」：用户一旦显式绑定，就必须盖过它们，否则绑定永远
    不会生效。

    为什么不能沿用「只补空位」或「只认像凭据头的名字」的过滤：绑定是用户明确
    表达的意图，而配置里的旧值往往正是他要替换的东西。旧实现遇到「值非空 +
    名字不含 token/key/auth」的 Header 会直接跳过——连库都不查，使用记录里一条
    都没有，用户只看到「绑了但没生效」。更糟的是前端绑定后值输入框被隐藏，
    他没法自己把旧值清空，等于死锁。

    遍历的是**绑定槽位**而不是 headers：绑定表才是「谁要用凭据」的唯一事实
    来源，配置里有没有这个 Header 与此无关（没有就补上）。
    """
    if not headers:
        return headers

    user_id, dept_code = _current_user(user_info)
    user_attrs = _current_user_attrs(user_info)
    if not user_id:
        return headers

    server_id = str(getattr(server, "id", "") or "")
    if not server_id:
        return headers

    slots = await list_bound_slots(
        CONSUMER_MCP, server_id, prefix="header:", user_id=user_id
    )
    if not slots:
        return headers

    # 回传给调用方：这个服务对这个用户**有**绑定，出站连接就不能跨用户复用。
    # SSE 的 Header 在建连时冻结，共用会话等于把先建连那个人的凭据发给所有人。
    if used is not None:
        used["user_id"] = user_id
        used["slots"] = list(slots)

    target = host_of(getattr(server, "sse_url", None))
    trace_id = _current_trace_id()
    resolved = dict(headers)
    limit = await _max_inject_per_call()
    injected = 0

    for slot in slots:
        if injected >= limit:
            logger.warning(
                "[SecretInject] MCP 服务 %s 命中注入上限 %s 条，其余槽位不再注入",
                getattr(server, "server_name", None) or server_id,
                limit,
            )
            break
        name = slot.partition(":")[2]
        if not name:
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_MCP,
                consumer_type=CONSUMER_MCP,
                consumer_id=server_id,
                slot=slot,
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
                user_attrs=user_attrs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SecretInject] MCP %s 的 %s 解析失败: %s", server_id, slot, exc)
            continue

        if result.ok and result.value is not None:
            plain = result.value.reveal()
            register_injected(plain)
            _set_case_insensitive(resolved, name, plain)
            injected += 1
        else:
            _record(denials, slot, result.denial)

    return resolved


def mcp_arg_slots(tool_record: Any) -> list[tuple[str, str]]:
    """工具声明过的凭据参数 → ``[(参数名, 槽位), ...]``。

    槽位是 ``arg:<短工具名>:<参数名>``。带工具名是因为同一服务下多个工具可能都有
    ``api_key``，光靠参数名会互相覆盖；用 ``:`` 后的短名即可，服务已经由
    ``consumer_id`` 限定。
    """
    import json as _json

    raw = getattr(tool_record, "credential_params", None)
    if not raw:
        return []
    try:
        names = _json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        # 字段写坏了按「没有声明」处理：宁可什么都不注入，也不能拿半截数据去猜
        logger.warning(
            "[SecretInject] 工具 %s 的 credential_params 不是合法 JSON，跳过",
            getattr(tool_record, "tool_name", "?"),
        )
        return []
    if not isinstance(names, list):
        return []

    full_name = str(getattr(tool_record, "tool_name", "") or "")
    short = full_name.split(":", 1)[1] if ":" in full_name else full_name
    if not short:
        return []
    return [
        (name, f"arg:{short}:{name}")
        for name in names
        if isinstance(name, str) and name.strip()
    ]


async def _mcp_server_host(server_id: str) -> str | None:
    """按 server_id 回查 MCP 服务的出站目标域名。

    Header 那条链路的调用方手里就有 ``server`` 对象，直接 ``host_of(sse_url)``
    就够了。参数级注入只拿得到 ``tool_record``，只能回查一次。

    **这一步不能省**：``host_allowed`` 在目标域名为空时**先**返回 ``False``，
    连白名单里的 ``*`` 都走不到。少传 host 的表现就是「明明设了 ``*``，还是
    denied_by_host」，而凭据页面上看不出任何问题。
    """
    from app.core.orm import AsyncSessionLocal
    from app.models.mcp import McpServer

    try:
        async with AsyncSessionLocal() as session:
            server = await session.get(McpServer, server_id)
    except Exception:  # noqa: BLE001 - 查不到就让白名单按「无目标域名」判定
        logger.warning(
            "[SecretInject] 读取 MCP 服务 %s 的地址失败，域名白名单按无目标域名处理",
            server_id,
            exc_info=True,
        )
        return None
    return host_of(getattr(server, "sse_url", None))


async def _resolve_choices(
    choices: Mapping[str, str],
    *,
    user_id: str,
    dept_code: str | None,
) -> dict[str, str]:
    """把模型填的 ``c3`` 换成 secret_id。

    重建候选用的是**和 ``list_my_credentials`` 完全相同的排序**（secret_key → id），
    所以模型看到第 3 条、回传 ``c3``，这里取到的就是同一条。

    任何一步不成立都返回空 dict（= 当成模型没选）：编号越界、查库失败、凭据已失效。
    **绝不猜**——猜错会把值注入到错误的凭据上。
    """
    from app.core.orm import AsyncSessionLocal
    from app.services.secrets.center import list_visible_secrets
    from app.services.secrets.locator import catalog_sort_key, parse_choice

    indexes = {name: parse_choice(text) for name, text in choices.items()}
    if not any(indexes.values()):
        # 模型给了编号但一个都解析不出来（比如写成「第 3 条」而不是 c3）。
        # 以前这里静默返回，事后无法回答「模型到底有没有挑」。
        logger.info(
            "[SecretCatalog] 模型填的凭据编号无法解析，按未选择处理: %s",
            {name: str(text) for name, text in choices.items()},
        )
        return {}

    try:
        async with AsyncSessionLocal() as session:
            rows = await list_visible_secrets(
                session, user_id=user_id, dept_code=dept_code, only_active=True
            )
    except Exception as exc:  # noqa: BLE001 - 拿不到候选就退回「模型没选」
        logger.warning("[SecretInject] 解析凭据编号时读取可见凭据失败: %s", exc)
        return {}

    ordered = sorted(rows, key=catalog_sort_key)
    resolved: dict[str, str] = {}
    for name, index in indexes.items():
        if index and index <= len(ordered):
            secret_id = str(getattr(ordered[index - 1], "id", "") or "")
            if secret_id:
                resolved[name] = secret_id
        elif index:
            # 越界：编号指向你根本看不到的那一条。必须留痕——用户看到的是
            # 「规则未命中」，而真正的原因可能是模型记错了清单长度。
            logger.info(
                "[SecretCatalog] 模型编号越界，按未选择处理: 参数=%s 编号=c%s 可见=%s 条",
                name,
                index,
                len(ordered),
            )
    return resolved


async def apply_mcp_arg_bindings(
    tool_record: Any,
    arguments: dict[str, Any],
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把凭据填进 MCP 工具调用的 ``arguments``。

    只处理工具上**声明过**的 ``credential_params``——声明过的参数已经不在模型
    可见的 schema 里，模型不会给值，这里填的就是唯一来源。没声明的参数原样透传。

    与 Header 那条链路的区别：Header 是「没人绑时的默认值」，所以绑定优先覆盖；
    这里没有「默认值」可言（模型看不到它），取不到就是取不到，由调用方报错。
    """
    pairs = mcp_arg_slots(tool_record)
    if not pairs:
        return arguments

    user_id, dept_code = _current_user(user_info)
    user_attrs = _current_user_attrs(user_info)
    if not user_id:
        return arguments

    server_id = str(getattr(tool_record, "server_id", "") or "")
    if not server_id:
        return arguments

    target = await _mcp_server_host(server_id)
    trace_id = _current_trace_id()
    resolved = dict(arguments)
    limit = await _max_inject_per_call()

    # 模型可能通过编号字段指定用哪条；只有规则未命中且开了 on_miss=model 时
    # 解析层才会采纳它，规则命中时会被忽略（并记「判断不一致」日志）。
    from app.services.secrets.locator import choice_field

    choices = {
        name: str(arguments.get(choice_field(name)) or "").strip()
        for name, _slot in pairs
        if str(arguments.get(choice_field(name)) or "").strip()
    }
    preferred = (
        await _resolve_choices(choices, user_id=user_id, dept_code=dept_code)
        if choices
        else {}
    )

    for index, (name, slot) in enumerate(pairs):
        if index >= limit:
            logger.warning(
                "[SecretInject] 工具 %s 声明了 %s 个凭据参数，超过单次注入上限 %s，其余不再注入",
                getattr(tool_record, "tool_name", "?"), len(pairs), limit,
            )
            break
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_MCP,
                consumer_type=CONSUMER_MCP,
                consumer_id=server_id,
                slot=slot,
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
                # 参数级注入：模型填的其余参数就是规则的匹配来源。
                # 去掉凭据参数自身——用一条凭据决定另一条会形成依赖环。
                rule_args={k: v for k, v in arguments.items() if k != name},
                user_attrs=user_attrs,
                preferred_secret_id=preferred.get(name),
            )
        except Exception as exc:  # noqa: BLE001 - 凭据中心挂了不该炸掉工具调用
            logger.warning(
                "[SecretInject] 工具 %s 的参数 %s 解析失败: %s",
                getattr(tool_record, "tool_name", "?"), name, exc,
            )
            if denials is not None:
                denials[slot] = None
            continue

        if result.ok and result.value is not None:
            # 无条件覆盖：模型理论上不会给这个参数值（schema 里已剔除），
            # 但如果它从上下文里猜出来传了，凭据才是唯一可信来源。
            plain = result.value.reveal()
            register_injected(plain)
            resolved[name] = plain
        else:
            # **不能复用 `_record`**：它特意跳过「没有绑定」，因为对 Header 而言
            # 那是常态。参数级正相反——声明过就必须有值，没绑同样是缺参数。
            if denials is not None:
                denials[slot] = result.denial

    return resolved


def shell_quote(value: str) -> str:
    """把一个值渲染成 POSIX shell 单引号字面量。

    **这不是洁癖，是防注入**。脚本环境变量文件是用 ``. file`` 加载的，而 ``.`` 会
    执行命令替换——凭据值里只要出现 ``$(...)`` 或反引号，就等于让提交凭据的人
    在别人的脚本里执行任意命令。单引号包起来之后 ``$`` 和反引号都不展开，只有
    单引号本身需要特殊处理（``'\''``：结束、转义一个、重开）。
    """
    return "'" + str(value).replace("'", "'\\''") + "'"


def build_env_file_content(env: Mapping[str, str]) -> str:
    """把环境变量渲染成可被 ``. file`` 安全加载的内容。"""
    return "\n".join(f"{name}={shell_quote(value)}" for name, value in env.items()) + "\n"


#: 查绑定失败后的熔断截止时刻。
#:
#: 表不存在（迁移没执行）时每次调用都会抛一次异常。没有熔断的话，脚本工具每跑
#: 一次就刷一条 WARNING、多一次必然失败的查询——日志被淹没之后，真正的故障反而
#: 看不见了。
_bindings_unavailable_until = 0.0
_BINDINGS_COOLDOWN_SECONDS = 60.0


async def list_bound_slots(
    consumer_type: str,
    consumer_id: str,
    *,
    prefix: str,
    user_id: str,
) -> list[str]:
    """列出某个消费者上「属于我」的绑定槽位。

    ``sys_secret_bindings`` 里既有管理员做的全局绑定（``bound_by_user_id IS NULL``）,
    也有别人做的个人绑定。后者必须排除——A 把凭据绑到共享脚本上，B 不该跟着用。
    """
    from sqlalchemy import select

    from app.core.orm import AsyncSessionLocal
    from app.models.secret import SecretBinding

    global _bindings_unavailable_until

    if not consumer_type or not consumer_id:
        return []
    if time.monotonic() < _bindings_unavailable_until:
        return []
    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(SecretBinding).where(
                        SecretBinding.consumer_type == consumer_type,
                        SecretBinding.consumer_id == consumer_id,
                    )
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 - 表不存在/库抖动一律按「没有绑定」
        _bindings_unavailable_until = time.monotonic() + _BINDINGS_COOLDOWN_SECONDS
        logger.warning(
            "[SecretInject] 读取绑定失败，%d 秒内不再重试（凭据注入暂停）: %s/%s",
            int(_BINDINGS_COOLDOWN_SECONDS),
            consumer_type,
            consumer_id,
            exc_info=True,
        )
        return []

    slots: list[str] = []
    for row in rows:
        # 空串与 NULL 都表示全局绑定
        if row.bound_by_user_id and row.bound_by_user_id != user_id:
            continue
        if not str(row.slot or "").startswith(prefix):
            continue
        slots.append(row.slot)
    return sorted(set(slots))


async def apply_script_env_bindings(
    tool_config: Any,
    *,
    user_info: Mapping[str, Any] | None = None,
    denials: dict[str, str] | None = None,
) -> dict[str, str]:
    """解析脚本工具要注入的环境变量，返回 ``{变量名: 值}``。

    **默认拿不到任何东西**：``SCENE_SCRIPT_ENV`` 要求平台策略
    (``secret_policy_allow_script_env``) 与用户偏好 (``sys_secret_user_prefs``)
    同时为真，两层默认都是关。闸门在 ``resolver.evaluate_secret`` 里，不在这里——
    这样「开关在哪、谁拦的」只有一个答案。

    值会写进会话工作区里的一个 0600 文件再由 shell ``.`` 加载，**不进命令行**：
    ``ps aux`` 和容器进程列表都看得到命令行，看得见就等于泄露。
    """
    from app.services.secrets.policy import CONSUMER_SCRIPT, SCENE_SCRIPT_ENV

    user_id, dept_code = _current_user(user_info)
    user_attrs = _current_user_attrs(user_info)
    if not user_id:
        return {}

    tool_id = str(getattr(tool_config, "id", "") or "")
    if not tool_id:
        return {}

    slots = await list_bound_slots(
        CONSUMER_SCRIPT, tool_id, prefix="env:", user_id=user_id
    )
    if not slots:
        return {}

    trace_id = _current_trace_id()
    resolved: dict[str, str] = {}
    limit = await _max_inject_per_call()
    if len(slots) > limit:
        logger.warning(
            "[SecretInject] 脚本 %s 绑了 %s 个环境变量，超过单次注入上限 %s，只注入前 %s 个",
            tool_id,
            len(slots),
            limit,
            limit,
        )
        slots = slots[:limit]

    for slot in slots:
        name = slot.partition(":")[2]
        if not name:
            continue
        try:
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_SCRIPT_ENV,
                consumer_type=CONSUMER_SCRIPT,
                consumer_id=tool_id,
                slot=slot,
                trace_id=trace_id,
                dept_code=dept_code,
                user_attrs=user_attrs,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[SecretInject] 脚本 %s 的 %s 解析失败", tool_id, slot, exc_info=True)
            continue
        if result.ok and result.value is not None:
            plain = result.value.reveal()
            register_injected(plain)
            resolved[name] = plain
        else:
            _record(denials, slot, result.denial)
    return resolved
