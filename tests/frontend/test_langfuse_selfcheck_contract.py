"""前端契约：自检按钮、层 2 轮询参数、卸载清理与健康度展示。"""

import pathlib
import re

COMPONENT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend/src/components/system/LangfuseConfig.vue"
)


def _source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_selfcheck_button_is_gated_by_permission_and_switch():
    source = _source()
    assert "发送测试 trace" in source
    button = re.search(r"发送测试 trace[\s\S]{0,600}?</button>", source)
    assert button, "未找到自检按钮"
    # 按钮所在模板块必须同时受启用状态与权限约束
    region = source[max(0, button.start() - 800) : button.end()]
    assert "form.enabled" in region
    assert "canSave" in region


def test_polling_uses_deadline_and_cleans_up():
    source = _source()
    assert re.search(r"POLL_INTERVAL_MS\s*=\s*3_?000", source), "轮询间隔应为 3 秒"
    assert re.search(r"POLL_TIMEOUT_MS\s*=\s*60_?000", source), "轮询上限应为 60 秒"
    assert "onUnmounted" in source, "必须在卸载时清理轮询定时器"
    assert re.search(r"clearTimeout|clearInterval", source)


def test_export_health_section_is_rendered():
    source = _source()
    assert "导出健康度" in source
    assert "export_health" in source
    assert "进程内" in source, "健康度是进程内状态，界面必须标注"


def test_selfcheck_endpoints_are_called():
    source = _source()
    assert "`${ENDPOINT}/selfcheck`" in source, "层 1 应调用自检接口"
    assert "${ENDPOINT}/selfcheck/${traceId}" in source, "层 2 应调用反查接口"
