import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_embed_chat_agent_messages_use_the_nanzi_agent_avatar_asset():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    settings_source = (ROOT / "frontend/src/components/embed/ChatSettings.vue").read_text(encoding="utf-8")
    bundled_avatar = ROOT / "frontend/src/assets/nanzi-agent-avatar.svg"
    brand_avatar = ROOT / "docs/brand/nanzi-agent-avatar.svg"

    assert 'import agentAvatarUrl from "@/assets/nanzi-agent-avatar.svg";' in source
    assert "'/branding/nanzi-agent-avatar.svg'" not in source
    # 气泡头像不再直接用全局形象：改为三层继承解析（智能体头像 → 全局 → 内置默认）
    assert ':src="msgAvatarSrc(msg) || agentAvatarUrl"' in source
    assert '@error="handleAgentAvatarError"' in source
    assert 'alt="NanZi AI agent"' in source
    assert bundled_avatar.is_file()
    assert bundled_avatar.read_bytes() == brand_avatar.read_bytes()

    # ChatSettings 界面设置中支持 AI 头像自定义配置
    assert 'AI 助手头像' in settings_source
    assert 'PRESET_AGENT_AVATARS' in settings_source
    assert '/api/portal/portal-prefs/agent-avatar' in settings_source
    assert '/api/portal/portal-prefs/agent-avatar/upload' in settings_source

    # 权限隔离：仅管理员有权修改与上传，普通用户受保护并展示管理员统一配置提示
    assert 'isAdmin?: boolean' in settings_source
    assert 'v-if="isAdmin"' in settings_source
    assert '管理员统一配置' in settings_source
    assert ':is-admin="currentUser?.role === \'admin\'"' in source

    # 头像裁剪与大图预览组件契约
    cropper_path = ROOT / "frontend/src/components/common/AvatarCropperModal.vue"
    assert cropper_path.is_file(), "AvatarCropperModal.vue 组件必须存在"
    cropper_source = cropper_path.read_text(encoding="utf-8")
    assert "cropperZoom" in cropper_source
    assert "canvas.toBlob" in cropper_source
    assert "实时气泡效果预览" in cropper_source
    assert "@wheel.prevent" in cropper_source

    # ChatSettings 中集成裁剪弹窗与大图处理
    assert "AvatarCropperModal" in settings_source
    assert "showAvatarCropper" in settings_source
    assert "handleAvatarCropped" in settings_source
    assert "<AvatarCropperModal" in settings_source


def test_chat_settings_avatar_write_is_explicit_and_conflict_guarded():
    """陈旧页面不得隐式回写 AI 头像：只有显式编辑才提交，且必须带乐观并发基线。"""
    settings_source = (ROOT / "frontend/src/components/embed/ChatSettings.vue").read_text(encoding="utf-8")

    # ① 显式编辑标记：失焦只在用户确实改过输入框时才提交
    assert "isAvatarInputDirty" in settings_source
    assert "const handleAvatarInputBlur" in settings_source
    assert "if (isAvatarInputDirty.value) {" in settings_source
    # 输入框不再直接绑定无条件的 blur 回写
    assert '@blur="handleCustomAvatarBlur"' not in settings_source
    assert '@blur="handleAvatarInputBlur"' in settings_source
    assert '@input="markAvatarInputDirty"' in settings_source

    # ② 服务端权威值驱动 UI：config.agentAvatar 更新时不抢占用户正在编辑的输入
    assert "syncAgentAvatarFromServer" in settings_source
    assert "watch(() => props.config.agentAvatar" in settings_source

    # ③ 乐观并发：提交时携带 base_avatar，服务端 409 时以服务端为准并提示
    assert "base_avatar" in settings_source
    assert "base_avatar: baseAvatar" in settings_source
    assert "status === 409" in settings_source
    assert "已被其他管理员更新" in settings_source

    # ④ 保存失败必须回滚本地显示，避免"本地新头像、服务端旧值"
    assert "serverAgentAvatar" in settings_source
    assert "previousAvatar" in settings_source


