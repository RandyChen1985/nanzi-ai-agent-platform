"""Regression tests for dev.sh daemon status and stop commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_repo(tmp_path: Path, pid: int) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    (repo / "frontend").mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()
    shutil.copy(ROOT / "dev.sh", repo / "dev.sh")
    (repo / "requirements.txt").write_text("example-package==1.0\n", encoding="utf-8")
    (repo / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
    (repo / ".env").write_text("API_SERVICE_PORT=8123\n", encoding="utf-8")
    (repo / ".dev-server.pid").write_text(
        f"pid={pid}\n"
        f"project_root={repo}\n"
        f"port=8123\n"
        f"python={repo / '.venv/bin/python'}\n",
        encoding="utf-8",
    )
    return repo, fake_bin, home


def _install_process_probes(fake_bin: Path, pid: int, project_root: Path, state_file: Path) -> None:
    _write_executable(
        fake_bin / "ps",
        f"""#!/bin/sh
case "$*" in
  *"stat="*)
    if [ -e '{state_file}' ]; then
      printf '%s\\n' 'S'
    else
      printf '%s\\n' 'Z'
    fi
    exit 0
    ;;
esac
if [ -e '{state_file}' ] && kill -0 {pid} 2>/dev/null; then
  printf '%s\\n' '{project_root}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8123 --reload'
fi
""",
    )
    _write_executable(
        fake_bin / "lsof",
        f"""#!/bin/sh
if [ -e '{state_file}' ] && kill -0 {pid} 2>/dev/null; then
  printf '%s\\n' '{pid}'
fi
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
printf '%s\\n' '{"status":"ok"}'
""",
    )


def _install_start_commands(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "uv",
        """#!/bin/sh
case "${1:-}" in
  --version)
    printf '%s\\n' 'uv 0.8.0'
    ;;
  venv)
    mkdir -p .venv/bin
    cat > .venv/bin/python <<'PYTHON'
#!/bin/sh
if [ "${1:-}" = "-c" ]; then
  printf '%s\\n' '3.11'
  exit 0
fi
exec sleep 30
PYTHON
    chmod +x .venv/bin/python
    ;;
  pip|python)
    ;;
esac
""",
    )
    # dev.sh 启动前会校验 Node.js / npm 版本（Vite 7 要求 >=22.12.0）。
    # 这里给满足阈值的假命令，避免测试依赖宿主机是否装了 Node.js。
    _write_executable(fake_bin / "node", "#!/bin/sh\nprintf '%s\\n' 'v22.12.0'\n")
    _write_executable(fake_bin / "npm", "#!/bin/sh\nprintf '%s\\n' '10.9.0'\n")
    _write_executable(fake_bin / "npx", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "lsof", "#!/bin/sh\nexit 1\n")
    _write_executable(
        fake_bin / "ps",
        """#!/bin/sh
case "$*" in
  *"stat="*) printf '%s\\n' 'S' ;;
