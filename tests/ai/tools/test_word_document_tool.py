import pytest

from app.core.context import AgentContext, set_agent_context


pytestmark = pytest.mark.no_infrastructure


class _FakeArtifactSession:
    """最小 fake async session：使 register_artifact 在无 DB 时也能记录产物。"""

    def __init__(self):
        self._added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self._added.append(obj)

    async def commit(self):
        # AiArtifact.id 已由 uuid4().hex 在构造时生成，无需数据库回填
        pass


@pytest.fixture
def word_context(tmp_path, monkeypatch):
    from docx import Document
    from app.services.ai.tools import document_paths, generated_file_service

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "letter.docx"
    document = Document()
    document.add_paragraph("Hello {{name}}")
    document.add_paragraph("Second paragraph")
    table1 = document.add_table(rows=3, cols=2)
    table1.rows[0].cells[0].text = "接口"
    table1.rows[0].cells[1].text = "路径"
    table1.rows[1].cells[0].text = "创建令牌"
    table1.rows[1].cells[1].text = "/api/v1/tokens"
    table1.rows[2].cells[0].text = "撤销令牌"
    table1.rows[2].cells[1].text = "/api/v1/tokens/{id}"
    table2 = document.add_table(rows=2, cols=2)
    table2.rows[0].cells[0].text = "配置项"
    table2.rows[0].cells[1].text = "默认值"
    table2.rows[1].cells[0].text = "timeout"
    table2.rows[1].cells[1].text = "30s"
    document.save(source)
    monkeypatch.setattr(document_paths, "get_data_base_dir", lambda: str(tmp_path))
    async def workspace_root():
        return str(tmp_path / "agent_workspaces")
    monkeypatch.setattr(document_paths, "resolve_workspace_root", workspace_root)
    monkeypatch.setattr(generated_file_service, "generated_files_root", lambda: tmp_path / "generated")
    # register_artifact 的依赖：workspace 根与 DB 会话均改为测试内无 DB 版本
    async def artifact_workspace_root():
        return tmp_path / "agent_workspaces"
    monkeypatch.setattr(generated_file_service, "_workspace_root", artifact_workspace_root)
    monkeypatch.setattr(
        generated_file_service, "AsyncSessionLocal", lambda: _FakeArtifactSession()
    )
    set_agent_context(AgentContext(
        agent_id="agent", agent_name="Agent", user_id=1, conversation_id="conv",
        authorized_attachment_paths=[str(source)],
    ))
    return source


@pytest.mark.asyncio
async def test_word_read_content_is_paginated(word_context):
    from app.services.ai.tools.word_document_tool import word_document_read

    result = await word_document_read.ainvoke({
        "action": "read_content", "path": str(word_context), "start": 1, "limit": 1,
    })

    assert result["data"]["paragraphs"] == ["Second paragraph"]
    assert result["data"]["start"] == 1
    assert result["data"]["limit"] == 1
    assert result["data"]["total_paragraphs"] == 2
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_word_inspect_includes_table_previews(word_context):
    from app.services.ai.tools.word_document_tool import word_document_read

    result = await word_document_read.ainvoke({
        "action": "inspect", "path": str(word_context),
    })

    assert result["data"]["paragraph_count"] == 2
    assert result["data"]["table_count"] == 2
    assert result["data"]["table_previews"] == [
        {"index": 0, "row_count": 3, "col_count": 2, "header": ["接口", "路径"]},
        {"index": 1, "row_count": 2, "col_count": 2, "header": ["配置项", "默认值"]},
    ]


