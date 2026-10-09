"""弹框最大化的契约。

用户诉求：知识库「预览原文件」的弹框是 1024px 宽（`max-w-5xl`）× 固定 70vh，
看宽表格或大页 PDF 时偏窄。用户明确选择「应用内最大化」（而非浏览器 Fullscreen API），
并要求能力放在通用 `Modal` 上但默认关闭。

为什么是源码契约而不是行为测试：`Modal` 是单文件组件，Node 无法直接加载它；
本仓库对这类改动的既定做法是「源码断言 + SFC 真编译一次」（字符串断言看不出模板语法错误）。
运行时会怎么样，仍由用户在浏览器里做最终验收。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_maximizable_is_opt_in_and_defaults_off():
    """默认必须关闭：Modal 被 8 个文件共 32 处使用，不能顺带改掉既有弹框。"""
    modal = _source("frontend/src/components/Modal.vue")

    assert "maximizable?: boolean" in modal, "Modal 未提供最大化开关"
    assert "maximizable: false" in modal, "maximizable 必须默认 false（opt-in）"
    # 按钮必须受开关控制，否则另外 31 处弹框的标题栏都会多一个按钮
    assert 'v-if="maximizable"' in modal, "最大化按钮未受 maximizable 控制"


def test_maximize_toggle_button_contract():
    modal = _source("frontend/src/components/Modal.vue")

    assert "isMaximized" in modal, "缺少最大化状态"
    assert "toggleMaximize" in modal, "缺少切换动作"
    # 图标与文案都要随状态切换，否则用户看不出当前处于哪种状态
    assert "最大化预览" in modal and "还原窗口" in modal, "缺少状态相关文案"
    assert ':title="isMaximized' in modal, "title 未随状态切换"
    assert ':aria-label="isMaximized' in modal, "aria-label 未随状态切换"
    assert "M4 8V4h4m12 4V4h-4M4 16v4h4m12-4v4h-4" in modal, "缺少展开图标"
    assert "M4 14h6v6m10-6h-6v6M4 10h6V4m10 6h-6V4" in modal, "缺少收起图标"
    # 小屏隐藏：窄屏下 Modal 本身就是 p-3 + w-full，点最大化没有可见差异
    assert "hidden sm:inline-flex" in modal, "最大化按钮未在小屏隐藏"


def test_maximized_container_collapses_width_and_pins_height():
    """最大化必须同时「放开宽度」与「钉住高度」，缺一不可。

    内容区是 `flex-1 min-h-0`：容器高度若仍由内容决定，使用方的 `h-full` 就没有
    可靠参照（百分比高度会解析失败），于是只能去硬编码「header + padding 占多少 px」
    这类脆弱估算，估错就是溢出或多出滚动条。
    """
    modal = _source("frontend/src/components/Modal.vue")

    assert "max-w-none" in modal, "最大化未放开宽度上限"
    # 必须是不带 max- 前缀的确定高度。这里刻意用正则而不是朴素子串断言：静态 class 里的
    # `max-h-[calc(100dvh-1.5rem)]` 天然包含子串 `h-[calc(100dvh-1.5rem)]`，朴素的
    # `in` 判断会被它满足 —— 这条假绿正是变异验证 M3 抓出来的（去掉最大化分支的确定高度
    # 后断言照样通过）。勿退回弱断言。
    assert re.search(r"(?<!max-)h-\[calc\(100dvh-1\.5rem\)\]", modal), (
        "最大化未钉住视口高度（内容区的 h-full 会失去参照）"
    )
    assert re.search(r"(?<!max-)h-\[calc\(100vh-2rem\)\]", modal), "最大化未钉住大屏断点高度"
    # size（知识库传的是 max-w-5xl）在最大化时必须让位
    assert ": (size || 'max-w-md')" in modal, "size 与最大化状态未互斥"


def test_slot_exposes_maximized_and_state_resets_on_close():
    modal = _source("frontend/src/components/Modal.vue")

    assert '<slot :maximized="isMaximized">' in modal, "默认插槽未透出 maximized"
    # 关闭即复位：每次打开都回到普通尺寸，不记忆上次状态
    assert "watch(" in modal and "props.show" in modal, "未监听 show 变化"
    assert "isMaximized.value = false" in modal, "关闭弹框时未复位最大化状态"


def test_knowledge_base_preview_opts_in_and_switches_height():
    kb = _source("frontend/src/views/KnowledgeBaseManagement.vue")

    preview_at = kb.index("Document original file preview")
    block = kb[preview_at : preview_at + 1800]

    assert "maximizable" in block, "知识库预览弹框未开启最大化"
    assert '<template #default="{ maximized }">' in block, "未消费 maximized slot prop"
    assert "maximized ? 'h-full w-full' : 'h-[70vh] w-full'" in block, (
        "未按最大化状态切换预览高度（普通态必须保持 70vh 原行为）"
    )
    # iframe 兜底分支仍在同一容器内，才能与 DocumentViewer 一起受益
    assert "<iframe" in block, "iframe 兜底分支丢失或移出了共用容器"


def test_only_knowledge_base_preview_opts_in():
    """除知识库预览外不应有别的使用点开启最大化 —— 本次范围就这一处。"""
    users = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "frontend/src").rglob("*")
        if path.is_file() and "maximizable" in path.read_text(encoding="utf-8", errors="ignore")
    )

    assert users == [
        "frontend/src/components/Modal.vue",
        "frontend/src/views/KnowledgeBaseManagement.vue",
    ], f"最大化出现了预期之外的使用点：{users}"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能编译 SFC")
def test_sfc_compiles_without_errors():
    """源码字符串断言看不出模板语法错误，必须真编译一次。"""
    script = """
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const fs = require('fs')
const files = ['src/components/Modal.vue', 'src/views/KnowledgeBaseManagement.vue']
let failed = 0
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) { console.error(file, 'parse:', errors.map(String).join('; ')); failed += 1; continue }
  try { compileScript(descriptor, { id: file }) }
  catch (error) { console.error(file, 'script:', String(error)); failed += 1 }
  const tpl = compileTemplate({ source: descriptor.template.content, filename: file, id: file })
  if (tpl.errors.length) { console.error(file, 'template:', tpl.errors.map(String).join('; ')); failed += 1 }
}
if (failed) { console.error('失败数: ' + failed); process.exit(1) }
console.log('SFC 编译通过（' + files.length + ' 个文件）')
"""
    # cwd 必须是 frontend，否则 require('vue/compiler-sfc') 解析不到（vue 装在 frontend/node_modules）
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SFC 编译通过" in proc.stdout
