import json
from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow


def _fake_request(method: str = "POST"):
    """端点里只用到 method / client / state.trace_id，构造一个够用的替身。"""
    return SimpleNamespace(
        method=method,
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(trace_id="test-trace"),
    )


@pytest.mark.asyncio
async def test_require_dataset_access_rejects_invisible_dataset(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_user_permissions(self, user_id, agent_id=None):
            return SimpleNamespace(
                permissions=SimpleNamespace(datasets=["allowed-ds"])
            )

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": False,
                "accessible_ids": {"allowed-ds"},
                "writable_ids": {"allowed-ds"},
            }

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.require_dataset_access(
            {"role": "user", "user_id": "1"},
            db=None,
            dataset_ids=["denied-ds"],
        )

    assert exc.value.status_code == 403
    assert "denied-ds" in exc.value.detail


@pytest.mark.asyncio
async def test_require_dataset_access_allows_admin(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": True,
                "accessible_ids": None,
                "writable_ids": None,
            }

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    await ragflow.require_dataset_access(
        {"role": "admin", "user_id": "1"},
        db=None,
        dataset_ids=["any-ds"],
    )


@pytest.mark.asyncio
async def test_require_element_permission_allows_non_admin_with_permission(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def check_permission(self, user_id, resource_type, resource_id):
            return (
                user_id == 2
                and resource_type == "element"
                and resource_id == "element:knowledge:create"
            )

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    await ragflow.require_element_permission(
        {"role": "user", "user_id": "2"},
        db=None,
        permission_id="element:knowledge:create",
    )


@pytest.mark.asyncio
async def test_require_element_permission_rejects_non_admin_without_permission(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def check_permission(self, user_id, resource_type, resource_id):
            return False

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.require_element_permission(
            {"role": "user", "user_id": "2"},
            db=None,
            permission_id="element:knowledge:create",
        )

    assert exc.value.status_code == 403
    assert "element:knowledge:create" in exc.value.detail


@pytest.mark.asyncio
async def test_get_ragflow_config_summary_does_not_expose_api_key(monkeypatch):
    async def fake_get(key, *args, **kwargs):
        return {
            "knowledge_ragflow_api_url": "http://ragflow.example/",
            "knowledge_ragflow_api_key": "secret-api-key",
            "metadata_provider": "ragflow",
        }.get(key, kwargs.get("default"))

    monkeypatch.setattr(ragflow.ConfigService, "get", fake_get)

    result = await ragflow.get_ragflow_config_summary()

    assert result["code"] == 0
    assert result["data"] == {
        "api_url": "http://ragflow.example/",
        "api_key_configured": True,
        "configured": True,
        "knowledge_base_enabled": True,
        "metadata_provider": "ragflow",
    }
    assert "secret-api-key" not in str(result)


@pytest.mark.asyncio
async def test_list_datasets_rejects_override_for_non_admin(monkeypatch):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": False,
                "accessible_ids": {"ds-1"},
                "writable_ids": set(),
            }

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RagFlowClient should not be created for rejected override")

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_ragflow_datasets(
            override_url="http://127.0.0.1:8080",
            override_key="secret",
            user={"role": "user", "user_id": "2", "user_name": "tester"},
            db=None,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_dataset_permissions_requires_write_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_write_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_dataset_write_access)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_dataset_permissions(
            dataset_id="denied-ds",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]


@pytest.mark.asyncio
async def test_dataset_portal_recommendations_requires_dataset_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    class FakeRagFlowClient:
        async def list_documents(self, *args, **kwargs):
            calls["client_called"] = True
            return []

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_dataset_portal_recommendations(
            dataset_id="denied-ds",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]
    assert "client_called" not in calls


@pytest.mark.asyncio
async def test_delete_dataset_invalidates_permission_caches_for_grantees(monkeypatch):
    calls = {}

    class FakeDB:
        async def execute(self, stmt):
            calls["delete_executed"] = True

    class FakePermissionService:
        def __init__(self, db):
            pass

        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = sorted(user_ids)

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def list_datasets(self, page=1, page_size=500):
            return [{"id": "ds-1"}]

        async def delete_datasets(self, ids):
            calls["deleted_ids"] = ids

    class FakeMetadataService:
        def __init__(self, db):
            pass

        async def mark_deleted(self, ids, user_name=None):
            calls["marked_deleted"] = ids

    async def fake_require_element_permission(user, db, permission_id):
        calls["permission_id"] = permission_id

    async def fake_require_dataset_write_access(user, db, dataset_ids):
        calls["write_checked"] = dataset_ids

    async def fake_collect_affected_users(db, dataset_ids):
        calls["collected_dataset_ids"] = dataset_ids
        return {2, 3}

    async def fake_record_knowledge_audit(*args, **kwargs):
        calls["audit_status"] = kwargs.get("status_code")

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_require_element_permission)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_dataset_write_access)
    monkeypatch.setattr(ragflow, "_collect_affected_dataset_permission_user_ids", fake_collect_affected_users, raising=False)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record_knowledge_audit)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="DELETE",
        client=SimpleNamespace(host="127.0.0.1"),
    )

    result = await ragflow.delete_ragflow_datasets(
        payload=ragflow.DeleteDatasetsRequest(ids=["ds-1"]),
        request=request,
        user={"user_name": "admin", "role": "admin", "user_id": "1"},
        db=FakeDB(),
    )

    assert result["code"] == 0
    assert calls["collected_dataset_ids"] == ["ds-1"]
    assert calls["invalidated_user_ids"] == [2, 3]
    assert calls["deleted_ids"] == ["ds-1"]


