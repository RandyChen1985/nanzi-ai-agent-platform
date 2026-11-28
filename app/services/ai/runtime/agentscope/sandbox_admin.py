"""管理员视角的 Docker / Kubernetes 沙箱实例巡检与运维服务。

面向「系统配置与诊断 → 参数配置 → 安全沙箱」管理弹层，提供只针对平台沙箱
（标签 ``agentscope.workspace=true``）的实时列出、详情与日志查看、手动 Kill、
重启以及一键清理孤儿/僵死能力。

安全边界：
1. **标签硬隔离**：所有列出、详情、Kill、重启操作均强制校验目标带有
   ``agentscope.workspace=true`` 标签，绝不列出或操作平台主服务、数据库、Redis
   等非沙箱容器/Pod；
2. **数据保留**：Kill / 重启只销毁沙箱运行壳，宿主 bind mount 或共享 PVC 中的
   用户工作区文件完整保留；
3. **日志脱敏**：容器/Pod 日志在返回前统一经 ``mask_sensitive_data`` 脱敏并做长度
   截断，避免脚本输出中的凭据明文暴露。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Literal

from app.services.ai.runtime.agentscope import workspace as ws

logger = logging.getLogger(__name__)

SandboxAdminBackend = Literal["docker", "k8s"]

#: 单台容器/Pod 实时资源统计拉取超时（秒），避免拖慢列表接口。
ADMIN_STATS_TIMEOUT_SECONDS = 2.0
#: 列表与单台查询/销毁操作超时（秒），防止异常 daemon/Pod 卡死整个请求。
ADMIN_LIST_TIMEOUT_SECONDS = 10.0
ADMIN_OP_TIMEOUT_SECONDS = 15.0
#: 单次批量操作允许的最大目标数。
ADMIN_BATCH_MAX_TARGETS = 200
#: 并发拉取容器 stats 的最大并发度。
ADMIN_STATS_CONCURRENCY = 6
#: 详情日志默认与最大行数/字节上限。
ADMIN_LOG_DEFAULT_TAIL = 100
ADMIN_LOG_MAX_TAIL = 500
ADMIN_LOG_MAX_CHARS = 64_000
#: AgentScope 沙箱内 FastMCP Gateway 的真实日志路径（PID 1 为 ``sleep infinity``，
#: 容器 stdout/stderr 往往为空，真正的启动与报错日志落在此文件）。
SANDBOX_GATEWAY_LOG_PATH = "/root/.agentscope/gateway.log"
#: 容器 ID/名称与 Pod 名称安全白名单（杜绝 ``..``、``/`` 等 URL 路径穿越字符）。
_SAFE_DOCKER_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$")
_SAFE_K8S_POD_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def _validate_target_id(target_id: str, *, backend: SandboxAdminBackend) -> str:
    """校验容器/Pod 标识，阻断空值、过长串及 URL 路径穿越字符。"""
    tid = str(target_id or "").strip()
    if not tid:
        raise SandboxAdminTargetError("目标沙箱标识不能为空", status_code=400)
    if ".." in tid or "/" in tid or "\\" in tid:
        raise SandboxAdminTargetError(f"非法的沙箱标识：{tid}", status_code=400)
    pattern = _SAFE_DOCKER_TARGET_RE if backend == "docker" else _SAFE_K8S_POD_RE
    if not pattern.fullmatch(tid):
        raise SandboxAdminTargetError(f"非法的沙箱标识格式：{tid}", status_code=400)
    return tid


class SandboxAdminTargetError(ValueError):
    """目标不存在、非法或不属于平台沙箱资源。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


async def resolve_admin_sandbox_backend(
    requested: str | None = None,
) -> SandboxAdminBackend:
    """解析目标后端（显式指定优先，否则读取系统配置 ``sandbox_policy``）。"""
    raw = str(requested or "").strip().lower()
    if raw in ("docker", "k8s"):
        return raw  # type: ignore[return-value]
    if raw and raw != "auto":
        raise SandboxAdminTargetError(
            f"不支持的沙箱后端类型：{requested}（仅支持 docker 或 k8s）",
            status_code=400,
        )

    from app.services.config_service import (
        ConfigService,
        resolve_effective_sandbox_policy,
    )

    policy = resolve_effective_sandbox_policy(
        await ConfigService.get("sandbox_policy", ws.SANDBOX_POLICY_LOCAL),
        ws.SANDBOX_POLICY_LOCAL,
    )
    if policy in (ws.SANDBOX_POLICY_DOCKER, ws.SANDBOX_POLICY_K8S):
        return policy  # type: ignore[return-value]
    raise SandboxAdminTargetError(
        f"当前沙箱策略为 {policy}，仅 docker 或 k8s 策略支持管理沙箱实例。",
        status_code=409,
    )


def _split_user_and_token(workspace_id: str) -> tuple[str, str]:
    """从 ``agentscope.workspace.id``（或容器/Pod 名）拆出 ``(user_key, skill_token)``。"""
    cleaned = str(workspace_id or "").strip()
    if cleaned.startswith("as_ws_"):
        cleaned = cleaned[len("as_ws_") :]
    elif cleaned.startswith("as-ws-"):
        cleaned = cleaned[len("as-ws-") :]
    parts = ws._workspace_id_parts(cleaned)
    if parts is not None:
        return parts[0], parts[1]
    return cleaned or "unknown", "历史无令牌"


def _format_iso_utc(ts: float | None) -> str | None:
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _safe_sanitize_logs(raw_text: str) -> str:
    text = str(raw_text or "")
    if len(text) > ADMIN_LOG_MAX_CHARS:
        text = "…(已截断早期日志)…\n" + text[-ADMIN_LOG_MAX_CHARS:]
    try:
        from app.utils.masking import mask_sensitive_data

        masked = mask_sensitive_data(text)
        return str(masked) if masked is not None else text
    except Exception:  # noqa: BLE001
        return text


def _build_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "all": len(items),
        "managed": sum(1 for item in items if item.get("category") == "managed"),
        "orphan": sum(1 for item in items if item.get("category") == "orphan"),
        "zombie": sum(1 for item in items if item.get("category") == "zombie"),
    }


def _idle_multiplier_for(refcount: int) -> float:
    """仍有会话持有引用时，空闲阈值放宽的倍数（1.0 表示按配置值原样回收）。

    页面展示与实际回收必须共用这一个来源：回收器在 ``reap_idle_docker_workspaces`` /
    ``reap_idle_k8s_workspaces`` 里乘同一个倍数，页面若只显示 ``sandbox_idle_time``，
    用户看到「配置 15 分钟、页面 60 分钟」就会怀疑配置没生效。
    """
    return ws.DOCKER_WORKSPACE_REFERENCED_IDLE_MULTIPLIER if refcount > 0 else 1.0


