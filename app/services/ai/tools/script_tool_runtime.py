"""脚本工具执行器：把一段 Python 脚本放进**当前会话沙箱**里跑一次。

为什么不是直接 ``sys.executable``：平台的沙箱是分策略的（local / docker / k8s /
e2b / ssh），Bash 工具会绑定到会话沙箱执行。脚本工具如果自己起子进程，在
docker/k8s 部署下就会跑在宿主机上，与「沙箱内执行」的承诺不符，也绕过了已有的
命令策略与取消机制。所以这里复用沙箱 bash 通道：

    docker/k8s          → await bash(command=..., cwd=<会话目录相对沙箱根>)
    local               → run_shell_command_capture(command, cwd=<会话工作区绝对路径>)

两条分支执行的是**同一条 shell 命令**，因此结果解析、超时、截断只有一套逻辑。

e2b / ssh 策略没有「宿主工作区 → 沙箱路径」映射，脚本无法定位工作区文件，
因此这两种部署下明确报错（页面也会提前禁用入口），不做「看起来能跑」的降级。

运行契约（与前端「?」指南一致）：
- 参数：stdin 收一个 JSON 对象
- 结果：stdout 文本；若能解析成 JSON 则结构化返回
- 失败：退出码非 0，stderr 作为错误详情
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from app.services.ai.tools.parameter_schema import (
    apply_user_placeholders,
    parse_parameter_schema,
)

logger = logging.getLogger(__name__)

MAX_RESULT_BYTES = 100 * 1024
# 沙箱 bash 只保留 stdout 的**尾部** 100KB（workspace_container_mcp.bash 的
# stdout[-100000:]）。信封的退出码标记在最前面，所以单段上限必须远小于 100KB，
# 否则输出一多就先把标记裁掉，解析出「退出码未知」——看起来像脚本失败。
#
# 80KB + 8KB + 标记仍小于 100KB；同时给内置 file_to_base64 留出空间：
# 它默认最多读 48KB 文件，base64 后约 64KB，40KB 捕获会把 JSON 砍成非法文本。
STDOUT_CAPTURE_BYTES = 80 * 1024
STDERR_CAPTURE_BYTES = 8 * 1024
DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 120
# workspace_container_mcp.bash 内部 subprocess.run(timeout=600)；脚本配置允许到
# 3600 秒，但沙箱分支不可能跑更久，提前按 600 秒收口，错误文案才不会自相矛盾。
SANDBOX_COMMAND_TIMEOUT_SECONDS = 600.0
# 外层 RuntimeToolSpec.invoke 用同一个平台超时做 deadline；内层预留一点，
# 让「脚本执行超时（上限 N 秒）」这条更具体的错误先返回（与 exec_command 同口径）。
INNER_TIMEOUT_MARGIN_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 60.0

SCRIPT_DIR_NAME = ".script_tools"
LIBS_DIR_NAME = ".pylibs"
STAMP_DIR_NAME = ".stamps"

EXIT_MARK = "__NANZI_EXIT__:"
STDOUT_MARK = "__NANZI_STDOUT_START__"
STDERR_MARK = "__NANZI_STDERR_START__"


class ScriptToolError(RuntimeError):
    """脚本工具无法执行（沙箱不可用、工作区缺失、脚本落盘失败等）。"""


@dataclass(frozen=True)
class ScriptToolConfig:
    """执行脚本所需的配置快照（DB 行或内置定义都可以映射成它）。"""

    name: str
    code: str
    parameter_schema: Any = None
    requirements: tuple[str, ...] = ()
    timeout_seconds: Optional[int] = None
    language: str = "python"
    display_name: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ShellRunResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


@dataclass
class ScriptRunResult:
    """一次脚本调用的完整结果，供工具返回与「试运行」共用。"""

    ok: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    artifacts: List[str] = field(default_factory=list)
    message: str = ""


def code_fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]


def _requirements_fingerprint(requirements: tuple[str, ...]) -> str:
    joined = "\n".join(requirements)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def _result_text(result: Any) -> str:
    """MCP 工具返回 ``content`` 列表，普通 callable 返回 str。"""
    content = getattr(result, "content", None)
    if isinstance(content, (list, tuple)):
        parts = [str(getattr(block, "text", "") or "") for block in content]
        return "\n".join(part for part in parts if part)
    return str(result or "")


def build_envelope_command(run_command: str, out_file: str, err_file: str) -> str:
    """把「跑脚本 + 回传退出码/stdout/stderr」包成一条可解析的 shell 命令。

    沙箱 bash 通道只返回一段文本，不同类型的沙箱实现又不保证带退出码，
    因此这里用固定标记自描述，平台侧只解析一段文本即可。

    标记只在 ``STDOUT_MARK`` 之前生效（见 :func:`parse_envelope`）：脚本即使往
    stdout 写 ``__NANZI_EXIT__:0`` 也改不了真实退出码。
    """
    return (
        "set +e\n"
        f"{run_command} > {out_file} 2> {err_file}\n"
        "code=$?\n"
        f'echo "{EXIT_MARK}$code"\n'
        f'echo "{STDOUT_MARK}"\n'
        f"head -c {STDOUT_CAPTURE_BYTES} {out_file}\n"
        # head 输出不一定带结尾换行；不补一个空行，标记会被拼进最后一行内容里
        "echo\n"
        f'echo "{STDERR_MARK}"\n'
        f"head -c {STDERR_CAPTURE_BYTES} {err_file}\n"
        "echo\n"
    )


def parse_envelope(text: str) -> ShellRunResult:
    """解析 :func:`build_envelope_command` 的输出。

    退出码只认 ``STDOUT_MARK`` 之前的那一行：脚本自己往 stdout 写标记不影响结果。
    """
    lines = str(text or "").splitlines()
    exit_code: Optional[int] = None
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    section = "head"

    for line in lines:
        if section == "head" and line.startswith(EXIT_MARK):
            raw = line[len(EXIT_MARK):].strip()
            try:
                exit_code = int(raw)
            except ValueError:
                exit_code = None
            continue
        if line.strip() == STDOUT_MARK and section == "head":
            section = "stdout"
            continue
        if line.strip() == STDERR_MARK and section == "stdout":
            section = "stderr"
            continue
        if section == "stdout":
            stdout_lines.append(line)
        elif section == "stderr":
            stderr_lines.append(line)

    return ShellRunResult(
        exit_code=exit_code,
        stdout="\n".join(stdout_lines).strip(),
        stderr="\n".join(stderr_lines).strip(),
    )


class ScriptToolRunner:
    """脚本执行器：可被同一绑定复用，但每次 :meth:`run` 都生成独立 run_id。"""

    def __init__(
        self,
        config: ScriptToolConfig,
        *,
        workdir: Path | str,
        sandbox_ws: Any | None = None,
        sandbox_root: str | None = None,
        user_info: Optional[Mapping[str, Any]] = None,
        conversation_id: str | None = None,
    ) -> None:
        self.config = config
        self.workdir = Path(workdir).expanduser().resolve()
        self.sandbox_ws = sandbox_ws
        self.sandbox_root = sandbox_root
        self.user_info = dict(user_info or {})
        self.conversation_id = conversation_id

    # ------------------------------------------------------------------ 路径

    @property
    def in_sandbox(self) -> bool:
        return self.sandbox_ws is not None

    def _sandbox_relative_cwd(self) -> str:
        """会话目录相对沙箱根的路径；容器内 ``/workspace`` 即宿主 workspace_user_root。"""
        if not self.sandbox_root:
            policy = getattr(self.sandbox_ws, "_platform_sandbox_policy", None)
            if policy in {"e2b", "ssh"}:
                raise ScriptToolError(
                    f"当前沙箱策略（{policy}）暂不支持脚本工具："
                    "该策略没有「会话工作区 → 沙箱路径」的映射，脚本无法定位工作区文件"
                )
            raise ScriptToolError("无法解析沙箱工作区根目录，脚本工具未执行")
        root = Path(self.sandbox_root).expanduser().resolve()
        try:
            relative = self.workdir.relative_to(root)
        except ValueError as exc:
            raise ScriptToolError(
                "当前会话目录不在沙箱工作区内，脚本工具未执行（已拒绝在宿主机上兜底执行）"
            ) from exc
        return relative.as_posix() or "."

    def _cwd_for_engine(self) -> str:
        return self._sandbox_relative_cwd() if self.in_sandbox else str(self.workdir)

    # ------------------------------------------------------------------ 参数

    def prepare_params(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        _schema, properties, required = parse_parameter_schema(
            self.config.parameter_schema,
            tool_name=self.config.name,
        )
        prepared = apply_user_placeholders(
            self.config.parameter_schema,
            dict(params),
            _current_user_variables(),
            tool_name=self.config.name,
        )
        missing = [
            name
            for name in sorted(required)
            if name not in prepared
            or prepared[name] is None
            or (isinstance(prepared[name], str) and not prepared[name].strip())
        ]
        if missing:
            raise ScriptToolError("缺少必填参数：" + "、".join(missing))
        unknown = [name for name in prepared if properties and name not in properties]
        if unknown:
            # 不阻断执行，但写进 stderr 之外的提示里，方便排查「模型传错字段名」
            logger.info(
                "[script_tool] %s 收到未声明的参数：%s", self.config.name, ", ".join(unknown)
            )
        return prepared

    # ------------------------------------------------------------------ 落盘

    def _script_dir(self) -> Path:
        return self.workdir / SCRIPT_DIR_NAME

    def materialize(
        self, params: Mapping[str, Any], *, run_id: str | None = None
    ) -> tuple[Path, Path, Path]:
        """写入脚本 / 参数 / 依赖文件，返回 (script, params, requirements)。"""
        run_id = run_id or uuid.uuid4().hex[:8]
        script_dir = self._script_dir()
        try:
            script_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ScriptToolError(f"无法创建脚本目录：{exc}") from exc

        stem = f"{self.config.name}.{code_fingerprint(self.config.code)}"
        script_path = script_dir / f"{stem}.py"
        # 脚本文件可按代码指纹共享；参数/依赖/输出必须按调用隔离
        params_path = script_dir / f"{stem}.{run_id}.params.json"
        requirements_path = script_dir / f"{stem}.{run_id}.requirements.txt"

        _write_private(script_path, self.config.code)
        _write_private(
            params_path,
            json.dumps(params, ensure_ascii=False),
        )
        if self.config.requirements:
            _write_private(requirements_path, "\n".join(self.config.requirements) + "\n")
        return script_path, params_path, requirements_path

    # ------------------------------------------------------------------ 依赖

    def _stamp_path(self) -> Path:
        fingerprint = _requirements_fingerprint(tuple(self.config.requirements))
        return self.workdir / LIBS_DIR_NAME / STAMP_DIR_NAME / f"{self.config.name}.{fingerprint}"

    async def ensure_requirements(
        self, requirements_path: Path, *, run_id: str | None = None
    ) -> Optional[str]:
        """确保第三方依赖已装到会话工作区的 ``.pylibs``，返回失败原因或 None。"""
        if not self.config.requirements:
            return None
        run_id = run_id or uuid.uuid4().hex[:8]
        stamp = self._stamp_path()
        libs_dir = self.workdir / LIBS_DIR_NAME
        # 目录被用户清理但 stamp 还在时，不能认为依赖仍然可用，否则脚本会
        # 直接 ModuleNotFoundError。
        if stamp.is_file() and libs_dir.is_dir():
            return None

        # shell 的 `> .pylibs/.install.out` 会在命令启动前打开文件；目录不存在时
        # 重定向直接失败，uv/pip 根本没机会把 target 建出来。首次声明依赖必踩。
        try:
            libs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"无法创建依赖目录 {LIBS_DIR_NAME}：{exc}"

        install = (
            'if command -v uv >/dev/null 2>&1; then '
            f"uv pip install --target {LIBS_DIR_NAME} -r {_relative_for_command(requirements_path, self.workdir)}; "
            "else "
            f"python -m pip install --target {LIBS_DIR_NAME} -r {_relative_for_command(requirements_path, self.workdir)}; "
            "fi"
        )
        # 安装输出也按调用隔离：两个脚本并发安装时不能互相截断日志。
        envelope = build_envelope_command(
            install,
            f"{LIBS_DIR_NAME}/.install.{run_id}.out",
            f"{LIBS_DIR_NAME}/.install.{run_id}.err",
        )
        result = await self._run_shell(envelope, timeout_seconds=DEPENDENCY_INSTALL_TIMEOUT_SECONDS)
        if result.timed_out:
            return "依赖安装超时（上限 120 秒），脚本未执行"
        if result.exit_code != 0:
            # 退出码未知（None）按失败处理：写进 stamp 会让后续调用永远不再重试
            detail = (result.stderr or result.stdout or "").strip()[-1500:]
            return (
                "依赖安装失败（" + "、".join(self.config.requirements) + "），脚本未执行。"
                "常见原因：包名拼错、版本不存在、沙箱无外网。原始输出：\n" + detail
            )

        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            _write_private(stamp, "ok\n")
        except OSError:
            pass
        return None

    # ------------------------------------------------------------------ 执行

    async def run(self, params: Mapping[str, Any]) -> ScriptRunResult:
        prepared = self.prepare_params(params)
        # 同一个绑定后的 runner 会在会话里被多次调用（甚至并发）；run_id 必须
        # 每次执行都新生成，不能放在 __init__ 里，否则参数/输出文件会互相覆盖。
        run_id = uuid.uuid4().hex[:8]
        script_path, params_path, requirements_path = self.materialize(
            prepared, run_id=run_id
        )

        install_error = await self.ensure_requirements(
            requirements_path, run_id=run_id
        )
        if install_error:
            return ScriptRunResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=install_error,
                duration_ms=0,
                message=install_error,
            )

        timeout = await self.resolve_timeout()
        if self.in_sandbox:
            # 容器内 bash 通道自己 600 秒就杀进程，平台侧等待再久也没意义
            timeout = min(timeout, SANDBOX_COMMAND_TIMEOUT_SECONDS)
        stem = script_path.stem
        out_file = f"{SCRIPT_DIR_NAME}/{stem}.{run_id}.out"
        err_file = f"{SCRIPT_DIR_NAME}/{stem}.{run_id}.err"
        script_rel = _relative_for_command(script_path, self.workdir)
        params_rel = _relative_for_command(params_path, self.workdir)

        run_command = (
            f"PY=$(command -v python || command -v python3) && "
            f'PYTHONPATH={LIBS_DIR_NAME} "$PY" -u {script_rel} < {params_rel}'
        )
        envelope = build_envelope_command(run_command, out_file, err_file)

        started = time.monotonic()
        shell_result = await self._run_shell(envelope, timeout_seconds=timeout)
        duration_ms = int((time.monotonic() - started) * 1000)

        if shell_result.timed_out:
            message = f"脚本执行超时（上限 {timeout:g} 秒），已终止"
            return ScriptRunResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=message,
                duration_ms=duration_ms,
                timed_out=True,
                message=message,
            )

        ok = shell_result.exit_code == 0
        result = ScriptRunResult(
            ok=ok,
            exit_code=shell_result.exit_code,
            stdout=shell_result.stdout,
            stderr=shell_result.stderr,
            duration_ms=duration_ms,
        )
        result.artifacts = _extract_artifacts(shell_result.stdout)
        return result

    async def resolve_timeout(self) -> float:
        if self.config.timeout_seconds:
            return max(1.0, float(self.config.timeout_seconds))
        try:
            from app.services.ai.runtime.agentscope.tool_timeout import (
                load_agent_max_toolcall_timeout,
            )

            configured = await load_agent_max_toolcall_timeout()
        except Exception:
            configured = DEFAULT_TIMEOUT_SECONDS
        return max(1.0, float(configured) - INNER_TIMEOUT_MARGIN_SECONDS)

    async def _run_shell(self, command: str, *, timeout_seconds: float) -> ShellRunResult:
        if self.in_sandbox:
            return await self._run_sandbox(command, timeout_seconds=timeout_seconds)
        return await self._run_local(command, timeout_seconds=timeout_seconds)

    async def _run_sandbox(self, command: str, *, timeout_seconds: float) -> ShellRunResult:
        # 先算 cwd：会话目录不在沙箱工作区内时必须直接报错，不能先去拉起容器
        sandbox_cwd = self._sandbox_relative_cwd()
        from app.services.ai.runtime.agentscope.workspace import (
            _sandbox_activity_heartbeat,
            resolve_sandbox_bash_tool,
        )

        try:
            bash_tool = await resolve_sandbox_bash_tool(self.sandbox_ws)
        except Exception as exc:  # noqa: BLE001 - 沙箱拉起失败要变成用户可读文案
            raise ScriptToolError(f"当前会话沙箱不可用，脚本工具未执行：{exc}") from exc
        if bash_tool is None:
            raise ScriptToolError("当前会话沙箱的 bash 通道不可用，脚本工具未执行")

        import asyncio

        # 直接调用底层 bash 绕过了 LazySandboxBashNativeTool 的调用中心跳；长脚本
        # 超过 sandbox_idle_time 会被 reaper 当成空闲容器回收，这里补上同一套心跳。
        cache_key = getattr(self.sandbox_ws, "_sandbox_cache_key", None) or getattr(
            self.sandbox_ws, "cache_key", None
        )
        try:
            async with _sandbox_activity_heartbeat(cache_key=cache_key):
                raw = await asyncio.wait_for(
                    _await_maybe(bash_tool(command=command, cwd=sandbox_cwd)),
                    timeout=timeout_seconds + 5,
                )
        except asyncio.TimeoutError:
            return ShellRunResult(exit_code=None, stdout="", stderr="", timed_out=True)

        text = _result_text(raw)
        payload = _parse_sandbox_payload(text)
        if payload is not None:
            return _shell_result_from_payload(payload)

        parsed = parse_envelope(text)
        if parsed.exit_code is None and not parsed.stdout and not parsed.stderr:
            # 通道返回了无法识别的文本（例如 MCP 侧报错）：原样交给上游判断
            return ShellRunResult(exit_code=None, stdout=text, stderr="", truncated=False)
        return parsed

    async def _run_local(self, command: str, *, timeout_seconds: float) -> ShellRunResult:
        from app.services.ai.code_execution_service import run_shell_command_capture

        capture = await run_shell_command_capture(
            command,
            cwd=self.workdir,
            timeout_seconds=timeout_seconds,
        )
        if capture.timed_out:
            return ShellRunResult(exit_code=None, stdout="", stderr="", timed_out=True)
        parsed = parse_envelope(capture.stdout + "\n" + capture.stderr)
        parsed.truncated = capture.truncated
        return parsed


def _parse_sandbox_payload(text: str) -> Optional[dict]:
    """沙箱 Bash 通道返回的是 JSON，不是裸文本。

    ``workspace_container_mcp.bash()`` 返回
    ``{"ok", "exit_code", "stdout", "stderr"[, "error", "hints"]}``；
    早期实现把它当纯文本按信封解析，标记永远找不到，于是 docker/k8s 下
    每次调用都报「退出码未知」——脚本其实跑成功了。
    """
    raw = str(text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if "exit_code" in payload:
        return payload
    # 超时形态只有 {"ok": false, "error": "command timed out"}，没有 exit_code。
    # 不识别它就会落到 raw 文本分支，被报成「退出码未知」而不是「超时」。
    error = str(payload.get("error") or "").strip()
    if payload.get("ok") is False and ("timed out" in error.lower() or "timeout" in error.lower()):
        return {"ok": False, "timed_out": True, "error": error or "command timed out"}
    return None


def _shell_result_from_payload(payload: Mapping[str, Any]) -> ShellRunResult:
    """把通道 JSON 转成执行结果：超时优先，其次信封，最后通道自己的退出码。"""
    if payload.get("timed_out") is True:
        reason = str(payload.get("error") or "").strip() or "command timed out"
        return ShellRunResult(exit_code=None, stdout="", stderr=reason, timed_out=True)

    inner_stdout = str(payload.get("stdout") or "")
    inner_stderr = str(payload.get("stderr") or "")

    parsed = parse_envelope(inner_stdout + "\n" + inner_stderr)
    if parsed.exit_code is not None:
        return parsed

    # 信封没写出来（命令在 echo 之前就失败/被容器杀掉）：用通道的退出码兜底
    raw_code = payload.get("exit_code")
    exit_code = raw_code if isinstance(raw_code, int) and not isinstance(raw_code, bool) else None
    error = str(payload.get("error") or "").strip()
    return ShellRunResult(
        exit_code=exit_code,
        stdout=parsed.stdout or inner_stdout.strip(),
        stderr=parsed.stderr or inner_stderr.strip() or error,
        truncated=False,
    )


async def _await_maybe(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


def _current_user_variables() -> Mapping[str, Any]:
    try:
        from app.services.ai.tools.user_placeholders import current_user_variables

        return current_user_variables()
    except Exception:
        return {}


def _relative_for_command(path: Path, workdir: Path) -> str:
    """命令里一律用相对路径：脚本与沙箱内外路径口径一致，也避免拼绝对路径出错。"""
    try:
        return Path(path).resolve().relative_to(workdir).as_posix()
    except ValueError:
        return str(path)


def _write_private(path: Path, content: str) -> None:
    """参数文件可能含业务数据，落盘即 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