def test_preset_agent_avatars_use_bundled_assets_instead_of_inline_data_uris():
    """预设头像必须是短的静态资源路径。

    历史上「智能小机」「赛博科技」两个预设使用内联 `data:` URI（URL 编码的 SVG），
    长度分别为 2197 / 2196 字符，超过后端 `AgentAvatarUpdate.avatar` 的
    `max_length=2048`，点击后被 422 拒绝。预设值会被写入 Redis 全局键供全员使用，
    因此统一改为 `public/agent-avatars/` 下的静态资源（不会被 Vite 内联成 base64）。
    """
    presets_source = (ROOT / "frontend/src/utils/presetAgentAvatars.ts").read_text(encoding="utf-8")

    assert "data:image" not in presets_source
    assert "encodeURIComponent" not in presets_source
    assert "MAX_AGENT_AVATAR_URL_LENGTH = 2048" in presets_source
    assert "isAgentAvatarUrlTooLong" in presets_source
    assert 'PRESET_AGENT_AVATAR_DIR = "/agent-avatars"' in presets_source

    preset_assets = (
        "nanzi-agent-avatar-robot.svg",
        "nanzi-agent-avatar-spark.svg",
        "nanzi-agent-avatar-cyber.svg",
        "nanzi-agent-avatar-scholar.svg",
        "nanzi-agent-avatar-service.svg",
        "nanzi-agent-avatar-analytics.svg",
        "nanzi-agent-avatar-shield.svg",
    )
    for asset_name in preset_assets:
        assert f'"/agent-avatars/{asset_name}"' in presets_source, asset_name
        asset_path = ROOT / "frontend/public/agent-avatars" / asset_name
        assert asset_path.is_file(), asset_name
        # 资源必须是可解析的 SVG 矢量图
        root = ET.fromstring(asset_path.read_text(encoding="utf-8"))
        assert root.tag.endswith("svg"), asset_name
        # public 资源按原样提供：文件体积远小于 Vite 4KB 内联阈值，路径也不会被内联
        assert asset_path.stat().st_size < 4096, asset_name

    # 8 组预设（官方默认 + 7 款风格化）全部走静态资源
    assert presets_source.count("\n    url: ") == len(preset_assets) + 1

    # 所有内联字面量 URL 都必须在服务端长度上限内
    literal_urls = re.findall(r'url: "([^"]+)"', presets_source)
    assert len(literal_urls) == len(preset_assets)
    for literal in literal_urls:
        assert len(literal) <= 2048, literal

    # 前端长度护栏接入设置面板；8 款预设行需自动折行，避免窄屏横向溢出
    settings_source = (ROOT / "frontend/src/components/embed/ChatSettings.vue").read_text(encoding="utf-8")
    assert "isAgentAvatarUrlTooLong(target)" in settings_source
    assert "MAX_AGENT_AVATAR_URL_LENGTH" in settings_source
    assert 'class="flex flex-wrap items-center gap-2 mb-2.5"' in settings_source


def test_agent_avatar_resolution_is_agent_then_global_then_bundled():
    """头像继承顺序必须收敛在单一 util，且顺序为：智能体 → 全局 → 内置默认。"""
    util_source = (ROOT / "frontend/src/utils/agentAvatar.ts").read_text(encoding="utf-8")

    assert "export function agentOwnAvatarUrl" in util_source
    assert "export function buildAgentAvatarIndex" in util_source
    assert "export function resolveChatAgentAvatar" in util_source
    # 智能体头像地址受 DB 字段限制（String(255)），与全局头像的 2048 是两个上限
    assert "export const AGENT_AVATAR_URL_MAX_LENGTH = 255" in util_source

    # 未配置头像的智能体不入索引，查表落空才能干净回退到全局形象
    assert "if (!url) continue;" in util_source

    # 优先级：消息自带 → 按 id → 按 name → 全局（下标顺序即优先级顺序）
    order = [
        util_source.index("if (direct) return direct;"),
        util_source.index("if (byId) return byId;"),
        util_source.index("if (byName) return byName;"),
        util_source.index("return String(globalAvatar || \"\").trim();"),
    ]
    assert order == sorted(order), "三层继承的判定顺序被改动了"