@pytest.mark.asyncio
async def test_document_file_requires_dataset_access(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids
        raise ragflow.HTTPException(status_code=403, detail="blocked")

    class FakeRagFlowClient:
        async def download_document(self, *args, **kwargs):
            calls["client_called"] = True
            return b"pdf", "test.pdf", "application/pdf"

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_ragflow_dataset_document_file(
            dataset_id="denied-ds",
            document_id="doc-1",
            user={"role": "user", "user_id": "2"},
            db=None,
        )

    assert exc.value.status_code == 403
    assert calls["dataset_ids"] == ["denied-ds"]
    assert "client_called" not in calls


@pytest.mark.asyncio
async def test_document_file_passes_dataset_id_to_ragflow_client(monkeypatch):
    calls = {}

    async def fake_require_dataset_access(user, db, dataset_ids):
        calls["dataset_ids"] = dataset_ids

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def download_document(self, document_id, *, dataset_id=None):
            calls["document_id"] = document_id
            calls["dataset_id"] = dataset_id
            return b"pdf", "test.pdf", "application/pdf"

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_dataset_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)

    response = await ragflow.get_ragflow_dataset_document_file(
        dataset_id="ds-allowed",
        document_id="doc-1",
        user={"role": "user", "user_id": "2"},
        db=None,
    )

    assert calls["dataset_ids"] == ["ds-allowed"]
    assert calls["document_id"] == "doc-1"
    assert calls["dataset_id"] == "ds-allowed"
    assert response.body == b"pdf"

    calls = {}

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def list_datasets(self, page=1, page_size=500):
            calls["page_size"] = page_size
            return [{"id": "ds-1", "name": "知识库", "description": "描述"}]

    class FakeMetadataService:
        def __init__(self, db):
            pass

        async def sync_from_ragflow_datasets(self, datasets, user_name=None):
            calls["datasets"] = datasets
            calls["user_name"] = user_name
            return {"created": 1, "updated": 0, "skipped": 0}

    async def fake_require_element_permission(user, db, permission_id):
        calls["permission_id"] = permission_id

    async def fake_record_knowledge_audit(*args, **kwargs):
        calls["audit_payload"] = kwargs.get("payload")
        calls["audit_status"] = kwargs.get("status_code")

    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetadataService)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_require_element_permission)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record_knowledge_audit)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="POST",
        client=SimpleNamespace(host="127.0.0.1"),
    )

    result = await ragflow.sync_ragflow_datasets(
        request=request,
        user={"user_name": "tester", "role": "user", "user_id": "2"},
        db=None,
    )

    assert result["code"] == 0
    assert result["data"] == {"created": 1, "updated": 0, "skipped": 0}
    assert calls["page_size"] == 500
    assert calls["datasets"][0]["id"] == "ds-1"
    assert calls["user_name"] == "tester"
    assert calls["permission_id"] == "element:knowledge:edit"
    assert calls["audit_status"] == 200
    assert calls["audit_payload"]["sync_result"]["created"] == 1


