"""改图面板（图生图）的前端契约测试。

纯文本断言，不跑构建：改图牵动三个文件、几处措辞，靠人工 review 很容易漏掉
「面板能传图了但发送时忘了加前缀」这种半截实现——那会让平台把「改这张图」
当成「这张图讲了什么」来回答，用户白等一轮还不知道为什么。

用 ``pytest --confcutdir=tests/frontend`` 跑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
AGENT_MGMT = ROOT / "frontend/src/views/AgentManagement.vue"


@pytest.fixture(scope="module")
def chat_input() -> str:
    return CHAT_INPUT.read_text(encoding="utf-8")


def test_panel_has_two_tabs(chat_input: str):
    """面板必须分「生成 / 改图」两页，否则用户只能靠一句话让模型猜意图。"""
    assert "imagePanelTab" in chat_input
    assert '"gen"' in chat_input and '"edit"' in chat_input
    # tab 文案
    assert ">生成</button>" in chat_input
    assert ">改图</button>" in chat_input


def test_edit_tab_has_upload_and_instruction(chat_input: str):
    assert "imageEditFileInput" in chat_input
    assert "pickEditImage" in chat_input
    assert "onEditImageChange" in chat_input
    assert "imageEditInstruction" in chat_input


def test_edit_tab_has_two_slots(chat_input: str):
    """主图 + 可选参考图两个槽位。并排两格，用户不用读字就知道最多放两张。"""
    assert "imageEditRefFileInput" in chat_input
    assert "pickEditRefImage" in chat_input
    assert "onEditRefImageChange" in chat_input
    assert "clearEditRefImage" in chat_input
    # 两个槽位各自的角标
    assert ">主图</span>" in chat_input
    assert ">参考图</span>" in chat_input


def test_reference_uploaded_after_main(chat_input: str):
    """顺序即语义：主图必须先传，附件顺序反了模型会把参考图当成要改的那张。"""
    start = chat_input.index("const applyEditParams = async")
    end = chat_input.index("const exitImageEditMode", start)
    body = chat_input[start:end]
    main_idx = body.index("await uploadSingleFile(file)")
    ref_idx = body.index("await uploadSingleFile(refFile)")
    assert main_idx < ref_idx, "主图必须先于参考图进入附件列表"


def test_reference_upload_failure_rolls_back(chat_input: str):
    """参考图没传上去要把主图撤掉，否则用户以为两张都成了。"""
    start = chat_input.index("const applyEditParams = async")
    end = chat_input.index("const exitImageEditMode", start)
    body = chat_input[start:end]
    assert "if (!refUploaded)" in body
    assert "uploadedFiles.value.pop()" in body


def test_send_names_which_is_which(chat_input: str):
    """带参考图时前缀必须点名第一张是主图、第二张只参考，否则模型可能改错那张。"""
    assert "imageEditHasReference" in chat_input
    start = chat_input.index("const sendWithImageParams")
    body = chat_input[start : start + 1600]
    assert "第一张是要改的主图" in body
    assert "第二张只是参考" in body


def test_edit_submit_does_not_send_directly(chat_input: str):
    """「完成」只把图挂成附件、把要求写进输入框；直接发送会让手滑一次就扣一次钱。"""
    assert "const applyEditParams = async" in chat_input
    # applyEditParams 体内必须调 uploadSingleFile，且不能出现 emit("send")
    start = chat_input.index("const applyEditParams = async")
    end = chat_input.index("const exitImageEditMode", start)
    body = chat_input[start:end]
    assert "uploadSingleFile(file)" in body
    assert 'emit("send")' not in body, "改图页的「完成」不该直接把消息发出去"


def test_send_prefix_pins_the_intent(chat_input: str):
    """确定性来源：发送时给正文加「沿用所附图片进行修改：」。

    界面上分 tab 只是让用户看得见；真正让模型不误读的是这句前缀。
    """
    assert "沿用所附图片进行修改：" in chat_input
    assert "imageEditMode" in chat_input


def test_edit_mode_resets_only_when_input_cleared(chat_input: str):
    """发送被父组件拒绝时不能清标记，否则重发一次就变回「问答」。"""
    # 定位到那个会清参数与改图标记的 watch（文件里有多个 watch，只有它处理清空）
    start = chat_input.index("(value, previous) =>")
    body = chat_input[start : start + 800]
    assert "imageEditMode.value = false" in body


def test_edit_tab_blocked_with_reason(chat_input: str):
    """能力不满足时 tab 要置灰并说清找谁，不能只是一个点不动的灰按钮。"""
    assert "editPanelEnabled" in chat_input
    assert "editPanelBlockReason" in chat_input
    assert "edit_system_enabled" in chat_input
    # 委派架构下「当前智能体是否勾了 edit_image」判不准，不再据此置灰
    assert "status.agent_edit_enabled" not in chat_input


def test_style_only_in_edit_page(chat_input: str):
    """改图页复用风格 chips（实测改画风有效），但不放「张数」——一次只出一张。"""
    start = chat_input.index("imagePanelTab === 'gen'")
    gen_part = chat_input[start : start + 12000]
    assert "IMAGE_STYLE_OPTIONS" in gen_part
    # 改图页段落里应出现风格选项
    edit_start = chat_input.index("───── 改图页 ─────")
    edit_part = chat_input[edit_start:]
    assert "IMAGE_STYLE_OPTIONS" in edit_part


def test_agent_management_lists_edit_image():
    text = AGENT_MGMT.read_text(encoding="utf-8")
    assert 'name: "edit_image"' in text
    # 默认不勾选：和 generate_image 一样按次计费
    assert 'name: "generate_image"' in text


def test_system_config_has_edit_test_button():
    text = SYSTEM_CONFIG.read_text(encoding="utf-8")
    assert "runImageEditTest" in text
    assert "/configs/image-generation/test-edit" in text
    # 不再按接口格式置灰：改图固定走 chat，与生图协议无关
    assert "imageEditTestAvailable" not in text
    # 失败文案由后端翻译，前端只展示
    assert "imageEditTestError" in text


def test_edit_upload_failure_keeps_draft(chat_input: str):
    """上传失败不能进入改图模式，也不能清掉用户刚选的图。

    这条防的是 review 时发现的一个真 bug：``uploadSingleFile`` 原本返回 void，
    失败只 toast，于是 ``applyEditParams`` 会照常设 ``imageEditMode = true``
    并清空草稿——发送出去就是「沿用所附图片进行修改」配一片空白。
    """
    start = chat_input.index("const applyEditParams = async")
    end = chat_input.index("const exitImageEditMode", start)
    body = chat_input[start:end]
    assert "const uploaded = await uploadSingleFile(file)" in body
    assert "if (!uploaded)" in body
    # 失败分支必须在上传后立刻返回，不能落到 clearEditImage()
    fail_idx = body.index("if (!uploaded)")
    clear_idx = body.index("clearEditImage()")
    assert fail_idx < clear_idx
    assert "return;" in body[fail_idx:clear_idx]

    # 上传函数本身要能报告成败
    up_start = chat_input.index("const uploadSingleFile = async")
    up_body = chat_input[up_start : up_start + 1400]
    assert ": Promise<boolean>" in up_body
    assert "return true" in up_body
    assert "return false" in up_body


def test_edit_mode_drops_prefix_when_no_image_left(chat_input: str):
    """附件被删掉后不能还声称「沿用所附图片」——模型会去找一张不存在的图。"""
    start = chat_input.index("const sendWithImageParams")
    body = chat_input[start : start + 1400]
    assert "isImageAttachment" in body
    assert "hasImageAttachment" in body


def test_edit_mode_carries_style(chat_input: str):
    """改图页能选风格，发送时必须带上，否则用户点了等于没点。"""
    start = chat_input.index("const sendWithImageParams")
    body = chat_input[start : start + 1400]
    assert "imageParamStyle.value" in body
    assert "styleNote" in body


def test_edit_image_size_guarded_on_pick(chat_input: str):
    """上传接口允许 20MB，但改图上限 10MB，选图时就该拦住。"""
    assert "MAX_EDIT_IMAGE_BYTES" in chat_input
    start = chat_input.index("const onEditImageChange")
    body = chat_input[start : start + 900]
    assert "MAX_EDIT_IMAGE_BYTES" in body
    assert "showToast" in body


def test_edit_tab_falls_back_when_capability_lost(chat_input: str):
    """能力消失（换智能体/管理员改格式）时不能把面板停在置灰的改图页。"""
    start = chat_input.index("watch(editPanelEnabled")
    body = chat_input[start : start + 300]
    assert 'imagePanelTab.value = "gen"' in body


def test_enter_newline_does_not_clear_chips(chat_input: str):
    """空输入框敲回车会插入换行，不能因此把刚选好的参数标签清掉。

    这条来自真实反馈：用户选好「水彩手绘 / 1024x1024」，在空输入框里按回车换行，
    chips 整组消失。原因是清空监听只看“当前值为空”——``""`` 变成 ``"\\n"``
    trim 之后仍是空串，被误判成「这轮发完了」。
    """
    # 文件里有两个 watch(modelValue)：765 行那个只调高度，这个才管清空
    start = chat_input.index("(value, previous) =>")
    body = chat_input[start : start + 800]
    assert "wasFilled" in body, "必须判断“之前是否有内容”"
    assert "if (!wasFilled || !nowEmpty) return" in body


def test_send_clears_chips_without_text(chat_input: str):
    """只发图片不打字时 modelValue 从头到尾为空，chips 得由发送流程自己收。"""
    start = chat_input.index("const sendWithImageParams")
    body = chat_input[start : start + 2400]
    assert body.count("resetImageParams()") >= 2, "改图分支与参数分支都要显式收口"
