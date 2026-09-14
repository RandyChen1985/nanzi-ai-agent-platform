"""NanZi-specific K8sWorkspace mount and lifecycle adapter.

Adapts AgentScope's K8sWorkspace to support:
1. Shared cluster PVC with subPath (matching Docker's user workspace & public docs behavior).
   The sandbox workdir ``/workspace`` binds the whole per-user workspace
   ``agent_workspaces/{user_key}`` via subPath, so Bash inside the sandbox sees
   and operates the exact same files as the host file tools (Docker-aligned).
2. Local pre-creation of subPath directories to prevent Kubernetes MountVolume failures.
3. Identity guard: Prohibit unauthenticated users from mounting shared PVC root.
4. Non-privileged namespace creation graceful fallback (handling 403 Forbidden).
5. Automatic resource requests + limits configuration to prevent scheduler over-allocation.
6. Dynamic isolated PVC creation per user/session with optional delete_pvc_on_close.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_K8S_CPU_REQUEST = "100m"
DEFAULT_K8S_MEMORY_REQUEST = "128Mi"

#: Namespace the platform itself is deployed in (matches ``k8s_deploy/namespace.yaml``).
#: Kubernetes PVCs are namespace-scoped, so sharing the platform data volume with
#: sandbox Pods REQUIRES the sandbox namespace to equal this one.
DEFAULT_PLATFORM_NAMESPACE = "nanzi-ai-agent"

#: Legacy seeded default for ``sandbox_k8s_namespace``. It predates the shared-PVC
#: alignment and is treated as "unset" so existing installs follow the platform
#: namespace instead of silently mounting an isolated empty volume.
LEGACY_DEFAULT_K8S_NAMESPACE = "agent-sandboxes"

#: Env overrides, in priority order, for the platform's own namespace.
_PLATFORM_NAMESPACE_ENV_KEYS = ("NANZI_PLATFORM_NAMESPACE", "POD_NAMESPACE", "K8S_NAMESPACE")

#: In-cluster file every Pod gets; the authoritative way to know our namespace.
_SERVICEACCOUNT_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

#: ``sandbox_k8s_existing_pvc`` values that explicitly request the isolated
#: per-workspace empty PVC instead of sharing the platform data volume.
ISOLATED_PVC_SENTINELS = frozenset({"none", "disabled", "off", "false", "-"})

#: How long a platform-PVC auto-detection result stays cached (seconds).
_PLATFORM_PVC_CACHE_TTL_SECONDS = 300


def resolve_platform_namespace() -> str:
    """Return the Kubernetes namespace the platform itself runs in.

    Resolution order: explicit env override -> in-cluster ServiceAccount
    namespace file -> :data:`DEFAULT_PLATFORM_NAMESPACE`.
    """
    for key in _PLATFORM_NAMESPACE_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        with open(_SERVICEACCOUNT_NAMESPACE_FILE, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
            if value:
                return value
    except OSError:
        pass
    return DEFAULT_PLATFORM_NAMESPACE


def resolve_sandbox_namespace(configured: str | None) -> str:
    """Normalise ``sandbox_k8s_namespace`` into the effective sandbox namespace.

    An empty value *or* the legacy seeded default (``agent-sandboxes``) means
    "follow the platform namespace", which is what makes the shared-PVC workspace
    mount work out of the box (matching Docker sandbox behaviour). Any other value
    is treated as an explicit admin override.
    """
    value = (configured or "").strip()
    if not value or value == LEGACY_DEFAULT_K8S_NAMESPACE:
        return resolve_platform_namespace()
    return value


def _platform_pod_name() -> str:
    """Best-effort Pod name of the platform process itself.

    Inside Kubernetes a Pod's hostname defaults to its Pod name, so
    ``socket.gethostname()`` works without configuring a downward-API env var;
    ``POD_NAME``/``HOSTNAME`` are honoured first when present.
    """
    for key in ("POD_NAME", "HOSTNAME"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        return socket.gethostname().strip()
    except OSError:
        return ""


def _claim_name_for_data_mount(pod: Any, data_dir: str) -> str | None:
    """Return the PVC claim name backing ``data_dir`` in a Pod spec, if any.

    Pure helper (no I/O) so the mapping stays unit-testable: locate the container
    volumeMount whose ``mount_path`` equals the platform data dir, resolve it to
    the Pod volume of the same name, and return that volume's
    ``persistent_volume_claim.claim_name``. Returns ``None`` when the mount is
    absent or is not backed by a PVC (emptyDir / hostPath / no data volume).
    """
    spec = getattr(pod, "spec", None)
    if spec is None:
        return None

    target = os.path.normpath(data_dir) if data_dir else ""
    if not target:
        return None

    try:
        volumes = getattr(spec, "volumes", None) or []
        for container in getattr(spec, "containers", None) or []:
            for mount in getattr(container, "volume_mounts", None) or []:
                mount_path = getattr(mount, "mount_path", None)
                if not mount_path or os.path.normpath(mount_path) != target:
                    continue
                volume_name = getattr(mount, "name", None)
                for volume in volumes:
                    if getattr(volume, "name", None) != volume_name:
                        continue
                    claim = getattr(volume, "persistent_volume_claim", None)
                    claim_name = getattr(claim, "claim_name", None) if claim else None
                    return (str(claim_name).strip() or None) if claim_name else None
    except (TypeError, AttributeError, ValueError):
        # Defensive: the helper is best-effort and must never break sandbox startup.
        return None
    return None


#: data_dir -> (monotonic timestamp, detected claim name or None)
_platform_pvc_cache: dict[str, tuple[float, str | None]] = {}


def reset_platform_pvc_cache() -> None:
    """Clear the cached platform-PVC detection (used by tests and after upgrades)."""
    _platform_pvc_cache.clear()


async def detect_platform_data_pvc() -> str | None:
    """Best-effort: PVC claim name backing the platform's own data directory.

    Reads the platform Pod's own spec and returns the claim mounted at the
    platform data dir (``/app/data`` in the standard deployment). The claim name
    is deployment-specific, so auto-detection is used instead of hardcoding it —
    that keeps custom PVC names working without extra configuration.

    Returns ``None`` when the platform does not run in Kubernetes with a
    PVC-backed data directory, when permissions are missing, or on any API error.
    Never raises: callers fall back to the isolated per-workspace PVC.
    """
    try:
        from app.utils.fs_paths import get_data_base_dir

        data_dir = get_data_base_dir()
    except Exception:  # noqa: BLE001 - fall back to the standard in-container path
        data_dir = "/app/data"

    now = time.monotonic()
    cached = _platform_pvc_cache.get(data_dir)
    if cached is not None and (now - cached[0]) < _PLATFORM_PVC_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        claim_name = await _read_own_pvc_claim(data_dir)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort, never fatal
        logger.warning("[k8s_workspace] Platform data PVC auto-detection failed: %s", exc)
        claim_name = None
    _platform_pvc_cache[data_dir] = (now, claim_name)
    return claim_name


async def _read_own_pvc_claim(data_dir: str) -> str | None:
    """Read the platform's own Pod spec and extract the claim backing ``data_dir``."""
    pod_name = _platform_pod_name()
    if not pod_name:
        return None

    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_async_config
    except ImportError:
        logger.info(
            "[k8s_workspace] kubernetes-asyncio not installed; skip platform PVC auto-detection"
        )
        return None

    namespace = resolve_platform_namespace()
    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:  # noqa: BLE001 - detection is best-effort
            logger.info(
                "[k8s_workspace] No cluster credentials; skip platform PVC auto-detection: %s",
                exc,
            )
            return None

    try:
        async with k8s_client.ApiClient() as api_client:
            core_v1 = k8s_client.CoreV1Api(api_client)
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        logger.warning(
            "[k8s_workspace] Failed to auto-detect platform data PVC from pod %s/%s: %s",
            namespace,
            pod_name,
            exc,
        )
        return None

    claim_name = _claim_name_for_data_mount(pod, data_dir)
    if claim_name:
        logger.info(
            "[k8s_workspace] Auto-detected platform data PVC %r (mount %s) for sandbox sharing",
            claim_name,
            data_dir,
        )
    return claim_name

