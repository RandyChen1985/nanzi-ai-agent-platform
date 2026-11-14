"""命令禁用（``forbidden_commands``）的唯一策略来源。

监管的是"用户被禁止执行包含某个关键字的命令"这件事，而"执行命令"的入口有三个：
AgentScope 工具链（``tools.py``）、代码执行服务（``code_execution_service.py``）、
沙箱交互终端（``api/v1/endpoints/sandbox.py``）。三处各拼一套查询与匹配逻辑必然会漂移——
实际上首版就有两个漏：工具名只认 ``{exec_command, bash}``（沙箱 MCP 的 shell 叫
``mcp_sandbox_bash_<hash>``，这条路径完全不校验），终端接口一次都没查过。

因此这里收敛出两件事：
- ``is_shell_like_tool_name``：判定"这个工具是不是在执行 shell 命令"，替代两个硬编码名字；
- ``find_forbidden_command_rule`` / ``enforce_command_policy_for_http``：查询与匹配的唯一实现，
  分别供"需要 PermissionDecision 的工具链"和"需要 HTTP 异常的接口"使用。

降级方向与工具/命令侧既有约定一致：**查询失败 fail-closed**（由调用方转成 DENY / 5xx），
无身份与管理员放行（无身份只记日志，见 ``tools.py`` 的调用点）。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# 精确名字：平台自有的、以及与"执行 shell"等价的工具名。
SHELL_TOOL_NAMES = frozenset(
    {
        "bash",
        "sh",
        "shell",
        "exec",
        "exec_command",
        "execute_command",
        "run_command",
        "command",
        "terminal",
        # 平台自己的系统命令工具：名字不含 bash/sh/exec，此前完全不在判定里，
        # 于是它携带的命令不过黑名单——而它恰恰是最直接的一条 shell 通道。
        "execute_system_command",
    }
)

# MCP 的命令策略读取平台内部工具名。保留旧别名格式供历史调用记录识别。
# 只按"含 shell 语义的词"放宽，而不是所有 mcp_* 都当 shell——否则 jira/postgres 这类
# 带 query 参数的 MCP 工具会被无谓地拉进命令匹配。
#
# 但 marker 必须覆盖 SHELL_TOOL_NAMES 的**全部语义**，否则同一个工具"直连时被拦、走 MCP
# 就放行"。此前只有 bash/shell/exec，于是叫 run_command / terminal / command 的自建 MCP
# 工具（terminal、ssh 类很常见）掉出 shell 判定，其携带的命令完全不过命令黑名单——
# 这正是命令禁用最想防的绕过路径。"command" 一并覆盖 run_command/exec_command/
# execute_command；"sh" 刻意不列（作为子串会误伤 push/share 之类）。
MCP_SHELL_MARKERS = ("bash", "shell", "exec", "command", "terminal")


class CommandPolicyUnavailable(RuntimeError):
    """权限策略查询失败。调用方必须 fail-closed，不能放行。"""


def is_shell_like_tool_name(tool_name: Any) -> bool:
    """该工具是否会执行一条 shell 命令（决定要不要过命令黑名单）。"""
    name = str(tool_name or "").strip().lower()
    if not name:
        return False
    if name in SHELL_TOOL_NAMES:
        return True
    if ":" in name:
        return name.rsplit(":", 1)[1] in SHELL_TOOL_NAMES or any(marker in name for marker in MCP_SHELL_MARKERS)
    if name.startswith("mcp_"):
        return any(marker in name for marker in MCP_SHELL_MARKERS)
    return False


# 真正**分隔命令**的字符。
_COMMAND_SEPARATORS = ";&|()"

# 交给 shlex 的标点：必须包含重定向符，否则 `cat</etc/passwd` 会连成一个 token，
# 取 basename 后 `cat` 整个消失（实测可绕过"禁用 cat"）。
#
# 但 `<` `>` **不是命令分隔符**：把重定向目标切成分段后它会变成新段的"命令名"，
# 于是 `echo hi > /tmp/rm` 会命中"禁用 rm"（误杀），而 `sudo -u root cat` 里
# `-u` 的取值会被当命令、真正的 `cat` 丢失（漏判，安全方向）。所以只让它参与分词，
# 段内由 `_is_redirect_token` 识别并连同目标一起跳过。
_LEX_PUNCTUATION = _COMMAND_SEPARATORS + "<>"

# 重定向操作符：只由 `<>`、`&`、数字组成且**至少含一个** `<>`
# （`&` 单独出现是命令分隔符，不算重定向）。覆盖 `>` `>>` `<` `<<` `<>` `>&` `2>&1` 等。
_REDIRECT_CHARS = frozenset("<>&0123456789")

# 包装器：真正的命令在它们后面。不展开的话 `sudo cat x` 的命令名会被当成 `sudo`。
_WRAPPER_COMMANDS = {
    "env",
    "sudo",
    "doas",
    "nohup",
    "time",
    "command",
    "exec",
    "nice",
    "setsid",
    "stdbuf",
    "ionice",
    "timeout",
}

# 包装器里**带取值**的选项：不显式跳过取值，`sudo -u root cat` 会把 `root` 当成命令名，
# 真正的 `cat` 整个丢失（实测漏判，安全方向）。取值粘连写法（`-uroot` / `--user=root`）
# 命中不了精确匹配，但那种写法取值已含在同一个 token 里，跳一个即可。
_WRAPPER_OPTIONS_WITH_VALUE: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "sudo": frozenset({
        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
        "-C", "--close-from", "-r", "--role", "-t", "--type", "-U", "--other-user",
        "-D", "--chdir", "-R", "--chroot", "-a", "--auth-type",
    }),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"}),
    "stdbuf": frozenset({"-i", "--input", "-o", "--output", "-e", "--error"}),
    "time": frozenset({"-f", "--format", "-o", "--output", "-a", "--append"}),
    "exec": frozenset({"-a"}),
}

# 包装器的**位置**参数个数（`timeout 5 cat x` 里的 duration 不是命令名）。
_WRAPPER_POSITIONAL_COUNT = {"timeout": 1}

# shell 解释器：`sh -c "cat x"` 里真正执行的是内层脚本。
_SHELL_INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}

# 赋值前缀：`X=1 cat x` 里 `X=1` 不是命令名。
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 递归展开的上限：防止 `sh -c "sh -c \"sh -c ...\""` 这类构造导致无限递归。
_MAX_EXPAND_DEPTH = 4


def _lex(command: str) -> list[str]:
    # 换行是命令分隔符，但 shlex 把它当普通空白：`cat a\nrm -rf b` 会被粘成一段，
    # 于是第二行的命令名永远看不到（实测可绕过）。规范成 `;` 后再分词。
    normalized = command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=_LEX_PUNCTUATION)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def _is_redirect_token(token: str) -> bool:
    """该 token 是不是重定向操作符（`>` `>>` `<` `2>&1` …）。

    `&` 单独出现是命令分隔符，因此要求至少含一个 `<>`。
    """
    if not token:
        return False
    if not all(char in _REDIRECT_CHARS for char in token):
        return False
    return any(char in "<>" for char in token)


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """按**命令分隔符**切成"子命令"段（`a; b | c` → `[[a], [b], [c]]`）。

    重定向符不切段：`cat < /etc/passwd` 是一段，重定向目标是本段的参数，
    只有当不了命令名（否则"禁用 passwd"会拦下正常的读文件）。
    """
    segments: list[list[str]] = []
    current: list[str] = []
    skip_redirect_target = False
    for token in tokens:
        if skip_redirect_target:
            current.append(token)
            skip_redirect_target = False
            continue
        if _is_redirect_token(token):
            current.append(token)
            skip_redirect_target = True
            continue
        if token and all(char in _COMMAND_SEPARATORS for char in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _is_assignment(token: str) -> bool:
    return bool(_ASSIGNMENT_RE.match(token))


def _normalize(token: str) -> str:
    return os.path.basename(token).lower() or token.lower()


def _script_after_dash_c(args: list[str]) -> Optional[str]:
    """取出 `-c` / `-lc` / `--command` 后面那个（内层脚本）参数。"""
    for index, arg in enumerate(args):
        if arg == "--command":
            return args[index + 1] if index + 1 < len(args) else None
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return args[index + 1] if index + 1 < len(args) else None
    return None


def _skip_wrapper_arguments(segment: list[str], index: int, wrapper: str) -> int:
    """跳过包装器的实参，返回真正命令所在的下标。

    - 赋值前缀（`env FOO=1 cmd`）跳过；
    - 带取值的选项（`-u root`）连取值一起跳；
    - 无值选项 / 取值粘连（`-p`、`-uroot`、`--user=root`）跳一个；
    - `--` 之后就是命令；
    - 剩下按该包装器的位置参数个数跳过（`timeout 5 cat`）。
    """
    positional = _WRAPPER_POSITIONAL_COUNT.get(wrapper, 0)
    with_value = _WRAPPER_OPTIONS_WITH_VALUE.get(wrapper, frozenset())
    while index < len(segment):
        token = segment[index]
        if _is_assignment(token):
            index += 1
            continue
        if token == "--":
            return index + 1
        if token.startswith("-") and token != "-":
            index += 2 if token in with_value else 1
            continue
        if positional > 0:
            positional -= 1
            index += 1
            continue
        break
    return index


def _segment_command_names(segment: list[str], depth: int) -> list[str]:
    """一段里被执行的命令名（可能多个：包装器 + 内层脚本）。"""
    index = 0
    while index < len(segment):
        token = segment[index]
        if _is_redirect_token(token):
            # 前导重定向（`> f cmd`）：操作符与它的目标都不是命令名
            index += 2
            continue
        if _is_assignment(token):
            index += 1
            continue
        break

    while index < len(segment):
        if _is_redirect_token(segment[index]):
            index += 2
            continue
        name = _normalize(segment[index])
        if name in _WRAPPER_COMMANDS:
            index = _skip_wrapper_arguments(segment, index + 1, name)
            continue
        if name in _SHELL_INTERPRETERS and depth < _MAX_EXPAND_DEPTH:
            script = _script_after_dash_c(segment[index + 1 :])
            if script:
                # 内层脚本按新的命令解析（`sh -c "cat x"` 真正执行的是 cat）
                return [name, *shell_command_names(script, depth=depth + 1)]
            return [name]
        return [name]
    return []


def shell_command_names(command: str, depth: int = 0) -> list[str]:
    """命令**名**序列（各子命令的可执行名，basename + 小写）。

    只取命令位置的 token，参数一律不参与：旧实现对每个 token 都取 basename，导致
    `rm -rf /tmp/test` 会命中"禁用 test"（参数路径的 basename），把合法命令误杀。
    """
    if not isinstance(command, str) or not command.strip():
        return []
    names: list[str] = []
    for segment in _split_segments(_lex(command)):
        names.extend(_segment_command_names(segment, depth))
    return names


def shell_command_tokens(command: str, depth: int = 0) -> list[str]:
    """规范化的 token 序列（basename + 小写），用于**多词规则**（如 `rm -rf`）的窗口匹配。

    同时展开包装器与 `sh -c "..."`：否则 `sh -c "cat /etc/passwd"` 会变成
    ``['sh', '-c', 'passwd']``——`cat` 丢失，"禁用 cat"被绕过（实测复现）。
    """
    if not isinstance(command, str) or not command.strip():
        return []
    tokens: list[str] = []
    for segment in _split_segments(_lex(command)):
        stripped = [token for token in segment if not _is_assignment(token)]
        # 丢掉包装器本身**及其实参**，让内层命令落到序列前部，多词规则才匹配得上。
        # 只丢名字不丢实参的话，`sudo -u root rm -rf /` 会变成 ['-u','root','rm','-rf','/']，
        # 多词规则 `rm -rf` 依旧滑不到窗口。
        while stripped and _normalize(stripped[0]) in _WRAPPER_COMMANDS:
            wrapper = _normalize(stripped[0])
            cut = _skip_wrapper_arguments(stripped, 1, wrapper)
            stripped = [token for token in stripped[cut:] if not _is_assignment(token)]
        if not stripped:
            continue
        if (
            depth < _MAX_EXPAND_DEPTH
            and _normalize(stripped[0]) in _SHELL_INTERPRETERS
        ):
            script = _script_after_dash_c(stripped[1:])
            if script:
                tokens.extend(shell_command_tokens(script, depth=depth + 1))
                continue
        tokens.extend(_normalize(token) for token in stripped)
    return tokens


def matches_forbidden_command(command: str, rule: str) -> bool:
    """命令是否命中某条禁用规则。

    按规则形态分派，这是修掉"参数误杀/误报"的关键：

    - **单词规则**（`cat`、`rm`）：只与**命令名序列**比。参数不参与，因此
      `rm -rf /tmp/test` 不会命中"禁用 test"，`echo rm` 也不会命中"禁用 rm"
      （后者是 docstring 一直声称、旧实现却没做到的行为）。
    - **多词规则**（`rm -rf`）：在规范化 token 序列上滑窗，保留原有表达能力。
    """
    if not isinstance(command, str) or not isinstance(rule, str):
        return False
    command = command.strip()
    rule = rule.strip()
    if not command or not rule:
        return False

    rule_tokens = shell_command_tokens(rule)
    if not rule_tokens:
        return False

    if len(rule_tokens) == 1:
        return rule_tokens[0] in shell_command_names(command)

    command_tokens = shell_command_tokens(command)
    if not command_tokens:
        return False
    window = len(rule_tokens)
    return any(
        command_tokens[start : start + window] == rule_tokens
        for start in range(len(command_tokens) - window + 1)
    )


async def find_forbidden_command_rule(
    user_id: Any,
    commands: Iterable[Any],
) -> Optional[str]:
    """返回命中的禁用规则关键字；没命中返回 ``None``。

    - 无身份（``None``/空）直接放行：本函数不做日志，调用方负责留痕（工具链那边已经有）；
    - 管理员放行；
    - 查询失败抛 ``CommandPolicyUnavailable``，由调用方转成 DENY / 5xx（fail-closed）。
    """
    if user_id is None or str(user_id).strip() == "":
        return None

    candidates = [str(cmd) for cmd in commands if cmd is not None and str(cmd).strip()]
    if not candidates:
        return None

    try:
        from app.core.orm import AsyncSessionLocal
        from app.services.permission_service import PermissionService

        async with AsyncSessionLocal() as session:
            perms = await PermissionService(session).get_user_permissions(int(user_id))
            if "admin" in (perms.roles or set()):
                return None
            rules = [
                str(rule).lower().strip()
                for rule in (getattr(perms.permissions, "forbidden_commands", None) or [])
                if str(rule).strip()
            ]
    except Exception as exc:  # noqa: BLE001 —— 统一转成可识别的失败信号
        raise CommandPolicyUnavailable(str(exc)) from exc

    if not rules:
        return None
    for command in candidates:
        for rule in rules:
            if matches_forbidden_command(command, rule):
                logger.warning(
                    "[forbidden] Command blacklist hit user=%s rule=%s command=%s",
                    user_id,
                    rule,
                    command[:200],
                )
                return rule
    return None


def command_forbidden_message(rule: str) -> str:
    """与工具链完全同源的用户可见文案，避免三个入口各写一套说法。"""
    return f"安全策略拦截：您的账号已被禁止在该智能体中执行包含 '{rule}' 的命令。"


async def enforce_command_policy_for_http(
    *,
    user_id: Any,
    commands: Iterable[Any],
) -> None:
    """HTTP 入口（沙箱交互终端）的命令校验：命中 403，策略不可用 503。

    放在端点里、真正下发执行之前，与 ``code_execution_service`` 口径一致——
    同一个"执行命令"动作不该因为走的是终端面板就绕过禁用。
    """
    from fastapi import HTTPException

    try:
        rule = await find_forbidden_command_rule(user_id, commands)
    except CommandPolicyUnavailable:
        logger.exception("[forbidden] Command policy unavailable for user=%s", user_id)
        raise HTTPException(
            status_code=503,
            detail="安全策略暂时无法验证，命令未执行，请稍后重试。",
        )
    if rule:
        raise HTTPException(status_code=403, detail=command_forbidden_message(rule))