def test_embed_chat_bubble_uses_per_agent_avatar_with_global_fallback():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    # ① 气泡走三层继承解析，且以内置默认资源兜底
    assert ':src="msgAvatarSrc(msg) || agentAvatarUrl"' in source
    assert "resolveChatAgentAvatar(" in source
    assert "buildAgentAvatarIndex(allowedAgents.value)" in source
    assert "agentAvatarUrl?: string" in source  # Message 接口字段

    # ② 历史接口下发的气头像必须被消费（已停用智能体的历史消息只能靠它）
    assert "agentAvatarUrl: item.agent_avatar_url ?? undefined" in source
    assert "agentAvatarUrl: latestServerItem.agent_avatar_url ?? undefined" in source

    # ③ 单个智能体头像 404 时先回落全局形象，再退内置默认；不得出现 error 死循环
    assert "failedAvatarSrcs" in source
    error_handler = source[source.index("const handleAgentAvatarError"):]
    error_handler = error_handler[: error_handler.index("\n};") + 3]
    assert "String(config.agentAvatar || \"\").trim()" in error_handler
    assert "if (!failedAvatarSrcs.has(candidate))" in error_handler


def test_agent_selection_lists_share_the_same_avatar_resolver():
    """选择列表兜底仍是首字母，但判定口径必须与气泡同源（不得各自读 avatar_url）。"""
    cascade = (ROOT / "frontend/src/components/embed/ExpertCascadeMenu.vue").read_text(encoding="utf-8")
    mention = (ROOT / "frontend/src/components/agent/MentionList.vue").read_text(encoding="utf-8")

    for source in (cascade, mention):
        assert "agentOwnAvatarUrl" in source
        assert "agent.avatar_url" not in source and "row.agent.avatar_url" not in source


def test_agent_avatar_field_is_shared_by_create_and_edit_flows():
    """新建与编辑必须共用同一个头像控件：外观与能力完全一致。

    回归背景：新建流程「智能体信息」步骤最初只有一个 URL 输入框（无上传、无预设），
    而编辑弹窗有完整控件，用户反馈「新建的时候怎么只有一个输入框」。
    """
    field = (ROOT / "frontend/src/components/agent/AgentAvatarField.vue").read_text(encoding="utf-8")

    # ① 控件内含完整能力：预览 / URL 输入 / 上传 / 继承全局形象
    assert "智能体头像" in field
    assert "agentAvatarPreview" not in field  # 预览已内联为 computed
    assert "未设置" in field
    assert 'placeholder="可选：填写图片 URL，或点右侧上传"' in field
    assert "继承全局形象" in field

    # ② 预设快选：点「官方默认」= 清空（继承全局），与全局头像同一套资源
    assert "PRESET_AGENT_AVATARS" in field
    assert "avatarUrl = preset.isDefault ? '' : preset.url" in field

    # ③ 输入框对齐 DB 字段上限，避免 MySQL 1406
    assert ':maxlength="AGENT_AVATAR_URL_MAX_LENGTH"' in field

    # ④ 上传/裁剪只回填 v-model，绝不代替用户保存
    assert "onUploaded: (url) => {" in field
    assert "avatarUrl.value = url;" in field
    assert "axios.put(" not in field
    assert 'title="裁剪智能体头像"' in field

    # ⑤ 两处调用方都使用该组件，且上传按钮**不再**按 isCreatingAgent 条件隐藏
    mgmt = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")
    drawer = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    for source in (mgmt, drawer):
        assert "<AgentAvatarField" in source
        assert "v-model=\"agentForm.avatar_url\"" in source
        assert ":agent-id=\"selectedAgent?.id\"" in source
        # 上传细节只应存在于控件/组合式里
        assert "v-if=\"!isCreatingAgent && selectedAgent?.id\"" not in source
        assert "/avatar/upload" not in source


def test_create_agent_info_step_matches_the_edit_modal_layout():
    """新建步骤的排版与编辑弹窗一致：标识符/显示名称/排序权重 同行，头像独占一行。"""
    drawer = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    mgmt = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")
    field = (ROOT / "frontend/src/components/agent/AgentAvatarField.vue").read_text(encoding="utf-8")

    # ① 排序权重与标识符/显示名称同处一行（编辑弹窗的三列栅格）
    assert "md:grid-cols-[1fr_1fr_8rem]" in mgmt
    assert "md:grid-cols-[1fr_1fr_8rem]" in drawer
    assert 'v-model.number="agentForm.sort_order"' in drawer
    assert "排序权重" in drawer
    # 排序权重带用途说明（仅影响选择列表顺序）
    assert "仅影响聊天页面的智能体选择列表顺序，值越大越靠前" in drawer

    # ② 头像不再是半行，而是独占一行的独立区块
    assert drawer.index("<AgentAvatarField") > drawer.index("排序权重")
    assert "grid grid-cols-1 gap-4 sm:grid-cols-2" in drawer  # 仍保留其它成对字段的行
    assert "rounded-xl border border-gray-200 bg-gray-50/70 p-3" in field

    # ③ 「智能体信息」步骤确实只在新建时出现（这正是当初入口不可达的原因）
    assert "isCreatingAgent.value ? [{ id: 'agent' as const" in mgmt


