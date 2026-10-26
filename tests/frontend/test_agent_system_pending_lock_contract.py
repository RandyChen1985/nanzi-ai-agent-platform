"""系统级「审核中」在卡片上的锁定契约。

这里锁的是**用户能感知到的后果**，不是实现细节：

- 审核中的卡片必须有明确的提示条，说清**冻的是发布**、不是开发；
- 待审期间草稿照改、版本照建、调试照用——只有发布被拦住。
  卡片的「配置与发布」必须仍然可点，锁掉它等于要求用户提交申请后彻底停工；
- 元数据（改名 / 启停 / 删除）仍然锁死：提交时那份配置才是审核对象；
- **复制智能体必须保留**——想独立发布时的出口。
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
    assert "审核中 · " in src
    # 冻结条要带上提交时间，用户才知道自己等了多久
    assert "system_requested_at" in src
    # 冻结条可点，直接看申请进度（而不是让人去猜在哪儿看）
    assert 'v-if="isPendingReview(agent)"' in src
    assert "openSystemHistory(agent)" in src


def test_pending_review_locks_primary_action_and_switch():
    src = _source()

    # 主按钮**不锁**：待审期间仍要能进「配置与发布」改草稿
    assert "if (isPendingReview(agent)) return 'frozen'" not in src, \
        "主按钮不能在待审时被锁死——草稿开发不该停"
    assert "审核中不能发布新配置" in src

    # 启用/禁用开关仍然锁死（属于元数据）
    assert ':disabled="isPendingReview(agent)"' in src
    assert "审核中不能改动启用状态" in src


def test_pending_review_hides_edit_and_delete_but_keeps_duplicate():
    src = _source()

    # 编辑：网格菜单直接不出现
    assert 'v-if="agent.is_editable !== false && !isPendingReview(agent)"' in src
    # 删除：三个排除条件都在
    assert "!isPendingReview(agent)" in src
    # 列表行内的编辑按钮保留位置但禁用（避免图标跳动）
    assert "disabled:opacity-40 disabled:cursor-not-allowed" in src
    assert "审核中不能改元数据" in src

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

    # 剥离 is_system 的 helper 自身必须真的剔除该字段（后端对新建传 true 直接 400）
    helper = src[src.index("const agentPayloadWithoutSystemFlag = "):src.index("const saveAgent = ")]
    assert "is_system: _ignoredSystemFlag" in helper

    snapshot_at = src.index("const agentPayload = agentPayloadWithoutSystemFlag();")
    for later_write in (
        "agentForm.value.engine_config = {",
        "agentForm.value.agent_type = 'GENERAL';",
        "agentForm.value.engine_config.default_markdown_theme =",
    ):
        assert src.index(later_write) < snapshot_at, (
            f"{later_write} 在 payload 快照之后才写入，会被丢掉"
        )
    # 三个提交口都用同一个 payload
    # 更新时要在 agentPayload 基础上回传当前 is_system（省略会被后端判定成「想改成非系统级」）
    assert "agentApi.updateAgent(selectedAgent.value.id, {" in src
    assert "is_system: selectedAgent.value.is_system" in src
    assert "await agentApi.createAgent(agentPayload as any)" in src
    # 新建链路（外部引擎直建 / onboarding）一律走剥离 helper，不得整包透传 agentForm
    assert "createAgent(agentForm.value)" not in src
    assert "await agentApi.createAgent(agentPayloadWithoutSystemFlag() as any)" in src
