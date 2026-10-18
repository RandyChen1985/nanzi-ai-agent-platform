"""契约：生成中的状态点排在副标题前面。

用户诉求（原文）：「把这个圆点动效放副标题前面呢」

原来点在主标题右侧（`主助手(Main) ●` / `正在处理您的请求...`），观感上像是主标题的装饰；
用户希望它贴到状态描述前，表达「这个状态正在发生」。

连带一点必须一起处理：脉冲点原本与「锁定」徽章靠 `v-if` / `v-else-if` 二选一，点搬走后
互斥就断了——生成中会同时出现「锁定」徽章和状态点。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
TITLE_STYLE = "text-[13px] font-semibold text-gray-800"
PROCESSING_TEXT = "正在处理您的请求"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_status_dot_sits_immediately_before_the_subtitle_text():
    """点要贴在状态描述之前——只比较先后不够，两者必须在同一行内。"""
    source = _source()
    dot = source.index("animate-ping")
    text = source.index(PROCESSING_TEXT)
    assert dot < text, "状态点没有排在副标题文字之前"
    assert "</div>" not in source[dot:text], "状态点与副标题文字被拆到了不同的行"


def test_status_dot_left_the_title_row():
    """它已经不属于第一行——主标题与它之间必须隔着第一行的闭合。"""
    source = _source()
    title = source.index(TITLE_STYLE)
    dot = source.index("animate-ping")
    assert "</div>" in source[title:dot], "状态点还在第一行里（应移到副标题行）"


def test_lock_badge_and_status_dot_stay_exclusive():
    """点搬走后互斥条件要显式补上，否则生成中会同时显示「锁定」徽章和状态点。"""
    assert 'v-if="headerExpertLabel && !isProcessing"' in _source()


def test_the_sfc_compiles():
    """整个 SFC 必须能被 Vue 官方编译器解析。

    这条是拿一次翻车换来的：`vue-tsc --noEmit` 对**模板标签不平衡不报错**，
    一个多出来的 </div> 曾通过全部前端契约与类型检查，直到 vite build 才炸。
    """
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/views/EmbedChat.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(["node", "-e", script], cwd=str(ROOT / "frontend"),
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"EmbedChat.vue 模板无法编译：\n{proc.stderr}"
