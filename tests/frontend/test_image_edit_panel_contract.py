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


def test_status_fields_are_actually_mapped(chat_input: str):
    """status 接口回的能力字段必须真的灌进 ``imageFeature``，否则 tab 永远灰。

    这条来自真实故障：后端 ``/configs/image-generation/status`` 明明回了
    ``edit_system_enabled``，前端 ``loadImageFeatureStatus`` 却只映射了
    ``system_enabled / allowed_sizes / default_size / max_images``。于是
    ``editPanelEnabled`` 恒为 false，「改图」tab 一直是灰的，悬停还写着
    「平台还没开启改图能力」——管理员已经把总开关打开了，用户只能去问客服。

    上一条 ``test_edit_tab_blocked_with_reason`` 只查「字段名在文件里出现」，
    类型声明几行就满足了，抓不到漏映射。
    """
    start = chat_input.index("const loadImageFeatureStatus = async")
    end = chat_input.index("/** 刚手动关掉面板的时间", start)
    body = chat_input[start:end]
    assert "edit_system_enabled:" in body, "status 的 edit_system_enabled 没有映射进 imageFeature"
    assert "edit_unavailable_reason:" in body, "置灰原因要一起带过来，用户才知道该找谁"
    # 类型声明去掉可选标记：漏赋值时 vue-tsc 直接报错，而不是静默拿到 undefined
    assert "edit_system_enabled?: boolean" not in chat_input


def test_panel_footer_is_pinned(chat_input: str):
    """「完成」必须常驻面板底部——不能跟着内容滚出可视区。

    这条来自真实反馈：改图页传了主图/参考图、填了要求后，面板高度超过窗口，
    「完成」被挤到面板下沿之外，用户滚到底还以为没有确认键（截图里只剩一行
    灰色提示文字被裁断）。根因是整块面板共用一个 ``overflow-y-auto``，
    标题、tab、正文、按钮一起滚，并且桌面端 `top-0` 往下铺 85vh 会伸出窗口。

    现在的结构约定：面板 ``flex flex-col`` + 只有正文 ``flex-1 overflow-y-auto``，
    页脚 ``shrink-0`` 固定在底部；桌面端从 `bottom-0` 向上长。
    """
    start = chat_input.index('v-if="showImageParamPop"')
    end = chat_input.index("<!-- Desktop flyout: 数据 & 知识库", start)
    panel = chat_input[start:end]

    # 面板本身是纵向 flex 且不整体滚动
    assert "flex flex-col overflow-hidden" in panel
    assert "top-1/2 -translate-y-1/2 overflow-y-auto" not in panel, "移动端面板不该整体滚"
    assert "w-[360px] overflow-y-auto" not in panel, "桌面端面板不该整体滚"

    # 桌面端贴底向上长，避免 85vh 把下沿顶出窗口
    assert "absolute bottom-0 left-full" in panel, "桌面端应从菜单底部向上展开"
    assert "'absolute left-full top-0 z-[60] ml-1.5 max-h-[85vh] w-[360px] overflow-y-auto'" not in panel
    # 高度上限扣掉输入区，长输入框时顶栏也别被顶出窗口
    assert "calc(100vh-13rem)" in panel

    # 页脚「完成」所在容器不参与滚动
    assert panel.count("shrink-0 border-t border-gray-100 bg-gray-50/60") == 2, \
        "生成页和改图页的页脚都要固定住"
    # 正文才是滚动区
    assert panel.count("min-h-0 flex-1 space-y-3 overflow-y-auto") == 2, \
        "两页正文都要是可伸缩的滚动区"
    # 标题栏与 tab 也不该被压缩
    assert "flex shrink-0 items-center gap-2 border-b border-gray-100" in panel
    assert "flex shrink-0 gap-1 border-b border-gray-100" in panel


