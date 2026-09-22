# -*- coding: utf-8 -*-
"""SshWorkspace: a remote-sandbox AgentScope workspace driven over SSH.

This module provides a self-contained
:class:`agentscope.workspace.WorkspaceBase` subclass that treats a
remote host reached over the platform host's ``ssh`` CLI as the
sandbox workdir.  It backs the ``ssh`` sandbox policy selected via the
``sandbox_policy`` system config (the different policies are
documented in ``workspace.py``).

Why a plain ``ssh`` subprocess instead of a Python SSH library:

* The platform already ships the system ``ssh``/``sshpass`` CLI and
  does not want to add ``asyncssh``/``paramiko`` as hard runtime
  dependencies.
* Key authentication works out of the box over the CLI (no TTY
  needed); password authentication requires ``sshpass`` (a documented
  external dependency, checked at build time below).
* The blocking ``subprocess`` calls are wrapped in
  ``asyncio.to_thread`` so they never stall the event loop.

Structure:

* :class:`SshWorkspace` implements the full
  :class:`WorkspaceBase` contract (12 abstract methods) on top of the
  platform host's ``ssh`` CLI.
* ``_SSH_INLINE_SERVER`` is a thin stdio FastMCP server (the same
  ``sandbox`` four-tool shape as ``workspace_container_mcp``) that is
  spawned **locally** by the AgentScope gateway and forwards every
  tool call to the remote host through its own ``ssh`` child process.
  Connection parameters are injected through environment variables so
  the inline script needs no remote package installs.

The remote sandbox layout mirrors the container policy: ``workdir``
(the ``sandbox_ssh_remote_workdir`` value, default ``/workspace``)
holds ``data/``, ``skills/`` and ``sessions/``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agentscope.mcp import MCPClient  # type: ignore[attr-defined]
from agentscope.mcp import StdioMCPConfig  # type: ignore[attr-defined]
from agentscope.message import (  # type: ignore[attr-defined]
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from agentscope.skill import Skill  # type: ignore[attr-defined]
from agentscope.tool import ToolBase  # type: ignore[attr-defined]
from agentscope.workspace import WorkspaceBase  # type: ignore[attr-defined]
from pydantic import AnyUrl

logger = logging.getLogger(__name__)

# Name used for the injected SSH tool server.  Exposed to the model as
# ``<name>::<tool>``, mirroring the container sandbox policy.
SSH_MCP_NAME = "sandbox"

# Working directory inside the remote sandbox (SSH equivalent of the
# container's /workspace).  Overridable per connection.
SSH_REMOTE_WORKDIR = "/workspace"

# Relative layout the workspace manages inside the remote workdir.
_REMOTE_DATA_DIR = "data"
_REMOTE_SKILLS_DIR = "skills"
_REMOTE_SESSIONS_DIR = "sessions"


@dataclass(frozen=True)
class _ExecResult:
    """Captured remote command outcome (mirrors the container policy)."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        return self.exit_code == 0


def _have_sshpass() -> bool:
    """True when the ``sshpass`` CLI is on the platform host's PATH."""
    return shutil.which("sshpass") is not None


#: Upper bound for the ssh diagnostics embedded in errors/logs.
_SSH_DIAGNOSTIC_LIMIT = 400

#: Install hints surfaced when the platform host lacks the ssh/sshpass CLI.
_SSH_CLI_INSTALL_HINT = (
    "install the OpenSSH client and the 'sshpass' CLI on the platform host "
    "(Debian/Ubuntu: apt-get install -y openssh-client sshpass; "
    "macOS: brew install sshpass)"
)


def _missing_ssh_cli_reason(exc: FileNotFoundError) -> str:
    """Explain a missing ``ssh``/``sshpass`` binary on the platform host.

    ``subprocess`` raises :class:`FileNotFoundError` when the executable is not
    on ``PATH``; without this hint the caller only sees a generic
    "cannot reach host" message even though nothing was ever dialed.
    """
    binary = os.path.basename(str(exc.filename or "").strip()) or "ssh"
    return f"the '{binary}' CLI is not available on the platform host: {_SSH_CLI_INSTALL_HINT}"


#: ``ssh-keygen``-derived classification of a materialized private key.
_KEY_PROBE_OK = "ok"
_KEY_PROBE_ENCRYPTED = "encrypted"
_KEY_PROBE_INVALID = "invalid"
_KEY_PROBE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class _PrivateKeyProbe:
    """Outcome of inspecting a configured private key on the platform host."""

    status: str
    fingerprint: str = ""
    detail: str = ""


def _public_key_fingerprint(public_key_line: str) -> str:
    """``SHA256:...`` fingerprint of an OpenSSH public-key line (ssh-keygen format)."""
    parts = str(public_key_line or "").split()
    if len(parts) < 2:
        return ""
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception:  # noqa: BLE001
        return ""
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    return f"SHA256:{digest.rstrip('=')}"


