"""出图尺寸：面板里的「分辨率」必须真的锁住本次出图尺寸。

这条链路跨了四个文件，任何一环断了都会退化成「界面能选、出图不认」：
ChatInput 收集尺寸 → emit 给父页面 → 父页面塞进 execution_options.image_size
→ 后端 generate_image 读 context 覆盖 target_size。

为什么值得用契约锁：这种「看起来生效了其实没有」的错最难查——用户点了 512x512，
出图仍是 1024x1024，而界面上标签、面板选中态全都正常，没有任何报错可看。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
BACKEND = ROOT / "app/services/ai/tools/image_generation_tool.py"
pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tone_group_is_gone():
    """「色调」整组已被分辨率替换，不该有残留状态或常量。"""
    source = _read(CHAT_INPUT)

    assert "IMAGE_TONE_OPTIONS" not in source
    assert "imageParamTone" not in source
    assert "色调" not in source


def test_size_group_exists_and_reads_backend_whitelist():
    source = _read(CHAT_INPUT)

    assert "分辨率" in source
    assert "imageParamSize" in source
    # 尺寸列表必须来自后端白名单，不能在界面里写死
    assert "imageFeature.value?.allowed_sizes" in source
    # 注释里举例几个尺寸是允许的（HTML 注释也一样），代码里出现才是写死
    code = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    code = "\n".join(
        line for line in code.splitlines()
        if not re.match(r"\s*(//|\*|/\*)", line)
    )
    for hardcoded in ("1328x1328", "1664x928", "928x1664"):
        assert hardcoded not in code, f"界面里不该写死尺寸 {hardcoded}"


def test_default_size_card_shows_backend_value_and_is_prechecked():
    """第一格必须显示后端配的默认尺寸，并且默认就亮着。

    写「自动 / 默认」的话，后端明明配了 512x512，界面上却只有两个字「默认」，
    用户得点开面板挨个比才知道这次到底出多大。
    """
    source = _read(CHAT_INPUT)

    assert "const imageDefaultSize = computed(" in source
    # 第一格文案是具体像素，不是笼统的「自动」
    assert "{{ imageDefaultSize || '默认' }}" in source

    # 状态查回来就选上，不必用户先点一次
    status_start = source.index("const loadImageFeatureStatus")
    status_body = source[status_start : source.index("\n};", status_start)]
    assert "imageParamSize.value = String(data?.default_size" in status_body

    # 每轮重置回默认尺寸，而不是清成「不指定」——否则发完第一张，第二张又变回没选
    reset_start = source.index("const resetImageParams = () => {")
    reset_body = source[reset_start : source.index("\n};", reset_start)]
    assert "imageParamSize.value = imageDefaultSize.value" in reset_body


def test_default_size_is_not_duplicated_in_the_grid():
    """默认尺寸独占第一格，列表里要过滤掉它，否则同一个值同时出现两次。"""
    source = _read(CHAT_INPUT)

    start = source.index("const otherImageSizeChoices = computed(")
    body = source[start : source.index("\n);", start)]
    assert "opt.size !== imageDefaultSize.value" in body

    # 折叠与可见集都必须基于过滤后的列表，否则「更多 +N」会多算一个
    assert "otherImageSizeChoices.value.length > IMAGE_SIZE_COLLAPSE_THRESHOLD" in source
    assert "const all = otherImageSizeChoices.value" in source


def test_closing_panel_collapses_the_whole_flyout():
    """X 只收面板、把加号菜单留在屏幕上 = 关了一半，用户还得再点一次空白处。

    面板是贴着加号菜单右侧展开的 flyout，点 X 的意思是「这个弹层不要了」，
    不该分两步收；「完成」原本就是整体收起的，两者必须一致。
    """
    source = _read(CHAT_INPUT)
    start = source.index("const closeImageParamPop = () => {")
    body = source[start : source.index("\n};", start)]

    assert "showPlusMenu.value = false" in body

    # 「完成」必须复用同一个关闭函数，不能再各写一套——两套写法迟早又对不上
    apply_start = source.index("const applyImageParams = () => {")
    apply_body = source[apply_start : source.index("\n};", apply_start)]
    assert "closeImageParamPop()" in apply_body


def test_ratio_group_is_gone():
    """「画面比例」和分辨率表达的是同一件事，已合并进分辨率组。

    分辨率卡片自带 1:1 / 16:9 小字，形状信息一点没丢；两组并存时还会互相打架
    （选「1:1」+「1664x928」，正文说方图、下发的却是横图）。
    """
    source = _read(CHAT_INPUT)

    assert "IMAGE_RATIO_OPTIONS" not in source
    assert "imageParamRatio" not in source
    # 模板里不该再有这一组的标题（注释里解释「为什么删」是允许的）
    assert ">画面比例<" not in source


def test_size_is_not_written_into_message_body():
    """尺寸不能拼进正文：它必须是白名单精确值，走结构化参数。"""
    source = _read(CHAT_INPUT)
    start = source.index("const buildImageParamPrefix = () => {")
    body = source[start : source.index("\n};", start)]

    assert "imageParamSize" not in body
    assert "parts.push(`尺寸" not in body
    # 比例已随「画面比例」组一起删掉，正文里不该再拼它
    assert "parts.push(`比例" not in body


def test_input_emits_size_to_parent():
    source = _read(CHAT_INPUT)

    assert "(e: 'update:image-size'" in source
    assert 'emit("update:image-size"' in source


@pytest.mark.parametrize("page", [EMBED_CHAT, AGENT_DEBUG])
def test_both_chat_pages_forward_size_into_execution_options(page: Path):
    source = _read(page)

    assert '@update:image-size="' in source
    assert "const turnImageSize = imageSizeOverride.value" in source, (
        f"{page.name} 必须在 sendMessage 开头快照 imageSizeOverride，避免清空输入框触发重置后丢失"
    )
    assert re.search(r"image_size:\s*turnImageSize", source) or re.search(
        r"debugOptions\.image_size\s*=\s*turnImageSize", source
    ), f"{page.name} 没有把快照后的尺寸放进 execution_options"
    # 快照必须发生在清空 userInput 之前：否则清空 v-model 会触发 ChatInput 重置 chips，
    # 等 await nextTick() 后再读 imageSizeOverride.value 已经变成 null/默认。
    send_start = source.index("const sendMessageInternal = async")
    send_body = source[send_start : send_start + 3500]
    assert send_body.index("const turnImageSize = imageSizeOverride.value") < send_body.index(
        'userInput.value = ""'
    ), f"{page.name} 必须在清空 userInput 之前快照 imageSizeOverride"


def test_send_resets_params_after_emit_send():
    """回归：sendWithImageParams 不能在 emit('send') 之前调用 resetImageParams()。

    以前在 nextTick(() => emit('send')) 之前就 reset，watch(imageParamSize) 抢在
    父组件发送前把 imageSizeOverride 清回默认，导致用户选的分辨率永远传不过去。
    """
    source = _read(CHAT_INPUT)
    start = source.index("const sendWithImageParams = () => {")
    end = source.index("\n};", start)
    body = source[start:end]
    # 每次 resetImageParams() 都必须在 emit("send") 之后（同一 nextTick 回调内）
    assert "resetImageParams();\n    nextTick" not in body
    assert "resetImageParams();\n  nextTick" not in body
    assert body.count('emit("send");\n      resetImageParams();') >= 1
    assert body.count('emit("send");\n    resetImageParams();') >= 1


def test_default_size_is_not_emitted_as_override():
    """默认尺寸不应作为 override 上报，否则会覆盖模型口头选尺寸，也会把改图压成默认方形。"""
    source = _read(CHAT_INPUT)
    start = source.index("watch(imageParamSize, (size) => {")
    body = source[start : start + 300]
    assert "imageDefaultSize.value" in body
    assert 'emit("update:image-size"' in body


def test_backend_prefers_user_size_over_model_choice():
    source = _read(BACKEND)

    assert 'get_execution_option("image_size")' in source
    # 用户尺寸必须在白名单校验分支之前生效，否则会被「模型传值 → 默认值」覆盖
    start = source.index("preferred_size = normalize_size")
    block = source[start : start + 1200]
    assert "in allowed_sizes" in block
    assert "target_size = preferred_size" in block


def test_footnote_copy_removed():
    """页脚那段说明文字已被用户要求删掉，别再写回去。

    原来写的是「选择会随消息一起发出……尺寸走结构化参数，不写进正文」。
    真实反馈：面板底部只要按钮就够了，这段解释占掉一整行、把「完成」又往下推。
    """
    source = _read(CHAT_INPUT)

    assert "不出现像素值" not in source
    assert "尺寸走结构化参数" not in source


# ── 尺寸过多时的「更多」折叠 ──────────────────────────────────


def test_size_grid_renders_collapsed_view_not_full_list():
    """模板必须只渲染可见集：直接 v-for 全量就等于没有折叠。"""
    source = _read(CHAT_INPUT)

    assert "IMAGE_SIZE_VISIBLE_COUNT" in source
    assert "imageSizeCollapsed" in source
    assert 'v-for="opt in visibleImageSizeChoices"' in source
    assert 'v-for="opt in imageSizeChoices"' not in source


def test_selected_size_is_pulled_out_of_collapsed_area():
    """选中项在被折叠的那一截里时必须顶出来。

    否则用户点了第 8 个尺寸、面板一收，界面上看不到任何选中反馈，
    他的判断只会是「没点上」，然后再点一次。
    """
    source = _read(CHAT_INPUT)
    start = source.index("const visibleImageSizeChoices = computed(")
    body = source[start : source.index("\n});", start)]

    assert "imageParamSize.value" in body
    assert "IMAGE_SIZE_VISIBLE_COUNT - 1" in body  # 用最后一格顶出选中项
    assert "return head" in body  # 选中项本就在可见区时不改动顺序


def test_collapse_resets_between_turns():
    """展开是「正在挑尺寸」的临时态，发送后不该带到下一轮。"""
    source = _read(CHAT_INPUT)
    start = source.index("const resetImageParams = () => {")
    body = source[start : source.index("\n};", start)]

    assert "imageSizeCollapsed.value = true" in body
