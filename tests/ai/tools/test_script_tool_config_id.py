import pytest
from app.services.ai.tools.script_tool_runtime import ScriptToolConfig


def test_script_config_carries_id():
    """缺 id 会让 apply_script_env_bindings 在第一道判断就返回 {}。

    env: 绑定按 consumer_id 查，而 consumer_id 就是脚本工具主键。运行时只拿到
    config，config 里没有 id，查出来的永远是空——界面上绑好了，脚本里也是空。
    """
    assert ScriptToolConfig(name="a", code="print(1)").id == ""
    assert ScriptToolConfig(name="a", code="print(1)", id="42").id == "42"


def test_factory_passes_row_id():
    from pathlib import Path

    source = (
        Path(__file__).parents[3] / "app/services/ai/tools/script_tool_factory.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("def config_from_row"):source.index("def config_from_builtin")]
    assert "id=str(row.id)" in body
