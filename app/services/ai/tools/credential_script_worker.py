"""隔离容器内的标准库执行程序；源码经 -c 传入，请求仅经私有 stdin。

本文件不能导入平台模块，也不能在异常中输出请求。宿主只接收已处理的协议结果。
"""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

MAX_REQUEST = 16 * 1024 * 1024
MAX_CAPTURE = 4 * 1024 * 1024
MAX_ARTIFACT = 1024 * 1024
MAX_ARTIFACTS = 8


def make_redactor(values):
    variants = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        variants.add(value)
        variants.add(json.dumps(value, ensure_ascii=True)[1:-1])
        variants.add(json.dumps(value, ensure_ascii=False)[1:-1])
    pattern = re.compile("|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))) if variants else None
    replacement = "[REDACTED]"
    if pattern and pattern.search(replacement):
        replacement = ""
    def redact(text):
        return pattern.sub(replacement, text) if pattern else text
    redact.contains = lambda text: bool(pattern and pattern.search(text))
    redact.replacement = replacement
    return redact


def clean_json(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [clean_json(item, redact) for item in value]
    if isinstance(value, dict):
        return {redact(key): clean_json(item, redact) for key, item in value.items()}
    raw = json.dumps(value)
    return redact.replacement if redact.contains(raw) else value


def clean_output(text, redact):
    try:
        parsed = json.loads(text)
        cleaned = clean_json(parsed, redact)
        output = json.dumps(cleaned, ensure_ascii=False)
        # 凭据可能本身就是 JSON / 跨节点片段；叶子替换不能处理时隐藏整段。
        if (redact.contains(text) and cleaned == parsed) or redact.contains(output):
            return ""
        return output
    except RecursionError:
        return ""
    except (ValueError, TypeError):
        return redact(text)


def contains_echo(text, redact):
    if redact.contains(text):
        return True
    try:
        value = json.loads(text)
        return clean_json(value, redact) != value
    except (ValueError, TypeError):
        return False
    except RecursionError:
        return True


def bounded_text(text, limit):
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def safe_path(root, relative):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative or "\x00" in relative:
        raise ValueError("invalid path")
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("symlink")
    return candidate


def run_child(command, *, cwd, env, stdin, timeout):
    # 管道流式限量：超限立即杀进程，不能先无界写满容器磁盘再检查。
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out, overflow, offset = False, False, 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, name, event in [(proc.stdin, "stdin", selectors.EVENT_WRITE),
                                      (proc.stdout, "stdout", selectors.EVENT_READ),
                                      (proc.stderr, "stderr", selectors.EVENT_READ)]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, event, name)
            while selector.get_map() and not overflow:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == "stdin":
                        try:
                            offset += os.write(key.fd, stdin[offset:offset + 65536]) if offset < len(stdin) else 0
                        except BrokenPipeError:
                            offset = len(stdin)
                        if offset == len(stdin):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        if len(outputs[key.data]) + len(data) > MAX_CAPTURE:
                            overflow = True
                            break
                        outputs[key.data].extend(data)
            if not timed_out and not overflow:
                try:
                    proc.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
    finally:
        # 正常结束也终止遗留孙进程，导出期间不允许后台进程继续改写文件。
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            pipe.close()
    if timed_out:
        return None, "", "脚本执行超时，已终止", True
    if overflow:
        return 1, "", "脚本输出超过凭据执行通道上限", False
    return proc.returncode, outputs["stdout"].decode("utf-8", "replace"), outputs["stderr"].decode("utf-8", "replace"), False


def execute(request):
    env = request["env"]
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and "\x00" not in v for k, v in env.items()):
        raise ValueError("invalid environment")
    redact = make_redactor(env.values())
    with tempfile.TemporaryDirectory(prefix="nanzi-credential-") as temporary:
        root = Path(temporary)
        work = root / "work"
        work.mkdir()
        files = request.get("files", {})
        if len(files) > 256:
            raise ValueError("too many inputs")
        for relative, encoded in files.items():
            path = safe_path(work, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded, validate=True))
        script = root / "script.py"
        script.write_text(request["code"], encoding="utf-8")
        libs = root / "libs"
        libs.mkdir()
        # 不继承服务的环境；安装依赖时不含请求中的凭据。
        clean_env = {"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(root), "TMPDIR": str(root)}
        requirements = request.get("requirements", [])
        if requirements:
            req = root / "requirements.txt"
            req.write_text("\n".join(requirements), encoding="utf-8")
            code, _, _, timed_out = run_child(
                [sys.executable, "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--target", str(libs), "-r", str(req)],
                cwd=root, env=clean_env, stdin=b"", timeout=min(120, float(request["timeout"])),
            )
            if code != 0 or timed_out:
                return {"exit_code": 1, "stdout": "", "stderr": "凭据脚本依赖安装失败，未执行脚本", "timed_out": timed_out, "artifacts": []}
        wrapper = "import runpy,sys; p=sys.argv[2]; sys.path.insert(0,sys.argv[1]); sys.argv=[p]; runpy.run_path(p,run_name='__main__')"
        code, stdout, stderr, timed_out = run_child(
            [sys.executable, "-I", "-u", "-c", wrapper, str(libs), str(script)],
            cwd=work, env={**clean_env, **env},
            stdin=json.dumps(request["params"], ensure_ascii=False).encode(), timeout=float(request["timeout"]),
        )
        artifacts, blocked = [], 0
        try:
            parsed = json.loads(stdout)
        except (ValueError, TypeError):
            parsed = None
        declared = parsed.get("artifacts", []) if isinstance(parsed, dict) else []
        if not isinstance(declared, list):
            declared = []
        for index, relative in enumerate(declared):
            try:
                if code != 0 or index >= MAX_ARTIFACTS or not isinstance(relative, str) or redact(relative) != relative:
                    raise ValueError("rejected artifact")
                path = safe_path(work, relative)
                if not path.is_file() or path.stat().st_size > MAX_ARTIFACT:
                    raise ValueError("unsupported artifact")
                data = path.read_bytes()
                text = data.decode("utf-8")
                # 未实现格式内扫描的二进制/压缩文件不发布。
                if "\x00" in text or contains_echo(text, redact):
                    raise ValueError("unsafe artifact")
                artifacts.append({"path": relative, "data": base64.b64encode(data).decode("ascii")})
            except (ValueError, OSError, UnicodeError):
                blocked += 1
        if isinstance(parsed, dict) and "artifacts" in parsed:
            parsed["artifacts"] = [item["path"] for item in artifacts]
            stdout = json.dumps(parsed, ensure_ascii=False)
        # 检查完整输出后才截断，避免截断导致无法匹配完整秘密。
        stdout = clean_output(stdout, redact)
        stderr = clean_output(stderr, redact)
        return {"exit_code": code, "stdout": bounded_text(stdout, 80 * 1024), "stderr": bounded_text(stderr, 8 * 1024),
                "timed_out": timed_out, "artifacts": artifacts, "blocked_artifacts": blocked}


def main():
    try:
        size = int(sys.stdin.buffer.readline(32))
        if not 0 < size <= MAX_REQUEST:
            raise ValueError("invalid length")
        data = sys.stdin.buffer.read(size)
        if len(data) != size:
            raise ValueError("incomplete request")
        result = execute(json.loads(data))
    except Exception:
        result = {"exit_code": 1, "stdout": "", "stderr": "凭据脚本执行失败", "timed_out": False, "artifacts": []}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
