import uuid
import pytest
from httpx import AsyncClient
from app.services.auth_service import AuthService
from app.core.redis import get_redis


@pytest.mark.asyncio
async def test_embed_ticket_lifecycle(client: AsyncClient, db_session):
    """
    测试 Embed Ticket 完整生命周期：
    1. 签发 Ticket (POST /api/v1/embed/tickets)
    2. 兑换 Session Token (POST /api/v1/embed/tickets/exchange)
    3. 防重放测试 (同一 Ticket 无法兑换两次)
    4. Session Token 鉴权与滑动续期能力验证 (/api/portal/auth/me)
    """
    suffix = uuid.uuid4().hex[:8]
    # 1. 准备测试用户与操作人 Key
    admin_name = f"ticket_adm_{suffix}"
    admin_key = await AuthService.generate_api_key(
        user_name=admin_name, role="admin", db=db_session
    )

    target_user_name = f"ticket_user_{suffix}"
    target_key = await AuthService.generate_api_key(
        user_name=target_user_name,
        real_name="测试员工张三",
        role="user",
        db=db_session,
    )

    # 2. 服务端代表 target_user_name 签发 Ticket
    ticket_resp = await client.post(
        "/api/v1/embed/tickets",
        json={
            "username": target_user_name,
            "agent_id": "sys-agent-chatbi",
            "expires_in": 300,
        },
        headers={"X-API-Key": admin_key},
    )
    assert ticket_resp.status_code == 200
    ticket_data = ticket_resp.json()
    assert ticket_data["code"] == 200
    assert "ticket" in ticket_data["data"]
    ticket_str = ticket_data["data"]["ticket"]
    assert ticket_str.startswith("emt_")
    assert ticket_data["data"]["target_user"]["user_name"] == target_user_name

    # 3. 前端 iframe 调用兑换接口换取 Session Token
    exchange_resp = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": ticket_str},
    )
    assert exchange_resp.status_code == 200
    session_data = exchange_resp.json()
    assert session_data["code"] == 200
    assert "session_token" in session_data["data"]
    session_token = session_data["data"]["session_token"]
    assert session_token.startswith("emb_ses_")
    assert session_data["data"]["user_info"]["user_name"] == target_user_name
    assert session_data["data"]["user_info"]["real_name"] == "测试员工张三"

    # 4. 防重放校验：再次使用该 ticket 兑换应立即失败 (400)
    replay_resp = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": ticket_str},
    )
    assert replay_resp.status_code == 400
    assert "not found" in replay_resp.text.lower() or "expired" in replay_resp.text.lower()

    # 5. 使用兑换出的 session_token 调用受保护接口验证鉴权
    me_resp = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": session_token},
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["data"]["user_name"] == target_user_name
    assert me_data["data"]["real_name"] == "测试员工张三"

    # 6. 验证滑动续期：请求后 Redis TTL 应被维持在 ~86400 秒 (24 小时)
    r = await get_redis()
    if r:
        from app.utils.encryption import get_api_key_manager

        manager = get_api_key_manager()
        h = manager.hash_api_key(session_token)
        ttl = await r.ttl(f"auth:api_key:{h}")
        assert ttl > 80000  # 接近 86400 秒 (24 小时)


@pytest.mark.asyncio
async def test_embed_ticket_invalid_user_and_token(client: AsyncClient, db_session):
    """
    测试边界与异常场景：
    - 管理员调用但目标用户不存在时报错 404
    - 伪造 ticket 兑换报错 400
    """
    # 1. 管理员调用但目标用户不存在，应返回 404
    suffix = uuid.uuid4().hex[:8]
    admin_name = f"ticket_adm404_{suffix}"
    admin_key = await AuthService.generate_api_key(
        user_name=admin_name, role="admin", db=db_session
    )
    not_found_resp = await client.post(
        "/api/v1/embed/tickets",
        json={"username": "non_existent_user_999"},
        headers={"X-API-Key": admin_key},
    )
    assert not_found_resp.status_code == 404

    # 2. 伪造 Ticket 兑换，应返回 400
    bad_resp = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": "emt_fake_ticket_123"},
    )
    assert bad_resp.status_code == 400


