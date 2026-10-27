"""Contract tests for the user question card and receipt helpers."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_user_question_parser_and_receipt_contract():
    result = _run_typescript(
        "frontend/src/utils/userQuestion.ts",
        """
const parsed = api.parseUserQuestionEvent({
  type: 'user_question',
  question_id: 'uq_1',
  question: '按什么维度统计？',
  options: [{ id: 'daily', label: '按天' }, { id: 'monthly', label: '按月' }],
  is_multi_select: false,
  allow_custom_input: true,
  context: '销售数据',
});
return {
  parsed,
  receipt: api.buildUserQuestionUserMessage('uq_1', ['monthly'], '排除退款', false, '按什么维度统计？', [{ id: 'daily', label: '按天' }, { id: 'monthly', label: '按月' }]),
  cancelledReceipt: api.buildUserQuestionUserMessage('uq_1', [], '', true, '按什么维度统计？'),
};
""",
    )
    assert result["parsed"]["question_id"] == "uq_1"
    assert result["parsed"]["options"][1]["id"] == "monthly"
    assert result["receipt"].startswith("【用户回答】")
    assert "问题: 按什么维度统计？" in result["receipt"]
    assert "所选选项: 按月 (monthly)" in result["receipt"]
    assert "question_id: uq_1" in result["receipt"]
    assert 'selected_option_ids: ["monthly"]' in result["receipt"]
    assert "排除退款" in result["receipt"]
    assert "cancelled: true" in result["cancelledReceipt"]
    assert "停止当前任务" in result["cancelledReceipt"]


def test_user_question_frontend_wiring_is_independent_from_business_confirmation():
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")
    handlers = (ROOT / "frontend/src/utils/agentscopeSseHandlers.ts").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "UserQuestionCard" in card
    assert "is_multi_select" in card
    assert "allow_custom_input" in card
    assert 'case "user_question"' in handlers
    assert "handleUserQuestion" in handlers
    assert "UserQuestionCard" in embed
    assert "UserQuestionCard" in debug
    assert "取消提问" in card
    assert "cancelled" in card
    assert "BusinessConfirmationCard" not in card


def test_user_question_card_submit_reentrancy_guard():
    """提交/取消防重入守卫：isSubmitting 应纳入按钮禁用与 emit 前置判断。"""
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")

    # 防重入标志存在，并进入 submit/cancel 的前置判断
    assert "const isSubmitting = ref(false)" in card
    assert "isSubmitting.value = true" in card
    assert "isSubmitting.value) return" in card
    # 提交/取消按钮在提交期间禁用
    assert 'disabled="locked || isSubmitting' in card
    # 通过 status 回写复位，避免父组件未同步时卡死
    assert "props.payload.status === \"pending\"" in card
    assert 'if (nextStatus !== "pending") {' in card


def test_user_question_card_expanded_state_is_source_driven():
    """展开态由单一职责 watch 驱动：question_id 负责整体重置，status 单独驱动折叠。"""
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")

    # 不再使用 immediate 初始化强制重置（避免初始化阶段与 status watch 重复写 expanded）
    assert "const expanded = ref(props.payload.status === \"pending\")" in card
    # question_id watch 负责换问题时整体重置（无 immediate）
    assert '() => props.payload.question_id' in card
    # status watch 单独驱动展开/折叠
    assert "nextStatus !== \"pending\" && prevStatus === \"pending\"" in card
    # 不允许在 question_id watch 上使用 immediate:true
    assert "question_id,\n  () => {\n    // 同一实例换到新问题" in card
    assert "immediate: true" not in card


def test_cards_collapsible_and_toggle_contract():
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")
    biz_card = (ROOT / "frontend/src/components/BusinessConfirmationCard.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    # UserQuestionCard 折叠支持
    assert "toggleExpand" in card
    assert "rotate-180" in card
    assert "expanded" in card

    # BusinessConfirmationCard 折叠支持
    assert "toggleExpand" in biz_card
    assert "rotate-180" in biz_card
    assert "expanded" in biz_card

    # EmbedChat 与 AgentDebug 中的工具确认框与外部执行框折叠支持
    assert "msg.pendingPermission.expanded" in embed
    assert "msg.pendingExternalExecution.expanded" in embed
    assert "msg.pendingPermission.expanded" in debug
    assert "msg.pendingExternalExecution.expanded" in debug



def test_option_disabled_survives_parsing_and_renders_locked():
    """禁用态要一路活到渲染：解析时丢了，卡片就又是一片可点。"""
    result = _run_typescript(
        "frontend/src/utils/userQuestion.ts",
        """
