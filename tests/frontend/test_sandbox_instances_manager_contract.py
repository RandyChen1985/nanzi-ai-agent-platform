"""安全沙箱 Docker/K8s 容器与 Pod 运维管理弹层前端契约测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
MODAL = ROOT / "frontend/src/components/system/SandboxInstancesModal.vue"
SANDBOX_API = ROOT / "app/api/v1/endpoints/sandbox.py"


def test_system_config_gates_sandbox_instances_entry_by_policy_and_permission():
    src = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "import SandboxInstancesModal" in src
    assert "String(category) === 'sandbox' && canSave && sandboxManageBackend" in src
    assert "v-if=\"canSave && (item.value === 'docker' || item.value === 'k8s')\"" in src
    assert "<SandboxInstancesModal" in src

    start = src.index("const fetchSandboxInstancesSummary = async")
    call = src.index("/api/v1/admin/sandbox/instances", start)
    assert "!canSave" in src[start:call], "拉取摘要前必须检查 canSave 防止无权静默 403"


def test_sandbox_instances_modal_matches_backend_admin_endpoints():
    modal_src = MODAL.read_text(encoding="utf-8")
    backend_src = SANDBOX_API.read_text(encoding="utf-8")

    for path in (
        "/admin/sandbox/instances",
        "/admin/sandbox/instances/kill",
        "/admin/sandbox/instances/restart",
        "/admin/sandbox/instances/cleanup-stale",
    ):
        assert path in backend_src, f"后端缺少端点 {path}"
        assert f"/api/v1{path}" in modal_src, f"前端弹窗未对接 {path}"

    assert "/detail" in modal_src
    assert "/admin/sandbox/instances/{target_id}/detail" in backend_src
    assert "include_stats: false" in modal_src
    assert "include_stats: true" in modal_src
    assert "<Teleport to=\"body\">" in modal_src


def test_idle_reclaim_quota_explains_the_referenced_relaxation():
    """剩余时间是 sandbox_idle_time 的 N 倍时必须解释清楚，否则管理员会以为配置没生效。

    背景（用户）：系统配置里 sandbox_idle_time 设成 15 分钟，沙箱实例面板却显示
    「60 分钟后空闲回收」——那是「仍被会话引用（refcount > 0）」时按 4 倍放宽的正常结果，
    但页面一个字都没解释，读者只能以为配置没读到。
    """
    modal_src = MODAL.read_text(encoding="utf-8")
    admin_src = (ROOT / "app/services/ai/runtime/agentscope/sandbox_admin.py").read_text(encoding="utf-8")
    system_config_src = SYSTEM_CONFIG.read_text(encoding="utf-8")

    # 后端：倍数与基础阈值必须有单一来源，并且随条目一起下发
    assert "def _idle_multiplier_for(refcount: int) -> float:" in admin_src
    assert "deadline_seconds = idle_seconds * _idle_multiplier_for(refcount)" in admin_src
    assert admin_src.count('"idle_seconds": int(idle_seconds),') == 2, "Docker 与 K8s 两条链路都要下发基础阈值"
    assert admin_src.count('"idle_multiplier": _idle_multiplier_for(') == 2

    # 前端：类型、格式化函数与模板渲染缺一不可（只加类型不渲染等于没做）
    assert "idle_seconds?: number | null" in modal_src
    assert "idle_multiplier?: number | null" in modal_src
    assert "const formatIdleQuotaNote = (item: SandboxInstanceItem): string => {" in modal_src
    assert "受管引用 · 阈值放宽 ${multiplier}×（基础 ${Math.round(baseSeconds / 60)} 分钟）" in modal_src
    assert 'v-if="formatIdleQuotaNote(item)"' in modal_src

    # 配置项自身也要写明这条规则（文案来自库里的 description，前端补一段，不动迁移）
    assert "if (item.key === 'sandbox_idle_time') {" in system_config_src
    sandbox_note = system_config_src[system_config_src.index("if (item.key === 'sandbox_idle_time') {"):]
    sandbox_note = sandbox_note[: sandbox_note.index("}\n") + 1]
    assert "按 4 倍放宽" in sandbox_note
    assert "沙箱实例面板会标出当前生效的倍数" in sandbox_note