def _extract_artifacts(stdout: str) -> List[str]:
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("artifacts")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw if str(item or "").strip()]


async def publish_artifacts(artifacts: List[str], *, workdir: Path) -> List[Dict[str, Any]]:
    """把脚本声明的产物登记成可下载文件（尽力而为，失败不影响脚本结果）。"""
    published: List[Dict[str, Any]] = []
    for relative in artifacts:
        candidate = Path(relative)
        source = candidate if candidate.is_absolute() else (workdir / candidate)
        try:
            from app.services.ai.tools.generated_file_tool import publish_generated_file

            payload = await publish_generated_file(str(source))
            if isinstance(payload, dict) and payload.get("download_url"):
                published.append(
                    {
                        "path": relative,
                        "filename": payload.get("filename"),
                        "download_url": payload.get("download_url"),
                        "expires_at": payload.get("expires_at"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.info("[script_tool] 产物登记失败（不影响脚本结果）：%s -> %s", relative, exc)
    return published


def _truncate(text: str, limit: int = MAX_RESULT_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore") + "\n…[输出已截断，上限 100KB]"


async def render_result_for_model(result: ScriptRunResult, *, workdir: Path) -> str:
    """把执行结果转成模型可读文本：成功给 stdout，失败给退出码 + stderr。"""
    if result.timed_out:
        return result.message or "脚本执行超时，已终止"

    if not result.ok:
        code = "未知" if result.exit_code is None else str(result.exit_code)
        stderr = _truncate(result.stderr.strip() or result.message or "（无错误输出）")
        head = f"脚本执行失败（退出码 {code}）"
        if result.stdout.strip():
            return head + "\n--- stdout ---\n" + _truncate(result.stdout.strip()) + "\n--- stderr ---\n" + stderr
        return head + "\n--- stderr ---\n" + stderr

    published = await publish_artifacts(result.artifacts, workdir=workdir) if result.artifacts else []
    stdout = result.stdout.strip()
    if not stdout:
        return "脚本执行成功，没有输出。" + (
            "\n已登记可下载文件：" + "、".join(item["filename"] or item["path"] for item in published)
            if published
            else ""
        )

    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        payload = None

    if isinstance(payload, dict) and published:
        payload["published_artifacts"] = published
        return _truncate(json.dumps(payload, ensure_ascii=False))
    if isinstance(payload, dict):
        return _truncate(json.dumps(payload, ensure_ascii=False))
    return _truncate(stdout)