@pytest.mark.asyncio
async def test_word_read_tables_supports_table_and_row_pagination(word_context):
    from app.services.ai.tools.word_document_tool import word_document_read

    first_table = await word_document_read.ainvoke({
        "action": "read_tables",
        "path": str(word_context),
        "start": 0,
        "limit": 1,
        "row_start": 0,
        "row_limit": 2,
    })

    assert first_table["status"] == "ok"
    assert first_table["truncated"] is True
    assert first_table["data"]["total_tables"] == 2
    assert first_table["data"]["tables"] == [
        {
            "index": 0,
            "row_count": 3,
            "col_count": 2,
            "row_start": 0,
            "rows": [["接口", "路径"], ["创建令牌", "/api/v1/tokens"]],
            "truncated": True,
        }
    ]

    second_table = await word_document_read.ainvoke({
        "action": "read_tables",
        "path": str(word_context),
        "start": 1,
        "limit": 10,
    })

    assert second_table["truncated"] is False
    assert second_table["data"]["tables"] == [
        {
            "index": 1,
            "row_count": 2,
            "col_count": 2,
            "row_start": 0,
            "rows": [["配置项", "默认值"], ["timeout", "30s"]],
            "truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_word_read_rejects_unknown_action_with_supported_choices(word_context):
    from app.services.ai.tools.document_paths import DocumentPathError
    from app.services.ai.tools.word_document_tool import word_document_read

    with pytest.raises(DocumentPathError, match="inspect、read_content 或 read_tables"):
        await word_document_read.ainvoke({
            "action": "read_all", "path": str(word_context),
        })


@pytest.mark.asyncio
async def test_word_read_tolerates_none_and_string_pagination_args(word_context):
    from app.services.ai.tools.word_document_tool import word_document_read

    content_result = await word_document_read.ainvoke({
        "action": "read_content",
        "path": str(word_context),
        "start": None,
        "limit": None,
        "row_start": None,
        "row_limit": None,
    })
    assert content_result["data"]["start"] == 0
    assert content_result["data"]["limit"] == 20
    assert len(content_result["data"]["paragraphs"]) == 2

    table_result = await word_document_read.ainvoke({
        "action": "read_tables",
        "path": str(word_context),
        "start": "0",
        "limit": None,
        "row_start": None,
        "row_limit": "2",
    })
    assert table_result["data"]["start"] == 0
    assert table_result["data"]["limit"] == 10
    assert len(table_result["data"]["tables"]) == 2
    assert len(table_result["data"]["tables"][0]["rows"]) == 2


def test_extract_row_cells_falls_back_on_malformed_vmerge_and_truncates_preview():
    from app.services.ai.tools.word_document_tool import _extract_row_cells

    class _FakeTc:
        def __init__(self, text: str):
            self._text = text

        def itertext(self):
            return [self._text]

    class _FakeTr:
        def __init__(self, texts: list[str]):
            self.tc_lst = [_FakeTc(t) for t in texts]

    class _BrokenRow:
        def __init__(self, texts: list[str]):
            self._tr = _FakeTr(texts)

        @property
        def cells(self):
            raise AttributeError("'NoneType' object has no attribute 'vMerge'")

    row = _BrokenRow(["正常表头", "超长" * 60])
    preview = _extract_row_cells(row, max_cols=2, max_cell_chars=10)
    assert preview[0] == "正常表头"
    assert preview[1].endswith("…") and len(preview[1]) == 11


def test_word_tools_schema_exposes_action_enum_and_descriptions():
    from app.services.ai.tools.word_document_tool import word_document_read, word_document_write

    read_schema = word_document_read.args_schema.model_json_schema()
    assert read_schema["properties"]["action"]["enum"] == ["inspect", "read_content", "read_tables"]
    assert read_schema["properties"]["action"].get("description")
    assert read_schema["properties"]["limit"].get("description")
    assert read_schema["required"] == ["action", "path"]

    write_schema = word_document_write.args_schema.model_json_schema()
    assert write_schema["properties"]["action"]["enum"] == [
        "create", "replace_text", "append_paragraphs", "append_table"
    ]
    assert write_schema["properties"]["action"].get("description")
    assert write_schema["required"] == ["action", "output_filename"]


@pytest.mark.asyncio
async def test_word_replace_text_writes_a_copy(word_context):
    from app.services.ai.tools.word_document_tool import word_document_write

    result = await word_document_write.ainvoke({
        "action": "replace_text", "path": str(word_context),
        "replacements": [{"find": "{{name}}", "replace": "Alice"}],
        "output_filename": "letter_alice.docx",
    })

    assert result["changes"]["replacements"] == 1
    assert result["artifact"]["download_url"]
