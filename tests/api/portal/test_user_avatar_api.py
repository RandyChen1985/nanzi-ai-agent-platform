"""用户自定义头像（个人中心）：落盘口径、隔离边界与端点行为。

两层用例：

1. **纯单元 / 端点直调**（`no_infrastructure`，不碰库）：目录与 URL 约定、
   `save_user_avatar` / `delete_user_avatar` 的清理范围、类型与大小校验、幂等删除。
   这些用例只依赖 Python 侧的 `User.avatar_url` 属性，**不需要**真实库里有该列。
2. **HTTP 集成**（`client` + 真实库）：`POST/DELETE /me/avatar` 落库、`GET /me` 回显。

⚠️ 第二层依赖迁移 `db-prod/V198-add-user-avatar.sql`（PG 为 `db-prod-pg/V98-add-user-avatar.sql`）。
本仓库的迁移由用户执行、Agent 不跑 DDL，所以在该迁移应用前，集成用例会因
`Unknown column 'ai_agent_users.avatar_url'`（PG: column does not exist）而失败——
这是**预期红灯**，不是实现缺陷。迁移执行后重跑本文件即可全绿。

隔离边界的教训来自 `app/core/avatar_assets.py` 模块头部的历史坑：全局 AI 头像上传
会用 `agent_avatar*` 通配清空自己的目录。用户头像因此单独放在
`data/uploads/avatars/{user_id}/`，且只允许删自己目录下的 `avatar_*`——本文件用
「别人的文件 / 别的目录的文件必须还在」这种反向断言把这条边界钉死。
"""

from io import BytesIO

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.portal.endpoints import auth as auth_endpoint
from app.core import avatar_assets
from app.models.user import User

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-user-avatar"

# 模块导入时先记下常量声明值：下面的 autouse 夹具会把 USER_AVATAR_ROOT 换成临时目录，
# 「声明值必须是 data/uploads/avatars」这条约定只能在被替换前取证据。
_DECLARED_USER_AVATAR_ROOT = avatar_assets.USER_AVATAR_ROOT
_DECLARED_USER_AVATAR_URL = avatar_assets.USER_AVATAR_URL


def _upload(
    filename="me.png",
    content=PNG_BYTES,
    content_type="image/png",
):
    return StarletteUploadFile(
        filename=filename,
        file=BytesIO(content),
        headers={"content-type": content_type},
    )


class FakeSession:
    """端点只用到 `session.get` 与 `session.commit`。"""

    def __init__(self, user=None):
        self.user = user
        self.commits = 0

    async def get(self, _model, _user_id):
        return self.user

    async def commit(self):
        self.commits += 1


def _user(user_id=7, avatar_url=None):
    return User(id=user_id, user_name=f"u{user_id}", real_name=f"用户{user_id}", avatar_url=avatar_url)


