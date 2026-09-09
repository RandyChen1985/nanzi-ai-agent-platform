"""环境探测（bash 运行环境横幅）后端单测。

验证 `app/utils/env.py`：可信信号（/.dockerenv、/proc/self/cgroup
内容）命中任一即判为 docker；普通 `/app` 目录不能单独证明容器；
探测异常静默降级 host；
`get_env` 进程级缓存只探一次；`reset_probe_cache` 可重置缓存。
"""
import pytest

from app.utils import env


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前置清空模块级缓存，避免探测结果互相污染。"""
    env.reset_probe_cache()
    yield
    env.reset_probe_cache()


def test_dockerenv_present_is_docker(monkeypatch):
    monkeypatch.setattr(env.os.path, "exists", lambda p: p == env._DOCKERENV_PATH)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)

    assert env.detect_env() == "docker"


def test_app_dir_alone_is_not_docker(monkeypatch):
    # 宿主机也可能存在 /app，普通目录不能单独证明当前进程运行在容器内。
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env, "_cgroup_has_container_marker", lambda: False)

    assert env.detect_env() == "host"


def test_cgroup_docker_marker_is_docker(monkeypatch):
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(
        env,
        "_cgroup_has_container_marker",
        lambda: True,
    )

    assert env.detect_env() == "docker"


def test_cgroup_kubepods_marker_is_docker(monkeypatch):
    # k8s Pod 的 cgroup 含 kubepods，也应视为容器
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(
        env,
        "_cgroup_has_container_marker",
        lambda: any(m in ("0::/kubepods.slice/burstable/x") for m in env._CGROUP_MARKERS),
    )

    assert env.detect_env() == "docker"


def test_no_markers_is_host(monkeypatch):
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(env, "_cgroup_has_container_marker", lambda: False)

    assert env.detect_env() == "host"


def test_cgroup_read_failure_silently_falls_back_to_host(monkeypatch):
    """cgroup 读取失败被吞掉（返回 False），整体静默降级 host，不抛异常。"""
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)

    import builtins

    def _boom(*_a, **_k):
        raise OSError("permission denied")

    monkeypatch.setattr(builtins, "open", _boom)

    assert env._cgroup_has_container_marker() is False
    assert env.detect_env() == "host"


def test_get_env_caches_single_probe(monkeypatch):
    """get_env 进程级只探一次并缓存。"""
    calls = []

    real_detect = env.detect_env

    def _counted():
        calls.append(1)
        return real_detect()

    monkeypatch.setattr(env, "detect_env", _counted)

    first = env.get_env()
    second = env.get_env()

    assert first == second
    assert len(calls) == 1


def test_reset_probe_cache_forces_reprobe(monkeypatch):
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(env.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(env, "_cgroup_has_container_marker", lambda: False)

    assert env.get_env() == "host"
    env.reset_probe_cache()
    # 重置后即便外部信号变化也应重新探测（此处 host 判定仍成立）
    assert env.get_env() == "host"


# --- Kubernetes in-cluster 探测：detect_in_k8s / in_k8s -----------------


@pytest.fixture
def _reset_k8s_cache():
    """前置清空 k8s 进程级缓存，避免探测结果互相污染。"""
    env.reset_in_k8s_cache()
    yield
    env.reset_in_k8s_cache()


def test_in_k8s_via_service_host_env(monkeypatch, _reset_k8s_cache):
    """KUBERNETES_SERVICE_HOST 存在（K8s 注入每个 Pod 的服务发现变量）即判为 in-cluster。"""
    monkeypatch.setenv(env._K8S_SERVICE_HOST_ENV, "10.96.0.1")
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)

    assert env.detect_in_k8s() is True


def test_in_k8s_via_serviceaccount_token(monkeypatch, _reset_k8s_cache):
    """无 service env，但存在 ServiceAccount 自动挂载的 token 文件也判为 in-cluster。"""
    monkeypatch.delenv(env._K8S_SERVICE_HOST_ENV, raising=False)

    def _exists(p):
        return p == env._K8S_SERVICE_ACCOUNT_TOKEN

    monkeypatch.setattr(env.os.path, "exists", _exists)

    assert env.detect_in_k8s() is True


def test_not_in_k8s_when_neither_signal_present(monkeypatch, _reset_k8s_cache):
    """宿主机 / 普通 Docker：既无 service env 也无 SA token 文件 → 判定非 in-cluster。"""
    monkeypatch.delenv(env._K8S_SERVICE_HOST_ENV, raising=False)
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)

    assert env.detect_in_k8s() is False


def test_not_in_k8s_when_env_var_absent_but_any_other_file(monkeypatch, _reset_k8s_cache):
    """env 缺失时，仅当命中 SA token 这个特定路径才算 in-cluster，普通文件不算。"""
    monkeypatch.delenv(env._K8S_SERVICE_HOST_ENV, raising=False)

    def _exists(p):
        return p != env._K8S_SERVICE_ACCOUNT_TOKEN  # /app 等普通文件存在，但 SA token 不存在

    monkeypatch.setattr(env.os.path, "exists", _exists)

    assert env.detect_in_k8s() is False


def test_in_k8s_caching_single_probe(monkeypatch, _reset_k8s_cache):
    """in_k8s 进程级只探一次并缓存。"""
    calls = []
    real = env.detect_in_k8s

    def _counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(env, "detect_in_k8s", _counted)

    first = env.in_k8s()
    second = env.in_k8s()

    assert first == second
    assert len(calls) == 1


def test_reset_in_k8s_cache_forces_reprobe(monkeypatch, _reset_k8s_cache):
    monkeypatch.delenv(env._K8S_SERVICE_HOST_ENV, raising=False)
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    assert env.in_k8s() is False

    # 重置缓存后，若新增 service env，应能重新探测到 in-cluster
    monkeypatch.setenv(env._K8S_SERVICE_HOST_ENV, "10.96.0.1")
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    env.reset_in_k8s_cache()
    assert env.in_k8s() is True


# --- Docker daemon 可用性：detect_docker_available / docker_available ---------


@pytest.fixture
def _reset_docker_cache():
    """前置清空 docker 可用性缓存，避免探测结果互相污染。"""
    env.reset_docker_cache()
    yield
    env.reset_docker_cache()


def test_docker_available_via_docker_host_env(monkeypatch, _reset_docker_cache):
    """显式配置 DOCKER_HOST（指向 TCP daemon 或挂入的 socket）即判定可用。"""
    monkeypatch.setenv(env._DOCKER_HOST_ENV, "tcp://1.2.3.4:2375")
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)

    assert env.detect_docker_available() is True


def test_docker_available_via_default_socket(monkeypatch, _reset_docker_cache):
    """未配 DOCKER_HOST，但存在 /var/run/docker.sock（宿主机 / DooD 挂载）即判定可用。"""

    def _exists(p):
        return p == env._DOCKER_UNIX_SOCKET

    monkeypatch.delenv(env._DOCKER_HOST_ENV, raising=False)
    monkeypatch.setattr(env.os.path, "exists", _exists)

    assert env.detect_docker_available() is True


def test_docker_unavailable_when_no_host_and_no_socket(monkeypatch, _reset_docker_cache):
    """K8s Pod 未挂 docker.sock 且未配 DOCKER_HOST → 判定不可用。"""
    monkeypatch.delenv(env._DOCKER_HOST_ENV, raising=False)
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)

    assert env.detect_docker_available() is False


def test_docker_available_caching_single_probe(monkeypatch, _reset_docker_cache):
    """docker_available 进程级只探一次并缓存。"""
    calls = []
    real = env.detect_docker_available

    def _counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(env, "detect_docker_available", _counted)

    first = env.docker_available()
    second = env.docker_available()

    assert first == second
    assert len(calls) == 1


def test_reset_docker_cache_forces_reprobe(monkeypatch, _reset_docker_cache):
    monkeypatch.delenv(env._DOCKER_HOST_ENV, raising=False)
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    assert env.docker_available() is False

    # 重置缓存后，若新增 DOCKER_HOST，应能重新探测到可用
    monkeypatch.setenv(env._DOCKER_HOST_ENV, "tcp://1.2.3.4:2375")
    monkeypatch.setattr(env.os.path, "exists", lambda p: False)
    env.reset_docker_cache()
    assert env.docker_available() is True
