"""凭据判定策略：把「这条凭据能不能在此时此地被解出来」做成纯函数。

单独抽出来的原因是可测性与确定性——判定里没有任何 IO，只有一份从库里取出的
快照（``SecretRecord``）和本次调用的上下文。查库、解密、记流水都在
``resolver.py``，两者分开后，边界条件（过期、域名、场景、停用）能穷举测试。

判定顺序刻意固定，因为拒绝原因会写进审计，顺序变了统计口径就变了：

1. 绑定（bindings 查不到 → ``denied_by_binding``，在上层产生）
2. 状态（停用 → ``disabled``）
3. 有效期（过期且旧值也过期 → ``expired``）
4. 场景（不在 allowed_scenarios → ``denied_by_scenario``；脚本另有平台/用户开关）
5. 域名（不在 allowed_hosts → ``denied_by_host``）
6. 授权（系统级是否 grant 给该用户 → ``denied_by_grant``，在上层产生）
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

# ── 场景常量 ────────────────────────────────────────────────────────────
SCENE_MCP = "mcp"
SCENE_API_TOOL = "api_tool"
SCENE_SCRIPT_ENV = "script_env"
# 历史遗留：曾经用来给「无人值守调用」单独加一道闸门。现在定时任务和其它调用
# 走同一套判定，这个场景不再参与任何判断。常量留着只为兼容已存数据里的取值，
# 不要在新代码里用它做条件。
SCENE_SCHEDULED_TASK = "scheduled_task"
SCENE_AGENT = "agent"

ALL_SCENES: frozenset[str] = frozenset(
    {SCENE_MCP, SCENE_API_TOOL, SCENE_SCRIPT_ENV, SCENE_SCHEDULED_TASK, SCENE_AGENT}
)

# ── 消费者常量 ──────────────────────────────────────────────────────────
CONSUMER_MCP = "mcp_server"
CONSUMER_API_TOOL = "api_tool"
CONSUMER_SCRIPT = "script_tool"
CONSUMER_AGENT = "agent"

# ── 拒绝原因（会落进 sys_secret_usage_logs.result）───────────────────────
DENY_BINDING = "denied_by_binding"
DENY_GRANT = "denied_by_grant"
DENY_HOST = "denied_by_host"
DENY_SCENARIO = "denied_by_scenario"
DENY_SCRIPT_POLICY = "denied_by_script_policy"
DENY_EXPIRED = "expired"
DENY_DISABLED = "disabled"

# ── 类型常量 ────────────────────────────────────────────────────────────
TYPE_CREDENTIAL = "credential"
TYPE_VARIABLE = "variable"

SCOPE_USER = "user"
SCOPE_SYSTEM = "system"


class SecretLeakAttempt(RuntimeError):
    """有人试图把明文当普通字符串用（f-string、print、json.dumps）。"""


class SensitiveValue:
    """凭据明文的包装。

    存在的意义只有一个：让「顺手把值打进日志」这件事**失败**，而不是悄悄成功。

    .. code-block:: python

        token = SensitiveValue("s3cr3t")
        logger.info(f"token={token}")     # 抛 SecretLeakAttempt
        json.dumps({"t": token})          # 抛 SecretLeakAttempt
        print(token)                      # 抛 SecretLeakAttempt
        http.post(url, headers={"A": token.reveal()})   # 正确用法

    刻意**不**实现 ``__eq__``/``__hash__``：一旦可以比较，就会有人在断言里写
    ``assert token == "s3cr3t"``，等于把明文又漏回测试日志里。
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SensitiveValue 只接受字符串")
        self._value = value

    def reveal(self) -> str:
        """取出明文。只应出现在「真正发请求」的那一行。"""
        return self._value

    def __repr__(self) -> str:
        return "SensitiveValue('***')"

    def __str__(self) -> str:
        raise SecretLeakAttempt(
            "禁止把 SensitiveValue 当字符串使用；请显式调用 .reveal() 并确认目标不会落库"
        )

    def __format__(self, format_spec: str) -> str:
        raise SecretLeakAttempt(
            "禁止把 SensitiveValue 放进 f-string / format；请显式调用 .reveal()"
        )

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        """长度可用于「是否为空」判断，但不泄露内容。"""
        return len(self._value)


@dataclass(frozen=True)
class SecretRecord:
    """判定所需的凭据快照。**不含明文**——解密发生在判定通过之后。"""

    id: str
    owner_scope: str
    owner_id: str
    secret_key: str
    secret_type: str = TYPE_CREDENTIAL
    status: str = "active"
    expires_at: datetime | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_scenarios: frozenset[str] = field(default_factory=frozenset)
    previous_expires_at: datetime | None = None

    @property
    def is_system(self) -> bool:
        return self.owner_scope == SCOPE_SYSTEM

    @property
    def readable(self) -> bool:
        """变量类可读可复制；凭证类永远不可读（含创建者与管理员）。"""
        return self.secret_type == TYPE_VARIABLE

    def is_live(self, now: datetime) -> bool:
        """凭据本身是否仍在有效期内（含轮换双写窗口）。"""
        if self.expires_at is None:
            return True
        if self.expires_at > now:
            return True
        # 轮换过渡期：新值已生效但旧值仍在双写窗口内，整体视为可用。
        return self.previous_expires_at is not None and self.previous_expires_at > now