def parse_existing_pvc_config(configured: str | None) -> tuple[str | None, bool]:
    """Split ``sandbox_k8s_existing_pvc`` into ``(explicit_claim, isolation_requested)``.

    - blank                -> ``(None, False)``  : auto-detect the platform PVC;
    - ``none``/``disabled``-> ``(None, True)``   : force the isolated empty PVC;
    - any other value      -> ``(value, False)`` : use that shared PVC.
    """
    value = (configured or "").strip()
    if not value:
        return None, False
    if value.lower() in ISOLATED_PVC_SENTINELS:
        return None, True
    return value, False


async def resolve_shared_pvc(configured: str | None) -> dict[str, Any]:
    """Resolve the effective shared PVC for the sandbox workspace mount.

    ``source`` is one of:

    - ``configured``  : admin explicitly named a shared PVC;
    - ``auto``        : blank config, platform data PVC auto-detected;
    - ``isolated``    : admin explicitly requested the isolated empty PVC;
    - ``unavailable`` : blank config and auto-detection found no platform PVC.
    """
    explicit, isolation_requested = parse_existing_pvc_config(configured)
    if explicit:
        return {"pvc": explicit, "source": "configured"}
    if isolation_requested:
        return {"pvc": None, "source": "isolated"}

    detected = await detect_platform_data_pvc()
    if detected:
        return {"pvc": detected, "source": "auto"}
    return {"pvc": None, "source": "unavailable"}