@pytest.fixture(autouse=True)
def _isolated_user_avatar_root(monkeypatch, tmp_path):
    """把用户头像根目录指向临时目录，绝不写真实 data/uploads。"""
    root = tmp_path / "uploads_avatars"
    monkeypatch.setattr(avatar_assets, "USER_AVATAR_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# 1. 目录与 URL 约定（三足鼎立，互不清理）
# ---------------------------------------------------------------------------
@pytest.mark.no_infrastructure
def test_user_avatar_root_and_url_convention():
    assert _DECLARED_USER_AVATAR_ROOT == "data/uploads/avatars"
    assert _DECLARED_USER_AVATAR_URL == "/static/uploads/avatars"
    # 挂载点：app/main.py 把 data/uploads 挂到 /static/uploads，URL 前缀必须与之对齐
    assert _DECLARED_USER_AVATAR_URL == "/static/uploads/avatars"


@pytest.mark.no_infrastructure
def test_user_avatar_dir_is_isolated_from_branding_dirs():
    """用户头像目录必须与全局头像、智能体头像目录彻底分离。

    品牌目录存在「上传全局头像即清空 agent_avatar*」的历史行为，用户头像一旦落进去
    就会被顺带删光；反过来用户的 glob 也不能展开到品牌目录。
    """
    root = avatar_assets.USER_AVATAR_ROOT
    for branding_dir in (avatar_assets.BRANDING_AVATARS_DIR, avatar_assets.AGENT_AVATAR_DIR):
        assert root != branding_dir
        assert not root.startswith(branding_dir + "/")
        assert not branding_dir.startswith(root + "/")
    assert avatar_assets.USER_AVATAR_URL != avatar_assets.BRANDING_AVATARS_URL
    assert avatar_assets.USER_AVATAR_URL != avatar_assets.AGENT_AVATAR_URL


@pytest.mark.no_infrastructure
def test_user_avatar_dir_is_per_user_and_created(_isolated_user_avatar_root):
    import os

    seven = avatar_assets.user_avatar_dir(7)
    eight = avatar_assets.user_avatar_dir("8")

    assert os.path.basename(seven) == "7"
    assert os.path.basename(eight) == "8"
    assert seven != eight
    assert os.path.isdir(seven) and os.path.isdir(eight)


@pytest.mark.no_infrastructure
@pytest.mark.parametrize("bad_user_id", ["../../etc", "7/../../etc", "abc", None, ""])
def test_user_avatar_dir_rejects_non_integer_user_id(bad_user_id):
    """目录名只接受整数：非整数在 int() 就抛错，杜绝把任意字符串拼进路径。"""
    with pytest.raises((ValueError, TypeError)):
        avatar_assets.user_avatar_dir(bad_user_id)


# ---------------------------------------------------------------------------
# 2. 落盘 / 删除：URL 形态、内容一致、只清自己
# ---------------------------------------------------------------------------
@pytest.mark.no_infrastructure
def test_save_user_avatar_returns_usable_url_and_writes_file(_isolated_user_avatar_root):
    url = avatar_assets.save_user_avatar(7, PNG_BYTES, ".png")

    assert url.startswith("/static/uploads/avatars/7/avatar_")
    assert url.endswith(".png")
    # 短路径：远低于 ai_agent_users.avatar_url 的 String(500) 上限
    assert len(url) < 500
    filename = url.rsplit("/", 1)[-1]
    saved = _isolated_user_avatar_root / "7" / filename
    assert saved.read_bytes() == PNG_BYTES


@pytest.mark.no_infrastructure
def test_save_user_avatar_replaces_own_previous_file(_isolated_user_avatar_root):
    old_url = avatar_assets.save_user_avatar(7, b"first", ".png")
    old_path = _isolated_user_avatar_root / "7" / old_url.rsplit("/", 1)[-1]
    assert old_path.exists()

    new_url = avatar_assets.save_user_avatar(7, b"second", ".webp")
    new_path = _isolated_user_avatar_root / "7" / new_url.rsplit("/", 1)[-1]

    assert new_url != old_url
    assert new_path.read_bytes() == b"second"
    assert not old_path.exists(), "同一用户的历史头像必须被清理，否则目录会无限膨胀"
    assert len(list((_isolated_user_avatar_root / "7").glob("avatar_*"))) == 1


@pytest.mark.no_infrastructure
def test_save_user_avatar_never_touches_other_users_or_root_files(_isolated_user_avatar_root):
    """核心回归：清旧头像只允许在自己 user_id 目录内展开。"""
    other_dir = _isolated_user_avatar_root / "8"
    other_dir.mkdir(parents=True, exist_ok=True)
    other_user_file = other_dir / "avatar_1700000000000.png"
    other_user_file.write_bytes(b"other-user-avatar")

    root_level_file = _isolated_user_avatar_root / "avatar_stray.png"
    root_level_file.write_bytes(b"stray")

    own_dir = _isolated_user_avatar_root / "7"
    own_dir.mkdir(parents=True, exist_ok=True)
    (own_dir / "avatar_1600000000000.png").write_bytes(b"stale-own")

    avatar_assets.save_user_avatar(7, PNG_BYTES, ".png")

    assert not (own_dir / "avatar_1600000000000.png").exists(), "自己的旧头像应被清理"
    assert other_user_file.read_bytes() == b"other-user-avatar", "别的用户目录被误删了"
    assert root_level_file.read_bytes() == b"stray", "根目录下的文件不属于任何用户，绝不能碰"
    assert len(list(own_dir.glob("avatar_*"))) == 1


@pytest.mark.no_infrastructure
def test_delete_user_avatar_returns_count_and_is_idempotent(_isolated_user_avatar_root):
    avatar_assets.save_user_avatar(7, b"a", ".png")

    assert avatar_assets.delete_user_avatar(7) == 1
    assert list((_isolated_user_avatar_root / "7").glob("avatar_*")) == []
    # 重复删除：不再有文件，返回 0 且不抛异常
    assert avatar_assets.delete_user_avatar(7) == 0
    # 从未上传过的用户：目录都不存在，同样返回 0
    assert avatar_assets.delete_user_avatar(999) == 0
    assert not (_isolated_user_avatar_root / "999").exists(), "删除路径不应顺手创建目录"


@pytest.mark.no_infrastructure
def test_delete_user_avatar_only_removes_own_dir(_isolated_user_avatar_root):
    other_dir = _isolated_user_avatar_root / "8"
    other_dir.mkdir(parents=True, exist_ok=True)
    other_user_file = other_dir / "avatar_1700000000000.png"
    other_user_file.write_bytes(b"keep-me")
    root_level_file = _isolated_user_avatar_root / "avatar_stray.png"
    root_level_file.write_bytes(b"keep-me-too")

    avatar_assets.save_user_avatar(7, b"mine", ".gif")
    assert avatar_assets.delete_user_avatar(7) == 1

    assert other_user_file.exists()
    assert root_level_file.exists()


@pytest.mark.no_infrastructure
def test_save_user_avatar_rejects_svg_and_traversal_ext(_isolated_user_avatar_root):
    """资产层再兜一道：即便调用方绕过端点校验，也不落 svg / 不越出用户目录。"""
    with pytest.raises(ValueError):
        avatar_assets.save_user_avatar(7, b"<svg/>", ".svg")
    for bad_ext in ["", ".png/../x", "..\\x"]:
        with pytest.raises(ValueError):
            avatar_assets.save_user_avatar(7, PNG_BYTES, bad_ext)
    assert not list(_isolated_user_avatar_root.rglob("avatar_*"))


# ---------------------------------------------------------------------------
# 3. 类型校验：复用全局白名单，但用户头像单独拒绝 svg
# ---------------------------------------------------------------------------
@pytest.mark.no_infrastructure
def test_max_avatar_bytes_stays_2mb():
    assert avatar_assets.MAX_AVATAR_BYTES == 2 * 1024 * 1024


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "content_type,filename,expected",
    [
        ("image/png", "a.png", ".png"),
        ("image/jpeg", "a.jpg", ".jpg"),
        ("image/webp", "a.webp", ".webp"),
        ("image/gif", "a.gif", ".gif"),
        # MIME 缺失时按文件名兜底，与全局口径一致
        ("", "photo.JPEG", ".jpg"),
    ],
)
def test_resolve_user_avatar_extension_accepts_raster_images(content_type, filename, expected):
    assert avatar_assets.resolve_user_avatar_extension(content_type, filename) == expected


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "content_type,filename",
    [
        ("image/svg+xml", "vector.svg"),          # MIME 命中全局白名单，但用户头像拒绝
        ("image/svg+xml", "vector.png"),          # MIME 优先，不允许靠改名绕过
        ("application/octet-stream", "evil.svg"),  # 文件名兜底也要拒
        ("application/octet-stream", "evil.exe"),
        ("text/html", "page.html"),
        (None, None),
    ],
)
def test_resolve_user_avatar_extension_rejects_svg_and_unknown(content_type, filename):
    assert avatar_assets.resolve_user_avatar_extension(content_type, filename) is None
    # 对照：全局解析仍然接受 svg（品牌图/智能体图标的口径没变）
    if filename and str(filename).lower().endswith(".svg"):
        assert avatar_assets.resolve_avatar_extension(content_type, filename) == ".svg"