const parsed = api.parseUserQuestionEvent({
  type: 'user_question',
  question_id: 'uq_scope',
  question: '要检索哪些知识库？',
  options: [
    { id: '__scope_all__', label: '全部 12 个库' },
    { id: '__scope_bound__', label: '仅智能体默认（未绑定）', description: '该智能体尚未绑定知识库', disabled: true },
  ],
  is_multi_select: false,
  allow_custom_input: true,
});
return { options: parsed.options };
""",
    )
    options = result["options"]
    assert options[0]["disabled"] is False
    assert options[1]["disabled"] is True
    assert options[1]["description"] == "该智能体尚未绑定知识库"


def test_card_blocks_toggling_and_submitting_disabled_options():
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")

    assert ":disabled=\"locked || option.disabled\"" in card
    assert "if (locked.value || isOptionDisabled(id)) return;" in card
    # 多选场景下可能先选中、之后才被置灰，提交时必须剔除
    assert "selectedOptionIds: selectedOptionIds.value.filter((id) => !isOptionDisabled(id))," in card
    assert "cursor-not-allowed" in card


def test_option_action_is_parsed_and_fires_alongside_selection():
    """选项可以带页面动作，但动作是"顺手做"，不替代选中与提交。"""
    result = _run_typescript(
        "frontend/src/utils/userQuestion.ts",
        """
const parsed = api.parseUserQuestionEvent({
  type: 'user_question',
  question_id: 'uq_scope',
  question: '要检索哪些知识库？',
  options: [
    { id: '__scope_all__', label: '全部 12 个库' },
    { id: '__scope_pick__', label: '我自己指定', action: 'open_dataset_picker' },
  ],
  is_multi_select: false,
  allow_custom_input: true,
});
return { options: parsed.options };
""",
    )
    # 未带 action 的选项序列化后不出现该键（undefined 被 JSON 丢弃），不是 None
    assert result["options"][0].get("action") is None
    assert result["options"][1]["action"] == "open_dataset_picker"


def test_card_emits_option_action_without_swallowing_submit():
    card = (ROOT / "frontend/src/components/UserQuestionCard.vue").read_text(encoding="utf-8")
    assert '(event: "option-action", action: string): void;' in card
    # 单选：选中之后再发动作
    assert 'selectedOptionIds.value = [id];' in card
    assert 'if (action) emit("option-action", action);' in card
    # 多选：取消选中不该触发动作，只有选上才算点它
    assert 'if (action && next.includes(id)) emit("option-action", action);' in card
    # 提交逻辑不能被动作影响
    assert 'emit("submit", {' in card


@pytest.mark.parametrize(
    "page", ["frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"]
)
def test_pages_wire_option_action_to_knowledge_portal(page):
    """「我自己指定」点了就该把知识库门户拉出来，省得用户自己找入口。"""
    source = (ROOT / page).read_text(encoding="utf-8")

    assert '@option-action="handleUserQuestionOptionAction"' in source
    assert "function handleUserQuestionOptionAction(action: string)" in source
    handler = source[source.index("function handleUserQuestionOptionAction"):]
    handler = handler[: handler.index("\n}") + 2]
    assert 'action === "open_dataset_picker"' in handler
    # 必须走统一入口：它才会 await fetchDatasets()，只翻开关会得到空面板
    assert "void openKnowledgePortal();" in handler
    assert "showKnowledgePortal.value = true" not in handler


def test_scope_pick_option_carries_the_action():
    gate = (ROOT / "app/services/ai/knowledge_scope_gate.py").read_text(encoding="utf-8")
    pick_block = gate[gate.index('"id": SCOPE_PICK,'):]
    pick_block = pick_block[: pick_block.index("},")]
    assert '"action": "open_dataset_picker"' in pick_block