def _compute_idle_seconds_left(
    *,
    last_used: float | None,
    refcount: int,
    idle_seconds: float,
    now_monotonic: float,
) -> int | None:
    if last_used is None:
        return None
    deadline_seconds = idle_seconds * _idle_multiplier_for(refcount)
    remaining = deadline_seconds - max(0.0, now_monotonic - last_used)
    return max(0, int(remaining))


def _parse_docker_stats_payload(payload: Any) -> dict[str, Any]:
    """从 Docker ``/stats?stream=0`` 返回结构提取 CPU%、内存与 PIDs。"""
    sample = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(sample, dict):
        return {}
    result: dict[str, Any] = {}
    try:
        cpu_stats = sample.get("cpu_stats") or {}
        precpu_stats = sample.get("precpu_stats") or {}
        cpu_total = (cpu_stats.get("cpu_usage") or {}).get("total_usage")
        precpu_total = (precpu_stats.get("cpu_usage") or {}).get("total_usage")
        sys_cpu = cpu_stats.get("system_cpu_usage")
        presys_cpu = precpu_stats.get("system_cpu_usage")
        online_cpus = (
            cpu_stats.get("online_cpus")
            or len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or [])
            or 1
        )
        if (
            isinstance(cpu_total, (int, float))
            and isinstance(precpu_total, (int, float))
            and isinstance(sys_cpu, (int, float))
            and isinstance(presys_cpu, (int, float))
        ):
            cpu_delta = cpu_total - precpu_total
            sys_delta = sys_cpu - presys_cpu
            if sys_delta > 0 and cpu_delta >= 0:
                result["cpu_percent"] = round(
                    (cpu_delta / sys_delta) * float(online_cpus) * 100.0, 2
                )
    except Exception:  # noqa: BLE001
        pass

    try:
        mem_stats = sample.get("memory_stats") or {}
        usage = mem_stats.get("usage")
        limit = mem_stats.get("limit")
        stats_sub = mem_stats.get("stats") or {}
        # cgroups v1/v2 扣除文件页缓存，口径与 `docker stats` 对齐
        cache = stats_sub.get("inactive_file")
        if cache is None:
            cache = stats_sub.get("cache", 0)
        if isinstance(usage, (int, float)):
            effective = max(0, int(usage) - int(cache or 0))
            result["memory_usage_bytes"] = effective
        if isinstance(limit, (int, float)) and limit > 0:
            result["memory_limit_bytes"] = int(limit)
    except Exception:  # noqa: BLE001
        pass

    try:
        pids_stats = sample.get("pids_stats") or {}
        if isinstance(pids_stats.get("current"), int):
            result["pids_current"] = pids_stats["current"]
        if isinstance(pids_stats.get("limit"), int) and pids_stats["limit"] > 0:
            result["pids_limit"] = pids_stats["limit"]
    except Exception:  # noqa: BLE001
        pass

    return result


