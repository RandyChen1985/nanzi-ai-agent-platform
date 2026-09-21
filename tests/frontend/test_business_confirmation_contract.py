"""Contract tests for business data confirmation card wiring."""
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


def test_business_confirmation_message_builder_includes_prefix_and_snapshot():
    result = _run_typescript(
        "frontend/src/utils/businessConfirmation.ts",
        """
const fields = [
  { key: 'supplier_name', label: '供应商名称', value: '北京神马科技有限公司' },
  { key: 'note', label: '备注', value: '新供应商' },
];
return {
  confirm: api.buildBusinessConfirmationUserMessage(true, 'bc_1', fields),
  cancel: api.buildBusinessConfirmationUserMessage(false, 'bc_1', fields),
  prefix: api.BUSINESS_CONFIRMATION_MESSAGE_PREFIX,
};
""",
    )
    assert result["prefix"] == "【业务确认】"
    assert "【业务确认】用户已确定" in result["confirm"]
    assert "supplier_name" in result["confirm"]
    assert "北京神马科技有限公司" in result["confirm"]
    assert "【业务确认】用户已取消" in result["cancel"]
    assert "不要调用写入类工具" in result["cancel"]
    assert "禁止再次调用 request_user_confirmation" in result["cancel"]
    assert "不要重新弹确认卡" in result["cancel"]


def test_business_confirmation_sse_parser_and_stale_marking():
    result = _run_typescript(
        "frontend/src/utils/businessConfirmation.ts",
        """
const parsed = api.parseBusinessConfirmationEvent({
  type: 'business_confirmation',
  confirmation_id: 'bc_new',
  title: '请确认',
  fields: [{ key: 'a', label: 'A', value: '1' }],
  confirm_label: '确定',
  cancel_label: '取消',
});
const messages = [
  { businessConfirmation: { confirmation_id: 'bc_old', status: 'pending', title: '旧', fields: [], confirm_label: '确定', cancel_label: '取消' } },
  { businessConfirmation: parsed },
];
api.markOtherBusinessConfirmationsStale(messages, 'bc_new');
return {
  ok: !!parsed,
  oldStatus: messages[0].businessConfirmation.status,
  newStatus: messages[1].businessConfirmation.status,
  suppressCancel: api.shouldSuppressBusinessConfirmation([
    { role: 'user', content: '【业务确认】用户已取消\\nconfirmation_id: bc_1' },
  ]),
  allowAfterNewInput: api.shouldSuppressBusinessConfirmation([
    { role: 'user', content: '【业务确认】用户已取消' },
    { role: 'agent', content: '已取消' },
    { role: 'user', content: '把备注改成新的，继续录入' },
  ]),
};
""",
    )
    assert result["ok"] is True
    assert result["oldStatus"] == "stale"
    assert result["newStatus"] == "pending"
    assert result["suppressCancel"] is True
    assert result["allowAfterNewInput"] is False


def test_business_confirmation_frontend_wiring_contract():
    card = (ROOT / "frontend/src/components/BusinessConfirmationCard.vue").read_text(encoding="utf-8")
    handlers = (ROOT / "frontend/src/utils/agentscopeSseHandlers.ts").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")
    util = (ROOT / "frontend/src/utils/businessConfirmation.ts").read_text(encoding="utf-8")

    assert "业务数据确认" in card or "请确认以下信息" in card
    assert "emit('submit'" in card or 'emit("submit"' in card or "event: 'submit'" in card
    assert 'case "business_confirmation"' in handlers
    assert "handleBusinessConfirmation" in handlers
    assert "shouldSuppressBusinessConfirmation" in handlers
    assert "已拦截业务确认卡" in handlers
    assert "shouldSuppressBusinessConfirmation" in util
    assert "BusinessConfirmationCard" in embed
    assert "submitBusinessConfirmation" in embed
    assert embed.index("MessageRenderer") < embed.index("<BusinessConfirmationCard") or embed.rfind("BusinessConfirmationCard") > embed.find('v-if="msg.content && !msg.groundingBlocked"')
    # 确认卡应出现在主正文区块之后（避免排在 AI 消息前面）
    content_marker = 'v-if="msg.content && !msg.groundingBlocked"'
    assert embed.find(content_marker) < embed.find("<BusinessConfirmationCard")
    assert debug.find(content_marker) < debug.find("<BusinessConfirmationCard")
    assert "buildBusinessConfirmationUserMessage" in embed
    assert "BusinessConfirmationCard" in debug
    assert "submitBusinessConfirmation" in debug
    # EmbedChat 的派发器新增了 bash 环境横幅事件处理回调作为第五个参数。
    assert "dispatchAgentscopeStreamEvent(agentMsg.value, data, addEmbedLogFromStream, messages.value, handleBashEnvEvent)" in embed
    assert "dispatchAgentscopeStreamEvent(agentMsg.value, data, addRealLog, messages.value)" in debug
    assert 'hide-quick-buttons="!!msg.businessConfirmation || !!msg.userQuestion"' in embed
    assert 'hide-quick-buttons="!!msg.businessConfirmation || !!msg.userQuestion"' in debug
    assert "hideQuickButtons" in (ROOT / "frontend/src/components/MessageRenderer.vue").read_text(encoding="utf-8")
    assert "stripQuickButtons" in (ROOT / "frontend/src/utils/quickButtons.ts").read_text(encoding="utf-8")


def test_strip_quick_buttons_removes_quick_markdown():
    result = _run_typescript(
        "frontend/src/utils/quickButtons.ts",
        """
const text = '请核对确认卡信息。\\n\\n确认完成后，您还可以继续:\\n- [确认录入该供应商](quick:确认录入)\\n- [取消本次录入](quick:取消录入)\\n';
const classic = '结果已生成。\\n\\n### 💬 您可能还想了解\\n---\\n- [查看趋势](quick:查看趋势)\\n- [对比明细](quick:对比明细)\\n';
return {
  stripped: api.stripQuickButtons(text),
  classic: api.stripQuickButtons(classic),
  parsedHasBtn: api.parseQuickButtons(text).includes('quick-action-btn'),
};
""",
    )
    assert "请核对确认卡信息。" in result["stripped"]
    assert "确认完成后" not in result["stripped"]
    assert "还可以继续" not in result["stripped"]
    assert "确认录入该供应商" not in result["stripped"]
    assert "取消本次录入" not in result["stripped"]
    assert "-" not in result["stripped"].strip().splitlines()[-1] if result["stripped"].strip() else True
    assert result["stripped"].count("•") == 0
    assert result["classic"] == "结果已生成。"
    assert result["parsedHasBtn"] is True
