"""提问轮补全正文的「拆成两段」契约。

提问轮模型通常只调工具、不输出正文，后端在 finalize 时用提问卡内容补一条正文
（`build_user_question_fallback_content`），模板固定为：

    任务进度：<提问前的任务进度>
    待你确认：<问题原文>

这条正文必须进历史（给下一轮的模型读），但在界面上它和展开的提问卡整段重复。
这里把「认出这条正文、并拆成可分别渲染的两段」定成行为契约：

- 认得出 → 界面渲染成「任务进度」块 +「待你确认」块，卡片只留选项；
- 认不出 → 返回 None，调用方按普通正文渲染（**绝不猜**）。

取值口径：优先用提问卡的结构化字段（`question` / `context`），正文只用来**确认**
「它就是模板产物」，不靠正则从正文里抠内容 —— 模型换个措辞不会让显示坏掉。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE = "frontend/src/utils/userQuestion.ts"

PROGRESS = "已完成：读取技能全文（13590 字符），定位到 3.2 空间层级、6.4 核查模板。待办：写入技能并更新版本记录。"
QUESTION = "「设备不在机柜就在仓库，位置信息二选一」这条规则请确认要覆盖的范围："


def _run_typescript(expression: str):
    script = f"""
(async () => {{
const {{ loadModule }} = require('./tests/frontend/ts_module_loader.cjs');
const api = loadModule({json.dumps(MODULE)});
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


def _payload(context: str | None = PROGRESS, question: str = QUESTION) -> str:
    data = {
        "question_id": "q-1",
        "question": question,
        "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "is_multi_select": False,
        "allow_custom_input": False,
        "status": "submitted",
    }
    if context is not None:
        data["context"] = context
    return json.dumps(data)


def test_compose_matches_backend_template():
    """合成串必须与后端模板逐字一致（进度在前、换行分隔、标签相同）。"""
    result = _run_typescript(
        f"""
const payload = {_payload()};
return api.composeUserQuestionFallbackBody(payload.context, payload.question);
"""
    )
    assert result == f"任务进度：{PROGRESS}\n待你确认：{QUESTION}"


def test_compose_folds_multiline_progress_like_backend():
    """后端把 context 的换行折叠成「；」，合成串要与后端一致才认得出。"""
    result = _run_typescript(
        """
return api.composeUserQuestionFallbackBody(' 已完成：A \\n\\n 待办：B \\r\\n', '问题？');
"""
    )
    assert result == "任务进度：已完成：A；待办：B\n待你确认：问题？"


def test_compose_truncates_progress_at_backend_limit():
    """进度段收在同一上限（300 字符），超长补省略号，否则与后端对不上。"""
    result = _run_typescript(
        """
const long = '甲'.repeat(400);
const composed = api.composeUserQuestionFallbackBody(long, '问题？');
const firstLine = composed.split('\\n')[0];
return { length: firstLine.length, tail: firstLine.slice(-2) };
"""
    )
    # 「任务进度：」5 字 + 进度（300 字，末位是省略号）
    assert result["length"] == 5 + 300
    assert result["tail"].endswith("…")


def test_split_returns_two_parts_for_template_body():
    """模板正文 + 匹配的提问卡 → 拆成进度与问句两段。"""
    result = _run_typescript(
        f"""
const payload = {_payload()};
const body = api.composeUserQuestionFallbackBody(payload.context, payload.question);
const parts = api.splitUserQuestionBody(body, payload);
return parts;
"""
    )
    assert result["progress"] == PROGRESS
    assert result["question"] == QUESTION


def test_split_prefers_structured_fields_over_parsing_body():
    """内容一律取提问卡字段：正文被换行差异改写过也照样给出原文。"""
    result = _run_typescript(
        f"""
const payload = {_payload()};
const body = '任务进度：' + payload.context + '\\n\\n待你确认：' + payload.question;
return api.splitUserQuestionBody(body, payload);
"""
    )
    assert result["progress"] == PROGRESS
    assert result["question"] == QUESTION


def test_split_handles_body_without_progress():
    """提问卡没有 context 时后端只补「我需要你补充确认：…」，进度段为空。"""
    result = _run_typescript(
        f"""
const payload = {_payload(context=None)};
const body = api.composeUserQuestionFallbackBody('', payload.question);
const parts = api.splitUserQuestionBody(body, payload);
return {{ body, parts }};
"""
    )
    assert result["body"] == f"我需要你补充确认：{QUESTION}"
    assert result["parts"]["progress"] == ""
    assert result["parts"]["question"] == QUESTION


def test_split_rejects_ordinary_assistant_body():
    """模型正常写的正文即使提到「任务进度」也不能被当成模板产物。"""
    result = _run_typescript(
        f"""
const payload = {_payload()};
return api.splitUserQuestionBody('任务进度：我还在想，先不确认了。', payload);
"""
    )
    assert result is None


def test_split_requires_payload_and_body():
    """缺提问卡或正文为空时不拆（历史里没有卡的老消息照常按正文渲染）。"""
    result = _run_typescript(
        f"""
const payload = {_payload()};
return {{
  noPayload: api.splitUserQuestionBody('任务进度：x\\n待你确认：y', null),
  noBody: api.splitUserQuestionBody('', payload),
  nullBody: api.splitUserQuestionBody(null, payload),
}};
"""
    )
    assert result["noPayload"] is None
    assert result["noBody"] is None
    assert result["nullBody"] is None