def test_panel_footer_has_no_descriptive_copy(chat_input: str):
    """两个页脚只留按钮，不再放整段说明文字。

    用户原话「这块描述性文字没必要，去掉吧」——它占掉一整行，既把按钮又往下推，
    说的还是「点完成会怎样」这种点一下就知道的事。
    """
    start = chat_input.index('v-if="showImageParamPop"')
    end = chat_input.index("<!-- Desktop flyout: 数据 & 知识库", start)
    panel = chat_input[start:end]

    assert "点「完成」后图片会挂到输入框" not in panel
    assert "尺寸走结构化参数" not in panel
    # 页脚里不该再有 <p> 说明
    marker = "shrink-0 border-t border-gray-100 bg-gray-50/60"
    for chunk in panel.split(marker)[1:]:
        footer = chunk[: chunk.index("</div>")]
        assert "<p " not in footer, "页脚只放按钮，说明文字已经按需求删掉"


def test_edit_submit_never_fails_silently(chat_input: str):
    """「完成」点下去必须有可见反应——成功、上传中、失败三种都要看得见。

    这条来自真实反馈「改图点完成没反应」：旧实现里所有失败路径都只是 ``return``，
    其中 ``uploadSingleFile`` 一进去就 ``if (isInteractionLocked) return false``，
    既无提示也无 loading。面板是浮在加号菜单上的独立 flyout，交互锁上时菜单收起、
    面板却还留着，于是用户对着一屏完好的界面点「完成」，屏幕上什么都不会变。

    约定：
    1. 上传中按钮变「处理中…」并禁用（网络请求要几秒，没有状态就是「没反应」）；
    2. 交互锁 / 缺图 / 异常都要 showToast，不能静默 return；
    3. 成功后说清下一步（「完成」不发送，只把图和文字备好）。
    """
    start = chat_input.index("const applyEditParams = async")
    end = chat_input.index("const exitImageEditMode", start)
    body = chat_input[start:end]

    assert "editSubmitting" in body, "上传期间要有可见状态，否则按钮点下去毫无变化"
    assert "if (editSubmitting.value) return" in body, "连点保护"
    assert "isInteractionLocked.value" in body and "showToast" in body, \
        "交互锁要说明原因，不能静默 return false"
    assert "catch (error: any)" in body, "意外异常也要提示，不能静默吞掉"
    assert "finally" in body, "无论成败都要复位 editSubmitting"
    assert "showToast(" in body and "按发送开始改图" in body, \
        "成功也要说一句：点完成不等于发送"

    # uploadSingleFile 自己被锁挡住时也不能一声不吭
    up_start = chat_input.index("const uploadSingleFile = async")
    up_body = chat_input[up_start : up_start + 1600]
    lock_idx = up_body.index("if (isInteractionLocked.value)")
    lock_block = up_body[lock_idx : up_body.index("return false;", lock_idx) + 12]
    assert "showToast" in lock_block, "被交互锁挡掉要告诉用户，而不是只 return false"

    # 按钮本身要反映处理中
    assert "editSubmitting ? '处理中…' : '完成'" in chat_input
    assert ':disabled="!imageEditFile || editSubmitting"' in chat_input


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


def test_edit_instruction_ref_is_declared(chat_input: str):
    """模板 v-model 与 applyEditParams 用到的 imageEditInstruction 必须真的声明。

    漏声明时 `imageEditInstruction.value.trim()` 抛 ReferenceError，且这行在 try 之外，
    async 函数静默 reject——「完成」按钮没有任何反应（不转圈、不报错）。
    """
    assert "const imageEditInstruction = ref(" in chat_input


def test_mobile_image_panel_survives_plus_menu(chat_input: str):
    """移动端点「图像参数」不能把加号菜单收掉，面板要 Teleport 出去并自带遮罩。

    面板是挂在加号菜单（v-if="showPlusMenu"）里的：openImageParamPop 若在移动端先
    `showPlusMenu = false`，菜单卸载，面板跟着消失——点了像没反应。
    """
    start = chat_input.index("const openImageParamPop")
    body = chat_input[start : chat_input.index("};", start)]
    assert "showPlusMenu.value = false" not in body, "打开面板不得收起加号菜单"

    # 移动端 Teleport 到 body（桌面端 disabled 原地渲染），并有点击收起的遮罩
    assert '<Teleport to="body" :disabled="!isMobileViewport">' in chat_input
    assert "showImageParamPop && isMobileViewport" in chat_input
    # 面板打开时隐藏菜单列表，免得叠在一起
    assert 'v-show="!(isMobileViewport && showImageParamPop)"' in chat_input