# ---------------------------------------------------------------------------
# 4. 端点直调：POST /me/avatar
# ---------------------------------------------------------------------------
@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_upload_writes_avatar_url_into_user_row(_isolated_user_avatar_root):
    target = _user(7)
    session = FakeSession(target)

    result = await auth_endpoint.upload_my_avatar(
        file=_upload(), user={"user_id": "7", "user_name": "u7"}, db=session
    )

    assert result["avatar_url"].startswith("/static/uploads/avatars/7/avatar_")
    assert target.avatar_url == result["avatar_url"]
    assert session.commits == 1, "上传成功后必须 commit，否则刷新页面头像就没了"
    assert (_isolated_user_avatar_root / "7" / result["avatar_url"].rsplit("/", 1)[-1]).exists()


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_upload_rejects_svg_with_explicit_reason(_isolated_user_avatar_root):
    with pytest.raises(HTTPException) as exc_info:
        await auth_endpoint.upload_my_avatar(
            file=_upload(filename="x.svg", content=b"<svg/>", content_type="image/svg+xml"),
            user={"user_id": "7", "user_name": "u7"},
            db=FakeSession(_user(7)),
        )

    assert exc_info.value.status_code == 400
    assert "SVG" in exc_info.value.detail
    # 不能落成「只支持 PNG/JPEG/WebP/GIF」那种含糊文案：svg 是被刻意拒的，要说明原因
    assert "只支持" not in exc_info.value.detail
    assert not list(_isolated_user_avatar_root.rglob("avatar_*")), "被拒的上传不得落盘"


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,content_type",
    [("a.txt", "text/plain"), ("a.exe", "application/octet-stream"), ("a.html", "text/html")],
)
async def test_upload_rejects_unsupported_type(filename, content_type):
    with pytest.raises(HTTPException) as exc_info:
        await auth_endpoint.upload_my_avatar(
            file=_upload(filename=filename, content=b"data", content_type=content_type),
            user={"user_id": "7", "user_name": "u7"},
            db=FakeSession(_user(7)),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "只支持 PNG/JPEG/WebP/GIF 格式"


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_upload_rejects_oversize_with_413(monkeypatch):
    monkeypatch.setattr(avatar_assets, "MAX_AVATAR_BYTES", 8)

    with pytest.raises(HTTPException) as exc_info:
        await auth_endpoint.upload_my_avatar(
            file=_upload(content=b"x" * 64),
            user={"user_id": "7", "user_name": "u7"},
            db=FakeSession(_user(7)),
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "头像不能超过 2MB"


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_upload_accepts_exactly_2mb_boundary(monkeypatch):
    """边界不弱化：恰好 2MB 放行，多 1 字节拒绝。"""
    monkeypatch.setattr(avatar_assets, "MAX_AVATAR_BYTES", 16)
    target = _user(7)
    result = await auth_endpoint.upload_my_avatar(
        file=_upload(content=b"y" * 16), user={"user_id": "7", "user_name": "u7"}, db=FakeSession(target)
    )
    assert result["avatar_url"]

    with pytest.raises(HTTPException) as exc_info:
        await auth_endpoint.upload_my_avatar(
            file=_upload(content=b"y" * 17), user={"user_id": "7", "user_name": "u7"}, db=FakeSession(_user(7))
        )
    assert exc_info.value.status_code == 413


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_upload_missing_user_returns_404():
    with pytest.raises(HTTPException) as exc_info:
        await auth_endpoint.upload_my_avatar(
            file=_upload(), user={"user_id": "7", "user_name": "u7"}, db=FakeSession(None)
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. 端点直调：DELETE /me/avatar
# ---------------------------------------------------------------------------
@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_delete_clears_column_and_removes_file(_isolated_user_avatar_root):
    target = _user(7)
    upload = await auth_endpoint.upload_my_avatar(
        file=_upload(), user={"user_id": "7", "user_name": "u7"}, db=FakeSession(target)
    )
    saved = _isolated_user_avatar_root / "7" / upload["avatar_url"].rsplit("/", 1)[-1]
    assert saved.exists()

    session = FakeSession(target)
    result = await auth_endpoint.delete_my_avatar(
        user={"user_id": "7", "user_name": "u7"}, db=session
    )

    assert result == {"avatar_url": None}
    assert target.avatar_url is None
    assert session.commits == 1
    assert not saved.exists()


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_delete_is_idempotent_for_repeated_calls(_isolated_user_avatar_root):
    """重复删除必须幂等：连点按钮 / 文件已被清理都不能报错。"""
    target = _user(7, avatar_url="/static/uploads/avatars/7/avatar_1.png")
    session = FakeSession(target)

    # 库里已有 URL 但磁盘上没有对应文件
    first = await auth_endpoint.delete_my_avatar(user={"user_id": "7"}, db=session)
    second = await auth_endpoint.delete_my_avatar(user={"user_id": "7"}, db=session)

    assert first == {"avatar_url": None}
    assert second == {"avatar_url": None}
    assert target.avatar_url is None
    assert session.commits == 2, "两次删除都应正常走完并 commit"


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_delete_does_not_touch_other_users_files(_isolated_user_avatar_root):
    other_dir = _isolated_user_avatar_root / "8"
    other_dir.mkdir(parents=True, exist_ok=True)
    other_file = other_dir / "avatar_1700000000000.png"
    other_file.write_bytes(b"other")

    avatar_assets.save_user_avatar(7, b"mine", ".png")
    await auth_endpoint.delete_my_avatar(user={"user_id": "7"}, db=FakeSession(_user(7)))

    assert other_file.read_bytes() == b"other"


# ---------------------------------------------------------------------------
# 6. HTTP 集成（依赖 V198/V98 迁移；未应用前预期红灯）
# ---------------------------------------------------------------------------
import uuid

from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.services.auth_service import AuthService

ME_URL = "/api/portal/auth/me"
AVATAR_URL = "/api/portal/auth/me/avatar"


async def _seed_user(user_name):
    """建一个启用状态的用户并返回其明文 API Key。"""
    async with AsyncSessionLocal() as s:
        return await AuthService.generate_api_key(
            user_name, real_name="头像测试", status=1, db=s
        )


async def _drop_user(user_name):
    async with AsyncSessionLocal() as s:
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()
        if uid is not None:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


async def test_avatar_endpoints_require_api_key(client):
    """auth.router 没有整组鉴权，两个端点都必须显式挂 require_api_key。

    漏挂就是匿名可上传（还能写任意用户目录），所以单独立一条钉住。
    """
    anon_upload = await client.post(
        AVATAR_URL, files={"file": ("me.png", PNG_BYTES, "image/png")}
    )
    assert anon_upload.status_code == 401, "匿名应当 401，而不是被当成上传成功"

    anon_delete = await client.delete(AVATAR_URL)
    assert anon_delete.status_code == 401


async def test_me_returns_avatar_url_field(client, valid_api_key):
    """`GET /me` 必须带 avatar_url（前端个人中心读 data.avatar_url）。"""
    resp = await client.get(ME_URL, headers={"X-API-Key": valid_api_key})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "avatar_url" in data
    assert data["avatar_url"] is None, "未设置自定义头像时必须是 None，由前端用姓名首字兜底"


async def test_upload_then_read_then_delete_roundtrip(client, _isolated_user_avatar_root):
    name = f"zavatar_{uuid.uuid4().hex[:8]}"
    try:
        api_key = await _seed_user(name)
        headers = {"X-API-Key": api_key}

        upload = await client.post(
            AVATAR_URL, files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=headers
        )
        assert upload.status_code == 200, upload.text
        avatar_url = upload.json()["avatar_url"]
        assert avatar_url.startswith("/static/uploads/avatars/")
        assert avatar_url.endswith(".png")

        me = await client.get(ME_URL, headers=headers)
        assert me.status_code == 200
        assert me.json()["data"]["avatar_url"] == avatar_url

        removed = await client.delete(AVATAR_URL, headers=headers)
        assert removed.status_code == 200
        assert removed.json() == {"avatar_url": None}

        me_after = await client.get(ME_URL, headers=headers)
        assert me_after.json()["data"]["avatar_url"] is None

        # 幂等：再删一次仍是 200
        again = await client.delete(AVATAR_URL, headers=headers)
        assert again.status_code == 200
        assert again.json() == {"avatar_url": None}
    finally:
        await _drop_user(name)


async def test_upload_rejects_oversize_and_svg_over_http(client, _isolated_user_avatar_root):
    """HTTP 层同样不能弱化类型/大小校验（防止只在直调用例里生效）。"""
    name = f"zavatar_{uuid.uuid4().hex[:8]}"
    try:
        api_key = await _seed_user(name)
        headers = {"X-API-Key": api_key}

        oversized = await client.post(
            AVATAR_URL,
            files={"file": ("big.png", b"x" * (avatar_assets.MAX_AVATAR_BYTES + 1), "image/png")},
            headers=headers,
        )
        assert oversized.status_code == 413
        assert oversized.json()["detail"] == "头像不能超过 2MB"

        svg = await client.post(
            AVATAR_URL,
            files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
            headers=headers,
        )
        assert svg.status_code == 400
        assert "SVG" in svg.json()["detail"]
    finally:
        await _drop_user(name)