@pytest.mark.asyncio
async def test_record_knowledge_audit_masks_api_key(monkeypatch):
    captured = {}

    async def fake_log_request_data(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ragflow.AuditService, "log_request_data", fake_log_request_data)

    request = SimpleNamespace(
        state=SimpleNamespace(trace_id="trace-1"),
        method="POST",
        client=SimpleNamespace(host="127.0.0.1"),
    )
    await ragflow.record_knowledge_audit(
        request=request,
        user={"user_name": "tester"},
        action="retrieval-test",
        status_code=200,
        payload={"dataset_ids": ["ds-1"], "api_key": "secret"},
    )

    assert captured["trace_id"] == "trace-1"
    assert captured["user_name"] == "tester"
    assert captured["endpoint"] == "/api/portal/ragflow/retrieval-test"
    request_params = json.loads(captured["request_params"])
    assert request_params == {"dataset_ids": ["ds-1"]}


@pytest.mark.asyncio
async def test_add_dataset_permissions_success(monkeypatch):
    calls = {}
    
    async def fake_require_write(user, db, ids):
        calls["require_write_ids"] = ids
        
    class FakePermissionService:
        def __init__(self, db):
            pass
        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = user_ids

    class FakeSession:
        def __init__(self):
            self.added = []
            
        def add(self, model):
            self.added.append(model)
            
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return None
            return FakeResult()
            
        async def flush(self):
            calls["flushed"] = True

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_write)
    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    payload = ragflow.AddDatasetPermissionsRequest(target_type="user", target_ids=[123])
    db_session = FakeSession()

    audit = {}

    async def fake_audit(request, user, action, status_code, payload_, error_message=None, started_at=None):
        audit.update(action=action, status_code=status_code, payload=payload_)

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)

    res = await ragflow.add_dataset_permissions(
        dataset_id="ds-1",
        payload=payload,
        request=_fake_request(),
        db=db_session,
        user={"user_id": 1, "role": "admin"}
    )
    
    assert res["code"] == 0
    assert calls["require_write_ids"] == ["ds-1"]
    assert calls["flushed"] is True
    assert calls["invalidated_user_ids"] == {123}
    # 授权是敏感操作，必须留痕
    assert audit["action"] == "datasets:grant"
    assert audit["payload"]["dataset_id"] == "ds-1"
    assert audit["payload"]["target_type"] == "user"
    assert audit["payload"]["target_ids"] == [123]
    assert len(db_session.added) == 1
    assert db_session.added[0].user_id == 123
    assert db_session.added[0].resource_type == "dataset"
    assert db_session.added[0].resource_id == "ds-1"


@pytest.mark.asyncio
async def test_delete_dataset_permission_success(monkeypatch):
    calls = {}
    
    async def fake_require_write(user, db, ids):
        calls["require_write_ids"] = ids
        
    class FakePermissionService:
        def __init__(self, db):
            pass
        async def invalidate_cached_permissions_for_users(self, user_ids):
            calls["invalidated_user_ids"] = user_ids

    class FakeSession:
        def __init__(self):
            self.executed = []
            
        async def execute(self, stmt):
            self.executed.append(stmt)
            class FakeResult:
                def scalar_one_or_none(self):
                    # 元数据缺失 → visibility 按 public 兜底，不拦截授权
                    return None

                def scalars(self):
                    class FakeScalars:
                        def all(self):
                            return []
                    return FakeScalars()
            return FakeResult()
            
        async def flush(self):
            calls["flushed"] = True

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_write)
    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)

    audit = {}

    async def fake_audit(request, user, action, status_code, payload_, error_message=None, started_at=None):
        audit.update(action=action, status_code=status_code, payload=payload_)

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)

    payload = ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=123)
    db_session = FakeSession()

    res = await ragflow.delete_dataset_permission(
        dataset_id="ds-1",
        payload=payload,
        request=_fake_request("DELETE"),
        db=db_session,
        user={"user_id": 1, "role": "admin"}
    )
    
    assert res["code"] == 0
    assert calls["require_write_ids"] == ["ds-1"]
    assert calls["flushed"] is True
    assert calls["invalidated_user_ids"] == {123}
    # 删除侧刻意不查可见范围（要让私有库的历史授权能清理），只发一条 DELETE
    assert len(db_session.executed) == 1
    # 撤销授权同样要留痕，action 与新增区分开
    assert audit["action"] == "datasets:revoke"
    assert audit["payload"]["dataset_id"] == "ds-1"
    assert audit["payload"]["target_id"] == 123


