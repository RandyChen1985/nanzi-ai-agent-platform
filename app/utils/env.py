"""运行环境探测：判断当前后端进程跑在容器（Docker / k8s Pod）还是宿主机。

Bash 工具由后端进程直接 `subprocess` 执行（`CancellableLocalBackend`），因此
「Bash 跑在哪」==「后端跑在哪」，是进程级恒定值。本模块基于文件系统特征在
首次调用时探测一次并缓存，供 SSE 事件流把结果下发给前端用于环境风险横幅。
"""
from __future__ import annotations

import os
from typing import Literal

EnvKind = Literal["docker", "host"]

# cgroup 中的容器标记：docker 与 k8s Pod（kubepods）都视为容器，避免误标为宿主机
_CGROUP_MARKERS = ("docker", "kubepods")
_DOCKERENV_PATH = "/.dockerenv"

_cache: EnvKind | None = None


def _cgroup_has_container_marker() -> bool:
    """读 /proc/self/cgroup，命中 docker/kubepods 即视为容器。"""
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except OSError:
        return False
    return any(marker in content for marker in _CGROUP_MARKERS)


def detect_env() -> EnvKind:
    """判定当前环境为容器还是宿主机。

    - 存在 `/.dockerenv` 或 cgroup 含 docker/kubepods → ``docker``
    - 其余（含探测异常）→ ``host``（静默降级，由调用方记录 debug 日志）
    """
    if os.path.exists(_DOCKERENV_PATH):
        return "docker"
    if _cgroup_has_container_marker():
        return "docker"
    return "host"


def get_env() -> EnvKind:
    """返回探测结果，进程级只探一次并缓存。"""
    global _cache
    if _cache is None:
        _cache = detect_env()
    return _cache


def reset_probe_cache() -> None:
    """清空进程级缓存（主要供测试重置状态）。"""
    global _cache
    _cache = None


# --- Kubernetes in-cluster 运行环境探测 -------------------------------

# k8s 会给每个 Pod 注入的服务发现入口变量；SA token 是 ServiceAccount 自动挂载的标识文件
_K8S_SERVICE_HOST_ENV = "KUBERNETES_SERVICE_HOST"
_K8S_SERVICE_ACCOUNT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"

_k8s_cache: bool | None = None


def detect_in_k8s() -> bool:
    """判定当前进程是否运行在 Kubernetes Pod 内。

    K8s 会为每个 Pod 注入 ``KUBERNETES_SERVICE_HOST`` 环境变量并自动挂载
    ServiceAccount token。平台后端作为 Pod 部署时这两者必有其一；而直接部署在
    宿主机或普通 Docker 容器时均不存在。二者任一命中即判定为 in-cluster。
    """
    if os.environ.get(_K8S_SERVICE_HOST_ENV):
        return True
    try:
        return os.path.exists(_K8S_SERVICE_ACCOUNT_TOKEN)
    except OSError:
        return False


def in_k8s() -> bool:
    """返回进程级「运行在 K8s Pod 内」探测结果，只探一次并缓存。"""
    global _k8s_cache
    if _k8s_cache is None:
        _k8s_cache = detect_in_k8s()
    return _k8s_cache


def reset_in_k8s_cache() -> None:
    """清空 k8s 探测缓存（主要供测试重置状态）。"""
    global _k8s_cache
    _k8s_cache = None


# --- Docker daemon 可用性探测 -----------------------------------------

# 平台 Docker 沙箱通过 docker SDK `from_env()` 连接 daemon：优先取 DOCKER_HOST 环境变量，
# 未设置时回落到宿主机 UNIX socket /var/run/docker.sock（DooD 亦挂载该路径到 Pod）。
_DOCKER_HOST_ENV = "DOCKER_HOST"
_DOCKER_UNIX_SOCKET = "/var/run/docker.sock"

_docker_cache: bool | None = None


def detect_docker_available() -> bool:
    """判定当前进程能否连到 Docker daemon 以支持 docker 沙箱策略。

    docker SDK 的标准连接语义：显式配置 ``DOCKER_HOST``（可指向 TCP daemon 或
    挂载进来的 socket）即视为可用；否则仅在默认 socket
    ``/var/run/docker.sock`` 存在时判定可用。宿主机/普通 Docker 默认命中 socket；
    K8s Pod 未挂载 socket 且未配 DOCKER_HOST 时判为不可用。
    """
    if os.environ.get(_DOCKER_HOST_ENV):
        return True
    try:
        return os.path.exists(_DOCKER_UNIX_SOCKET)
    except OSError:
        return False


def docker_available() -> bool:
    """返回进程级「Docker daemon 可用」探测结果，只探一次并缓存。"""
    global _docker_cache
    if _docker_cache is None:
        _docker_cache = detect_docker_available()
    return _docker_cache


def reset_docker_cache() -> None:
    """清空 docker 可用性缓存（主要供测试重置状态）。"""
    global _docker_cache
    _docker_cache = None