@pytest.mark.asyncio
async def test_embed_ticket_impersonation_permissions(client: AsyncClient, db_session):
    """
    测试代客签发 (Impersonation) 权限边界：
    1. 普通用户为自身签发 Ticket -> 200 成功
    2. 普通用户不传参数签发 Ticket (默认自身) -> 200 成功
    3. 普通用户尝试为他人签发 (无权限) -> 403 拒绝
    4. 普通用户仅持旧的 GET:/api/v1/users/profile 权限 -> 仍 403
       （该权限已回归「获取用户信息」本义，不再兼作代客签发凭证）
    5. 普通用户获得 POST:/api/v1/embed/tickets 权限后代他人签发 -> 200 成功

    代客签发使用独立的 API 权限码 POST:/api/v1/embed/tickets，可在后台「API 权限」
    中按角色分配。注意该权限码的实际语义是「可代表他人调用签发接口」：/embed/*
    本身在 V1 接口白名单内不做拦截，而自己为自己签发也不需要本权限，
    平台内智能体预览走「代表自己」分支，同样不经过本权限检查。
    """
    from app.services.permission_service import PermissionService
    from app.schemas.permission import PermissionUpdate

    suffix = uuid.uuid4().hex[:8]
    # 创建两个普通用户和一个管理员
    user_a_name = f"user_a_{suffix}"
    user_a_key = await AuthService.generate_api_key(
        user_name=user_a_name, role="user", db=db_session
    )

    user_b_name = f"user_b_{suffix}"
    user_b_key = await AuthService.generate_api_key(
        user_name=user_b_name, role="user", db=db_session
    )

    # 获取 user_a 的实际 ID
    user_a_info = await AuthService.verify_api_key(user_a_key, db=db_session)
    user_a_id = int(user_a_info["user_id"])

    # 1. user_a 为自己签发 (传自己用户名) -> 应该 200 成功
    self_resp1 = await client.post(
        "/api/v1/embed/tickets",
        json={"username": user_a_name},
        headers={"X-API-Key": user_a_key},
    )
    assert self_resp1.status_code == 200
    assert self_resp1.json()["data"]["target_user"]["user_name"] == user_a_name

    # 2. user_a 为自己签发 (不传 username/user_id，默认自身) -> 应该 200 成功
    self_resp2 = await client.post(
        "/api/v1/embed/tickets",
        json={},
        headers={"X-API-Key": user_a_key},
    )
    assert self_resp2.status_code == 200
    assert self_resp2.json()["data"]["target_user"]["user_name"] == user_a_name

    # 3. user_a 试图为 user_b 代客签发（此时无任何相关权限）-> 应该 403 拒绝
    impersonate_resp = await client.post(
        "/api/v1/embed/tickets",
        json={"username": user_b_name},
        headers={"X-API-Key": user_a_key},
    )
    assert impersonate_resp.status_code == 403
    assert "permission denied" in impersonate_resp.text.lower() or "embed/tickets" in impersonate_resp.text

    # 4. 仅授予 user_a 旧的 'GET:/api/v1/users/profile' API 权限 -> 仍应 403
    #    该权限已回归本义，不再作为代客签发凭证；此断言锁定「只认新权限码」的策略。
    perm_service = PermissionService(db_session)
    await perm_service.update_user_permissions(
        user_id=user_a_id,
        updates=PermissionUpdate(apis=["GET:/api/v1/users/profile"]),
    )
    legacy_only_resp = await client.post(
        "/api/v1/embed/tickets",
        json={"username": user_b_name},
        headers={"X-API-Key": user_a_key},
    )
    assert legacy_only_resp.status_code == 403

    # 5. 授予 user_a 代客签发 API 权限 -> 应该 200 成功
    #    （update_user_permissions 为覆盖式，上一步的旧权限会被清掉）
    await perm_service.update_user_permissions(
        user_id=user_a_id,
        updates=PermissionUpdate(apis=["POST:/api/v1/embed/tickets"]),
    )

    # 6. user_a 再次为 user_b 代客签发 -> 应该 200 成功
    authorized_impersonate_resp = await client.post(
        "/api/v1/embed/tickets",
        json={"username": user_b_name},
        headers={"X-API-Key": user_a_key},
    )
    assert authorized_impersonate_resp.status_code == 200
    assert authorized_impersonate_resp.json()["data"]["target_user"]["user_name"] == user_b_name


@pytest.mark.asyncio
async def test_origin_mismatch_does_not_consume_ticket(client: AsyncClient, db_session):
    """来源校验失败**不得**消耗票据——这是可修复错误，换对来源应当仍能兑换。

    回归背景：exchange 原先「先 GETDEL 核销、再校验 allowed_origins」，一次来源不匹配
    就把票白白吃掉，用户重试只能拿到 400 "already used"，界面上也只显示「凭证为一次性
    使用、已失效」，真实原因（域名白名单不匹配）被彻底掩盖——实测有人据此误判为
    「票被别人用过了」。

    同时锁定两点不变式：校验通过后依旧是**一次性**（防重放不能被削弱）。
    """
    suffix = uuid.uuid4().hex[:8]
    admin_key = await AuthService.generate_api_key(
        user_name=f"ticket_origin_adm_{suffix}", role="admin", db=db_session
    )
    target_name = f"ticket_origin_user_{suffix}"
    await AuthService.generate_api_key(
        user_name=target_name, role="user", db=db_session
    )

    resp = await client.post(
        "/api/v1/embed/tickets",
        json={
            "username": target_name,
            "allowed_origins": ["https://crm.example.com"],
            "expires_in": 300,
        },
        headers={"X-API-Key": admin_key},
    )
    assert resp.status_code == 200
    ticket = resp.json()["data"]["ticket"]

    # 1. 来自未授权来源 -> 403
    bad = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": ticket},
        headers={"Origin": "http://localhost:8001"},
    )
    assert bad.status_code == 403
    assert "not allowed" in bad.text.lower()

    # 2. 同一张票换用受信来源 -> 仍可正常兑换（票没有被上一次失败消耗）
    good = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": ticket},
        headers={"Origin": "https://crm.example.com"},
    )
    assert good.status_code == 200
    assert good.json()["data"]["session_token"].startswith("emb_ses_")

    # 3. 兑换成功后仍然是一次性的：再次兑换失败（防重放不被削弱）
    replay = await client.post(
        "/api/v1/embed/tickets/exchange",
        json={"ticket": ticket},
        headers={"Origin": "https://crm.example.com"},
    )
    assert replay.status_code == 400