def probe_private_key(key_path: str) -> _PrivateKeyProbe:
    """Classify a materialized private key before ``ssh`` ever dials out.

    ``ssh`` reports a **passphrase-protected** key and an **unauthorized** key
    with the exact same ``Permission denied (publickey,password)`` line (both
    verified against a live sshd), so that message alone cannot tell an operator
    which side to fix.  ``ssh-keygen -y`` separates the cases: it decrypts the
    key locally (stdin is closed on purpose, so it can never prompt) and either
    prints the public half — giving us the fingerprint to compare with the
    remote ``authorized_keys`` — or fails with ``incorrect passphrase``.
    """
    try:
        proc = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", key_path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
        )
    except FileNotFoundError:
        return _PrivateKeyProbe(
            _KEY_PROBE_UNAVAILABLE,
            detail=(
                "the 'ssh-keygen' CLI is not available on the platform host: "
                f"{_SSH_CLI_INSTALL_HINT}"
            ),
        )
    except subprocess.TimeoutExpired:
        return _PrivateKeyProbe(_KEY_PROBE_UNAVAILABLE, detail="'ssh-keygen' timed out")
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return _PrivateKeyProbe(
            _KEY_PROBE_INVALID,
            detail=_summarize_ssh_diagnostics(f"{type(exc).__name__}: {exc}"),
        )

    if proc.returncode == 0:
        fingerprint = _public_key_fingerprint(proc.stdout.decode("utf-8", "replace"))
        return _PrivateKeyProbe(_KEY_PROBE_OK, fingerprint=fingerprint)

    raw = proc.stderr or proc.stdout
    detail = _summarize_ssh_diagnostics(raw) or "ssh-keygen rejected the private key"
    lowered = detail.lower()
    if "incorrect passphrase" in lowered or "enter passphrase" in lowered:
        return _PrivateKeyProbe(_KEY_PROBE_ENCRYPTED, detail=detail)
    return _PrivateKeyProbe(_KEY_PROBE_INVALID, detail=detail)


def build_remote_command(remote_args: Sequence[str]) -> str:
    """Join a remote argv into one shell-quoted command string for ``ssh``.

    ``ssh`` does not forward an argv: it joins every argument after the
    destination with spaces and hands the result to the **remote login shell**,
    which re-parses it.  Unquoted elements are therefore split at every space
    and newline — a multi-line ``python3 -c`` payload reaches the remote as
    ``python3 -c import`` (SyntaxError) and a ``bash -lc`` snippet silently
    loses its ``cd`` because only the first word becomes the ``-c`` argument.
    Quoting each element keeps the remote argv byte-identical.
    """
    return " ".join(shlex.quote(str(arg)) for arg in remote_args)