esac
""",
    )


def _start_managed_probe_process(tmp_path: Path) -> tuple[subprocess.Popen[str], Path]:
    state_file = tmp_path / "managed-process.alive"
    state_file.write_text("alive\n", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys, time\n"
                "from pathlib import Path\n"
                "state = Path(sys.argv[1])\n"
                "def stop(*_args):\n"
                "    state.unlink(missing_ok=True)\n"
                "    raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "while True:\n"
                "    time.sleep(1)\n"
            ),
            str(state_file),
        ]
    )
    return process, state_file


def _run_command(repo: Path, fake_bin: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    return subprocess.run(
        ["bash", str(repo / "dev.sh"), *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_dev_sh_status_reports_pid_port_and_health_without_starting(tmp_path: Path):
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    try:
        repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
        _install_process_probes(fake_bin, managed_process.pid, repo, state_file)

        result = _run_command(repo, fake_bin, home, "status")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "后台服务正在运行" in result.stdout
        assert f"PID: {managed_process.pid}" in result.stdout
        assert "8123" in result.stdout and "已监听" in result.stdout
        assert "健康检查: 正常" in result.stdout
        assert "正在准备 uv" not in result.stdout
    finally:
        state_file.unlink(missing_ok=True)
        managed_process.terminate()
        managed_process.wait(timeout=5)


def test_dev_sh_stop_terminates_managed_process_and_removes_pid_file(tmp_path: Path):
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
    _install_process_probes(fake_bin, managed_process.pid, repo, state_file)

    result = _run_command(repo, fake_bin, home, "stop")

    try:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "后台服务已停止" in result.stdout
        assert not (repo / ".dev-server.pid").exists()
        managed_process.wait(timeout=5)
        assert "正在准备 uv" not in result.stdout
    finally:
        state_file.unlink(missing_ok=True)
        if managed_process.poll() is None:
            managed_process.terminate()
            managed_process.wait(timeout=5)


def test_dev_sh_status_reports_stale_pid_as_stopped(tmp_path: Path):
    repo, fake_bin, home = _prepare_repo(tmp_path, 99999999)
    _install_process_probes(fake_bin, 99999999, repo, tmp_path / "missing.alive")

    result = _run_command(repo, fake_bin, home, "status")

    assert result.returncode != 0
    assert "后台服务未运行" in result.stdout
    assert "PID 文件已失效" in result.stdout
    assert "正在准备 uv" not in result.stdout


def test_dev_sh_daemon_start_persists_pid_file(tmp_path: Path):
    repo, fake_bin, home = _prepare_repo(tmp_path, 99999999)
    pid_file = repo / ".dev-server.pid"
    pid_file.unlink()
    _install_start_commands(fake_bin)

    result = _run_command(repo, fake_bin, home, "-d")

    try:
        assert result.returncode == 0, result.stdout + result.stderr
        assert pid_file.is_file()
        pid_lines = dict(
            line.split("=", 1)
            for line in pid_file.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        assert pid_lines["pid"].isdigit()
        assert pid_lines["project_root"] == str(repo)
        assert pid_lines["python"] == str(repo / ".venv/bin/python")
        assert "PID 文件: .dev-server.pid" in result.stdout
    finally:
        if pid_file.is_file():
            pid_lines = dict(
                line.split("=", 1)
                for line in pid_file.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            pid = int(pid_lines["pid"])
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_dev_sh_stop_refuses_pid_file_from_another_project(tmp_path: Path):
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    try:
        repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
        _install_process_probes(fake_bin, managed_process.pid, repo, state_file)
        (repo / ".dev-server.pid").write_text(
            f"pid={managed_process.pid}\n"
            "project_root=/tmp/another-project\n"
            "port=8123\n"
            "python=/tmp/another-project/.venv/bin/python\n",
            encoding="utf-8",
        )

        result = _run_command(repo, fake_bin, home, "stop")

        assert result.returncode != 0
        assert "PID 文件不属于当前项目" in result.stderr
        assert managed_process.poll() is None
        assert (repo / ".dev-server.pid").is_file()
    finally:
        state_file.unlink(missing_ok=True)
        if managed_process.poll() is None:
            managed_process.terminate()
        managed_process.wait(timeout=5)


def test_dev_sh_rejects_unknown_command_without_bootstrap(tmp_path: Path):
    repo, fake_bin, home = _prepare_repo(tmp_path, 99999999)

    result = _run_command(repo, fake_bin, home, "restart")

    assert result.returncode != 0
    assert "未知参数：restart" in result.stderr
    assert "正在准备 uv" not in result.stdout


def _install_custom_probe_command(fake_bin: Path, pid: int, state_file: Path, command: str) -> None:
    """Install fake ps/lsof that report a listener with a custom command line."""
    _write_executable(
        fake_bin / "ps",
        f"""#!/bin/sh
