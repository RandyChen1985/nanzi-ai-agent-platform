"""聊天日志页面方案 A（精炼文档流）重构契约。

覆盖本次 UI/UX 重构的关键设计边界：
1. 状态字典全中文化（`cancelled` 等不再以全大写英文混排在「成功/异常」旁），快捷筛选支持 `cancelled`；
2. TaskCenter 自动化任务前缀解析（剥离全局执行规则样板头，提取任务 ID 与真实指令摘要）；
3. 单行融合工具条、左侧列表可折叠、多轮快捷定位条与可折叠轨迹瀑布；
4. `chatLogPresentation.ts` 纯函数语义边界稳定。
"""

from pathlib import Path
import json
import subprocess

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CHAT_LOGS = ROOT / "frontend/src/views/ChatLogs.vue"
PRESENTATION_TS = ROOT / "frontend/src/utils/chatLogPresentation.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_chat_logs_uses_presentation_helpers_and_chinese_status():
    source = _read(CHAT_LOGS)
    helper = _read(PRESENTATION_TS)

    assert "from '@/utils/chatLogPresentation'" in source
    assert "getChatLogStatusMeta(" in source
    assert "parseChatLogQuery(" in source
    assert "summarizeTraceStep(" in source
    assert "countTurnToolSteps(" in source

    # 快捷筛选和高级筛选都必须覆盖已取消状态
    assert "value: 'cancelled', label: '已取消'" in source
    assert '<option value="cancelled">已取消</option>' in source

    # 状态映射必须包含 cancelled / interrupted / awaiting_* 中文标签
    assert "cancelled:" in helper
    assert "label: '已取消'" in helper
    assert "label: '待回答'" in helper
    assert "label: '待审批'" in helper


def test_chat_logs_supports_list_collapse_turn_jump_and_collapsible_trace():
    source = _read(CHAT_LOGS)

    # 左侧列表折叠开关
    assert "const isListCollapsed = ref(false)" in source
    assert 'v-show="!isListCollapsed"' in source

    # 多轮快捷定位条与平滑滚动锚点
    assert "const scrollToTurn =" in source
    assert 'chat-log-turn-${index}' in source
    assert "轮次定位" in source

    # 轨迹步骤支持单步折叠与一键全部展开/收起
    assert "const isTraceStepExpanded =" in source
    assert "const setAllTraceStepsExpanded =" in source
    assert "全部展开" in source
    assert "全部收起" in source
    assert "stepDurationPercent(" in source

    # 自动化系统执行规则默认折叠、支持按需展开原文
    assert "收起系统规则" in source
    assert "查看系统规则" in source


def test_chat_log_presentation_ts_runtime_behavior():
    """通过 Node 直接运行 TypeScript 转译后的纯函数，验证状态字典与 TaskCenter 指令提取行为。"""
    ts_runner = r"""
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const src = fs.readFileSync('./frontend/src/utils/chatLogPresentation.ts', 'utf8');
const { outputText } = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
});
const mod = { exports: {} };
new Function('exports', 'require', 'module', outputText)(mod.exports, require, mod);
const api = mod.exports;

const rawAuto = [
  '【🌐 TaskCenter 自动化全局执行规则】',
  '1. 无人值守模式：本次为后台自动触发运行。',
  '',
  '【📋 任务执行指令 - ID: 42】@主助手(Main)',
  '执行每日 10:00 知识库索引巡检并汇报失败文档。',
  '',
  '【📢 通知投递】推送到飞书群'
].join('\n');

const out = {
  cancelled: api.getChatLogStatusMeta('CANCELLED').label,
  success: api.getChatLogStatusMeta('success').label,
  awaiting: api.getChatLogStatusMeta('awaiting_user').label,
  autoParsed: api.parseChatLogQuery(rawAuto),
  humanParsed: api.parseChatLogQuery('我们有哪些目录权限'),
  stepSummaryErr: api.summarizeTraceStep({ error_message: 'HTTP 403 Forbidden' }),
  stepSummaryObj: api.summarizeTraceStep({ tool_input: JSON.stringify({ path: '/workspace/a.md' }) }),
  toolCount: api.countTurnToolSteps([
    { kind: 'log', id: 1, title: 'a', details: '', status: 'success' },
    { kind: 'text', id: 't1', textKind: 'reasoning', content: 'x', pending: false, children: [
      { kind: 'log', id: 2, title: 'b', details: '', status: 'success' },
      { kind: 'log', id: 3, title: 'c', details: '', status: 'success' },
    ]}
  ]),
  durMs: api.formatDurationMs(3600),
  durSec: api.formatDurationMs(12450),
};
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "-e", ts_runner],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout.strip())

    assert data["cancelled"] == "已取消"
    assert data["success"] == "成功"
    assert data["awaiting"] == "待回答"
    assert data["autoParsed"]["isAutomation"] is True
    assert data["autoParsed"]["taskId"] == "42"
    assert data["autoParsed"]["displayText"] == "执行每日 10:00 知识库索引巡检并汇报失败文档。"
    assert "TaskCenter 自动化全局执行规则" in data["autoParsed"]["systemPrefix"]
    assert data["humanParsed"]["isAutomation"] is False
    assert data["humanParsed"]["displayText"] == "我们有哪些目录权限"
    assert data["stepSummaryErr"] == "HTTP 403 Forbidden"
    assert data["stepSummaryObj"] == "/workspace/a.md"
    assert data["toolCount"] == 3
    assert data["durMs"] == "3600ms"
    assert data["durSec"] == "12.5s"
