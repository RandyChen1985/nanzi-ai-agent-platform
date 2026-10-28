"""用户头像（上传 / 裁剪 / 删除）的前端契约。

这些用例只做源码断言，不连后端、不启动浏览器：锁定「个人中心支持上传裁剪与删除、
聊天页左下角跟随 avatar_url、客户端先拦 2MB 与图片类型、删除必须二次确认」这几条
容易在后续重构中悄悄漂移的约定。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL_API = ROOT / "frontend/src/api/portal.ts"
PERSONAL_CENTER = ROOT / "frontend/src/views/PersonalCenter.vue"
DASHBOARD = ROOT / "frontend/src/views/Dashboard.vue"
CROPPER = ROOT / "frontend/src/components/common/AvatarCropperModal.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} 不存在"
    return path.read_text(encoding="utf-8")


def test_portal_api_exposes_avatar_upload_and_idempotent_delete():
    """上传/删除接口落在 portal 认证 API 上，且上传走 FormData 的 `file` 字段。"""
    source = _read(PORTAL_API)

    assert "uploadMyAvatar" in source
    assert "deleteMyAvatar" in source
    assert "/api/portal/auth/me/avatar" in source
    assert "Promise<string | null>" in source

    # multipart：FormData + 字段名 file
    assert "FormData" in source
    assert "formData.append('file'" in source

    # 上传函数内不手写 Content-Type / headers，交给浏览器生成带 boundary 的请求头
    upload_body = source[source.index("uploadMyAvatar:"):source.index("deleteMyAvatar:")]
    assert "Content-Type" not in upload_body
    assert "headers" not in upload_body

    assert "axios.delete('/api/portal/auth/me/avatar')" in source


def test_personal_center_uses_avatar_url_and_shared_cropper():
    """个人中心消费 avatar_url，裁剪复用公共弹窗，上传复用 portal API。"""
    source = _read(PERSONAL_CENTER)

    # 自定义头像分支 + 既有姓名首字兜底
    assert 'v-if="avatarUrl"' in source
    assert ':src="avatarUrl"' in source
    assert "userInitialOf(userInfo.real_name || userInfo.user_name)" in source

    # 复用现成裁剪弹窗，不自己实现裁剪
    assert "import AvatarCropperModal from '../components/common/AvatarCropperModal.vue'" in source
    assert "<AvatarCropperModal" in source
    assert 'title="裁剪头像"' in source
    assert ':output-size="256"' in source
    assert '@confirm="handleAvatarCropped"' in source
    assert '@close="closeAvatarCropper"' in source

    # 文件选择与上传
    assert 'accept="image/png,image/jpeg,image/webp,image/gif"' in source
    assert "URL.createObjectURL(file)" in source
    assert "await uploadMyAvatar(blob" in source
    assert "头像已更新" in source

    # 裁剪弹窗自身仍是唯一裁剪实现（canvas.toBlob）
    assert "canvas.toBlob" in _read(CROPPER)


def test_personal_center_has_primary_actions_and_inline_delete_confirmation():
    """「更换头像 / 删除头像」文案齐全，删除必须先进入行内确认态。"""
    source = _read(PERSONAL_CENTER)

    assert "更换头像" in source
    assert "删除头像" in source
    assert "已还原默认头像" in source
    assert "await deleteMyAvatar()" in source
    assert '@click="requestRemoveAvatar"' in source
    assert '@click="confirmRemoveAvatar"' in source
    assert '@click="cancelRemoveAvatar"' in source
    assert 'v-if="confirmingRemove"' in source

    delete_call = source.index("await deleteMyAvatar()")
    request_start = source.index("const requestRemoveAvatar")
    confirm_start = source.index("const confirmRemoveAvatar")

    # 确认态变量与「进入确认」的入口都必须出现在真正删除之前
    assert source.index("const confirmingRemove") < delete_call
    assert request_start < confirm_start < delete_call
    # 第一下点击只切确认态，不发删除请求
    assert "confirmingRemove.value = true" in source[request_start:confirm_start]
    assert "deleteMyAvatar" not in source[request_start:confirm_start]
    # 只有确认态为真才允许删除，避免其它路径绕过二次确认
    assert "if (!confirmingRemove.value || removingAvatar.value) return" in source


def test_personal_center_guards_avatar_size_and_type_client_side():
    """客户端先拦 2MB 与图片类型（省一次往返），文案与后端 detail 对齐。"""
    source = _read(PERSONAL_CENTER)

    assert "2 * 1024 * 1024" in source
    for mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        assert mime in source

    # 大小/类型校验发生在选文件阶段，且在上传调用之前
    change_start = source.index("const handleAvatarFileChange")
    upload_call = source.index("await uploadMyAvatar(")
    guard_block = source[change_start:upload_call]
    assert "file.size > AVATAR_MAX_BYTES" in guard_block
    assert "AVATAR_ALLOWED_TYPES.includes(file.type)" in guard_block

    assert "头像不能超过 2MB" in source
    assert "只支持 PNG/JPEG/WebP/GIF 格式" in source


def test_dashboard_sidebar_prefers_uploaded_avatar_and_keeps_initial_fallback():
    """聊天页左下角优先渲染 avatar_url，首字兜底逻辑保持原样。"""
    source = _read(DASHBOARD)

    assert "avatar_url" in source
    assert ':src="userInfo.avatar_url"' in source
    assert "object-cover" in source
    assert "rounded-full" in source

    # 既有首字逻辑不动：简称与尺寸仍由 sidebarInitial / sidebarInitialClass 提供
    assert "const sidebarInitial = computed" in source
    assert "userInitialOf(" in source
    assert "sidebarInitialClass" in source

    # 个人中心改头像后派发的事件，Dashboard 已有监听并重新拉 /auth/me
    assert "user-info-updated" in source