@pytest.mark.asyncio
async def test_dataset_permission_endpoints_reject_private_dataset(monkeypatch):
    """个人私有知识库不允许**新增**授权；但已有授权必须能删除。

    private 的语义是「仅创建人」，若允许写授权记录，页面会出现"加了人却看不到"
    的假操作，所以新增侧拦下并提示先改可见范围；而清理侧的放行是为了不留下
    "能看见但删不掉"的脏记录。
    """

    async def fake_require_write(user, db, ids):
        return None

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushed = False

        def add(self, model):
            self.added.append(model)

        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return "private"

            return FakeResult()

        async def flush(self):
            self.flushed = True

    monkeypatch.setattr(ragflow, "require_dataset_write_access", fake_require_write)

    async def fake_audit(request, user, action, status_code, payload_, error_message=None, started_at=None):
        return None

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)

    add_session = FakeSession()
    with pytest.raises(ragflow.HTTPException) as add_exc:
        await ragflow.add_dataset_permissions(
            dataset_id="ds-private",
            payload=ragflow.AddDatasetPermissionsRequest(target_type="user", target_ids=[123]),
            request=_fake_request(),
            db=add_session,
            user={"user_id": 1, "role": "admin"},
        )
    assert add_exc.value.status_code == 400
    assert "个人私有" in add_exc.value.detail
    assert add_session.added == []

    # 删除侧必须放行：改成私有之前留下的历史授权要能逐条清理，
    # 否则会出现「看得见这条授权、点删除却 400」的死角
    delete_session = FakeSession()
    res = await ragflow.delete_dataset_permission(
        dataset_id="ds-private",
        payload=ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=123),
        request=_fake_request("DELETE"),
        db=delete_session,
        user={"user_id": 1, "role": "admin"},
    )
    assert res["code"] == 0
    assert delete_session.flushed is True


@pytest.mark.asyncio
async def test_get_dataset_portal_recommendations_with_custom_questions(monkeypatch):
    class FakeSession:
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return SimpleNamespace(
                        extra_config={
                            "custom_questions": [
                                {"label": "问题1", "query": "完整提问1"},
                                {"label": "问题2", "query": "完整提问2"},
                                {"label": "问题3", "query": "完整提问3"}
                            ]
                        }
                    )
            return FakeResult()

    async def fake_require_access(user, db, ids):
        pass

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass
        async def list_documents(self, dataset_id, page_size):
            return [{"id": "doc-1", "name": "测试文档.pdf"}]

    class FakeLLMClient:
        async def generate_text(self, messages):
            return '["生成提问1", "生成提问2", "生成提问3"]'

    async def fake_get_llm(streaming, temperature):
        class FakeLLM:
            pass
        return FakeLLM()

    def fake_chat_client(llm):
        return FakeLLMClient()

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr("app.core.llm.client.get_llm_async", fake_get_llm)
    monkeypatch.setattr("app.services.ai.runtime.agentscope.chat.chat_client_from_handle", fake_chat_client)

    res = await ragflow.get_dataset_portal_recommendations(
        dataset_id="ds-1",
        user={"user_id": 1, "role": "user"},
        db=FakeSession()
    )

    assert res["code"] == 0
    data = res["data"]
    custom_qs = data["custom_questions"]
    qs = data["questions"]
    assert len(custom_qs) == 3
    assert custom_qs[0]["label"] == "问题1"
    assert custom_qs[0]["query"] == "完整提问1"
    assert len(qs) == 3
    assert qs[0]["label"] == "生成提问1"


