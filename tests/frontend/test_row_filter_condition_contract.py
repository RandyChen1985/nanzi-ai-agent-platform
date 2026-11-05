"""契约测试：数据权限弹窗打开后，已有条件必须回填到规则构建器。

行为断言在 `row_filter_condition.mjs` 里（真跑解析函数，能拆的拆对、拆不了返回 null）；
这里锁住接线，避免以后被顺手改回「只单向拼接」：

1. 页面确实引用了解析器；
2. 回填发生在 `fetchDatasetColumns` await 之后——下拉框的值只要在 options 里找不到
   就渲染成占位符，表结构没到位时回填等于没回填；
3. 回填态再点「+」走替换而不是追加，否则会拼出 `A = 1 AND A = 1`。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = "frontend/src/views/MetadataDatasets.vue"


def _source() -> str:
    return (ROOT / VIEW).read_text(encoding="utf-8")


def test_condition_parser_behaviour_via_node():
    node = subprocess.run(
        ["node", "tests/frontend/row_filter_condition.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_view_imports_condition_parser():
    assert "import { parseRowFilterCondition } from '../utils/rowFilterCondition'" in _source()


def test_hydration_runs_after_dataset_columns_loaded():
    source = _source()
    await_index = source.index("await fetchDatasetColumns(ds.id)")
    hydrate_index = source.index("hydrateBuilderFromConditions()", await_index)
    assert await_index < hydrate_index


def test_append_replaces_when_builder_came_from_condition():
    source = _source()
    start = source.index("const appendBuilderCondition")
    body = source[start:start + 1000]
    assert "rule._builder_from_condition" in body
    assert "replacing" in body
    assert "rule.condition = condition" in body


def test_builder_buttons_switch_between_append_and_replace():
    """四个策略区块（用户例外 / 角色 / 默认 / 用户例外-空态）都要有替换语义的 title。"""
    source = _source()
    assert source.count(':title="rule._builder_from_condition ?') == 4
