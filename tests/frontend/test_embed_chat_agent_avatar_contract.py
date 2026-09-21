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
    assert ':src="config.agentAvatar || agentAvatarUrl"' in source
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

    # 5 组预设（默认 + 4 组风格化）全部走静态资源
    assert presets_source.count("\n    url: ") == len(preset_assets) + 1

    # 所有内联字面量 URL 都必须在服务端长度上限内
    for literal in re.findall(r'url: "([^"]+)"', presets_source):
        assert len(literal) <= 2048, literal

    # 前端长度护栏接入设置面板
    settings_source = (ROOT / "frontend/src/components/embed/ChatSettings.vue").read_text(encoding="utf-8")
    assert "isAgentAvatarUrlTooLong(target)" in settings_source
    assert "MAX_AGENT_AVATAR_URL_LENGTH" in settings_source