@pytest.mark.asyncio
async def test_get_dataset_portal_recommendations_hybrid(monkeypatch):
    class FakeSession:
        async def execute(self, stmt):
            class FakeResult:
                def scalar_one_or_none(self):
                    return SimpleNamespace(
                        extra_config={
                            "custom_questions": [
                                {"label": "固定问题", "query": "固定提问内容"}
                            ]
                        }
                    )
            return FakeResult()

    async def fake_require_access(user, db, ids):
        pass

    class FakeRagFlowClient:
        def __init__(self, *args, **kwargs):
            pass
        async def list_documents(self, dataset_id, page_size):
            return [{"id": "doc-1", "name": "测试文档.pdf"}]

    class FakeLLMClient:
        async def generate_text(self, messages):
            return '["生成提问1", "生成提问2", "生成提问3"]'

    async def fake_get_llm(streaming, temperature):
        class FakeLLM:
            pass
        return FakeLLM()

    def fake_chat_client(llm):
        return FakeLLMClient()

    monkeypatch.setattr(ragflow, "require_dataset_access", fake_require_access)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeRagFlowClient)
    monkeypatch.setattr("app.core.llm.client.get_llm_async", fake_get_llm)
    monkeypatch.setattr("app.services.ai.runtime.agentscope.chat.chat_client_from_handle", fake_chat_client)

    res = await ragflow.get_dataset_portal_recommendations(
        dataset_id="ds-1",
        user={"user_id": 1, "role": "user"},
        db=FakeSession()
    )

    assert res["code"] == 0
    data = res["data"]
    custom_qs = data["custom_questions"]
    qs = data["questions"]
    assert len(custom_qs) == 1
    assert custom_qs[0]["label"] == "固定问题"
    assert custom_qs[0]["query"] == "固定提问内容"
    assert len(qs) == 3
    assert qs[0]["label"] == "生成提问1"
    assert qs[0]["query"] == "生成提问1"


def _patch_upstream_status(monkeypatch, status_code: int):
    """让 /agents 代理拿到指定状态码的上游响应。"""

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None, params=None):
            return SimpleNamespace(status_code=status_code, text="upstream error body")

    monkeypatch.setattr(ragflow.httpx, "AsyncClient", FakeAsyncClient)


@pytest.mark.parametrize("upstream_status", [401, 403])
@pytest.mark.asyncio
async def test_list_ragflow_agents_maps_upstream_auth_failure_to_502(monkeypatch, upstream_status):
    """上游鉴权失败不得原样透传 401。

    门户前端把任何 401 都当作「登录态失效」而直接跳登录页，管理员只是打开
    RAGFlow 智能体列表就会被莫名登出。
    """
    _patch_upstream_status(monkeypatch, upstream_status)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_ragflow_agents(
            override_url="http://ragflow.example", override_key="secret-key"
        )

    assert exc.value.status_code == 502
    assert "RAGFlow" in exc.value.detail


@pytest.mark.asyncio
async def test_list_ragflow_agents_keeps_non_auth_upstream_status(monkeypatch):
    """非鉴权类上游错误保持原状态码，不改变既有排查信息。"""
    _patch_upstream_status(monkeypatch, 404)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_ragflow_agents(
            override_url="http://ragflow.example", override_key="secret-key"
        )

    assert exc.value.status_code == 404


def test_permission_flags_give_admin_full_control():
    """管理员在列表里必须拿到完整的读写标志。

    后端判定返回的是 ``accessible_ids=None``（不受限），前端只能靠这几个
    ``can_*`` 标志决定按钮是否可点；这里漏掉 admin 分支会让「管理员可以操作
    别人的私有库」在界面上变成灰色按钮。
    """
    from app.api.portal.endpoints.ragflow import _apply_knowledge_permission_flags

    items = [
        {"ragflow_dataset_id": "ds-a", "id": "ds-a"},
        {"ragflow_dataset_id": "ds-b", "id": "ds-b"},
    ]
    _apply_knowledge_permission_flags(
        items,
        {"is_admin": True, "accessible_ids": None, "writable_ids": None},
    )
    for item in items:
        assert item["can_write"] is True
        assert item["can_view_chunks"] is True
        assert item["is_read_only"] is False