def _summarize_ssh_diagnostics(raw: bytes | str, *, limit: int = _SSH_DIAGNOSTIC_LIMIT) -> str:
    """Collapse raw ssh output into one short, credential-free diagnostic line.

    ssh writes its actionable failures to stderr ("Host key verification
    failed", "Permission denied (publickey,password)", "Connection refused").
    The text is masked before it reaches an error message or a log line, so a
    password echoed by a wrapper can never leak.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw or "")
    text = " ".join(text.split())
    if not text:
        return ""

    from app.utils.masking import mask_sensitive_data

    masked = str(mask_sensitive_data(text) or "")
    if len(masked) > limit:
        masked = masked[:limit] + "…"
    return masked


def _normalize_private_key(raw_key: str) -> str:
    """Normalize private key text into a valid multi-line PEM/OpenSSH format.

    Handles:
    - Escaped newlines ('\\n' or '\\r\\n' -> '\n')
    - CRLF to LF ('\r\n' -> '\n')
    - Surrounding string quotes if pasted literally
    - Keys flattened into a single line with space separators
    - Ensuring trailing newline required by OpenSSH
    """
    if not raw_key:
        return ""
    text = raw_key.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    if "\\n" in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    else:
        text = text.replace("\r\n", "\n").replace("\r", "\n")

    # If the key was flattened into a single line (newlines replaced by spaces)
    if "\n" not in text:
        m = re.search(
            r"(-----BEGIN [A-Z0-9 ]+?PRIVATE KEY-----)(.+?)(-----END [A-Z0-9 ]+?PRIVATE KEY-----)",
            text,
        )
        if m:
            header = m.group(1).strip()
            body = m.group(2).strip()
            footer = m.group(3).strip()
            parts = [p for p in body.split() if p]
            lines: list[str] = []
            for p in parts:
                if len(p) > 64:
                    for i in range(0, len(p), 64):
                        lines.append(p[i : i + 64])
                else:
                    lines.append(p)
            text = "\n".join([header, *lines, footer])

    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    clean_text = "\n".join(lines)
    if clean_text and not clean_text.endswith("\n"):
        clean_text += "\n"
    return clean_text


class SshWorkspace(WorkspaceBase):
    """A remote-host sandbox operated through the platform host's SSH CLI.

    All file/command I/O is forwarded to a remote sandbox host over
    ``ssh``.  ``workdir`` (the ``remote_workdir`` value) is the remote
    working directory, so the higher layers that read
    ``workspace.workdir`` keep working unchanged across policies.

    Args:
        workspace_id: Optional stable id (defaults to a fresh uuid).
        host: Remote host (hostname or IP).  Required.
        port: SSH port (default 22).
        user: Remote user.  Empty means "current local user".
        auth_type: ``"password"`` (via ``sshpass``, requires the CLI)
            or ``"key"`` (private key agent/CLI).
        password: Cleartext password used with ``auth_type="password"``.
        private_key: Private key *contents* (PEM) used with
            ``auth_type="key"``; materialized to a temp file that is
            removed on :meth:`close`.
        remote_workdir: Remote sandbox working directory.
        default_mcps: Static MCP servers seeded on first init.
        skill_paths: Local skill paths copied into ``skills/`` on first
            init (mirrors the container policy seeding).
    """

    # ── WorkspaceBase contract fields ───────────────────────────
    workspace_id: str
    workdir: str
    is_alive: bool

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        host: str,
        port: int = 22,
        user: str = "",
        auth_type: str = "password",
        password: str | None = None,
        private_key: str | None = None,
        remote_workdir: str = SSH_REMOTE_WORKDIR,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(workspace_id=workspace_id)
        self.host = host
        self.port = int(port) if port else 22
        self.user = user or ""
        self.auth_type = (auth_type or "password").lower()
        self.password = password or ""
        self.private_key = private_key or ""
        self.remote_workdir = remote_workdir or SSH_REMOTE_WORKDIR
        self.workdir = self.remote_workdir

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self.is_alive = False
        self._mcps: list[MCPClient] = []
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()
        # Materialized private key temp file, cleaned in close().
        self._local_key_path: str | None = None
        # SHA256 fingerprint of the configured key (from the local probe).
        self._key_fingerprint = ""
        # Password file used only by the local inline MCP child. Keeping the
        # secret out of that child's environment and argv avoids disclosure
        # through process inspection.
        self._local_password_path: str | None = None
        # Connection args cached after a successful control test.
        self._control_active = False

    # ── connection helpers ──────────────────────────────────────

    def _ssh_args(self) -> list[str]:
        """Base ``ssh`` argument list (without the target command)."""
        args = [
            "ssh",
            "-p", str(self.port),
            # Require a pre-verified known_hosts entry.  TOFU/disabled host
            # verification is not acceptable for a remote code sandbox.
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes" if self.auth_type == "key" else "BatchMode=no",
        ]
        if self.auth_type == "key" and self._local_key_path:
            args += ["-i", self._local_key_path]
        return args

    def _build_ssh_command(
        self,
        remote_args: list[str],
        *,
        password_fd: int | None = None,
    ) -> list[str]:
        """Build an SSH argv without ever placing the password in argv.

        The remote command is shell-quoted into a **single** argv element: ssh
        forwards it as one string to the remote login shell, which re-parses
        it.  Passing the elements separately would split every multi-line
        ``python3 -c`` payload and every ``bash -lc`` snippet at whitespace.
        """
        command = self._ssh_args() + [
            self._target(),
            build_remote_command(remote_args),
        ]
        if self.auth_type == "password":
            if password_fd is None:
                raise ValueError("password SSH commands require a password fd")
            return ["sshpass", "-d", str(password_fd), *command]
        return command

    def _run_remote_sync(
        self,
        remote_args: list[str],
        *,
        input_data: bytes | None = None,
        timeout: float = 600,
    ) -> subprocess.CompletedProcess:
        """Run one SSH command, feeding sshpass through an inherited fd."""
        password_read_fd: int | None = None
        password_write_fd: int | None = None
        process: subprocess.Popen | None = None
        command: list[str] = []
        try:
            if self.auth_type == "password":
                password_read_fd, password_write_fd = os.pipe()
            command = self._build_ssh_command(
                remote_args,
                password_fd=password_read_fd,
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(password_read_fd,) if password_read_fd is not None else (),
            )
            if password_write_fd is not None:
                with os.fdopen(password_write_fd, "wb") as password_stream:
                    password_stream.write(self.password.encode("utf-8") + b"\n")
                    password_stream.flush()
                password_write_fd = None
            if password_read_fd is not None:
                os.close(password_read_fd)
                password_read_fd = None

            stdout, stderr = process.communicate(input=input_data, timeout=timeout)
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )
        except subprocess.TimeoutExpired:
            if process is None:
                return subprocess.CompletedProcess(command, -1, b"", b"timed out")
            process.kill()
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, -1, stdout, stderr)
        finally:
            for fd in (password_read_fd, password_write_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _target(self) -> str:
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host

    def _materialize_key(self) -> None:
        """Write ``self.private_key`` contents to a temp file once.

        The key is also *classified* here: a passphrase-protected or corrupted
        key is rejected with an explicit message instead of being handed to
        ``ssh``, whose ``Permission denied (publickey,password)`` output cannot
        distinguish those cases from a merely unauthorized key.
        """
        if not self.private_key or self._local_key_path:
            return
        clean_key = _normalize_private_key(self.private_key)
        fd, path = tempfile.mkstemp(prefix="dsh-ssh-key-", suffix=".pem")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(clean_key)
            os.chmod(path, 0o600)
        except Exception:
            os.unlink(path)
            raise
        self._local_key_path = path

        probe = probe_private_key(path)
        self._key_fingerprint = probe.fingerprint
        if probe.status in (_KEY_PROBE_ENCRYPTED, _KEY_PROBE_INVALID):
            # Never leave an unusable (possibly passphrase-protected) key on disk.
            self._discard_local_key()
            if probe.status == _KEY_PROBE_ENCRYPTED:
                raise RuntimeError(
                    "SshWorkspace: the configured private key is passphrase-protected "
                    "but the platform connects with BatchMode=yes and cannot prompt for "
                    "a passphrase — use a key without a passphrase, or switch "
                    f"auth_type to password. ssh-keygen said: {probe.detail}"
                )
            raise RuntimeError(
                "SshWorkspace: the configured private key is not a usable private "
                f"key: {probe.detail}"
            )
        if probe.status == _KEY_PROBE_OK:
            logger.debug(
                "SshWorkspace: private key loaded (%s, %s)",
                probe.fingerprint or "fingerprint unavailable",
                path,
            )
        else:
            # No ssh-keygen on the platform host: keep going, the key still
            # works for ssh itself.
            logger.debug("SshWorkspace: skipped private key probe: %s", probe.detail)

    def _discard_local_key(self) -> None:
        """Remove the materialized private key temp file (best effort)."""
        if self._local_key_path:
            try:
                os.unlink(self._local_key_path)
            except OSError:
                pass
        self._local_key_path = None
        self._key_fingerprint = ""

    def _materialize_password(self) -> None:
        """Write the MCP child's password to a mode-600 temp file once."""
        if self.auth_type != "password" or not self.password or self._local_password_path:
            return
        fd, path = tempfile.mkstemp(prefix="dsh-ssh-pass-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.password)
                fh.write("\n")
            os.chmod(path, 0o600)
        except Exception:
            os.unlink(path)
            raise
        self._local_password_path = path

    async def _exec(
        self,
        remote_cmd: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> _ExecResult:
        """Run ``bash -lc <remote_cmd>`` on the remote host.

        The remote working directory defaults to ``remote_workdir``
        unless ``cwd`` is given (relative to ``remote_workdir``).
        Callee is responsible for quoting into ``remote_cmd``.
        """
        if cwd:
            resolved = cwd if cwd.startswith("/") else posixpath.join(self.remote_workdir, cwd)
            command = f"cd {shlex.quote(resolved)} && {remote_cmd}"
        else:
            command = f"cd {shlex.quote(self.remote_workdir)} && {remote_cmd}"
        def _run() -> _ExecResult:
            try:
                proc = self._run_remote_sync(
                    ["bash", "-lc", command],
                    timeout=timeout if timeout is not None else 600,
                )
                return _ExecResult(
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            except subprocess.TimeoutExpired:
                return _ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

        if timeout is None:
            return await asyncio.to_thread(_run)
        try:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        except asyncio.TimeoutError:
            return _ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

    def _key_auth_hint(self, reason: str) -> str:
        """Append the guidance ``ssh`` itself cannot give for a key-auth failure.

        The remote prints one identical "Permission denied (publickey,password)"
        line whether the key is unauthorized or unusable, so the fingerprint is
        surfaced to make the two cases distinguishable from the UI alone.
        """
        if self.auth_type != "key":
            return ""
        lowered = str(reason or "").lower()
        if not any(
            marker in lowered
            for marker in ("permission denied", "publickey", "authentication", "passphrase")
        ):
            return ""
        if self._local_key_path and self._key_fingerprint:
            return (
                f" | 已提供私钥 {self._key_fingerprint}（SHA256 指纹）：该报错无法区分"
                "「对应公钥未加入远端 authorized_keys」与「私钥受 passphrase 保护」，"
                "请确认远端 ~/.ssh/authorized_keys 含此指纹对应的公钥，且 ~/.ssh 为 700、"
                "authorized_keys 为 600"
            )
        if not self._local_key_path:
            return (
                " | 当前未提供私钥内容，ssh 只能回退平台主机的默认身份或 ssh-agent；"
                "若需使用指定私钥，请在「SSH 私钥内容」中填写"
            )
        return ""

    async def _connect_test(self) -> tuple[bool, str]:
        """Run a trivial remote command.

        Returns ``(reachable, reason)``: ``reason`` is empty on success and a
        short, credential-free explanation on failure, so the caller can report
        a missing ``ssh``/``sshpass`` CLI, "Host key verification failed" or
        "Permission denied" instead of a bare "cannot reach".
        """

        def _run() -> tuple[bool, str]:
            try:
                proc = self._run_remote_sync(
                    ["echo", "dsh-ssh-ok"],
                    timeout=45,
                )
            except FileNotFoundError as exc:
                # The platform host has no ssh/sshpass binary at all.
                return False, _missing_ssh_cli_reason(exc)
            except Exception as exc:  # noqa: BLE001
                return False, _summarize_ssh_diagnostics(
                    f"{type(exc).__name__}: {exc}"
                )
            if proc.returncode == 0 and b"dsh-ssh-ok" in proc.stdout:
                return True, ""
            return (
                False,
                _summarize_ssh_diagnostics(proc.stderr or proc.stdout)
                or f"ssh probe exited with code {proc.returncode}",
            )

        return await asyncio.to_thread(_run)

    def _remote_abspath(self, path: str) -> str:
        """Resolve a remote path (absolute kept, relative to ``remote_workdir``)."""
        return path if path.startswith("/") else posixpath.join(self.remote_workdir, path)

    async def _read(self, path: str) -> bytes:
        """Fetch a remote file as raw bytes via base64 over stdin.

        ``python3`` is used on the remote side to base64-encode the
        file so the bytes cross the ssh channel verbatim (no termios/CRLF
        rewriting) regardless of content.
        """
        abs_path = self._remote_abspath(path)
        py = (
            "import base64,sys;"
            f"p={abs_path!r};"
            "try:\n"
            " f=open(p,'rb').read();\n"
            " sys.stdout.write(base64.b64encode(f).decode())\n"
            "except FileNotFoundError:\n"
            " sys.stdout.write('__DOSH_ERR__')\n"
            " sys.exit(3)\n"
            "except Exception as e:\n"
            " sys.stdout.write('__DOSH_ERR__'+type(e).__name__); sys.exit(4)\n"
        )

        def _run() -> _ExecResult:
            try:
                proc = self._run_remote_sync(
                    ["python3", "-c", py],
                    timeout=120,
                )
                return _ExecResult(proc.returncode, proc.stdout, proc.stderr)
            except subprocess.TimeoutExpired:
                return _ExecResult(-1, b"", b"timed out")

        res = await asyncio.to_thread(_run)
        out = res.stdout.strip()
        if res.exit_code == 3 or out.startswith(b"__DOSH_ERR__"):
            raise FileNotFoundError(path)
        if res.exit_code != 0:
            raise OSError(f"remote read failed: {res.stderr.decode(errors='replace')}")
        return base64.b64decode(out)

    async def _write(self, path: str, content: bytes) -> None:
        """Write raw bytes to a remote file via a python3 stdin sink.

        Parent directories are created by the embedded python program itself,
        so a single ssh round-trip is enough — seeding N skills therefore costs
        N connections instead of 2N, which matters on a high-latency link.
        """
        abs_path = self._remote_abspath(path)
        py = (
            "import base64,sys,os;\n"
            f"p={abs_path!r};\n"
            "os.makedirs(os.path.dirname(p), exist_ok=True);\n"
            "open(p,'wb').write(base64.b64decode(sys.stdin.read()))\n"
        )
        b64 = base64.b64encode(content).decode()

        def _run() -> _ExecResult:
            try:
                proc = self._run_remote_sync(
                    ["python3", "-c", py],
                    input_data=b64.encode("utf-8"),
                    timeout=120,
                )
                return _ExecResult(proc.returncode, proc.stdout, proc.stderr)
            except subprocess.TimeoutExpired:
                return _ExecResult(-1, b"", b"timed out")

        res = await asyncio.to_thread(_run)
        if res.exit_code != 0:
            raise OSError(
                "remote write failed: "
                + (
                    _summarize_ssh_diagnostics(res.stderr)
                    or f"exit code {res.exit_code}"
                )
            )

    async def _write_many(self, files: Mapping[str, bytes]) -> None:
        """Write several remote files over a **single** ssh connection.

        Seeding one skill per connection costs an ssh handshake (multi-second on
        a high-latency link) per file, which is what blew the 60s prewarm
        budget.  The remote program creates parent directories itself and
        reports per-file failures on stdout, so one bad path cannot abort the
        whole batch.
        """
        resolved = {self._remote_abspath(p): c for p, c in files.items()}
        if not resolved:
            return
        payload = json.dumps(
            {p: base64.b64encode(c).decode("utf-8") for p, c in resolved.items()}
        )
        py = (
            "import base64,json,os,sys;\n"
            "d=json.loads(sys.stdin.read());\n"
            "errors={};\n"
            "for p,c in d.items():\n"
            "    try:\n"
            "        os.makedirs(os.path.dirname(p),exist_ok=True)\n"
            "        open(p,'wb').write(base64.b64decode(c))\n"
            "    except Exception as e:\n"
            "        errors[p]=repr(e)\n"
            "print(json.dumps({'written':len(d)-len(errors),'errors':errors}))\n"
        )

        def _run() -> _ExecResult:
            try:
                proc = self._run_remote_sync(
                    ["python3", "-c", py],
                    input_data=payload.encode("utf-8"),
                    timeout=180,
                )
                return _ExecResult(proc.returncode, proc.stdout, proc.stderr)
            except subprocess.TimeoutExpired:
                return _ExecResult(-1, b"", b"timed out")

        res = await asyncio.to_thread(_run)
        if res.exit_code != 0:
            raise OSError(
                "remote batch write failed: "
                + (
                    _summarize_ssh_diagnostics(res.stderr)
                    or f"exit code {res.exit_code}"
                )
            )
        try:
            report = json.loads(res.stdout.decode("utf-8", "replace").strip() or "{}")
        except ValueError:
            return
        failed = report.get("errors") or {}
        if failed:
            logger.warning(
                "SshWorkspace: %d of %d seeded file(s) failed: %s",
                len(failed),
                len(resolved),
                _summarize_ssh_diagnostics(json.dumps(failed, ensure_ascii=False)),
            )

    # ── lifecycle ───────────────────────────────────────────────

    async def initialize(self) -> None:
        """Probe reachability, prepare ``remote_workdir``, seed MCPs/skills.

        Uses the same restoration semantics as the container policy: a
        remote ``.mcp`` file (if present) takes precedence over
        ``default_mcps``; otherwise ``default_mcps`` is adopted and
        persisted the first time it changes.
        """
        if self.is_alive:
            return

        # Restore / seed MCPs up front (needed by the sandbox bridge).
        self._mcps = await self._restore_or_seed_mcps()

        if self.auth_type == "key" and self.private_key:
            self._materialize_key()

        reachable, reason = await self._connect_test()
        if not reachable:
            raise RuntimeError(
                f"SshWorkspace: cannot reach {self._target()}:{self.port} "
                f"over ssh (auth_type={self.auth_type}): "
                f"{reason or 'ssh probe failed'}"
                f"{self._key_auth_hint(reason)}"
            )

        # Prepare the remote sandbox layout.
        mkdir_res = await self._exec(
            "mkdir -p "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_DATA_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_SKILLS_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_SESSIONS_DIR} "
            f"{shlex.quote(self.remote_workdir)}"
        )
        if mkdir_res.exit_code != 0:
            raise RuntimeError(
                "SshWorkspace: failed to prepare remote workdir: "
                + (
                    _summarize_ssh_diagnostics(mkdir_res.stderr)
                    or f"exit code {mkdir_res.exit_code}"
                )
            )

        await self._seed_skills()
        self._control_active = True
        self.is_alive = True

    async def close(self) -> None:
        """Release the SSH workspace (best effort, never raises)."""
        mcps = list(self._mcps)
        self._mcps = []
        for mcp in mcps:
            close = getattr(mcp, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                if "not connected" in str(exc).lower():
                    logger.debug("SshWorkspace: MCP %s not connected at teardown", type(mcp).__name__)
                else:
                    logger.warning(
                        "SshWorkspace: failed to close MCP %s: %s",
                        type(mcp).__name__,
                        exc,
                    )
        if self._local_key_path:
            self._discard_local_key()
        if self._local_password_path:
            try:
                os.unlink(self._local_password_path)
            except OSError:
                pass
            self._local_password_path = None
        self._control_active = False
        self.is_alive = False

    async def reset(self) -> None:
        """Reset the remote sandbox to an empty state (best effort)."""
        await self._exec(
            f"rm -rf {shlex.quote(self.remote_workdir)}/{_REMOTE_SESSIONS_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_DATA_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_SKILLS_DIR} && "
            f"mkdir -p {shlex.quote(self.remote_workdir)}/{_REMOTE_DATA_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_SKILLS_DIR} "
            f"{shlex.quote(self.remote_workdir)}/{_REMOTE_SESSIONS_DIR}"
        )

    # ── introspection ───────────────────────────────────────────

    def get_instructions(self) -> str:
        """Static workspace instructions for the model."""
        return (
            "You are working in a remote SSH sandbox. The working directory "
            f"is {self.remote_workdir}. Use the provided sandbox tools to "
            "inspect and run commands there. The directory layout contains "
            "data/, skills/ and sessions/ subfolders."
        )

    def list_tools(self) -> list[ToolBase]:
        """Remote python has no platform tools; all tools go through MCP."""
        return []

    def list_mcps(self) -> list[MCPClient]:
        return list(self._mcps)

    async def list_skills(self) -> list[Skill]:
        """List remote ``skills/`` entries (mirrors the container policy)."""
        base = posixpath.join(self.remote_workdir, _REMOTE_SKILLS_DIR)
        found = (await self._exec(f"find {shlex.quote(base)} -name SKILL.md 2>/dev/null"))
        out = (
            found.stdout.decode(errors="replace")
            if found.exit_code == 0
            else ""
        )
        skills: list[Skill] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            doc = (await self._read(line)).decode(errors="replace")
            front, body = _parse_frontmatter(doc)
            name = front.get("name")
            description = front.get("description")
            if not name or not description:
                logger.warning("SshWorkspace: skip skill missing name/description: %s", line)
                continue
            skills.append(
                Skill(
                    dir=posixpath.dirname(line),
                    markdown=doc,
                    updated_at=0.0,
                )
            )
        return skills

    def _skill_targets(self, dirname: str) -> tuple[str, bytes]:
        """Local skill ``SKILL.md`` → (remote target path, payload bytes)."""
        skill_path = os.path.join(dirname, "SKILL.md")
        if not os.path.isfile(skill_path):
            raise FileNotFoundError(dirname)
        with open(skill_path, encoding="utf-8") as fh:
            content = fh.read()
        target = posixpath.join(
            self.remote_workdir,
            _REMOTE_SKILLS_DIR,
            os.path.basename(dirname),
            "SKILL.md",
        )
        return target, content.encode("utf-8")

    async def add_skill(self, dirname: str) -> None:
        """Copy a local skill ``SKILL.md`` dir into remote ``skills/``."""
        target, payload = self._skill_targets(dirname)
        await self._write(target, payload)

    async def remove_skill(self, dirname: str) -> None:
        """Remove a remote skill directory (KeyError when missing)."""
        target = posixpath.join(self.remote_workdir, _REMOTE_SKILLS_DIR, os.path.basename(dirname))
        res = await self._exec(f"rm -rf {shlex.quote(target)} && test ! -e {shlex.quote(target)}")
        if res.exit_code != 0:
            raise KeyError(dirname)

    # ── MCP management ──────────────────────────────────────────

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register an MCP server and persist to remote ``.mcp``."""
        async with self._mcp_lock:
            names = {m.name for m in self._mcps}
            if mcp_client.name in names:
                raise ValueError(f"MCP server {mcp_client.name} already exists")
            self._mcps.append(mcp_client)
            await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP server and persist to remote ``.mcp``."""
        async with self._mcp_lock:
            for idx, m in enumerate(self._mcps):
                if m.name == name:
                    self._mcps.pop(idx)
                    await self._save_mcp_file()
                    return
            raise ValueError(f"MCP server {name} not found")

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Adopt remote ``.mcp`` when present, else ``default_mcps``."""
        try:
            data = await self._read(posixpath.join(self.remote_workdir, ".mcp"))
            parsed = json.loads(data.decode("utf-8"))
            return [MCPClient.model_validate(m) for m in parsed]
        except Exception:  # noqa: BLE001
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to remote ``.mcp`` (best effort)."""
        try:
            payload = json.dumps(
                [m.model_dump() for m in self._mcps],
                indent=2,
                ensure_ascii=False,
            ).encode("utf-8")
            await self._write(posixpath.join(self.remote_workdir, ".mcp"), payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("SshWorkspace: failed to save remote .mcp: %s", e)

    async def _seed_skills(self) -> None:
        """Seed local ``skill_paths`` into remote ``skills/`` once.

        All skills travel in one batched write: a per-skill ssh round-trip
        (16 skills here) easily exceeds the 60s workspace prewarm budget of the
        chat pipeline on a high-latency remote host.
        """
        if not self.skill_paths:
            return
        try:
            existing = (await self._exec(f"ls -A {shlex.quote(posixpath.join(self.remote_workdir, _REMOTE_SKILLS_DIR))} 2>/dev/null"))
            if existing.stdout.strip():
                return  # already populated — respect user state
        except Exception:  # noqa: BLE001
            return

        pending: dict[str, bytes] = {}
        for path in self.skill_paths:
            try:
                target, payload = self._skill_targets(path)
            except Exception as e:  # noqa: BLE001
                logger.warning("SshWorkspace: skip skill %r: %s", path, e)
                continue
            pending[target] = payload
        if not pending:
            return
        try:
            await self._write_many(pending)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "SshWorkspace: failed to seed %d skill(s): %s", len(pending), e
            )

    # ── offload ─────────────────────────────────────────────────

    async def offload_context(self, session_id: str, msgs: list[Msg]) -> str:
        """Persist messages as JSONL under ``sessions/<id>/context.jsonl``."""
        base = posixpath.join(self.remote_workdir, _REMOTE_SESSIONS_DIR, session_id)
        path = posixpath.join(base, "context.jsonl")

        import copy as _copy

        copied = _copy.deepcopy(msgs)
        lines: list[str] = []
        for msg in copied:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(block.source, Base64Source):
                        block = await self._offload_data_block(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        existing = b""
        try:
            existing = await self._read(path)
        except (FileNotFoundError, OSError):
            pass
        await self._write(path, existing + ("\n".join(lines) + "\n").encode("utf-8"))
        return path

    async def offload_tool_result(self, session_id: str, tool_result: ToolResultBlock) -> str:
        """Persist a tool result as ``sessions/<id>/tool_result-<id>.txt``."""
        base = posixpath.join(self.remote_workdir, _REMOTE_SESSIONS_DIR, session_id)
        path = posixpath.join(base, f"tool_result-{tool_result.id}.txt")

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        d = await self._offload_data_block(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>"
                    )

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        await self._write(path, "".join(parts).encode("utf-8"))
        return path

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Extract a base64 DataBlock into ``data/`` and return a file:// URL."""
        if not isinstance(block.source, Base64Source):
            return block
        digest = hashlib.sha256(block.source.data.encode("utf-8")).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        rel = posixpath.join(self.remote_workdir, _REMOTE_DATA_DIR, f"{digest}{ext}")
        raw = base64.b64decode(block.source.data)
        await self._write(rel, raw)
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(url=AnyUrl(f"file://{rel}")),
        )


def _parse_frontmatter(doc: str) -> tuple[dict[str, str], str]:
    """Very small SKILL.md frontmatter parser (YAML-ish subset)."""
    front: dict[str, str] = {}
    rest = doc
    if doc.lstrip().startswith("---"):
        body = doc.lstrip()[3:]
        end = body.find("\n---")
        if end != -1:
            chunk = body[:end]
            for line in chunk.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    front[key.strip()] = val.strip()
            rest = body[end + 4:]
    return front, rest


# ─────────────────────────────────────────────────────────────────
# SSH inline stdio FastMCP server
# ─────────────────────────────────────────────────────────────────

# The runtime should be enough to run the sandbox MCP server.  On the
# platform host the gateway uses this project's python, which already
# ships ``mcp`` (a runtime dependency of agentscope).  The script is a
# single ``str`` so it compiles and unit-tests on the host while the
# gateway spawns ``python -c <script>`` locally.
_SSH_INLINE_SERVER = """\
import json
import hashlib
import os
import shlex as _sh
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

_HOST = os.environ.get("SSH_HOST", "")
_PORT = os.environ.get("SSH_PORT", "22")
_USER = os.environ.get("SSH_USER", "")
_AUTH_TYPE = os.environ.get("SSH_AUTH_TYPE", "password")
_PASSWORD_FILE = os.environ.get("SSH_PASSWORD_FILE", "")
_KEY_PATH = os.environ.get("SSH_KEY_PATH", "")
_ROOT = os.environ.get("SSH_REMOTE_WORKDIR", "/workspace")

mcp = FastMCP("sandbox")


def _ssh_args():
    args = [
        "ssh", "-p", str(_PORT),
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes" if _AUTH_TYPE == "key" else "BatchMode=no",
    ]
    if _AUTH_TYPE == "key" and _KEY_PATH:
        args += ["-i", _KEY_PATH]
    return args


def _target():
    if _USER:
        return f"{_USER}@{_HOST}"
    return _HOST


def _read_password():
    if not _PASSWORD_FILE:
        return ""
    try:
        with open(_PASSWORD_FILE, encoding="utf-8") as password_file:
            return password_file.read().rstrip("\\r\\n")
    except OSError:
        return ""


def _remote_command(argv):
    return " ".join(_sh.quote(str(arg)) for arg in argv)


def _call(argv, input_text=None, timeout=600):
    \"\"\"Run a remote command; ``argv`` excludes the ssh destination.

    ssh joins everything after the destination and lets the remote login shell
    re-parse it, so the argv is shell-quoted into a single command string.
    Passing the elements through unquoted would split ``bash -lc <snippet>``
    (only the first word reaches ``-c``) and multi-line ``python3 -c`` payloads.
    \"\"\"
    cmd = list(_ssh_args()) + [_target(), _remote_command(argv)]
    password_read_fd = None
    password_write_fd = None
    process = None
    try:
        if _AUTH_TYPE == "password":
            password = _read_password()
            if not password:
                return None
            password_read_fd, password_write_fd = os.pipe()
            cmd = ["sshpass", "-d", str(password_read_fd)] + cmd
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(password_read_fd,) if password_read_fd is not None else (),
        )
        if password_write_fd is not None:
            with os.fdopen(password_write_fd, "w", encoding="utf-8") as password_stream:
                password_stream.write(password)
                password_stream.flush()
            password_write_fd = None
        if password_read_fd is not None:
            os.close(password_read_fd)
            password_read_fd = None
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(cmd, -1, stdout, stderr)
        return None
    except FileNotFoundError:
        return None
    finally:
        for fd in (password_read_fd, password_write_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _remote_python_command(source, *args):
    # No "--" separator: after "-c" CPython treats "--" as a regular argument,
    # which would shift sys.argv and make sys.argv[1] resolve to "--".
    command = "python3 -c " + _sh.quote(source)
    if args:
        command += " " + " ".join(_sh.quote(str(arg)) for arg in args)
    return command


def _abspath(path):
    p = os.path.abspath(os.path.join(_ROOT, os.path.expanduser(path)))
    if not p.startswith(_ROOT):
        raise ValueError("path escapes workspace: " + path)
    return p


@mcp.tool()
def bash(command: str, cwd: str = ".") -> str:
    \"\"\"Run a shell command inside the remote sandbox.

    Args:
        command: The shell command line to execute.
        cwd: Relative directory (under the remote workdir) to run in.
    \"\"\"
    workdir = _ROOT if cwd in ("", ".") else _abspath(cwd)
    import shlex as _sh
    remote = f"cd {_sh.quote(workdir)} && {command}"
    proc = _call(["bash", "-lc", remote])
    if proc is None:
        return json.dumps({"ok": False, "error": "ssh unavailable or timed out"})
    return json.dumps({
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-100000:],
        "stderr": proc.stderr[-100000:],
    })


@mcp.tool()
def read(path: str, offset: int = 0, limit: int = 2000) -> str:
    \"\"\"Read a text file inside the remote sandbox.

    Args:
        path: File path (relative to the remote workdir).
        offset: Zero-based starting line.
        limit: Max number of lines to return.
    \"\"\"
    try:
        p = _abspath(path)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)})
    py = (
        "import json,sys\\n"
        "p=sys.argv[1]\\n"
        "try:\\n"
        " f=open(p,'r',encoding='utf-8',errors='replace')\\n"
        " lines=f.readlines(); f.close()\\n"
        "except FileNotFoundError:\\n"
        " sys.stdout.write('__DOSH_NOFILE__'); sys.exit(0)\\n"
        "except Exception as e:\\n"
        " sys.stdout.write('__DOSH_ERR__'+repr(e)); sys.exit(0)\\n"
        "sys.stdout.write(json.dumps({'lines': lines}))\\n"
    )
    proc = _call(
        ["bash", "-lc", _remote_python_command(py, p)],
        timeout=120,
    )
    if proc is None:
        return json.dumps({"ok": False, "error": "ssh unavailable or timed out"})
    out = proc.stdout.strip()
    if out == "__DOSH_NOFILE__":
        return json.dumps({"ok": False, "error": "no such file: " + path})
    if out.startswith("__DOSH_ERR__"):
        return json.dumps({"ok": False, "error": out.split("__DOSH_ERR__", 1)[1]})
    try:
        data = json.loads(out)
        lines = data.get("lines", [])
    except Exception:
        lines = []
    chunk = lines[offset: offset + limit]
    return json.dumps({
        "ok": True,
        "content": "".join(chunk),
        "total_lines": len(lines),
        "start_line": offset,
    })


@mcp.tool()
def write(path: str, content: str) -> str:
    \"\"\"Create or overwrite a text file inside the remote sandbox.

    Args:
        path: File path (relative to the remote workdir).
        content: Full file content to write.
    \"\"\"
    try:
        p = _abspath(path)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)})
    py = (
        "import os,sys\\n"
        "p=sys.argv[1]\\n"
        "data=sys.stdin.read()\\n"
        "os.makedirs(os.path.dirname(p),exist_ok=True)\\n"
        "open(p,'w',encoding='utf-8').write(data)\\n"
        "print('ok')\\n"
    )
    proc = _call(
        ["bash", "-lc", _remote_python_command(py, p)],
        input_text=content,
        timeout=120,
    )
    if proc is None:
        return json.dumps({"ok": False, "error": "ssh unavailable or timed out"})
    if proc.returncode != 0:
        return json.dumps({"ok": False, "error": proc.stderr.strip()[-100000:]})
    return json.dumps({"ok": True, "path": path})


@mcp.tool()
def glob(pattern: str, cwd: str = ".") -> str:
    \"\"\"List files under the remote workdir matching a glob pattern.

    Args:
        pattern: Glob pattern (relative to the remote workdir).
        cwd: Subdirectory to search in.
    \"\"\"
    try:
        base = _ROOT if cwd in ("", ".") else _abspath(cwd)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)})
    py = (
        "import glob,json,sys\\n"
        "pattern=sys.argv[1]\\n"
        "base=sys.argv[2]\\n"
        "try:\\n"
        " matches=glob.glob(pattern,root_dir=base,recursive=True)\\n"
        " print(json.dumps({'files':sorted(matches)[:500]}))\\n"
        "except Exception as e:\\n"
        " print('__DOSH_ERR__'+repr(e)); sys.exit(0)\\n"
    )
    proc = _call(
        ["bash", "-lc", _remote_python_command(py, pattern, base)],
        timeout=120,
    )
    if proc is None:
        return json.dumps({"ok": False, "error": "ssh unavailable or timed out"})
    out = proc.stdout.strip()
    if out.startswith("__DOSH_ERR__"):
        return json.dumps({"ok": False, "error": out.split("__DOSH_ERR__", 1)[1]})
    try:
        data = json.loads(out)
        files = data.get("files", [])
    except Exception:
        files = []
    return json.dumps({"ok": True, "files": files})


mcp.run(transport="stdio")
"""


def build_ssh_tool_mcp(
    *,
    host: str,
    port: int = 22,
    user: str = "",
    auth_type: str = "password",
    password_file_path: str | None = None,
    private_key_path: str | None = None,
    remote_workdir: str = SSH_REMOTE_WORKDIR,
) -> MCPClient:
    """Return the stateful STDIO MCP handshake for SSH-forwarded tools.

    The gateway spawns ``python -c <script>`` **locally** (on the
    platform host).  The script reaches the remote sandbox through its
    own ``ssh`` child process, so connection parameters must be
    injected through ``env`` — including the materialized private key
    file path (created by :class:`SshWorkspace` and cleaned up on
    :meth:`SshWorkspace.close`). Passwords use the same mode-600 file
    mechanism; the cleartext secret is never placed in the MCP child's
    argv or environment.
    """
    normalized_auth_type = (auth_type or "password").lower()
    env: dict[str, str] = {
        "SSH_HOST": host,
        "SSH_PORT": str(port),
        "SSH_USER": user or "",
        "SSH_AUTH_TYPE": normalized_auth_type,
        "SSH_REMOTE_WORKDIR": remote_workdir or SSH_REMOTE_WORKDIR,
    }
    if normalized_auth_type == "password":
        if not password_file_path:
            raise ValueError("password SSH MCP requires password_file_path")
        env["SSH_PASSWORD_FILE"] = password_file_path
    elif normalized_auth_type == "key" and private_key_path:
        env["SSH_KEY_PATH"] = private_key_path

    return MCPClient(
        name=SSH_MCP_NAME,
        is_stateful=True,
        mcp_config=StdioMCPConfig(
            command=sys.executable,
            args=["-c", _SSH_INLINE_SERVER],
            cwd=os.getcwd(),
            env=env,
        ),
    )