def _parse_k8s_cpu_millicores(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("n"):
            return max(0, int(round(float(text[:-1]) / 1_000_000.0)))
        if text.endswith("u"):
            return max(0, int(round(float(text[:-1]) / 1_000.0)))
        if text.endswith("m"):
            return max(0, int(round(float(text[:-1]))))
        return max(0, int(round(float(text) * 1000.0)))
    except ValueError:
        return None


def _parse_k8s_memory_bytes(raw: Any) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    units = (
        ("Ki", 1024),
        ("Mi", 1024**2),
        ("Gi", 1024**3),
        ("Ti", 1024**4),
        ("K", 1000),
        ("M", 1000**2),
        ("G", 1000**3),
        ("T", 1000**4),
    )
    try:
        for suffix, factor in units:
            if text.endswith(suffix):
                return max(0, int(round(float(text[: -len(suffix)]) * factor)))
        return max(0, int(round(float(text))))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Docker 管理员视图与运维操作
# ---------------------------------------------------------------------------


def _docker_cache_index_by_workspace_id() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for cache_key, workspace in list(ws._docker_workspace_cache.items()):
        wid = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if not wid:
            continue
        wid_str = str(wid)
        container_id = getattr(workspace, "_platform_container_id", None)
        if not container_id:
            container_obj = getattr(workspace, "_container", None)
            raw_cid = getattr(container_obj, "id", None)
            container_id = str(raw_cid) if isinstance(raw_cid, str) and raw_cid else None
        index[wid_str] = {
            "cache_key": cache_key,
            "last_used": ws._docker_workspace_last_used.get(cache_key),
            "refcount": ws._docker_workspace_refcounts.get(cache_key, 0),
            "container_id": container_id,
        }
    return index


async def _fetch_docker_container_stats_safe(
    container: Any, sem: asyncio.Semaphore
) -> dict[str, Any]:
    async with sem:
        try:
            payload = await asyncio.wait_for(
                container.stats(stream=False),
                timeout=ADMIN_STATS_TIMEOUT_SECONDS,
            )
            return _parse_docker_stats_payload(payload)
        except Exception:  # noqa: BLE001
            return {}


async def _list_docker_admin_sandboxes(*, include_stats: bool = False) -> dict[str, Any]:
    base_resp: dict[str, Any] = {
        "backend": "docker",
        "scope_label": "label: agentscope.workspace=true",
        "runtime_desc": "宿主机 Docker Daemon 实时视图（仅展示带平台标签的沙箱容器）",
        "available": False,
        "error": None,
        "stats_included": include_stats,
        "summary": {"all": 0, "managed": 0, "orphan": 0, "zombie": 0},
        "items": [],
    }
    try:
        import aiodocker
    except Exception as exc:  # noqa: BLE001
        base_resp["error"] = f"后端未安装 aiodocker：{exc}"
        return base_resp

    idle_seconds = await ws.resolve_effective_sandbox_idle_seconds(
        default_seconds=ws.DOCKER_WORKSPACE_IDLE_SECONDS
    )
    now_wall = time.time()
    now_mono = time.monotonic()
    cache_index = _docker_cache_index_by_workspace_id()

    client: Any | None = None
    try:
        client = aiodocker.Docker()
        listed = await asyncio.wait_for(
            client.containers.list(
                all=True, filters=ws._docker_workspace_label_filters()
            ),
            timeout=ADMIN_LIST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        base_resp["error"] = f"无法连接 Docker Daemon：{exc}"
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        return base_resp

    try:
        items: list[dict[str, Any]] = []
        running_containers: list[tuple[int, Any]] = []

        for container in listed or []:
            raw = getattr(container, "_container", None)
            if not isinstance(raw, dict):
                continue
            labels = raw.get("Labels") if isinstance(raw.get("Labels"), dict) else {}
            if str(labels.get("agentscope.workspace") or "").lower() != "true":
                continue

            full_id = str(getattr(container, "id", "") or raw.get("Id") or "")
            name = ws._container_name(container)
            workspace_id = str(labels.get("agentscope.workspace.id") or "")
            user_key, skill_token = _split_user_and_token(workspace_id or name)
            try:
                created = float(raw.get("Created") or 0)
            except (TypeError, ValueError):
                created = 0.0
            age_seconds = max(0, int(now_wall - created)) if created > 0 else None

            state = str(raw.get("State") or "").strip().lower() or "unknown"
            status_detail = str(raw.get("Status") or state).strip()
            cached = cache_index.get(workspace_id)
            in_cache = cached is not None and (
                not cached.get("container_id")
                or not full_id
                or cached["container_id"] == full_id
            )

            is_zombie = (
                state in {"exited", "dead", "paused"}
                or (
                    state == "created"
                    and age_seconds is not None
                    and age_seconds >= ws.SANDBOX_ZOMBIE_CREATED_MIN_AGE_SECONDS
                )
            )
            if is_zombie:
                category = "zombie"
            elif in_cache:
                category = "managed"
            else:
                category = "orphan"

            idle_left = (
                _compute_idle_seconds_left(
                    last_used=cached.get("last_used"),
                    refcount=int(cached.get("refcount") or 0),
                    idle_seconds=idle_seconds,
                    now_monotonic=now_mono,
                )
                if in_cache and cached is not None
                else None
            )

            mounts = raw.get("Mounts") if isinstance(raw.get("Mounts"), list) else []
            mount_summary = ""
            for m in mounts:
                if isinstance(m, dict) and m.get("Destination") == "/workspace":
                    mount_summary = f"{m.get('Source') or ''} -> /workspace"
                    break

            entry: dict[str, Any] = {
                "id": full_id or name,
                "short_id": (full_id[:12] if full_id else name[:12]),
                "name": name,
                "workspace_id": workspace_id,
                "user_key": user_key,
                "skill_token": skill_token,
                "image": str(raw.get("Image") or ""),
                "runtime_state": state,
                "state_detail": status_detail,
                "category": category,
                "in_cache": in_cache,
                "refcount": int(cached.get("refcount") or 0) if in_cache and cached else 0,
                "created_at": _format_iso_utc(created),
                "age_seconds": age_seconds,
                "idle_seconds_left": idle_left,
                # 展示「为什么剩余时间是配置值的 N 倍」所需的基础阈值与放宽倍数：
                # 只有仍被会话引用（refcount > 0）才放宽，页面据此标注，避免误会配置没生效
                "idle_seconds": int(idle_seconds),
                "idle_multiplier": _idle_multiplier_for(
                    int(cached.get("refcount") or 0) if in_cache and cached else 0
                ),
                "node_or_host": "Docker Daemon",
                "mount_summary": mount_summary,
                "cpu_percent": None,
                "memory_usage_bytes": None,
                "memory_limit_bytes": None,
                "pids_current": None,
                "pids_limit": None,
            }
            idx = len(items)
            items.append(entry)
            if include_stats and state == "running":
                running_containers.append((idx, container))

        if include_stats and running_containers:
            sem = asyncio.Semaphore(ADMIN_STATS_CONCURRENCY)
            stats_results = await asyncio.gather(
                *(
                    _fetch_docker_container_stats_safe(c, sem)
                    for _idx, c in running_containers
                ),
                return_exceptions=True,
            )
            for (idx, _c), stat in zip(running_containers, stats_results):
                if isinstance(stat, dict):
                    items[idx].update(stat)

        # 排序：僵死优先、孤儿次之、受管最后，同组内按存活时长倒序（最老在最前）
        order_rank = {"zombie": 0, "orphan": 1, "managed": 2}
        items.sort(
            key=lambda it: (
                order_rank.get(str(it.get("category")), 9),
                -(it.get("age_seconds") or 0),
            )
        )
        base_resp["available"] = True
        base_resp["items"] = items
        base_resp["summary"] = _build_summary(items)
        return base_resp
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()


async def _inspect_verified_docker_sandbox(
    client: Any, target_id: str
) -> tuple[Any, dict[str, Any]]:
    """获取容器并强制校验其带有 ``agentscope.workspace=true`` 标签。"""
    tid = _validate_target_id(target_id, backend="docker")
    try:
        container = await asyncio.wait_for(
            client.containers.get(tid),
            timeout=ADMIN_OP_TIMEOUT_SECONDS,
        )
        details = await asyncio.wait_for(
            container.show(),
            timeout=ADMIN_OP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) == 404:
            raise SandboxAdminTargetError(
                f"未找到容器 {tid}（可能已被回收）", status_code=404
            ) from exc
        raise SandboxAdminTargetError(
            f"查询容器 {tid} 失败：{exc}", status_code=502
        ) from exc

    config = details.get("Config") if isinstance(details, dict) else {}
    labels = config.get("Labels") if isinstance(config, dict) else {}
    labels = labels if isinstance(labels, dict) else {}
    if str(labels.get("agentscope.workspace") or "").lower() != "true":
        raise SandboxAdminTargetError(
            f"拒绝操作非沙箱容器 {tid}（缺少 agentscope.workspace=true 标签）",
            status_code=403,
        )
    return container, details if isinstance(details, dict) else {}


async def _read_docker_gateway_log_safe(container: Any, *, tail: int) -> str:
    """尽力读取运行中 Docker 容器内的 ``/root/.agentscope/gateway.log``。"""
    exec_fn = getattr(container, "exec", None)
    if not callable(exec_fn):
        return ""
    try:
        exec_obj = await asyncio.wait_for(
            exec_fn(
                cmd=["tail", "-n", str(tail), SANDBOX_GATEWAY_LOG_PATH],
                stdout=True,
                stderr=False,
                stdin=False,
                tty=False,
            ),
            timeout=3.0,
        )
        chunks: list[str] = []

        async def _collect() -> None:
            async with exec_obj.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    raw = getattr(msg, "data", msg)
                    if isinstance(raw, (bytes, bytearray)):
                        chunks.append(raw.decode("utf-8", errors="replace"))
                    else:
                        chunks.append(str(raw or ""))

        await asyncio.wait_for(_collect(), timeout=3.0)
        return "".join(chunks).strip()
    except Exception:  # noqa: BLE001
        return ""


def _open_docker_client() -> Any:
    """安全构造 ``aiodocker.Docker`` 客户端。

    当宿主机无 ``/var/run/docker.sock`` 且未设 ``DOCKER_HOST`` 时，``aiodocker.Docker()``
    会在 ``__init__`` 中直接抛 ``AssertionError`` / ``ValueError``；统一转为 503 业务异常，
    避免详情/Kill/重置端点爆出未捕获的 HTTP 500。
    """
    try:
        import aiodocker
    except Exception as exc:  # noqa: BLE001
        raise SandboxAdminTargetError(
            f"后端未安装 aiodocker：{exc}", status_code=503
        ) from exc
    try:
        return aiodocker.Docker()
    except Exception as exc:  # noqa: BLE001 - 无 socket 时 aiodocker 抛 AssertionError/ValueError
        raise SandboxAdminTargetError(
            f"无法连接 Docker Daemon：{exc or '未检测到有效 Docker Socket'}",
            status_code=503,
        ) from exc


async def _get_docker_sandbox_detail(target_id: str, *, tail: int) -> dict[str, Any]:
    client = _open_docker_client()
    try:
        container, details = await _inspect_verified_docker_sandbox(client, target_id)
        config = details.get("Config") or {}
        labels = config.get("Labels") or {}
        host_config = details.get("HostConfig") or {}
        state = details.get("State") or {}
        full_id = str(details.get("Id") or getattr(container, "id", "") or target_id)
        name = str(details.get("Name") or "").lstrip("/") or full_id[:12]
        workspace_id = str(labels.get("agentscope.workspace.id") or "")
        user_key, skill_token = _split_user_and_token(workspace_id or name)

        # 日志：运行中容器优先读取 AgentScope Gateway 日志（PID 1 是 sleep infinity，
        # docker logs stdout 通常为空），再补充/回退到容器 stdout/stderr 日志。
        # 注意 Docker API 中 Paused 容器的 State.Running 也为 True，必须排除 Paused
        # 避免 exec 报 409 或 stats 挂起。
        is_running = bool(state.get("Running")) and not bool(state.get("Paused"))
        gateway_log = ""
        if is_running:
            gateway_log = await _read_docker_gateway_log_safe(container, tail=tail)

        container_stdout_log = ""
        try:
            raw_logs = await asyncio.wait_for(
                container.log(stdout=True, stderr=True, follow=False, tail=str(tail)),
                timeout=5.0,
            )
            if isinstance(raw_logs, list):
                container_stdout_log = "".join(
                    line.decode("utf-8", errors="replace")
                    if isinstance(line, (bytes, bytearray))
                    else str(line)
                    for line in raw_logs
                ).strip()
            elif isinstance(raw_logs, (bytes, bytearray)):
                container_stdout_log = raw_logs.decode("utf-8", errors="replace").strip()
            else:
                container_stdout_log = str(raw_logs or "").strip()
        except Exception as exc:  # noqa: BLE001
            if not gateway_log:
                container_stdout_log = f"# 获取容器日志失败：{exc}"

        if gateway_log and container_stdout_log:
            logs_text = (
                f"=== Gateway Log ({SANDBOX_GATEWAY_LOG_PATH}) ===\n"
                f"{gateway_log}\n\n"
                f"=== Container stdout/stderr ===\n"
                f"{container_stdout_log}"
            )
        else:
            logs_text = gateway_log or container_stdout_log

        # 实时 stats（仅运行中且未暂停时拉取）
        stats: dict[str, Any] = {}
        if is_running:
            sem = asyncio.Semaphore(1)
            stats = await _fetch_docker_container_stats_safe(container, sem)

        mounts_raw = details.get("Mounts") if isinstance(details.get("Mounts"), list) else []
        mounts = [
            f"{m.get('Source', '')} -> {m.get('Destination', '')} ({'ro' if not m.get('RW', True) else 'rw'})"
            for m in mounts_raw
            if isinstance(m, dict)
        ]

        return {
            "backend": "docker",
            "id": full_id,
            "short_id": full_id[:12],
            "name": name,
            "workspace_id": workspace_id,
            "user_key": user_key,
            "skill_token": skill_token,
            "image": str(config.get("Image") or ""),
            "runtime_state": str(state.get("Status") or ""),
            "oom_killed": bool(state.get("OOMKilled")),
            "exit_code": state.get("ExitCode"),
            "init_enabled": bool(host_config.get("Init")),
            "memory_limit_bytes": host_config.get("Memory") or stats.get("memory_limit_bytes"),
            "pids_limit": host_config.get("PidsLimit") or stats.get("pids_limit"),
            "cpu_percent": stats.get("cpu_percent"),
            "memory_usage_bytes": stats.get("memory_usage_bytes"),
            "pids_current": stats.get("pids_current"),
            "created_at": details.get("Created"),
            "started_at": state.get("StartedAt"),
            "mounts": mounts,
            "labels": labels,
            "logs": _safe_sanitize_logs(logs_text),
        }
    finally:
        with contextlib.suppress(Exception):
            await client.close()


async def _evict_docker_cache_for_container(
    workspace_id: str, container_id: str, *, reason: str
) -> int:
    if not workspace_id:
        return 0
    snapshot = ws._snapshot_cached_sandboxes(
        ws._docker_workspace_cache, ws._docker_workspace_last_used
    )
    evicted = 0
    for key, expected_last_used, cached_cid in snapshot.get(workspace_id, []):
        if cached_cid and container_id and cached_cid != container_id:
            continue
        evicted += await ws._evict_docker_workspace_cache_entry(
            key, reason=reason, expected_last_used=expected_last_used
        )
    return evicted


async def _kill_docker_admin_sandboxes(target_ids: list[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    client = _open_docker_client()
    try:
        for tid in dict.fromkeys(str(x).strip() for x in target_ids if str(x).strip()):
            try:
                container, details = await _inspect_verified_docker_sandbox(client, tid)
                config = details.get("Config") or {}
                labels = config.get("Labels") or {}
                workspace_id = str(labels.get("agentscope.workspace.id") or "")
                full_id = str(details.get("Id") or getattr(container, "id", "") or tid)
                name = str(details.get("Name") or "").lstrip("/") or full_id[:12]

                await _evict_docker_cache_for_container(
                    workspace_id, full_id, reason="admin manual kill"
                )
                try:
                    await asyncio.wait_for(
                        container.delete(force=True),
                        timeout=ADMIN_OP_TIMEOUT_SECONDS,
                    )
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "status", None) != 404:
                        raise
                if workspace_id:
                    await ws._reclaim_orphan_sandbox_skills_dir_for_workspace_id(
                        workspace_id, policy=ws.SANDBOX_POLICY_DOCKER
                    )
                logger.warning(
                    "[sandbox_admin] Admin killed docker sandbox container=%s workspace_id=%s",
                    name,
                    workspace_id,
                )
                results.append(
                    {"id": full_id, "name": name, "ok": True, "action": "killed", "message": "已强制销毁"}
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"id": tid, "name": tid, "ok": False, "action": "kill", "message": str(exc)}
                )
    finally:
        with contextlib.suppress(Exception):
            await client.close()

    return {
        "backend": "docker",
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


async def _restart_docker_admin_sandboxes(target_ids: list[str]) -> dict[str, Any]:
    """重置 Docker 沙箱：逐出进程缓存并强制删除旧容器，下次使用自动拉起全新沙箱。

    刻意不使用 ``container.restart()``：沙箱容器主进程是 ``sleep infinity``，
    AgentScope Gateway 是 ``initialize()`` 通过 ``exec`` 后台拉起的；原地 restart
    会杀掉 Gateway 却只重启 ``sleep``，保留缓存会让后续 Bash 请求持续命中“无网关”
    的假活容器。销毁旧容器并清理缓存后，用户下次使用时会自动走完整 ``initialize()``
    拉起带健康 Gateway 的全新容器，且宿主机工作区数据不受影响。
    """
    results: list[dict[str, Any]] = []
    client = _open_docker_client()
    try:
        for tid in dict.fromkeys(str(x).strip() for x in target_ids if str(x).strip()):
            try:
                container, details = await _inspect_verified_docker_sandbox(client, tid)
                config = details.get("Config") or {}
                labels = config.get("Labels") or {}
                workspace_id = str(labels.get("agentscope.workspace.id") or "")
                full_id = str(details.get("Id") or getattr(container, "id", "") or tid)
                name = str(details.get("Name") or "").lstrip("/") or full_id[:12]

                await _evict_docker_cache_for_container(
                    workspace_id, full_id, reason="admin manual reset"
                )
                try:
                    await asyncio.wait_for(
                        container.delete(force=True),
                        timeout=ADMIN_OP_TIMEOUT_SECONDS,
                    )
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "status", None) != 404:
                        raise
                if workspace_id:
                    await ws._reclaim_orphan_sandbox_skills_dir_for_workspace_id(
                        workspace_id, policy=ws.SANDBOX_POLICY_DOCKER
                    )
                logger.info(
                    "[sandbox_admin] Admin reset docker sandbox container=%s workspace_id=%s",
                    name,
                    workspace_id,
                )
                results.append(
                    {
                        "id": full_id,
                        "name": name,
                        "ok": True,
                        "action": "reset_for_recreate",
                        "message": "已销毁旧容器并清理缓存，用户下次使用时将自动拉起全新健康沙箱",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"id": tid, "name": tid, "ok": False, "action": "restart", "message": str(exc)}
                )
    finally:
        with contextlib.suppress(Exception):
            await client.close()

    return {
        "backend": "docker",
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Kubernetes 管理员视图与运维操作
# ---------------------------------------------------------------------------


async def _load_k8s_namespace_and_client():
    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_async_config
    except ImportError as exc:
        raise SandboxAdminTargetError(
            "后端未安装 kubernetes-asyncio 依赖包", status_code=503
        ) from exc

    from app.services.ai.runtime.agentscope.k8s_workspace import resolve_sandbox_namespace
    from app.services.config_service import ConfigService

    try:
        namespace = resolve_sandbox_namespace(
            await ConfigService.get("sandbox_k8s_namespace", "")
        )
    except Exception:  # noqa: BLE001
        namespace = resolve_sandbox_namespace(None)

    try:
        k8s_async_config.load_incluster_config()
    except Exception:
        try:
            await k8s_async_config.load_kube_config()
        except Exception as exc:  # noqa: BLE001
            raise SandboxAdminTargetError(
                f"未检测到 Kubernetes 集群连接凭据：{exc}", status_code=503
            ) from exc

    return namespace, k8s_client


def _k8s_cache_index_by_workspace_id() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for cache_key, workspace in list(ws._k8s_workspace_cache.items()):
        wid = getattr(workspace, "_platform_workspace_id", None) or getattr(
            workspace, "workspace_id", None
        )
        if not wid:
            continue
        raw_pod = getattr(workspace, "_pod_name", None) or getattr(
            workspace, "pod_name", None
        )
        pod_name = str(raw_pod).strip() if isinstance(raw_pod, str) and raw_pod.strip() else None
        index[str(wid)] = {
            "cache_key": cache_key,
            "last_used": ws._k8s_workspace_last_used.get(cache_key),
            "refcount": ws._k8s_workspace_refcounts.get(cache_key, 0),
            "pod_name": pod_name,
        }
    return index


async def _fetch_k8s_pod_metrics_map(
    api_client: Any, k8s_client: Any, namespace: str
) -> dict[str, dict[str, Any]]:
    """尽力从 metrics-server 拉取命名空间内沙箱 Pod 资源占用，不可用时返回空字典。"""
    try:
        custom_api = k8s_client.CustomObjectsApi(api_client)
        payload = await asyncio.wait_for(
            custom_api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=namespace,
                plural="pods",
                label_selector="agentscope.workspace=true",
            ),
            timeout=ADMIN_STATS_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return {}

    metrics_map: dict[str, dict[str, Any]] = {}
    for item in (payload or {}).get("items") or []:
        pod_name = str(((item.get("metadata") or {}).get("name")) or "")
        if not pod_name:
            continue
        total_cpu_m = 0
        total_mem_b = 0
        has_cpu = False
        has_mem = False
        for c in item.get("containers") or []:
            usage = c.get("usage") or {}
            cpu_m = _parse_k8s_cpu_millicores(usage.get("cpu"))
            mem_b = _parse_k8s_memory_bytes(usage.get("memory"))
            if cpu_m is not None:
                total_cpu_m += cpu_m
                has_cpu = True
            if mem_b is not None:
                total_mem_b += mem_b
                has_mem = True
        metrics_map[pod_name] = {
            "cpu_millicores": total_cpu_m if has_cpu else None,
            "memory_usage_bytes": total_mem_b if has_mem else None,
        }
    return metrics_map


async def _list_k8s_admin_sandboxes(*, include_stats: bool = False) -> dict[str, Any]:
    base_resp: dict[str, Any] = {
        "backend": "k8s",
        "scope_label": "agentscope.workspace=true",
        "runtime_desc": "Kubernetes 集群实时视图（仅展示带平台标签的沙箱 Pod）",
        "available": False,
        "error": None,
        "stats_included": include_stats,
        "summary": {"all": 0, "managed": 0, "orphan": 0, "zombie": 0},
        "items": [],
    }
    try:
        namespace, k8s_client = await _load_k8s_namespace_and_client()
    except SandboxAdminTargetError as exc:
        base_resp["error"] = str(exc)
        return base_resp

    base_resp["scope_label"] = f"ns: {namespace} · agentscope.workspace=true"
    idle_seconds = await ws.resolve_effective_sandbox_idle_seconds(
        default_seconds=ws.K8S_WORKSPACE_IDLE_SECONDS
    )
    now_wall = time.time()
    now_mono = time.monotonic()
    cache_index = _k8s_cache_index_by_workspace_id()

    try:
        async with k8s_client.ApiClient() as api_client:
            core_v1 = k8s_client.CoreV1Api(api_client)
            pods = await asyncio.wait_for(
                core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector="agentscope.workspace=true",
                ),
                timeout=ADMIN_LIST_TIMEOUT_SECONDS,
            )
            metrics_map = (
                await _fetch_k8s_pod_metrics_map(api_client, k8s_client, namespace)
                if include_stats
                else {}
            )
    except Exception as exc:  # noqa: BLE001
        base_resp["error"] = f"查询 K8s 命名空间 {namespace} 沙箱 Pod 失败：{exc}"
        return base_resp

    items: list[dict[str, Any]] = []
    for pod in getattr(pods, "items", None) or []:
        metadata = getattr(pod, "metadata", None)
        labels = getattr(metadata, "labels", None) or {}
        if str(labels.get("agentscope.workspace") or "").lower() != "true":
            continue
        pod_name = str(getattr(metadata, "name", "") or "")
        pod_uid = str(getattr(metadata, "uid", "") or "")
        workspace_id = str(labels.get("agentscope.workspace.id") or "")
        user_key, skill_token = _split_user_and_token(workspace_id or pod_name)

        try:
            created = float(metadata.creation_timestamp.timestamp())
        except Exception:  # noqa: BLE001
            created = 0.0
        age_seconds = max(0, int(now_wall - created)) if created > 0 else None

        status = getattr(pod, "status", None)
        phase = str(getattr(status, "phase", "") or "Unknown")
        reason = str(getattr(status, "reason", "") or "").strip()
        deleting = bool(getattr(metadata, "deletion_timestamp", None))

        container_statuses = getattr(status, "container_statuses", None) or []
        ready_count = sum(1 for cs in container_statuses if getattr(cs, "ready", False))
        total_containers = len(container_statuses) or 1
        waiting_reason = ""
        for cs in container_statuses:
            cstate = getattr(cs, "state", None)
            waiting = getattr(cstate, "waiting", None) if cstate else None
            if waiting and getattr(waiting, "reason", None):
                waiting_reason = str(waiting.reason)
                break

        if deleting:
            state_detail = "Terminating"
        elif phase == "Running":
            state_detail = f"Running ({ready_count}/{total_containers} Ready)"
        elif waiting_reason:
            state_detail = f"{phase} ({waiting_reason})"
        elif reason:
            state_detail = f"{phase} ({reason})"
        else:
            state_detail = phase

        cached = cache_index.get(workspace_id)
        in_cache = cached is not None and (
            not cached.get("pod_name")
            or not pod_name
            or cached["pod_name"] == pod_name
        )

        is_zombie = (
            phase in {"Failed", "Succeeded", "Unknown"}
            or (
                phase == "Pending"
                and age_seconds is not None
                and age_seconds >= ws.SANDBOX_K8S_PENDING_ZOMBIE_SECONDS
            )
        )
        if is_zombie:
            category = "zombie"
        elif in_cache and not deleting:
            category = "managed"
        else:
            category = "orphan"

        spec = getattr(pod, "spec", None)
        containers = getattr(spec, "containers", None) or []
        first_container = containers[0] if containers else None
        image = str(getattr(first_container, "image", "") or "")
        resources = getattr(first_container, "resources", None)
        limits = getattr(resources, "limits", None) or {}
        cpu_limit_m = _parse_k8s_cpu_millicores(limits.get("cpu"))
        mem_limit_b = _parse_k8s_memory_bytes(limits.get("memory"))

        node_name = str(getattr(spec, "node_name", "") or "")
        pod_ip = str(getattr(status, "pod_ip", "") or "")
        node_or_host = " · ".join(p for p in (node_name, pod_ip) if p) or "待调度"

        volumes = getattr(spec, "volumes", None) or []
        pvc_claim = ""
        for vol in volumes:
            pvc = getattr(vol, "persistent_volume_claim", None)
            if pvc and getattr(pvc, "claim_name", None):
                pvc_claim = str(pvc.claim_name)
                break
        pvc_mode = f"PVC: {pvc_claim}" if pvc_claim else "临时卷"

        idle_left = (
            _compute_idle_seconds_left(
                last_used=cached.get("last_used"),
                refcount=int(cached.get("refcount") or 0),
                idle_seconds=idle_seconds,
                now_monotonic=now_mono,
            )
            if in_cache and cached is not None
            else None
        )

        m = metrics_map.get(pod_name) or {}
        items.append(
            {
                "id": pod_name,
                "short_id": pod_uid[:12] if pod_uid else pod_name[-12:],
                "uid": pod_uid,
                "name": pod_name,
                "namespace": namespace,
                "workspace_id": workspace_id,
                "user_key": user_key,
                "skill_token": skill_token,
                "image": image,
                "runtime_state": "Terminating" if deleting else phase,
                "state_detail": state_detail,
                "category": category,
                "in_cache": in_cache,
                "refcount": int(cached.get("refcount") or 0) if in_cache and cached else 0,
                "created_at": _format_iso_utc(created),
                "age_seconds": age_seconds,
                "idle_seconds_left": idle_left,
                "idle_seconds": int(idle_seconds),
                "idle_multiplier": _idle_multiplier_for(
                    int(cached.get("refcount") or 0) if in_cache and cached else 0
                ),
                "node_or_host": node_or_host,
                "mount_summary": pvc_mode,
                "cpu_millicores": m.get("cpu_millicores"),
                "cpu_limit_millicores": cpu_limit_m,
                "memory_usage_bytes": m.get("memory_usage_bytes"),
                "memory_limit_bytes": mem_limit_b,
            }
        )

    order_rank = {"zombie": 0, "orphan": 1, "managed": 2}
    items.sort(
        key=lambda it: (
            order_rank.get(str(it.get("category")), 9),
            -(it.get("age_seconds") or 0),
        )
    )
    base_resp["available"] = True
    base_resp["items"] = items
    base_resp["summary"] = _build_summary(items)
    return base_resp


async def _read_verified_k8s_sandbox_pod(
    core_v1: Any, namespace: str, pod_name: str
) -> Any:
    pname = _validate_target_id(pod_name, backend="k8s")
    try:
        pod = await asyncio.wait_for(
            core_v1.read_namespaced_pod(name=pname, namespace=namespace),
            timeout=ADMIN_OP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) == 404:
            raise SandboxAdminTargetError(
                f"未找到 Pod {namespace}/{pname}（可能已被销毁）", status_code=404
            ) from exc
        raise SandboxAdminTargetError(
            f"读取 Pod {namespace}/{pname} 失败：{exc}", status_code=502
        ) from exc

    metadata = getattr(pod, "metadata", None)
    labels = getattr(metadata, "labels", None) or {}
    if str(labels.get("agentscope.workspace") or "").lower() != "true":
        raise SandboxAdminTargetError(
            f"拒绝操作非沙箱 Pod {namespace}/{pname}（缺少 agentscope.workspace=true 标签）",
            status_code=403,
        )
    return pod


async def _get_k8s_sandbox_detail(target_id: str, *, tail: int) -> dict[str, Any]:
    namespace, k8s_client = await _load_k8s_namespace_and_client()
    async with k8s_client.ApiClient() as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        pod = await _read_verified_k8s_sandbox_pod(core_v1, namespace, target_id)
        metadata = getattr(pod, "metadata", None)
        labels = getattr(metadata, "labels", None) or {}
        spec = getattr(pod, "spec", None)
        status = getattr(pod, "status", None)

        pod_name = str(getattr(metadata, "name", "") or target_id)
        pod_uid = str(getattr(metadata, "uid", "") or "")
        workspace_id = str(labels.get("agentscope.workspace.id") or "")
        user_key, skill_token = _split_user_and_token(workspace_id or pod_name)

        containers = getattr(spec, "containers", None) or []
        first_c = containers[0] if containers else None
        container_name = str(getattr(first_c, "name", "") or "workspace")
        mounts = []
        for vm in getattr(first_c, "volume_mounts", None) or []:
            mp = getattr(vm, "mount_path", "")
            sp = getattr(vm, "sub_path", "")
            ro = "ro" if getattr(vm, "read_only", False) else "rw"
            mounts.append(f"{sp or '/'} -> {mp} ({ro})")

        deleting = bool(getattr(metadata, "deletion_timestamp", None))
        is_running_pod = (
            str(getattr(status, "phase", "") or "") == "Running" and not deleting
        )
        gateway_log = ""
        if is_running_pod:
            try:
                from app.services.ai.runtime.agentscope.k8s_workspace import (
                    exec_k8s_sandbox_command,
                )

                exec_res = await asyncio.wait_for(
                    exec_k8s_sandbox_command(
                        namespace,
                        pod_name,
                        f"tail -n {tail} {SANDBOX_GATEWAY_LOG_PATH} 2>/dev/null || true",
                    ),
                    timeout=4.0,
                )
                gateway_log = str((exec_res or {}).get("stdout") or "").strip()
            except Exception:  # noqa: BLE001
                gateway_log = ""

        pod_stdout_log = ""
        try:
            raw_logs = await asyncio.wait_for(
                core_v1.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container=container_name,
                    tail_lines=tail,
                ),
                timeout=5.0,
            )
            if isinstance(raw_logs, (bytes, bytearray)):
                pod_stdout_log = raw_logs.decode("utf-8", errors="replace").strip()
            else:
                pod_stdout_log = str(raw_logs or "").strip()
        except Exception as exc:  # noqa: BLE001
            if not gateway_log:
                pod_stdout_log = f"# 获取 Pod 日志失败：{exc}"

        if gateway_log and pod_stdout_log:
            logs_text = (
                f"=== Gateway Log ({SANDBOX_GATEWAY_LOG_PATH}) ===\n"
                f"{gateway_log}\n\n"
                f"=== Pod stdout/stderr ===\n"
                f"{pod_stdout_log}"
            )
        else:
            logs_text = gateway_log or pod_stdout_log

        metrics_map = (
            await _fetch_k8s_pod_metrics_map(api_client, k8s_client, namespace)
            if is_running_pod
            else {}
        )
        m = metrics_map.get(pod_name) or {}

        try:
            created_iso = metadata.creation_timestamp.astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            created_iso = None

        return {
            "backend": "k8s",
            "id": pod_name,
            "short_id": pod_uid[:12] if pod_uid else pod_name[-12:],
            "uid": pod_uid,
            "name": pod_name,
            "namespace": namespace,
            "workspace_id": workspace_id,
            "user_key": user_key,
            "skill_token": skill_token,
            "image": str(getattr(first_c, "image", "") or ""),
            "runtime_state": str(getattr(status, "phase", "") or ""),
            "node_or_host": " · ".join(
                p
                for p in (
                    str(getattr(spec, "node_name", "") or ""),
                    str(getattr(status, "pod_ip", "") or ""),
                )
                if p
            )
            or "待调度",
            "created_at": created_iso,
            "cpu_millicores": m.get("cpu_millicores"),
            "memory_usage_bytes": m.get("memory_usage_bytes"),
            "mounts": mounts,
            "labels": labels,
            "logs": _safe_sanitize_logs(logs_text),
        }


async def _delete_k8s_admin_sandboxes(
    target_ids: list[str], *, action: Literal["kill", "restart"]
) -> dict[str, Any]:
    namespace, k8s_client = await _load_k8s_namespace_and_client()
    results: list[dict[str, Any]] = []
    async with k8s_client.ApiClient() as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        for tid in dict.fromkeys(str(x).strip() for x in target_ids if str(x).strip()):
            try:
                pod = await _read_verified_k8s_sandbox_pod(core_v1, namespace, tid)
                metadata = getattr(pod, "metadata", None)
                labels = getattr(metadata, "labels", None) or {}
                pod_name = str(getattr(metadata, "name", "") or tid)
                pod_uid = str(getattr(metadata, "uid", "") or "")
                workspace_id = str(labels.get("agentscope.workspace.id") or "")

                if workspace_id:
                    snapshot = ws._snapshot_cached_sandboxes(
                        ws._k8s_workspace_cache, ws._k8s_workspace_last_used
                    )
                    for key, expected_last_used, _cid in snapshot.get(workspace_id, []):
                        cached_ws = ws._k8s_workspace_cache.get(key)
                        cached_pod = getattr(cached_ws, "_pod_name", None) or getattr(
                            cached_ws, "pod_name", None
                        )
                        if (
                            isinstance(cached_pod, str)
                            and cached_pod.strip()
                            and pod_name
                            and cached_pod.strip() != pod_name
                        ):
                            continue
                        await ws._evict_k8s_workspace_cache_entry(
                            key,
                            reason=f"admin manual {action}",
                            expected_last_used=expected_last_used,
                        )

                delete_kwargs: dict[str, Any] = {
                    "name": pod_name,
                    "namespace": namespace,
                }
                if pod_uid:
                    delete_kwargs["body"] = k8s_client.V1DeleteOptions(
                        preconditions=k8s_client.V1Preconditions(uid=pod_uid)
                    )
                try:
                    try:
                        await asyncio.wait_for(
                            core_v1.delete_namespaced_pod(**delete_kwargs),
                            timeout=ADMIN_OP_TIMEOUT_SECONDS,
                        )
                    except TypeError:
                        delete_kwargs.pop("body", None)
                        await asyncio.wait_for(
                            core_v1.delete_namespaced_pod(**delete_kwargs),
                            timeout=ADMIN_OP_TIMEOUT_SECONDS,
                        )
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "status", None) not in (404, 409, 412):
                        raise

                if workspace_id:
                    await ws._reclaim_orphan_sandbox_skills_dir_for_workspace_id(
                        workspace_id, policy=ws.SANDBOX_POLICY_K8S
                    )
                logger.warning(
                    "[sandbox_admin] Admin %s k8s sandbox pod=%s namespace=%s workspace_id=%s",
                    action,
                    pod_name,
                    namespace,
                    workspace_id,
                )
                results.append(
                    {
                        "id": pod_name,
                        "name": pod_name,
                        "ok": True,
                        "action": "killed" if action == "kill" else "reset_for_recreate",
                        "message": (
                            "已销毁沙箱 Pod"
                            if action == "kill"
                            else "已销毁旧 Pod 并清理缓存，用户下次使用时将自动拉起全新 Pod"
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"id": tid, "name": tid, "ok": False, "action": action, "message": str(exc)}
                )

    return {
        "backend": "k8s",
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 统一公共入口
# ---------------------------------------------------------------------------


async def list_admin_sandboxes(
    *,
    backend: str | None = None,
    include_stats: bool = False,
) -> dict[str, Any]:
    resolved = await resolve_admin_sandbox_backend(backend)
    if resolved == "docker":
        return await _list_docker_admin_sandboxes(include_stats=include_stats)
    return await _list_k8s_admin_sandboxes(include_stats=include_stats)


async def get_admin_sandbox_detail(
    *,
    backend: str | None = None,
    target_id: str,
    tail: int = ADMIN_LOG_DEFAULT_TAIL,
) -> dict[str, Any]:
    resolved = await resolve_admin_sandbox_backend(backend)
    bounded_tail = max(1, min(int(tail or ADMIN_LOG_DEFAULT_TAIL), ADMIN_LOG_MAX_TAIL))
    if resolved == "docker":
        return await _get_docker_sandbox_detail(target_id, tail=bounded_tail)
    return await _get_k8s_sandbox_detail(target_id, tail=bounded_tail)


async def kill_admin_sandboxes(
    *,
    backend: str | None = None,
    target_ids: list[str],
) -> dict[str, Any]:
    resolved = await resolve_admin_sandbox_backend(backend)
    if not target_ids:
        return {"backend": resolved, "succeeded": 0, "failed": 0, "results": []}
    if len(target_ids) > ADMIN_BATCH_MAX_TARGETS:
        raise SandboxAdminTargetError(
            f"单次批量操作最多支持 {ADMIN_BATCH_MAX_TARGETS} 台沙箱实例",
            status_code=400,
        )
    if resolved == "docker":
        return await _kill_docker_admin_sandboxes(target_ids)
    return await _delete_k8s_admin_sandboxes(target_ids, action="kill")


async def restart_admin_sandboxes(
    *,
    backend: str | None = None,
    target_ids: list[str],
) -> dict[str, Any]:
    resolved = await resolve_admin_sandbox_backend(backend)
    if not target_ids:
        return {"backend": resolved, "succeeded": 0, "failed": 0, "results": []}
    if len(target_ids) > ADMIN_BATCH_MAX_TARGETS:
        raise SandboxAdminTargetError(
            f"单次批量操作最多支持 {ADMIN_BATCH_MAX_TARGETS} 台沙箱实例",
            status_code=400,
        )
    if resolved == "docker":
        return await _restart_docker_admin_sandboxes(target_ids)
    return await _delete_k8s_admin_sandboxes(target_ids, action="restart")


async def cleanup_stale_admin_sandboxes(
    *,
    backend: str | None = None,
    include_running_orphans: bool = True,
) -> dict[str, Any]:
    """一键清理所有僵死终态及（可选）脱离注册表的孤儿沙箱，绝不触碰受管运行中沙箱。"""
    resolved = await resolve_admin_sandbox_backend(backend)
    listing = await list_admin_sandboxes(backend=resolved, include_stats=False)
    if not listing.get("available"):
        raise SandboxAdminTargetError(
            listing.get("error") or f"无法连接 {resolved} 沙箱后端", status_code=503
        )
    allowed_categories = {"zombie", "orphan"} if include_running_orphans else {"zombie"}
    target_ids = [
        str(item["id"])
        for item in listing.get("items") or []
        if item.get("category") in allowed_categories
        and item.get("runtime_state") != "Terminating"
        and item.get("id")
    ]
    if not target_ids:
        return {
            "backend": resolved,
            "succeeded": 0,
            "failed": 0,
            "results": [],
        }
    # 服务端自查出的全量孤儿/僵死直接走内部销毁，不受前端手选 200 台上限阻断
    if resolved == "docker":
        return await _kill_docker_admin_sandboxes(target_ids)
    return await _delete_k8s_admin_sandboxes(target_ids, action="kill")
