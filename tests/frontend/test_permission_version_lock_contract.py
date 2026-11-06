"""权限保存乐观锁的契约。

``update_user_permissions`` / ``update_role_permissions`` 是「删光 + 重建」的全量语义，
前端提交的是打开弹窗那一刻的快照。这期间别处（知识库页面分配、访问申请审批通过）可能
刚给同一主体加过授权——那次保存会把它们一起删掉，且审计里看不出来。

这里守的是三件事：

1. GET 一定带回版本，PUT 一定带回版本；
2. 版本不符时在**删任何行之前**就中止；
3. 409 之后前端会重新拉取最新配置，而不是留着一份过期快照继续编辑。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app/services/permission_service.py"
SCHEMA = ROOT / "app/schemas/permission.py"
USERS_ENDPOINT = ROOT / "app/api/portal/endpoints/management.py"
ROLES_ENDPOINT = ROOT / "app/api/portal/endpoints/roles.py"
USERS_VUE = ROOT / "frontend/src/views/Users.vue"
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _func_body(source: str, name: str) -> str:
    return source.split(f"async def {name}", 1)[1].split("\n    async def ", 1)[0]


# ---------------------------------------------------------------- 后端：版本从哪来、到哪去
def test_schema_carries_version_both_ways():
    source = _read(SCHEMA)
    update_block = source.split("class PermissionUpdate", 1)[1]
    response_block = source.split("class UserPermissionsResponse", 1)[1]
    assert "permissions_version" in update_block
    assert "permissions_version" in response_block


def test_get_user_permissions_returns_live_version_even_on_cache_hit():
    """缓存里存的是写入那一刻的指纹。

    命中缓存时若直接把它当版本返回，管理员每一次正常保存都会被判成「已被他人修改」，
    直到缓存过期。所以缓存分支必须现查一次——但只在 with_version=True 时查，
    登录/刷新这些热点路径不为它多付一次聚合查询。
    """
    source = _read(SERVICE)
    body = _func_body(source, "get_user_permissions")
    cache_branch = body.split("# 2. DB Query", 1)[0]
    assert "if with_version:" in cache_branch
    assert "cached.permissions_version = await self.get_permission_version(" in cache_branch


def test_version_is_only_fetched_for_the_permission_admin_endpoint():
    """热点路径（登录 / 刷新 / 用户信息）不该多一次 count+max 聚合查询。"""
    service = _read(SERVICE)
    assert "with_version: bool = False," in service
    body = _func_body(service, "get_user_permissions")
    db_branch = body.split("# 2. DB Query", 1)[1]
    assert "if with_version else None" in db_branch

    endpoint = _read(USERS_ENDPOINT)
    assert "get_user_permissions(user_id, with_version=True)" in endpoint


def test_get_role_permissions_returns_version():
    body = _func_body(_read(SERVICE), "get_role_permissions")
    assert "permissions_version=await self.get_permission_version(role_id=role_id)" in body


def test_version_check_runs_before_the_destructive_delete():
    """顺序比存在性更重要：先删后校验，一样会丢数据。"""
    source = _read(SERVICE)
    for name in ("update_user_permissions", "update_role_permissions"):
        body = _func_body(source, name)
        assert "_assert_permission_version" in body
        assert body.index("_assert_permission_version") < body.index(
            "delete(ResourcePermission)"
        ), f"{name}: 版本校验必须在 delete 之前"


def test_version_fingerprint_is_three_dimensional():
    body = _func_body(_read(SERVICE), "get_permission_version")
    for column in ("func.count(ResourcePermission.id)", "func.max(ResourcePermission.id)",
                   "func.max(ResourcePermission.updated_at)"):
        assert column in body


def test_version_scope_matches_the_delete_scope():
    """指纹的统计范围必须和全量重建的删除范围一致（都是全局行）。

    智能体级行不在删除范围内，算进指纹只会制造误报：有人在智能体卡片上调整该用户的
    限制，管理员的用户权限页就提示"已被他人修改"。
    """
    source = _read(SERVICE)
    version_body = _func_body(source, "get_permission_version")
    assert "await self._agent_scope_filters(None)" in version_body
    for name in ("update_user_permissions", "update_role_permissions"):
        assert "await self._agent_scope_filters(None)" in _func_body(source, name)


# ---------------------------------------------------------------- 后端：冲突映射成 409
def test_user_endpoint_maps_conflict_to_409():
    body = _read(USERS_ENDPOINT).split("async def update_user_permissions", 1)[1]
    body = body.split("@router.", 1)[0]
    assert "except PermissionVersionConflict" in body
    assert "status_code=409" in body


def test_role_endpoint_maps_conflict_to_409():
    body = _read(ROLES_ENDPOINT).split("async def update_role_resources", 1)[1]
    body = body.split("@router.", 1)[0]
    assert "except PermissionVersionConflict" in body
    assert "status_code=409" in body


# ---------------------------------------------------------------- 前端：带回版本 + 409 后重载
def test_users_page_sends_version_and_recovers_from_409():
    source = _read(USERS_VUE)
    assert "const permissionsVersion = ref(" in source
    assert "permissions_version: permissionsVersion.value || undefined" in source
    # 加载时记录版本
    assert "response.data as any)?.permissions_version" in source
    # 409 分支必须重新拉取，否则用户会拿着一份过期快照继续编辑
    branch = source.split("permError?.response?.status === 409", 1)[1]
    assert "await fetchUserPermissions(editingUserId.value)" in branch.split(
        "showToast(`权限保存失败", 1
    )[0]


def test_roles_page_sends_version_and_recovers_from_409():
    source = _read(ROLES_VUE)
    assert "const permissionsVersion = ref(" in source
    assert "permissions_version: permissionsVersion.value || undefined" in source
    assert "response.data as any)?.permissions_version" in source
    branch = source.split("e?.response?.status === 409", 1)[1]
    assert "await fetchRolePermissions(currentRole.value.id)" in branch.split(
        "showToast(`权限保存失败", 1
    )[0]


def test_save_returns_and_adopts_the_new_version():
    """保存成功要把新版本回给前端，否则弹窗没关时下一次保存会自己撞自己。

    Users 页的保存流程是「先 PUT 权限、再 PUT 资料、最后 closeDialogs()」。资料那步
    失败时弹窗不关，管理员重试就会拿旧版本再 PUT 一次权限——没有这一步回写，他会看到
    "权限已被他人修改"，而"他人"其实是他自己。
    """
    for path, body in ((USERS_ENDPOINT, "update_user_permissions"),
                       (ROLES_ENDPOINT, "update_role_resources")):
        source = _read(path)
        block = source.split(f"async def {body}", 1)[1].split("@router.", 1)[0]
        assert "\"permissions_version\": await service.get_permission_version(" in block

    for vue in (USERS_VUE, ROLES_VUE):
        source = _read(vue)
        assert "permRes.data as any)?.permissions_version" in source