def evaluate_k8s_workspace_mount_config(
    *,
    namespace: str | None,
    pvc: str | None,
    pvc_source: str = "unavailable",
    platform_namespace: str | None = None,
) -> list[str]:
    """Return admin-facing warnings about the K8s sandbox workspace mount config.

    These describe configurations that silently break the "sandbox sees the user
    workspace, like Docker" contract, so they are surfaced both in logs and in the
    RBAC self-check API response.
    """
    warnings: list[str] = []
    platform_ns = (platform_namespace or resolve_platform_namespace()).strip()
    effective_ns = resolve_sandbox_namespace(namespace)

    if not pvc:
        # ``isolated`` is an explicit admin choice, so it is not a warning.
        if pvc_source != "isolated":
            warnings.append(
                "未能确定共享数据卷（sandbox_k8s_existing_pvc 留空且无法自动探测平台数据 PVC，"
                "或平台未以 PVC 方式运行）：K8s 沙箱将使用每个工作区独立创建的空 PVC，"
                "沙箱内 /workspace 看不到用户工作区（与 Docker 沙箱行为不一致）。"
                "如需对齐 Docker，请把 sandbox_k8s_existing_pvc 显式设为平台主 PVC 名称，"
                f"并保持 sandbox_k8s_namespace 与平台命名空间（{platform_ns}）一致。"
            )
    elif effective_ns != platform_ns:
        warnings.append(
            f"sandbox_k8s_namespace（{effective_ns}）与平台命名空间（{platform_ns}）不一致，"
            "但已配置共享 PVC。Kubernetes PVC 是命名空间级的，"
            "沙箱 Pod 无法引用其他命名空间的 PVC，Pod 会因找不到该 PVC 而一直 Pending。"
            f"请将 sandbox_k8s_namespace 设为 {platform_ns}，或留空以自动跟随平台命名空间。"
        )
    return warnings


