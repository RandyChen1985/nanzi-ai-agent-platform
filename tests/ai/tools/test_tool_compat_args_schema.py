"""工具参数 schema 的注解解析回归。

`tool_compat._schema_from_signature` 原来直接把 `inspect.signature` 拿到的注解塞给
`create_model`。在带 ``from __future__ import annotations`` 的模块里，注解是**字符串**，
pydantic 只在 tool_compat 自己的命名空间里找 ``Optional``，找不到就留一个未定义的前向
引用——直到给模型生成工具清单、调用 ``model_json_schema()`` 时才抛
``PydanticUserError: 'xxxArgs' is not fully defined; you should define Optional``。

这个失败点离源码极远，表现是「工具莫名不可用」。本文件锁住三点：
1. 字符串注解（future import）能按**函数自己模块**的命名空间解析；
2. 纯内置类型的既有工具行为不变；
3. 真正解析不出的前向引用退回 ``str``，不让整条工具注册链崩掉。
"""
from __future__ import annotations

import json

import pytest

from app.services.ai.tools import tool_compat

pytestmark = pytest.mark.no_infrastructure


def test_optional_annotations_resolve_in_future_import_module():
    """resource_catalog_tools 有 future import，工具参数却能正确生成 schema。"""
    from app.services.ai.tools.resource_catalog_tools import list_accessible_datasets

    schema = list_accessible_datasets.args_schema.model_json_schema()

    assert schema["properties"]["keyword"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["dataset_id"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert not schema.get("required")


def test_plain_builtin_annotations_keep_working():
    """既有工具（只有 str/int 注解）不能被这次解析改动影响。"""
    from app.services.ai.tools.example_search_tool import search_qa_examples

    schema = search_qa_examples.args_schema.model_json_schema()

    assert schema["properties"]["query"]["type"] == "string"
    assert schema["properties"]["top_k"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert schema["required"] == ["query"]


def test_unresolvable_annotation_falls_back_to_str():
    """真正的前向引用（模块里没有这个类型）降级成 str，而不是让 schema 不可生成。"""

    def _tool(value: "NoSuchTypeAnywhere") -> str:  # noqa: F821 - 故意引用不存在的类型
        return str(value)

    model = tool_compat._schema_from_signature(_tool)
    schema = model.model_json_schema()

    assert schema["properties"]["value"]["type"] == "string"


@pytest.mark.asyncio
async def test_function_tool_ainvoke_filters_unknown_arguments():
    """模型偶尔会多传参数：多余键要被丢掉，而不是抛 TypeError。"""
    from app.services.ai.tools.tool_compat import tool

    @tool
    async def _echo(query: str, top_k: int = 3) -> str:
        return json.dumps({"query": query, "top_k": top_k})

    result = await _echo.ainvoke({"query": "x", "unexpected": 1})

    assert json.loads(result) == {"query": "x", "top_k": 3}
