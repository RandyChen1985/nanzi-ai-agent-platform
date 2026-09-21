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


