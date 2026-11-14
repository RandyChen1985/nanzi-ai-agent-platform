"""仅后端可用的凭据脚本入口：快照、私有协议、结果检查与受控导出。"""
import base64
import json
import os
from pathlib import Path
import stat
import time
import uuid

from app.services.ai.tools.credential_script_worker import make_redactor, clean_output, contains_echo, bounded_text
from app.services.secrets.injection import apply_script_env_bindings, BindingLookupError


class CredentialExecutionError(RuntimeError):
    """可以返回给用户的固定安全错误。"""


def worker_source() -> str:
    return Path(__file__).with_name("credential_script_worker.py").read_text(encoding="utf-8")


def open_workdir(workdir: Path, sandbox_root=None) -> int:
    """从可信根目录 fd 逐段打开，校验后不再 resolve 可被替换的会话路径。"""
    root = Path(sandbox_root or workdir).absolute()
    try:
        relative = Path(workdir).absolute().relative_to(root)
        if ".." in relative.parts:
            raise ValueError()
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    except (ValueError, OSError):
        raise CredentialExecutionError("凭据脚本工作区不可用或包含符号链接") from None


def snapshot_inputs(workdir: Path, *, sandbox_root=None) -> dict[str, str]:
    """复制有限的普通业务文件，不加载共享的 .pylibs 或 .script_tools。

    使用目录 fd 与 O_NOFOLLOW，防止并发改名/替换链接把读取指向工作区外。
    """
    snapshot = {}
    total = 0
    root_fd = open_workdir(workdir, sandbox_root)
    try:
        for directory, dirs, names, directory_fd in os.fwalk(".", dir_fd=root_fd, follow_symlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for name in dirs:
                if stat.S_ISLNK(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                    raise CredentialExecutionError("凭据脚本输入不能包含符号链接")
            for name in sorted(names):
                if name.startswith("."):
                    continue
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise CredentialExecutionError("凭据脚本输入只支持普通文件")
                    data = handle.read(8 * 1024 * 1024 + 1)
                total += len(data)
                if total > 8 * 1024 * 1024 or len(snapshot) >= 256:
                    raise CredentialExecutionError("凭据脚本输入超过 8 MiB 或 256 个文件上限")
                relative = (Path(directory) / name).as_posix()
                snapshot[relative] = base64.b64encode(data).decode("ascii")
    except OSError:
        raise CredentialExecutionError("凭据脚本输入快照失败，请检查文件与符号链接") from None
    finally:
        os.close(root_fd)
    return snapshot


def export_artifacts(raw, workdir: Path, redact, *, sandbox_root=None) -> tuple[list[str], int]:
    """只把可检查且没有凭据回显的文本写入共享工作区。"""
    if not isinstance(raw, list) or len(raw) > 8:
        raise CredentialExecutionError("凭据脚本产物协议无效")
    accepted, blocked = [], 0
    for item in raw:
        try:
            name = item["path"]
            if not isinstance(name, str) or redact(name) != name:
                raise ValueError()
            data = base64.b64decode(item["data"], validate=True)
            text = data.decode("utf-8")
            if len(data) > 1024 * 1024 or "\x00" in text or contains_echo(text, redact):
                raise ValueError()
            suffix = Path(name).suffix
            # 导出名由平台生成，远端不能指定宿主机路径。
            if not suffix.isascii() or len(suffix) > 12 or not suffix[1:].isalnum():
                suffix = ".txt"
            accepted.append((suffix, data))
        except (KeyError, TypeError, ValueError, UnicodeError):
            blocked += 1
    if not accepted:
        return [], blocked
    directory = ".credential-output-" + uuid.uuid4().hex
    outputs = []
    root_fd = open_workdir(workdir, sandbox_root)
    try:
        os.mkdir(directory, mode=0o700, dir_fd=root_fd)
        output_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            for index, (suffix, data) in enumerate(accepted):
                name = f"artifact-{index + 1}{suffix}"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output_fd)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                outputs.append(f"{directory}/{name}")
        finally:
            os.close(output_fd)
    finally:
        os.close(root_fd)
    return outputs, blocked


async def execute_credential_script(runner, params):
    from app.services.ai.tools.credential_script_providers import (
        CredentialExecutionUnavailable, run_credential_process, validate_credential_provider,
    )
    from app.services.ai.tools.script_tool_runtime import ScriptRunResult, ScriptToolError

    started = time.monotonic()
    env = {}
    try:
        # 能力/目录检查在取值前执行，不支持策略不读取凭据、不回退到宿主机。
        workspace = runner.sandbox_ws
        ensure_ready = getattr(workspace, "ensure_ready", None)
        if callable(ensure_ready):
            workspace = await ensure_ready()
        validate_credential_provider(workspace)
        runner._sandbox_relative_cwd()
        files = snapshot_inputs(runner.workdir, sandbox_root=runner.sandbox_root)
        env = await apply_script_env_bindings(runner.config, user_info=runner.user_info)
        if not env:
            raise CredentialExecutionError("凭据绑定已变化，请重新执行")
        timeout = min(await runner.resolve_timeout(), 600.0)
        payload = json.dumps({"code": runner.config.code, "params": params,
                              "requirements": list(runner.config.requirements), "env": env,
                              "files": files, "timeout": max(0.1, timeout - 1)}, ensure_ascii=False).encode()
        if len(payload) > 16 * 1024 * 1024:
            raise CredentialExecutionError("凭据脚本输入超过执行通道上限")
        raw = await run_credential_process(workspace, bootstrap=worker_source(), payload=payload, timeout_seconds=timeout)
        redact = make_redactor(env.values())
        if len(raw) > 16 * 1024 * 1024:
            raise CredentialExecutionError("凭据脚本输出超过执行通道上限")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise CredentialExecutionError("凭据脚本输出协议无效")
        exit_code = response.get("exit_code")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise CredentialExecutionError("凭据脚本退出状态无效")
        stdout = clean_output(str(response.get("stdout") or ""), redact)
        stderr = clean_output(str(response.get("stderr") or ""), redact)
        artifacts, blocked = export_artifacts(response.get("artifacts", []), runner.workdir, redact, sandbox_root=runner.sandbox_root)
        blocked += int(response.get("blocked_artifacts", 0))
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and "artifacts" in parsed:
            parsed["artifacts"] = artifacts
            stdout = json.dumps(parsed, ensure_ascii=False)
        message = "部分产物未通过凭据回显或文件格式检查，未发布" if blocked else ""
        if message:
            stderr = (stderr + "\n" + message).strip()
            if isinstance(parsed, dict):
                parsed["artifact_notice"] = message
                stdout = json.dumps(parsed, ensure_ascii=False)
            else:
                stdout += "\n" + message
        timed_out = response.get("timed_out") is True
        return ScriptRunResult(ok=exit_code == 0 and not timed_out, exit_code=exit_code,
                               stdout=bounded_text(stdout, 80 * 1024), stderr=bounded_text(stderr, 8 * 1024),
                               duration_ms=int((time.monotonic() - started) * 1000),
                               artifacts=artifacts, timed_out=timed_out,
                               message="脚本执行超时，已终止" if timed_out else message)
    except TimeoutError:
        return ScriptRunResult(ok=False, exit_code=None, stdout="", stderr="脚本执行超时，已终止",
                               timed_out=True, duration_ms=int((time.monotonic() - started) * 1000),
                               message="脚本执行超时，已终止")
    except (BindingLookupError, CredentialExecutionError, CredentialExecutionUnavailable) as exc:
        raise ScriptToolError(str(exc)) from None
    except Exception:
        raise ScriptToolError("凭据脚本执行失败，未返回原始输出") from None
    finally:
        env.clear()
