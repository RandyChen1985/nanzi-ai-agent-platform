"""契约测试：正文里的本地文件路径必须整段成链，不得把 `.json` 截断成 `.js`（#272）。

行为断言在 `message_file_path_links.mjs` 里（用 node 直接加载真实模块跑真实正则）；
这里锁住「接线没被摘掉」和两条容易被人手改回去的约束。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_file_path_links_behaviour_via_node():
    node = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/message_file_path_links.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_message_renderer_delegates_path_link_detection():
    renderer = _source("frontend/src/components/MessageRenderer.vue")

    # 识别逻辑只有一份，在 utils 里；组件只负责在「占位符还原前后」两趟调用它
    assert "import { injectOpenLinksForPaths } from '@/utils/messageFilePathLinks';" in renderer
    assert renderer.count("injectOpenLinksForPaths") >= 3  # 1 处 import + 2 处调用
    # 反向保护：不允许再把白名单/正则内联回组件（内联版就是 #272 的现场）
    assert "FILE_PATH_EXTENSIONS" not in renderer
    assert "filePathRegex" not in renderer
    assert "appendOpenLinkToPath" not in renderer


def test_file_path_helper_keeps_extension_boundary_and_longest_first():
    source = _source("frontend/src/utils/messageFilePathLinks.ts")

    # 结束边界是修 #272 的关键：没有它 `.json` 会被 `js` 分支吃掉前两个字符
    assert r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9])" in source
    # 前缀关系里「长」的必须在「短」的前面（.jsonl/.json、.jsx/.js、.tsx/.ts）
    extensions = source.split("const FILE_PATH_EXTENSIONS =", 1)[1].split(";", 1)[0]
    order = extensions.replace("'", "").replace('"', "").replace("\n", "").replace(" ", "").split("|")
    for longer, shorter in (("jsonl", "json"), ("jsonl", "js"), ("json", "js"), ("jsx", "js"), ("tsx", "ts")):
        assert longer in order and shorter in order, order
        assert order.index(longer) < order.index(shorter), order
    # `.xls` 由 `xlsx?` 的可选 x 覆盖，`xlsm` 必须排在它前面，否则 `.xlsm` 会被读成 `.xls`
    assert "xlsm" in order and "xlsx?" in order, order
    assert order.index("xlsm") < order.index("xlsx?"), order
    # 这些扩展名早先根本不在名单里，只加边界会把它们从「错链」变成「不出链接」
    for added in ("jsonl", "jsx", "tsx"):
        assert added in order, order


def test_file_path_helper_is_dependency_free():
    """零依赖：行为测试靠 `node --experimental-strip-types` 直接加载它。"""
    source = _source("frontend/src/utils/messageFilePathLinks.ts")

    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import "), stripped
        assert "from 'vue'" not in stripped
