"""系统级「审核中」在卡片上的锁定契约。

这里锁的是**用户能感知到的后果**，不是实现细节：

- 审核中的卡片必须有明确的冻结条（原型里的 freeze-bar），否则用户只看到
  「审核中」却不知道自己为什么点不动；
- 编辑 / 配置与发布 / 启用开关 / 删除都要真的不可用，而不是让用户点进去
  再吃一个 409（那是把错误提示当交互设计）；
- **复制智能体必须保留**——所有者此时唯一的出路就是复制一份继续开发，
  把它一起锁掉等于把人堵死。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend" / "src" / "views" / "AgentManagement.vue"


def _source() -> str:
    return MANAGEMENT.read_text(encoding="utf-8")


def test_pending_review_has_explicit_freeze_bar():
    src = _source()

    # 判定与文案集中在两个 helper 里，卡片只负责渲染
    assert "const isPendingReview = (agent: AIAgent): boolean" in src
    assert "agent.system_status === 'PENDING_REVIEW'" in src
    assert "const pendingReviewHint = (agent: AIAgent): string" in src
    assert "已冻结 · " in src
    # 冻结条要带上提交时间，用户才知道自己等了多久
    assert "system_requested_at" in src
    # 冻结条可点，直接看申请进度（而不是让人去猜在哪儿看）
    assert 'v-if="isPendingReview(agent)"' in src
    assert "openSystemHistory(agent)" in src


def test_pending_review_locks_primary_action_and_switch():
    src = _source()

    # 主按钮变成不可点的「审核中」，并解释原因与出路
    assert "if (isPendingReview(agent)) return 'frozen'" in src
    assert "if (action === 'frozen') return '审核中'" in src
    assert ":disabled=\"getPrimaryCardAction(agent) === 'frozen'\"" in src
    assert "审核中不能修改配置；可先撤回申请，或复制一份继续开发" in src

    # 启用/禁用开关：待审期间后端会 409，前端不该让人点
    assert ':disabled="isPendingReview(agent)"' in src
    assert "审核中不能改动启用状态；先撤回申请" in src


def test_pending_review_hides_edit_and_delete_but_keeps_duplicate():
    src = _source()

    # 编辑：网格菜单直接不出现
    assert 'v-if="agent.is_editable !== false && !isPendingReview(agent)"' in src
    # 删除：三个排除条件都在
    assert "!isPendingReview(agent)" in src
    # 列表行内的编辑按钮保留位置但禁用（避免图标跳动）
    assert "disabled:opacity-40 disabled:cursor-not-allowed" in src
    assert "审核中不能编辑，先撤回申请" in src

    # 复制入口不能被审核中条件排除：它是所有者唯一的自救路径
    duplicate_blocks = [
        block
        for block in src.split("openDuplicateModal(agent)")[:-1]
    ]
    assert duplicate_blocks, "复制入口必须存在"
    for block in duplicate_blocks:
        tail = block[-400:]
        assert "isPendingReview" not in tail, "复制入口不能被审核中条件拦住"


def test_header_puts_primary_actions_on_their_own_row():
    """主操作独立成行，宽度再窄也不会被挤到筛选控件后面。

    上一版把「视图切换 / 发布审核 / 新建」打包成一组参与换行，只是缓解；
    现在「发布审核 + 新建」直接放在标题行右侧，和下方搜索/筛选不在同一个 flex 流里。
    """
    src = _source()

    assert "第 1 行：标题 + 主操作固定右上" in src
    assert 'class="flex shrink-0 items-center gap-2"' in src
    # 搜索独占一栏
    assert "第 2 行：搜索独占一栏，视图切换与「筛选」跟在后面" in src
    assert 'class="relative min-w-[220px] flex-1"' in src
    # 旧的「统一操作组」包法已不存在
    assert "操作组：视图切换 / 发布审核 / 新建" not in src


def test_agent_payload_snapshot_taken_after_form_sync():
    """`agentPayload` 是浅拷贝，必须在所有 `agentForm` 同步之后才取值。

    早一步取值（曾经的实现）会让 engine_config / agent_type / capabilities
    的改动进不了 payload——保存后配置回退，属于静默数据丢失。
    """
    src = _source()

    snapshot_at = src.index("...agentPayload }")
    for later_write in (
        "agentForm.value.engine_config = {",
        "agentForm.value.agent_type = 'GENERAL';",
        "agentForm.value.engine_config.default_markdown_theme =",
    ):
        assert src.index(later_write) < snapshot_at, (
            f"{later_write} 在 payload 快照之后才写入，会被丢掉"
        )
    # 三个提交口都用同一个 payload
    assert "agentApi.updateAgent(selectedAgent.value.id, agentPayload as any)" in src
    assert "await agentApi.createAgent(agentPayload as any)" in src
