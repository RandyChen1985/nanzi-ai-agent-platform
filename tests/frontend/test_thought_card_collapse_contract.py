"""思考卡片折叠语义的契约。

「思考过程」开关只管**生成过程中**要不要展开——开=边跑边看推理链，关=只留标题行。
AI 回答生成完之后，无论这个开关是开是关，整张卡片一律折叠。早先的实现让开关接管了
「回答后」的行为（开就永不折叠），于是关不掉了。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests/frontend/thought_card_collapse_probe.cjs"
TIMELINE = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
SETTINGS = ROOT / "frontend/src/components/embed/ChatSettings.vue"


def _run_probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：决策真值表
# --------------------------------------------------------------------------


def test_done_always_collapses_regardless_of_setting():
    """生成完就折叠——开关开着也照样折。"""
    out = _run_probe()
    assert out["done_collapses_even_when_setting_on"] is False
    assert out["done_collapses_when_setting_off"] is False
    assert out["done_collapses_when_setting_unset"] is False


def test_running_honors_the_setting():
    """生成中才听开关：开=展开看推理链，关=只留标题行。"""
    out = _run_probe()
    assert out["running_opens_when_setting_on"] is True
    assert out["running_stays_closed_when_setting_off"] is False


def test_running_defaults_to_open_without_setting():
    """没设置过（老配置）默认展开，保持生成中能看进度。"""
    assert _run_probe()["running_opens_by_default_when_unset"] is True


def test_body_arrived_but_tools_still_running_is_still_running():
    """有正文但工具还在跑，仍属生成中——不能提前折。"""
    out = _run_probe()
    assert out["body_but_tools_running_honors_setting_off"] is False
    assert out["body_but_tools_running_opens_when_setting_on"] is True


def test_user_toggle_disables_auto_decision():
    """用户手动点过标题就不再自动改，返回 null。"""
    out = _run_probe()
    assert out["user_toggled_is_noop_when_done"] is None
    assert out["user_toggled_is_noop_when_running"] is None


def test_empty_input_falls_back_to_open():
    assert _run_probe()["empty_input_opens"] is True


# --------------------------------------------------------------------------
# 接线：组件与设置文案
# --------------------------------------------------------------------------


def test_timeline_uses_the_pure_decision():
    source = TIMELINE.read_text(encoding="utf-8")
    assert "resolveTimelineExpanded({" in source
    for field in ("hasAnswer:", "hasPending:", "expandThoughts:", "userToggled:"):
        assert field in source, f"决策缺字段 {field}"


def test_timeline_no_longer_branches_inline():
    """决策不能再散在 watch 里——它就是要被单测的那段逻辑。"""
    source = TIMELINE.read_text(encoding="utf-8")
    body = source[source.index("watch(\n  [hasPending"):]
    body = body[: body.index("{ immediate: true }")]
    assert "resolveTimelineExpanded({" in body
    assert "if (props.hasAnswer && !hasPending.value)" not in body
    assert "userToggledExpanded.value) return;" not in body


def test_body_first_char_fold_ignores_the_setting():
    """正文首字出现时收起思考面板，不再让位给「思考过程」开关。"""
    source = EMBED.read_text(encoding="utf-8")
    assert "&& !config.expandThoughts" not in source
    start = source.index("正文开始输出时收起思考面板")
    block = source[start: start + 400]
    assert "!timelineHasPending(msg.processTimeline)" in block


def test_setting_copy_states_the_new_semantics():
    """设置文案必须写明「回答完成后折叠」，否则用户不知道开关管到哪。"""
    source = SETTINGS.read_text(encoding="utf-8")
    assert "回答完成后自动折叠" in source
    assert "回答完成后同样折叠" in source
    assert "回答后仍保留在页面上" not in source