case "$*" in
  *"stat="*)
    if [ -e '{state_file}' ]; then
      printf '%s\\n' 'S'
    else
      printf '%s\\n' 'Z'
    fi
    exit 0
    ;;
esac
if [ -e '{state_file}' ] && kill -0 {pid} 2>/dev/null; then
  printf '%s\\n' '{command}'
fi
""",
    )
    _write_executable(
        fake_bin / "lsof",
        f"""#!/bin/sh
if [ -e '{state_file}' ] && kill -0 {pid} 2>/dev/null; then
  printf '%s\\n' '{pid}'
fi
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
printf '%s\\n' '{"status":"ok"}'
""",
    )


def test_dev_sh_stop_matches_relative_python_cmd(tmp_path: Path):
    """A uvicorn started via a relative .venv path must still be recognized and stopped."""
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    try:
        repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
        # Command uses the relative venv path form (mirrors real `.venv/bin/python`).
        _install_custom_probe_command(
            fake_bin,
            managed_process.pid,
            state_file,
            ".venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8123 --reload",
        )

        result = _run_command(repo, fake_bin, home, "stop")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "后台服务已停止" in result.stdout, result.stdout
        assert not (repo / ".dev-server.pid").exists()
        managed_process.wait(timeout=5)
    finally:
        state_file.unlink(missing_ok=True)
        if managed_process.poll() is None:
            managed_process.terminate()
        managed_process.wait(timeout=5)


def test_dev_sh_stop_matches_wrapped_python_change_cmd(tmp_path: Path):
    """A uvicorn started through `python3 -m uvicorn` (no venv path in argv) is still managed."""
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    try:
        repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
        _install_custom_probe_command(
            fake_bin,
            managed_process.pid,
            state_file,
            "python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8123 --reload",
        )

        result = _run_command(repo, fake_bin, home, "stop")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "后台服务已停止" in result.stdout, result.stdout
        assert not (repo / ".dev-server.pid").exists()
        managed_process.wait(timeout=5)
    finally:
        state_file.unlink(missing_ok=True)
        if managed_process.poll() is None:
            managed_process.terminate()
        managed_process.wait(timeout=5)


def test_dev_sh_probe_uses_ss_when_lsof_absent(tmp_path: Path):
    """When lsof is unavailable, status should fall back to `ss` to find the listener PID."""
    managed_process, state_file = _start_managed_probe_process(tmp_path)
    try:
        repo, fake_bin, home = _prepare_repo(tmp_path, managed_process.pid)
        # Install ps/curl probes, but NO lsof in fake_bin. Provide an `ss` fake instead.
        _write_executable(
            fake_bin / "ps",
            f"""#!/bin/sh
case "$*" in
  *"stat="*)
    if [ -e '{state_file}' ]; then printf '%s\\n' 'S'; else printf '%s\\n' 'Z'; fi
    exit 0
    ;;
esac
if [ -e '{state_file}' ] && kill -0 {managed_process.pid} 2>/dev/null; then
  printf '%s\\n' '{repo}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8123 --reload'
fi
""",
        )
        _write_executable(
            fake_bin / "curl",
            """#!/bin/sh
printf '%s\\n' '{"status":"ok"}'
""",
        )
        _write_executable(
            fake_bin / "ss",
            f"""#!/bin/sh
if [ -e '{state_file}' ] && kill -0 {managed_process.pid} 2>/dev/null; then
  printf '%s\\n' 'users:(("python",pid={managed_process.pid},fd=3))'
fi
""",
        )
        # Ensure no lsof exists on the probe's PATH.
        assert not (fake_bin / "lsof").exists()

        result = _run_command(repo, fake_bin, home, "status")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "后台服务正在运行" in result.stdout, result.stdout
        assert f"PID: {managed_process.pid}" in result.stdout, result.stdout
    finally:
        state_file.unlink(missing_ok=True)
        if managed_process.poll() is None:
            managed_process.terminate()
        managed_process.wait(timeout=5)
