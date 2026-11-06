"""契约测试：设「到期时间」时要提示「到期后权限会变大」。

背景：``expire_at`` 的语义是**整行失效**，不是权限降级 ——
``dataset_scope_service._is_expired`` 命中后直接 ``continue``，这条授权等于从未存在。
于是到期后真正生效的，是该用户在「没有这条授权」时的范围。

真实数据（数据集 1，用户 178）：

    #15593  role:4    scope=all        → 31 张表全开（默认角色）
    #15663  user:178  scope=list{92}   → 只 1 张表（收窄）

用户级覆盖角色级，所以现在生效的是 1 张表。**一旦给 #15663 设到期时间并到期**，
回落结果实测为 ``all []`` —— 1 张表变成 31 张。管理员设「到期」的预期是「到期收回」，
实际是「到期提权」，这是反直觉的静默提权。

本文件锁住配置阶段的风险提示：``expiryFallback`` 计算 + Step 4 的琥珀色警告块。
前端不知道「用户是否属于某角色」（成员关系在后端），所以分两档：
全员授权命中 → 确定回落；只有角色授权 → 提示「可能」。
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[2]
_CONSOLE = (_REPO / "frontend/src/components/metadata/DatasetPermissionConsole.vue").read_text(
    encoding="utf-8"
)


def _block(start: str, end: str) -> str:
    begin = _CONSOLE.index(start)
    return _CONSOLE[begin : _CONSOLE.index(end, begin)]


def _fallback_computed() -> str:
    return _block("const expiryFallback = computed(() => {", "const draftSummary = computed")


def _step4() -> str:
    # 有效期是抽屉的最后一步（加了字段范围后从 Step 4 顺延为 Step 5），
    # 用抽屉底部的翻页区作为结束边界
    return _block(
        "<!-- Step 5 有效期 -->",
        'class="flex items-center justify-between gap-3 border-t',
    )


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------


def test_fallback_only_evaluated_when_expiry_set() -> None:
    """没选「到期时间」时不做任何判断，避免凭空吓人。"""
    block = _fallback_computed()
    assert "draft.expiryMode !== 'date'" in block
    assert "!draft.expireAt" in block


def test_fallback_only_for_user_subjects() -> None:
    """只有「按人收窄」才会回落变宽。

    角色/全员本来就是这个数据集最宽的层，给它们设到期不会出现反直觉提权，
    对它们弹提示反而干扰。
    """
    block = _fallback_computed()
    assert "if (!draft.userIds.length) return null" in block


def test_fallback_ignores_expired_grants() -> None:
    """已经过期的授权不会再接管，不能把它当成回落目标。"""
    block = _fallback_computed()
    assert "allMembersGrant.value?.status === 'expired' ? null" in block
    assert "filter((g) => g.status !== 'expired')" in block


def test_fallback_distinguishes_definite_and_possible() -> None:
    """全员授权对所有人确定生效；角色授权取决于成员关系，只能「可能」。"""
    block = _fallback_computed()
    assert "definite: !!liveBase" in block
    assert "fallback: liveBase ? '全员授权' : '角色授权'" in block


def test_fallback_compares_table_counts() -> None:
    """全员授权本身也可能是 list，必须比表数才知道是不是真的变宽了。"""
    block = _fallback_computed()
    assert "liveBase.mode === 'all'" in block
    assert "liveBase.tables.length > draft.tableIds.length" in block


def test_fallback_covers_row_policy_regression() -> None:
    """行级条件失效是独立风险：表范围没变宽，但自定义行级条件会回落。"""
    block = _fallback_computed()
    assert "const rowWider = draft.rowPolicy === 'custom' && fallbackPolicy !== 'custom'" in block
    assert "if (!tableWider && !rowWider) return null" in block


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------


def test_step4_renders_warning_block() -> None:
    step4 = _step4()
    assert 'v-if="expiryFallback"' in step4
    # 与「长期有效 / 到期时间」的选择按钮区分开：琥珀色警示，不是绿色确认色
    assert "border-amber-200 bg-amber-50/80" in step4
    assert "整行失效" in step4


def test_warning_explains_how_to_actually_revoke() -> None:
    """只说「会变大」没用，要告诉管理员怎么才能真正收回。"""
    step4 = _step4()
    assert "长期有效」后手动移除" in step4
    assert "同时收窄上述授权" in step4


def test_warning_shows_concrete_fallback_target() -> None:
    step4 = _step4()
    assert "expiryFallback.certain" in step4
    assert "expiryFallback.tableCount" in step4
    assert "expiryFallback.rowWider" in step4


def test_warning_does_not_block_save() -> None:
    """风险提示是「让结果可预期」，不是禁止配置 —— 临时收紧再到期是合法用法。"""
    can_next = _block("const canNext = computed", "const expiryFallback = computed")
    assert "expiryFallback" not in can_next