class K8sSandboxUnavailableError(RuntimeError):
    """Normalized K8s sandbox unavailability error with user-friendly diagnosis."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "k8s_sandbox_unavailable",
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.user_message = user_message or (
            "Kubernetes 沙箱不可用，代码未执行。请检查集群网络、命名空间权限（RBAC）与镜像拉取状态。"
        )


def _ensure_local_subpath_dirs(
    user_key: str | None,
    candidate_roots: list[str] | None = None,
) -> None:
    """Pre-create subPath directories on the local filesystem if the shared volume is mounted locally.

    ``subPath`` paths are relative to the shared PVC's root (e.g. the
    platform data dir ``/app/data``), so the directories must physically
    exist *inside the PVC* before kubelet mounts them, or the Pod stays in
    ``ContainerCreating`` / MountVolume failure. This is only effective when
    the PVC is actually mounted on this platform container (hostPath /
    local PVC); it is a best-effort pre-flight helper, never a guarantee for
    remotely-bound volumes.
    """
    search_dirs: list[str] = []
    if candidate_roots:
        search_dirs.extend(candidate_roots)
    search_dirs.extend([
        os.environ.get("DATA_DIR", ""),
        "/app/data",
        "data",
    ])

    # Normalise real paths so genuine platform roots are not visited twice.
    seen: set[str] = set()
    for candidate in search_dirs:
        if not candidate:
            continue
        try:
            abs_cand = os.path.realpath(os.path.abspath(candidate))
        except (TypeError, ValueError):
            continue
        if abs_cand in seen:
            continue
        seen.add(abs_cand)
        try:
            if not os.path.isdir(abs_cand):
                continue
            if user_key:
                user_workdir = os.path.join(
                    abs_cand, "agent_workspaces", user_key
                )
                os.makedirs(user_workdir, exist_ok=True)
            docs_dir = os.path.join(abs_cand, "docs")
            os.makedirs(docs_dir, exist_ok=True)
            logger.debug(
                "[k8s_workspace] Successfully ensured local subPath directories in %s",
                abs_cand,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[k8s_workspace] Failed to ensure local subPath directory in candidate %s: %s",
                candidate,
                exc,
            )


def ensure_k8s_public_data_subdirs() -> str:
    """K8s 策略专用:确保后端数据根下的公共目录存在。

    只在 K8s 沙箱策略分支(``_policy_k8s_workspace``)调用,刻意不放在
    ``app.utils.fs_paths.get_data_base_dir`` 全局路径里——那会让 Docker
    策略的 ``_resolve_docker_public_docs_source`` 把"空 docs 目录"误判为
    "已挂载公共文档"(Docker 以 ``isdir(data_root/docs)`` 作存在性判断)。

    这里补建的是后端容器 ``/app/data``(K8s 下为 PVC 挂载点)下的 ``docs``:
    - 后端文件工具(Grep/Glob/Read)直接访问 ``<data_root>/docs``;
    - 共享 PVC 模式下沙箱 ``subPath: docs`` 也指向同一目录。
    空 PVC 从未初始化时该目录可能不存在,导致 Grep 抛
    ``Directory not found: /app/data/docs``、或沙箱 MountVolume 失败。
    只补建目录骨架,内容(公共手册)由 k8s_deploy/data-init-job 一次性同步。
    """
    from app.utils.fs_paths import get_data_base_dir

    data_root = get_data_base_dir()
    for sub_name in ("docs",):
        try:
            os.makedirs(os.path.join(data_root, sub_name), exist_ok=True)
        except OSError as exc:  # noqa: BLE001 - 只读/无权限时静默,由上层守卫提示
            logger.warning(
                "[k8s_workspace] Failed to ensure public subdir %s under %s: %s",
                sub_name,
                data_root,
                exc,
            )
    return data_root


def build_k8s_workspace_with_nanzi_adapter(
    base_workspace_class: type[Any],
    *,
    existing_pvc: str | None = None,
    sandbox_user_key: str | None = None,
    public_docs_mounted: bool = True,
    local_data_root: str | None = None,
    **kwargs: Any,
) -> Any:
    """Instantiate an AgentScope K8s workspace with NanZi storage and mount enhancements."""

    class _NanZiK8sWorkspace(base_workspace_class):
        def __init__(
            self,
            *,
            existing_pvc: str | None = None,
            sandbox_user_key: str | None = None,
            public_docs_mounted: bool = True,
            local_data_root: str | None = None,
            **init_kwargs: Any,
        ) -> None:
            # Normalize resources: if only limits are specified, ensure sensible requests
            # to prevent Kubernetes from defaulting requests = limits (which starves the cluster).
            res = init_kwargs.get("resources")
            if isinstance(res, dict):
                res_dict = dict(res)
                limits = res_dict.get("limits") or {}
                requests = res_dict.get("requests") or {}
                if limits and not requests:
                    # Provide default lightweight requests
                    res_dict["requests"] = {
                        "cpu": DEFAULT_K8S_CPU_REQUEST,
                        "memory": DEFAULT_K8S_MEMORY_REQUEST,
                    }
                    init_kwargs["resources"] = res_dict

            super().__init__(**init_kwargs)
            self._nanzi_existing_pvc = (existing_pvc or "").strip() or None
            self._nanzi_sandbox_user_key = (sandbox_user_key or "").strip() or None
            self._nanzi_public_docs_mounted = public_docs_mounted
            self._nanzi_local_data_root = local_data_root

        async def _ensure_namespace(self) -> None:
            """Ensure the target namespace exists. Gracefully tolerate RBAC 403 Forbidden."""
            try:
                await super()._ensure_namespace()
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status", None)
                if status in (403, 409):
                    logger.warning(
                        "[k8s_workspace] Namespace %r check/creation returned HTTP %s. "
                        "Assuming namespace is pre-created by cluster administrator: %s",
                        self._namespace,
                        status,
                        exc,
                    )
                    return
                logger.error(
                    "[k8s_workspace] Failed to verify namespace %r: %s",
                    self._namespace,
                    exc,
                )
                raise K8sSandboxUnavailableError(
                    f"无法访问 Kubernetes 命名空间 {self._namespace}，请检查集群连接与 RBAC 权限: {exc}",
                    reason_code="k8s_namespace_inaccessible",
                ) from exc

        async def _ensure_pvc(self) -> None:
            """Ensure PVC exists; if using an existing shared PVC, skip dynamic creation."""
            if self._nanzi_existing_pvc:
                logger.info(
                    "K8sWorkspace: Using existing shared PVC %r for pod %r",
                    self._nanzi_existing_pvc,
                    self._pod_name,
                )
                return
            await super()._ensure_pvc()

        async def _create_pod(self) -> None:
            """Create the workspace Pod with optional subPath and public docs mounts."""
            if not self._nanzi_existing_pvc:
                # Default isolated dynamic PVC path
                return await super()._create_pod()

            # Security Guard: shared PVC mode MUST have an authenticated user identity
            if not self._nanzi_sandbox_user_key:
                raise ValueError(
                    "K8S 共享 PVC 沙箱策略要求必须具备已认证的用户身份（sandbox_user_key 不能为空），"
                    "以防止未授权访问或越权挂载持久卷根目录。"
                )

            # Ensure host/PVC subPath directories physically exist before kubelet mounts them.
            # subPath is relative to the shared PVC root; the platform data dir root is
            # normally the parent of the resolved workspace root (e.g. /app/data), so we
            # probe the parent first, then the root itself, then the data-root conventions.
            candidate_roots: list[str] = []
            if self._nanzi_local_data_root:
                parent = os.path.dirname(
                    os.path.realpath(os.path.abspath(self._nanzi_local_data_root))
                )
                candidate_roots = [parent, self._nanzi_local_data_root]
            _ensure_local_subpath_dirs(self._nanzi_sandbox_user_key, candidate_roots)

            from kubernetes_asyncio import client as k8s_client
            try:
                from agentscope.workspace._k8s._constants import POD_WORKDIR
            except ImportError:
                POD_WORKDIR = "/workspace"

            # 沙箱 Pod 可能因平台重启/缓存丢失被重复 initialize：bootstrap 会再次执行
            # `uv venv /root/.agentscope/.venv`。uv 默认拒绝覆盖已存在的 venv（exit 2），
            # 导致整条初始化失败（状态异常、Bash MCP 不可用）。注入 UV_VENV_CLEAR=1
            # 使 venv 创建幂等：已存在则 clear 重建，不存在则正常创建。
            merged_env = dict(self.env or {})
            merged_env.setdefault("UV_VENV_CLEAR", "1")
            container_env = [
                k8s_client.V1EnvVar(name=k, value=str(v))
                for k, v in merged_env.items()
            ] if merged_env else None

            # 挂载整个用户工作区根（agent_workspaces/{user_key}），与 Docker 沙箱
            # bind 用户工作区的行为对齐：沙箱 Bash/read/write 能直接看到并操作
            # 用户在平台上工作区的全部内容（sessions/docs/历史落盘文件等），
            # 而非之前只挂工作区下的 sandbox 子目录。
            user_subpath = f"agent_workspaces/{self._nanzi_sandbox_user_key}"

            volume_mounts = [
                k8s_client.V1VolumeMount(
                    name="shared-data",
                    mount_path=POD_WORKDIR,
                    sub_path=user_subpath,
                ),
            ]

            if self._nanzi_public_docs_mounted:
                volume_mounts.append(
                    k8s_client.V1VolumeMount(
                        name="shared-data",
                        mount_path=f"{POD_WORKDIR}/public/docs",
                        sub_path="docs",
                        read_only=True,
                    ),
                )

            container = k8s_client.V1Container(
                name="workspace",
                image=self._image,
                image_pull_policy=self._image_pull_policy,
                command=["sleep", "infinity"],
                working_dir=POD_WORKDIR,
                ports=[
                    k8s_client.V1ContainerPort(
                        container_port=self.gateway_port,
                    ),
                ],
                resources=(
                    k8s_client.V1ResourceRequirements(**self._resources)
                    if self._resources
                    else None
                ),
                volume_mounts=volume_mounts,
                env=container_env,
            )

            volumes = [
                k8s_client.V1Volume(
                    name="shared-data",
                    persistent_volume_claim=(
                        k8s_client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=self._nanzi_existing_pvc,
                        )
                    ),
                ),
            ]

            spec_kwargs: dict[str, Any] = {
                "restart_policy": "OnFailure",
                "containers": [container],
                "volumes": volumes,
            }
            if self._node_selector:
                spec_kwargs["node_selector"] = self._node_selector
            if self._tolerations:
                spec_kwargs["tolerations"] = [
                    k8s_client.V1Toleration(**t) for t in self._tolerations
                ]
            if self._service_account:
                spec_kwargs["service_account_name"] = self._service_account
            if self._image_pull_secrets:
                spec_kwargs["image_pull_secrets"] = [
                    k8s_client.V1LocalObjectReference(name=s)
                    for s in self._image_pull_secrets
                ]

            pod = k8s_client.V1Pod(
                metadata=k8s_client.V1ObjectMeta(
                    name=self._pod_name,
                    namespace=self._namespace,
                    labels={
                        "app.kubernetes.io/managed-by": "agentscope",
                        "agentscope.workspace": "true",
                        "agentscope.workspace.id": self.workspace_id,
                    },
                ),
                spec=k8s_client.V1PodSpec(**spec_kwargs),
            )
            try:
                await self._v1.create_namespaced_pod(self._namespace, pod)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[k8s_workspace] Failed to create pod %r in namespace %r: %s",
                    self._pod_name,
                    self._namespace,
                    exc,
                )
                raise K8sSandboxUnavailableError(
                    f"创建 Kubernetes 沙箱 Pod 失败（命名空间: {self._namespace}, 镜像: {self._image}）: {exc}",
                    reason_code="k8s_pod_creation_failed",
                ) from exc

        async def _teardown_backend(self) -> None:
            """Teardown backend. If using an existing shared PVC, never delete the PVC."""
            if self._nanzi_existing_pvc:
                # Temporarily disable delete_pvc_on_close so shared app PVC is never touched
                original_flag = self._delete_pvc_on_close
                self._delete_pvc_on_close = False
                try:
                    await super()._teardown_backend()
                finally:
                    self._delete_pvc_on_close = original_flag
                return

            await super()._teardown_backend()

    return _NanZiK8sWorkspace(
        existing_pvc=existing_pvc,
        sandbox_user_key=sandbox_user_key,
        public_docs_mounted=public_docs_mounted,
        local_data_root=local_data_root,
        **kwargs,
    )


async def _probe_k8s_rbac_status(target_namespace: str) -> dict[str, Any]:
    """Probe cluster connectivity and RBAC permissions for ``target_namespace``."""
    try:
        from kubernetes_asyncio import client as k8s_client, config as k8s_async_config
    except ImportError:
        return {
            "ok": False,
            "error_type": "missing_dependency",
            "namespace": target_namespace,
            "message": "未安装 kubernetes-asyncio 依赖包，请在环境中安装。",
        }

    # 1. 尝试加载 InCluster 或 KubeConfig 凭据
    loaded = False
    try:
        k8s_async_config.load_incluster_config()
        loaded = True
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
            loaded = True
        except Exception as exc:
            return {
                "ok": False,
                "error_type": "no_k8s_config",
                "namespace": target_namespace,
                "message": f"未检测到 Kubernetes 集群连接凭据（既非集群内 InCluster Pod，也未在本地读取到有效 kubeconfig）：{exc}",
                "remedy": "如果是在本地测试，请确保 ~/.kube/config 存在；如果在集群内运行，请确保平台 Pod 绑定了 ServiceAccount。",
            }

    # 2. 发起权限与连通性自检
    try:
        async with k8s_client.ApiClient() as api_client:
            auth_v1 = k8s_client.AuthorizationV1Api(api_client)

            # 自检 Pod 创建权限 (SelfSubjectAccessReview)
            review_pod = k8s_client.V1SelfSubjectAccessReview(
                spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=k8s_client.V1ResourceAttributes(
                        namespace=target_namespace,
                        verb="create",
                        resource="pods",
                    )
                )
            )
            res_pod = await auth_v1.create_self_subject_access_review(review_pod)
            can_create_pods = bool(res_pod.status.allowed)

            # 自检 PVC 创建权限
            review_pvc = k8s_client.V1SelfSubjectAccessReview(
                spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=k8s_client.V1ResourceAttributes(
                        namespace=target_namespace,
                        verb="create",
                        resource="persistentvolumeclaims",
                    )
                )
            )
            res_pvc = await auth_v1.create_self_subject_access_review(review_pvc)
            can_create_pvcs = bool(res_pvc.status.allowed)

            if not can_create_pods:
                return {
                    "ok": False,
                    "error_type": "rbac_forbidden",
                    "namespace": target_namespace,
                    "can_create_pods": False,
                    "can_create_pvcs": can_create_pvcs,
                    "message": f"K8s API 连接成功，但在命名空间 [{target_namespace}] 内缺少 Pods 创建权限（RBAC 尚未授权）。",
                    "remedy": "kubectl apply -f k8s_deploy/sandbox-rbac.example.yaml",
                }

            return {
                "ok": True,
                "namespace": target_namespace,
                "can_create_pods": True,
                "can_create_pvcs": can_create_pvcs,
                "message": f"Kubernetes 集群连接正常，已具备命名空间 [{target_namespace}] 的 Pod 与 PVC 操作权限！",
            }
    except Exception as exc:
        return {
            "ok": False,
            "error_type": "api_error",
            "namespace": target_namespace,
            "message": f"连接 Kubernetes API Server 发生异常：{exc}",
            "remedy": "请检查集群网络连通性、API Server 地址及网络策略（NetworkPolicy）。",
        }


async def check_k8s_rbac_status(
    namespace: str | None = None,
) -> dict[str, Any]:
    """Check cluster connectivity, RBAC permissions and workspace mount config.

    Besides the RBAC probe, the response carries a ``warnings`` list and the
    resolved workspace volume (``workspace_mount``) describing what the sandbox
    will actually mount — e.g. a blank ``sandbox_k8s_existing_pvc`` that could not
    be auto-detected, or a sandbox namespace that cannot reach the platform PVC
    because PVCs are namespace-scoped.
    """
    from app.services.config_service import ConfigService

    configured_namespace = ""
    explicit_namespace = (namespace or "").strip()
    if not explicit_namespace:
        try:
            configured_namespace = (
                await ConfigService.get("sandbox_k8s_namespace", "")
            ) or ""
        except Exception as exc:  # noqa: BLE001 - config read must never break the probe
            logger.warning("[k8s_workspace] Failed to read sandbox_k8s_namespace: %s", exc)

    target_namespace = explicit_namespace or resolve_sandbox_namespace(configured_namespace)

    configured_pvc = ""
    try:
        configured_pvc = await ConfigService.get("sandbox_k8s_existing_pvc", "") or ""
    except Exception as exc:  # noqa: BLE001 - config read must never break the probe
        logger.warning("[k8s_workspace] Failed to read sandbox_k8s_existing_pvc: %s", exc)

    resolution = await resolve_shared_pvc(configured_pvc)
    warnings = evaluate_k8s_workspace_mount_config(
        namespace=configured_namespace or target_namespace,
        pvc=resolution["pvc"],
        pvc_source=resolution["source"],
    )

    result = await _probe_k8s_rbac_status(target_namespace)
    result["warnings"] = warnings
    result["workspace_mount"] = {
        "claim_name": resolution["pvc"],
        "source": resolution["source"],
    }
    for warning in warnings:
        logger.warning("[k8s_workspace] K8s sandbox mount config: %s", warning)
    return result


async def read_k8s_sandbox_pod(
    namespace: str,
    pod_name: str,
) -> dict[str, Any]:
    """Read a sandbox Pod's live status without creating or mutating anything.

    Return contract:
    - ``{"available": True, "found": True, "phase": str|None, "start_time": str|None,
       "ready": bool|None}`` when the Pod exists;
    - ``{"available": True, "found": False, ...}`` when the Pod is confirmed
      absent (HTTP 404);
    - ``{"available": False, "found": None, ...}`` when the lookup cannot be
      trusted (missing dependency, no cluster credentials, RBAC/API error).
      Callers must degrade gracefully in that case instead of failing hard.
    """
    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_async_config
    except ImportError:
        logger.warning("[k8s_workspace] kubernetes-asyncio is not installed; cannot read pod status")
        return {
            "available": False,
            "found": None,
            "phase": None,
            "start_time": None,
            "ready": None,
        }

    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:
            logger.warning("[k8s_workspace] No cluster credentials to read pod %s: %s", pod_name, exc)
            return {
                "available": False,
                "found": None,
                "phase": None,
                "start_time": None,
                "ready": None,
            }

    try:
        async with k8s_client.ApiClient() as api_client:
            core_v1 = k8s_client.CoreV1Api(api_client)
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            logger.info("[k8s_workspace] Sandbox pod %s/%s not found", namespace, pod_name)
            return {
                "available": True,
                "found": False,
                "phase": None,
                "start_time": None,
                "ready": None,
            }
        logger.warning("[k8s_workspace] Failed to read pod %s/%s: %s", namespace, pod_name, exc)
        return {
            "available": False,
            "found": None,
            "phase": None,
            "start_time": None,
            "ready": None,
        }

    status = getattr(pod, "status", None)
    phase = getattr(status, "phase", None)
    container_ready: bool | None = None
    container_statuses = getattr(status, "container_statuses", None) or []
    if container_statuses:
        container_ready = any(
            bool(getattr(cs, "ready", False)) for cs in container_statuses
        )
    metadata = getattr(pod, "metadata", None)
    # Terminating 的 Pod phase 仍为 Running，需用 deletionTimestamp 识别删除中
    deleting = bool(getattr(metadata, "deletion_timestamp", None))
    start_time = getattr(status, "start_time", None)
    if start_time is None:
        start_time = getattr(metadata, "creation_timestamp", None)
    start_time_text: str | None = None
    if start_time is not None:
        from datetime import datetime, timezone

        if isinstance(start_time, datetime):
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            start_time_text = start_time.astimezone(timezone.utc).isoformat()
        else:
            start_time_text = str(start_time)
    return {
        "available": True,
        "found": True,
        "phase": phase,
        "start_time": start_time_text,
        "ready": container_ready,
        "deleting": deleting,
    }


# 沙箱 Pod 内执行命令的容器名（k8s_workspace 创建 Pod 时使用）
K8S_SANDBOX_CONTAINER = "workspace"


async def exec_k8s_sandbox_command(
    namespace: str,
    pod_name: str,
    command: str,
    *,
    workdir: str = "",
) -> dict[str, Any]:
    """Execute a one-shot shell command inside a running sandbox Pod.

    Equivalent of ``kubectl exec`` via the Kubernetes WebSocket exec
    (``v4.channel.k8s.io``) protocol. The kubernetes_asyncio pre-load path
    merges stdout+stderr and does not surface the error channel, so:

    * a workdir marker is printed at the end to recover the real ``PWD``;
    * ``exit_code`` is reported as ``None`` when the underlying library does not
      expose it (the terminal UI degrades gracefully).
    """
    from kubernetes_asyncio.stream.ws_client import WsApiClient
    from kubernetes_asyncio import client as k8s_client
    from kubernetes_asyncio import config as k8s_async_config

    cmd_clean = str(command or "").strip()
    if not cmd_clean:
        return {"stdout": "", "stderr": "", "output": "", "exit_code": None,
                "duration_ms": 0, "workdir": workdir or "/workspace"}

    # 凭据加载：in-cluster 优先，其次 kubeconfig
    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:
            raise K8sSandboxUnavailableError(
                f"无法加载 Kubernetes 凭据以在 Pod 内执行命令: {exc}",
                reason_code="k8s_exec_credentials",
                user_message="当前后端无法连接 Kubernetes 集群以执行沙箱命令，请检查集群连接与 RBAC。",
            ) from exc

    start = time.monotonic()
    marker = f"__NANZI_PWD_{uuid.uuid4().hex}__"
    shell = (workdir or "").strip() or "/workspace"
    wrapped = (
        f"cd '{shell}' 2>/dev/null; "
        f"{{ {cmd_clean}\n}}\n"
        f"__NZ_RET=$?\n"
        f"printf '\\n{marker}:%s\\n' \"$PWD\"\n"
        f"exit $__NZ_RET"
    )

    api_client = None
    try:
        api_client = WsApiClient()
        core = k8s_client.CoreV1Api(api_client=api_client)
        resp = await core.connect_get_namespaced_pod_exec(
            name=pod_name,
            namespace=namespace,
            container=K8S_SANDBOX_CONTAINER,
            command=["/bin/bash", "-lc", wrapped],
            stdin=False,
            stdout=True,
            stderr=True,
            tty=False,
        )
        # pre-load 模式：返回 stdout+stderr 合并字符串
        output = resp if isinstance(resp, str) else str(resp or "")
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) == 404:
            raise K8sSandboxUnavailableError(
                f"沙箱 Pod {pod_name} 不存在或已销毁",
                reason_code="k8s_pod_not_found",
                user_message="Kubernetes 沙箱 Pod 不存在或已销毁，请先启动沙箱后再进入终端。",
            ) from exc
        raise K8sSandboxUnavailableError(
            f"在沙箱 Pod {pod_name} 内执行命令失败: {exc}",
            reason_code="k8s_exec_failed",
            user_message=f"沙箱命令执行失败，请检查 Pod 状态与集群连通性。",
        ) from exc
    finally:
        if api_client is not None:
            try:
                await api_client.close()
            except Exception:
                pass

    final_workdir = shell
    clean_output = output
    if marker in clean_output:
        parts = clean_output.split(f"{marker}:")
        clean_output = parts[0].rstrip("\r\n")
        if len(parts) > 1:
            tail = parts[1].splitlines()
            if tail and tail[0].strip():
                final_workdir = tail[0].strip()

    return {
        "stdout": clean_output,
        "stderr": "",
        "output": clean_output,
        "exit_code": None,
        "duration_ms": max(0, int((time.monotonic() - start) * 1000)),
        "workdir": final_workdir,
    }