def test_new_agent_avatar_uploads_before_the_agent_exists():
    """新建流程尚无 agent_id，上传必须走待绑定接口，而不是要求先保存。"""
    composable = (ROOT / "frontend/src/composables/useAgentAvatarUpload.ts").read_text(encoding="utf-8")

    # ① 有 id 走按智能体隔离的上传；无 id 走待绑定上传
    assert "const endpoint = agentId" in composable
    assert "`/api/portal/agents/${encodeURIComponent(agentId)}/avatar/upload`" in composable
    assert '"/api/portal/agents/avatar/upload"' in composable
    # ② 不得再以「请先保存」为由拒绝上传
    assert "请先保存智能体" not in composable


def test_agent_avatar_upload_flow_is_shared_not_copied():
    """上传/裁剪流程只允许存在于「组合式 + 控件」里，页面不得各自复制一份。"""
    composable = (ROOT / "frontend/src/composables/useAgentAvatarUpload.ts").read_text(encoding="utf-8")
    field = (ROOT / "frontend/src/components/agent/AgentAvatarField.vue").read_text(encoding="utf-8")

    # ① 组合式承载上传本身
    assert "export function useAgentAvatarUpload" in composable
    assert "/avatar/upload" in composable
    assert "showCropper" in composable
    assert "MAX_SELECT_BYTES" in composable

    # ② 控件承载 UI 并复用组合式
    assert "useAgentAvatarUpload" in field
    assert "AvatarCropperModal" in field

    # ③ 两个页面只引控件，不再各自持有上传实现
    for rel in (
        "frontend/src/views/AgentManagement.vue",
        "frontend/src/components/agent/AgentVersionEditorDrawer.vue",
    ):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "AgentAvatarField" in source, rel
        assert "useAgentAvatarUpload" not in source, rel
        assert "/avatar/upload" not in source, rel
        assert "AvatarCropperModal" not in source, rel


def test_agent_name_is_prechecked_for_duplicates_before_submit():
    """物理标识符必须在输入期就检查重名，而不是提交后只拿到一条 400。

    回归背景：用户在「新建智能体」时撞名，只看到后端返回的 400（原文还是英文
    `Agent with ID/Name 'x' already exists.`），在填写阶段毫无提示。
    """
    composable = (ROOT / "frontend/src/composables/useAgentNameAvailability.ts").read_text(encoding="utf-8")
    field = (ROOT / "frontend/src/components/agent/AgentNameField.vue").read_text(encoding="utf-8")
    mgmt = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")
    drawer = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")

    # ① 预检走服务端统一判定，不在前端自行臆断
    assert 'export function useAgentNameAvailability' in composable
    assert '"/api/portal/agents/name-availability"' in composable
    assert "exclude_agent_id" in composable
    # 乱序响应保护：旧请求不得覆盖新结论
    assert "requestSeq" in composable
    # 预检接口异常时不阻塞提交，交由后端权威裁决
    assert "available.value = null" in composable

    # ② 字段承载内联反馈（红框 + 原因 + 检查中状态）
    assert "物理标识符" in field
    assert "errorMessage" in field
    assert "正在检查标识符是否可用" in field
    assert "@blur=\"onBlur\"" in field
    assert "emit('check'" in field

    # ③ 两个创建界面都用同一控件
    for source in (mgmt, drawer):
        assert "AgentNameField" in source
        assert "<AgentNameField" in source

    # ④ 提交前兜底：两条创建路径都要拦截（弹窗 saveAgent + 抽屉/引导 persistNewAgentDraft）
    assert "ensureAgentNameUsable" in mgmt
    assert mgmt.count("await ensureAgentNameUsable()") == 2
    assert "physicalName" not in mgmt  # 防止误用未定义变量

    # ⑤ 打开界面时清空上一次结论，避免残留红字
    assert mgmt.count("resetAgentNameCheckState();") >= 3
