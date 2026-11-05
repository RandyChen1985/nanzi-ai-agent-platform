"""知识库管理工具（建库 / 上传文档）的行为测试。

不依赖真实 MySQL / RAGFlow：``AsyncSessionLocal``、``RagFlowClient``、
``PermissionService``、``KnowledgeQuotaService`` 全部换成假实现，
请求逻辑本身仍然真跑（包括 commit、审计、错误码映射）。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.tools import knowledge_admin_tools as tools
from app.services.ai.tools.document_paths import DocumentPathError

pytestmark = pytest.mark.no_infrastructure


class _FakeSessionCtx:
    def __init__(self) -> None:
        self.session = SimpleNamespace(committed=False)

        async def _commit():
            self.session.committed = True

        self.session.commit = _commit

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc_info):
        return False


class _FakeMetadataService:
    """内存版元数据服务：只实现工具用到的两个方法。"""

    def __init__(self, session=None):
        pass

    by_name = None
    records: dict = {}
    upserts: list = []

    async def find_active_by_name(self, name):
        return self.by_name

    async def get_by_dataset_id(self, dataset_id):
        return self.records.get(dataset_id)

    async def upsert_metadata(self, **kwargs):
        _FakeMetadataService.upserts.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)


class _FakePermissionService:
    def __init__(self, session=None):
        pass

    grant_element: bool = True
    access: dict = {}

    async def check_permission(self, user_id, scope, permission_id):
        return _FakePermissionService.grant_element

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return _FakePermissionService.access


class _FakeQuota:
    create_error = None
    upload_error = None

    @classmethod
    async def ensure_can_create_dataset(cls, db, user_id):
        if cls.create_error:
            raise cls.create_error

    @classmethod
    async def ensure_can_upload(cls, client, dataset_id, incoming=1):
        if cls.upload_error:
            raise cls.upload_error


class _FakeRagFlow:
    created: dict = {}
    documents: list = []
    parse_error = None
    upload_error = None
    parse_calls: list = []

    def __init__(self, **kwargs):
        pass

    async def create_dataset(self, name, description="", chunk_method="naive"):
        return _FakeRagFlow.created or {"id": "ds-new"}

    async def list_datasets(self, name=None):
        return []

    async def list_documents(self, dataset_id, name=None, page=1, page_size=100):
        return _FakeRagFlow.documents

    async def upload_document(self, dataset_id, file_name, blob, content_type="application/octet-stream"):
        if _FakeRagFlow.upload_error:
            raise _FakeRagFlow.upload_error
        return {"id": "doc-1", "name": file_name}

    async def parse_documents(self, dataset_id, document_ids):
        _FakeRagFlow.parse_calls.append((dataset_id, document_ids))
        if _FakeRagFlow.parse_error:
            raise _FakeRagFlow.parse_error


class _FakeAudit:
    entries: list = []

    @classmethod
    async def log(cls, **kwargs):
        cls.entries.append(kwargs)
        return True


@pytest.fixture()
def env(monkeypatch, tmp_path):
    _FakeMetadataService.by_name = None
    _FakeMetadataService.records = {}
    _FakeMetadataService.upserts = []
    _FakePermissionService.grant_element = True
    _FakePermissionService.access = {"is_admin": False, "content_writable_ids": {"ds-1"}}
    _FakeQuota.create_error = None
    _FakeQuota.upload_error = None
    _FakeRagFlow.created = {"id": "ds-new"}
    _FakeRagFlow.documents = []
    _FakeRagFlow.parse_error = None
    _FakeRagFlow.upload_error = None
    _FakeRagFlow.parse_calls = []
    _FakeAudit.entries = []

    ctx = SimpleNamespace(
        user_id=178,
        is_admin=False,
        conversation_id="c-1",
        authorized_attachment_paths=[],
        user_dimensions={"user_name": "alice"},
    )

    monkeypatch.setattr(tools, "get_current_agent_context", lambda: ctx)

    async def _enabled():
        return True

    monkeypatch.setattr(tools, "is_knowledge_base_enabled", _enabled)
    monkeypatch.setattr(tools, "AsyncSessionLocal", _FakeSessionCtx)
    monkeypatch.setattr(tools, "KnowledgeBaseMetadataService", _FakeMetadataService)
    monkeypatch.setattr(tools, "PermissionService", _FakePermissionService)
    monkeypatch.setattr(tools, "KnowledgeQuotaService", _FakeQuota)
    monkeypatch.setattr(tools, "RagFlowClient", _FakeRagFlow)
    monkeypatch.setattr(tools, "KnowledgeAuditService", _FakeAudit)
    monkeypatch.setattr(tools, "clear_dataset_list_cache", lambda: None)
    monkeypatch.setattr(tools, "resolve_context_user_name", lambda ctx: "alice")

    # 路径校验单独测（document_paths 自己已有用例），这里只关心拿到哪个文件
    doc = tmp_path / "manual.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")

    async def _resolve(path, **kwargs):
        if path == "forbidden.txt":
            raise DocumentPathError("文件不在当前会话允许访问的目录中")
        return doc

    monkeypatch.setattr(tools, "resolve_document_input_path", _resolve)

    return SimpleNamespace(ctx=ctx, doc=doc, tmp=tmp_path)


def _payload(raw: str) -> dict:
    """工具返回的是 JSON 字符串；@tool 把它包成 FunctionTool，要用 ainvoke 调。"""
    return json.loads(raw)


async def _create(**kwargs) -> str:
    return await tools.create_knowledge_base.ainvoke(kwargs)


async def _upload(**kwargs) -> str:
    return await tools.upload_document_to_knowledge_base.ainvoke(kwargs)


# ---------------- create_knowledge_base ----------------

@pytest.mark.anyio
async def test_create_requires_element_permission(env):
    _FakePermissionService.grant_element = False
    payload = _payload(await _create(name="制度汇编"))
    assert payload["ok"] is False
    assert payload["error"] == "forbidden"


@pytest.mark.anyio
async def test_create_rejects_duplicate_name(env):
    _FakeMetadataService.by_name = SimpleNamespace(id=1)
    payload = _payload(await _create(name="制度汇编"))
    assert payload["error"] == "name_taken"
    assert "已存在" in payload["message"]


@pytest.mark.anyio
async def test_create_rejects_blank_and_overlong_name(env):
    assert _payload(await _create(name="   "))["error"] == "invalid_name"
    assert _payload(await _create(name="x" * 65))["error"] == "invalid_name"


@pytest.mark.anyio
async def test_create_reports_quota_exceeded(env):
    from app.services.knowledge_quota_service import QuotaExceeded

    _FakeQuota.create_error = QuotaExceeded("你已拥有 10 个知识库，达到上限 10 个。")
    payload = _payload(await _create(name="制度汇编"))
    assert payload["error"] == "quota_exceeded"
    assert "上限" in payload["message"]


@pytest.mark.anyio
async def test_create_forces_private_visibility_and_owner(env):
    """工具签名里没有 visibility：模型无法让库变成 team / public。"""
    payload = _payload(await _create(name="制度汇编", description="供检索"))
    assert payload["ok"] is True
    assert payload["visibility"] == "private"
    assert payload["dataset_id"] == "ds-new"

    upsert = _FakeMetadataService.upserts[-1]
    assert upsert["visibility"] == "private"
    assert upsert["owner_user_id"] == 178
    assert upsert["name"] == "制度汇编"
    assert upsert["description"] == "供检索"
    # 提示要写清楚"本智能体不会自动检索它"，否则模型会承诺做不到的事
    assert "不会自动检索" in payload["note"]


@pytest.mark.anyio
async def test_create_aborts_when_upstream_id_ambiguous(env):
    """建库返回缺 id 且同名候选不唯一时必须中止，不能绑到别人的库上。"""
    _FakeRagFlow.created = {}

    class _TwoMatches(_FakeRagFlow):
        async def create_dataset(self, name, description="", chunk_method="naive"):
            # 建库"成功"却没回 id —— 触发按名字反查的兜底分支
            return {}

        async def list_datasets(self, name=None):
            return [{"id": "a"}, {"id": "b"}]

    tools.RagFlowClient = _TwoMatches  # type: ignore[assignment]
    try:
        payload = _payload(await _create(name="制度汇编"))
    finally:
        tools.RagFlowClient = _FakeRagFlow  # type: ignore[assignment]

    assert payload["error"] == "upstream_ambiguous"
    assert not _FakeMetadataService.upserts


# ---------------- upload_document_to_knowledge_base ----------------

@pytest.mark.anyio
async def test_upload_defaults_to_preview_without_side_effects(env):
    """默认只回预览：传错库撤不回来，所以"手滑直传"必须先经过确认。"""
    _FakeMetadataService.records = {
        "ds-1": SimpleNamespace(status="active", name="产品手册库")
    }
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf")
    )

    assert payload["ok"] is True
    assert payload["stage"] == "preview"
    assert payload["dataset_name"] == "产品手册库"
    assert payload["file_name"] == "manual.pdf"
    assert payload["will_parse"] is True
    assert "尚未上传" in payload["message"]
    # 预览阶段不能有任何副作用
    assert _FakeRagFlow.parse_calls == []
    assert _FakeAudit.entries == []
    assert _FakeMetadataService.upserts == []


@pytest.mark.anyio
async def test_upload_preview_still_reports_readonly(env):
    """预览不是"什么都过"：只读库在预览阶段就该被拒绝。"""
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active", name="只读库")}
    _FakePermissionService.access = {"is_admin": False, "content_writable_ids": set()}
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf")
    )
    assert payload["error"] == "forbidden_readonly"
    assert "只读库" in payload["message"]


@pytest.mark.anyio
async def test_upload_missing_dataset_id_returns_candidates(env, monkeypatch):
    """没给库 ID 时要能反问：带上用户可编辑的库，而不是干等用户报 ID。"""

    async def _candidates(db, ctx, *, user_id, user_name, limit=10):
        return [{"dataset_id": "ds-1", "name": "产品手册库"}]

    monkeypatch.setattr(tools, "_writable_dataset_candidates", _candidates)
    payload = _payload(await _upload(dataset_id="", file_path="data/uploads/manual.pdf"))
    assert payload["error"] == "invalid_argument"
    assert payload["candidates"] == [{"dataset_id": "ds-1", "name": "产品手册库"}]


@pytest.mark.anyio
async def test_upload_not_found_returns_candidates(env, monkeypatch):
    async def _candidates(db, ctx, *, user_id, user_name, limit=10):
        return [{"dataset_id": "ds-2", "name": "另一个库"}]

    monkeypatch.setattr(tools, "_writable_dataset_candidates", _candidates)
    payload = _payload(
        await _upload(dataset_id="ds-x", file_path="data/uploads/manual.pdf")
    )
    assert payload["error"] == "dataset_not_found"
    assert payload["candidates"][0]["dataset_id"] == "ds-2"


@pytest.mark.anyio
async def test_upload_rejects_readonly_dataset(env):
    _FakePermissionService.access = {"is_admin": False, "content_writable_ids": set()}
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf")
    )
    assert payload["error"] == "forbidden_readonly"
    assert "只有查看权限" in payload["message"]


@pytest.mark.anyio
async def test_upload_requires_existing_dataset(env):
    payload = _payload(
        await _upload(dataset_id="ds-x", file_path="data/uploads/manual.pdf")
    )
    assert payload["error"] == "dataset_not_found"


@pytest.mark.anyio
async def test_upload_rejects_file_outside_allowed_dirs(env):
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="forbidden.txt")
    )
    assert payload["error"] == "file_not_allowed"


@pytest.mark.anyio
async def test_upload_rejects_unsupported_type(env, monkeypatch, tmp_path):
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ")

    async def _resolve(path, **kwargs):
        return exe

    monkeypatch.setattr(tools, "resolve_document_input_path", _resolve)
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="payload.exe")
    )
    assert payload["error"] == "unsupported_type"


@pytest.mark.anyio
async def test_upload_skips_when_same_name_exists(env):
    """模型重试 / 用户说两遍都不能产生重复文档。"""
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    _FakeRagFlow.documents = [{"id": "doc-old", "name": "manual.pdf"}]
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf")
    )
    assert payload["error"] == "document_exists"
    assert "doc-old" in payload["message"]
    assert not _FakeRagFlow.parse_calls


@pytest.mark.anyio
async def test_upload_parses_by_default_and_records_audit(env):
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf", confirm=True)
    )
    assert payload["ok"] is True
    assert payload["parsed"] is True
    assert payload["document_id"] == "doc-1"
    assert _FakeRagFlow.parse_calls == [("ds-1", ["doc-1"])]

    audit = _FakeAudit.entries[-1]
    assert audit["action"] == "documents:upload"
    assert audit["detail"]["source"] == "agent_tool"
    assert audit["dataset_ids"] == ["ds-1"]


@pytest.mark.anyio
async def test_upload_keeps_success_when_parse_fails(env):
    """解析失败不算上传失败：文档已在库里，报错会让模型重传产生重复。"""
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    _FakeRagFlow.parse_error = RuntimeError("parser queue full")
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf", confirm=True)
    )
    assert payload["ok"] is True
    assert payload["parsed"] is False
    assert "parser queue full" in (payload["parse_error"] or "")
    assert payload["document_id"] == "doc-1"


@pytest.mark.anyio
async def test_upload_can_skip_parsing(env):
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    payload = _payload(
        await _upload(
            dataset_id="ds-1",
            file_path="data/uploads/manual.pdf",
            auto_parse=False,
            confirm=True,
        )
    )
    assert payload["ok"] is True
    assert payload["parsed"] is False
    assert not _FakeRagFlow.parse_calls


@pytest.mark.anyio
async def test_upload_admin_bypasses_content_write_check(env):
    _FakePermissionService.access = {"is_admin": True}
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}
    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="data/uploads/manual.pdf")
    )
    assert payload["ok"] is True


# ---------------- 登记一致性 ----------------

def test_upload_policy_matches_endpoint_constants():
    """页面接口与工具必须共用一套白名单，改一处忘另一处会在这里红。"""
    from app.api.portal.endpoints import ragflow
    from app.services.knowledge_upload_policy import ALLOWED_EXTENSIONS, DEFAULT_MAX_BYTES

    assert set(ALLOWED_EXTENSIONS) == set(ragflow.KNOWLEDGE_UPLOAD_ALLOWED_EXTENSIONS)
    assert DEFAULT_MAX_BYTES == ragflow.KNOWLEDGE_UPLOAD_MAX_BYTES


def test_tools_are_registered_documented_and_marked_as_write():
    """少登记任何一处都会让功能"存在但不可用"：勾不到、权限页空白、观测成 unknown。"""
    from app.services.ai.tool_policy import resolve_tool_metadata
    from app.services.ai.tools.forbidden_catalog import BUILTIN_TOOL_DESCRIPTIONS
    from app.services.ai.tools.registry import ToolRegistry

    for name in ("create_knowledge_base", "upload_document_to_knowledge_base"):
        assert name in ToolRegistry._registry
        assert BUILTIN_TOOL_DESCRIPTIONS.get(name)
        metadata = resolve_tool_metadata(name)
        assert metadata.side_effect == "write"
        assert metadata.confirmation == "ask"


@pytest.mark.anyio
async def test_upload_matches_attachment_by_filename(env, monkeypatch):
    """模型手里常只有文件名（session_status 只回 filenames），要能按名兜底命中。"""
    env.ctx.authorized_attachment_paths = [str(env.doc)]

    async def _strict(path, **kwargs):
        if str(path) == str(env.doc):
            return env.doc
        raise DocumentPathError("文件不存在或不可访问")

    monkeypatch.setattr(tools, "resolve_document_input_path", _strict)
    _FakeMetadataService.records = {"ds-1": SimpleNamespace(status="active")}

    payload = _payload(
        await _upload(dataset_id="ds-1", file_path="manual.pdf", confirm=True)
    )
    assert payload["ok"] is True
    assert payload["file_name"] == "manual.pdf"
    assert payload["parsed"] is True