def test_permission_flags_keep_non_admin_write_narrow():
    """非管理员的写权限只给创建人：被授权者与部门成员都是只读。"""
    from app.api.portal.endpoints.ragflow import _apply_knowledge_permission_flags

    items = [{"ragflow_dataset_id": "ds-a", "id": "ds-a"}]
    _apply_knowledge_permission_flags(
        items,
        {"is_admin": False, "accessible_ids": {"ds-a"}, "writable_ids": set()},
    )
    assert items[0]["can_write"] is False
    assert items[0]["can_view_chunks"] is False
    assert items[0]["is_read_only"] is True


def test_permission_endpoints_are_audited():
    """授权增删必须留痕。

    同一个数据集授权有两个入口：知识库页（增量增删）与用户/角色页（整份保存）。
    没有审计时，一旦出现「授权莫名消失」，无法判断是谁在什么时候改的。
    """
    import inspect

    post_src = inspect.getsource(ragflow.add_dataset_permissions)
    delete_src = inspect.getsource(ragflow.delete_dataset_permission)

    assert "record_knowledge_audit" in post_src
    assert "datasets:grant" in post_src
    assert "target_ids" in post_src
    assert "record_knowledge_audit" in delete_src
    assert "datasets:revoke" in delete_src
    assert "target_id" in delete_src


# ---------------------------------------------------------------- 两级写权限

def _fake_access(monkeypatch, **overrides):
    class FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": False,
                "accessible_ids": {"ds-team"},
                "writable_ids": set(),
                "content_writable_ids": {"ds-team"},
                **overrides,
            }

    monkeypatch.setattr(ragflow, "PermissionService", FakePermissionService)


@pytest.mark.asyncio
async def test_team_member_can_write_content_but_not_manage(monkeypatch):
    """部门成员：能传文档（内容级），不能改元数据/授权（管理级）。"""
    _fake_access(monkeypatch)
    user = {"user_id": 1, "user_name": "chenxiaolong"}

    # 内容级放行，不抛异常
    await ragflow.require_dataset_content_write_access(user, None, ["ds-team"])

    # 管理级拒绝
    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.require_dataset_write_access(user, None, ["ds-team"])
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_read_only_grantee_is_rejected_by_both_levels(monkeypatch):
    """被显式授权的非创建人在两级都不通过。"""
    _fake_access(monkeypatch, content_writable_ids=set())
    user = {"user_id": 1, "user_name": "chenxiaolong"}

    for check in (
        ragflow.require_dataset_content_write_access,
        ragflow.require_dataset_write_access,
    ):
        with pytest.raises(ragflow.HTTPException) as exc:
            await check(user, None, ["ds-team"])
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_passes_every_level(monkeypatch):
    _fake_access(monkeypatch, is_admin=True)
    user = {"user_id": 1, "user_name": "admin", "role": "admin"}

    await ragflow.require_dataset_content_write_access(user, None, ["ds-team"])
    await ragflow.require_dataset_write_access(user, None, ["ds-team"])


def test_permission_flags_expose_both_levels():
    """列表接口必须同时给出 can_write（内容级）与 can_manage（管理级）。"""
    items = [{"ragflow_dataset_id": "ds-team"}]
    ragflow._apply_knowledge_permission_flags(
        items,
        {
            "is_admin": False,
            "accessible_ids": {"ds-team"},
            "writable_ids": set(),
            "content_writable_ids": {"ds-team"},
        },
    )
    assert items[0]["can_write"] is True       # 能传文档
    assert items[0]["can_manage"] is False     # 不能改设置
    assert items[0]["can_view_chunks"] is True
    assert items[0]["is_read_only"] is False


def test_permission_flags_creator_gets_management():
    items = [{"ragflow_dataset_id": "ds-mine"}]
    ragflow._apply_knowledge_permission_flags(
        items,
        {
            "is_admin": False,
            "accessible_ids": {"ds-mine"},
            "writable_ids": {"ds-mine"},
            "content_writable_ids": {"ds-mine"},
        },
    )
    assert items[0]["can_manage"] is True