@dataclass(frozen=True)
class SecretContext:
    """本次调用的上下文。"""

    user_id: str
    scene: str
    target_host: str | None = None
    dept_code: str | None = None
    allow_script_env: bool = False
    allow_external_host: bool = True


def normalize_host(host: str | None) -> str:
    """归一化域名：小写、去端口、去协议前缀、去首尾点。"""
    if not host:
        return ""
    candidate = host.strip().lower()
    for prefix in ("https://", "http://"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    candidate = candidate.split("/", 1)[0]
    # 只去端口，不要动 IPv6 里的冒号
    if candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    return candidate.strip(".")


def host_allowed(target_host: str | None, allowed_hosts: Iterable[str]) -> bool:
    """目标域名是否落在白名单内。

    * 白名单为空 → 只允许平台内部目标（无域名 / localhost / 内网后缀）；
    * ``*`` → 任意域名（但仍要求**有**目标域名，见下）；
    * 支持 ``*.example.com`` 通配子域（**不**匹配 ``example.com`` 本身）；
    * 精确匹配，大小写不敏感。
    """
    patterns = [normalize_host(p) for p in (allowed_hosts or ()) if p]
    patterns = [p for p in patterns if p]

    target = normalize_host(target_host)
    if not patterns:
        return not target or _is_internal_host(target)
    if not target:
        # 平台内部调用：只要声明了外部白名单，就不该在无目标域名的场景下注入——
        # 否则「先注入、再拼 URL」的绕过路径就成立了。
        return False

    for pattern in patterns:
        # `*` = 不限域名（用户最自然的写法）。没有它的话，白名单里填了东西
        # 反而把所有外部域名都拒掉，且拒绝理由只有一句「域名不在白名单里」。
        if pattern == "*":
            return True
        if pattern == target:
            return True
        if pattern.startswith("*.") and fnmatch.fnmatchcase(target, pattern):
            return True
    return False


def evaluate_secret(secret: SecretRecord, context: SecretContext, *, now: datetime) -> str | None:
    """判定一条凭据能否在本次调用中使用。

    Returns:
        ``None`` 表示通过；否则返回拒绝原因常量。
    """
    if secret.status != "active":
        return DENY_DISABLED

    if not secret.is_live(now):
        return DENY_EXPIRED

    if context.scene not in secret.allowed_scenarios:
        return DENY_SCENARIO

    # 脚本环境变量是所有场景里唯一「值会离开平台」的：脚本能读 os.environ，
    # 就一定能 print 出来，再由 stdout 落进工具结果。所以它单独加一道开关，
    # 而且是「整类拒绝」，不逐条商量。
    if context.scene == SCENE_SCRIPT_ENV:
        return None if context.allow_script_env else DENY_SCRIPT_POLICY

    # 以下是带目标的注入（MCP / API 工具 / 定时任务 / 智能体）
    if not context.allow_external_host and _is_external_host(context.target_host):
        return DENY_HOST

    if not host_allowed(context.target_host, secret.allowed_hosts):
        return DENY_HOST

    return None


def _is_external_host(host: str | None) -> bool:
    return bool(normalize_host(host)) and not _is_internal_host(host)


def _is_internal_host(host: str | None) -> bool:
    """平台内部域名判定（保守）：只认无域名、localhost 与常见内网后缀。"""
    normalized = normalize_host(host)
    if not normalized:
        return True
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    return normalized.endswith((".local", ".internal", ".svc", ".cluster.local"))


def redact_for_display(secret_type: str, value: str | None) -> str:
    """返回可直接下发给前端的展示值。

    凭证类**永远**是掩码：没有「看一眼」的入口，也没有「复制」的入口。
    """
    if not value:
        return ""
    if secret_type == TYPE_VARIABLE:
        return value
    return "•" * 12


def describe_secret(record: SecretRecord) -> dict[str, Any]:
    """给前端 / 审计用的元数据视图，绝不含明文。"""
    return {
        "id": record.id,
        "owner_scope": record.owner_scope,
        "secret_key": record.secret_key,
        "secret_type": record.secret_type,
        "status": record.status,
        "readable": record.readable,
        "allowed_scenarios": sorted(record.allowed_scenarios),
        "allowed_hosts": list(record.allowed_hosts),
    }
