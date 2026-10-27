"""资源广场接口契约：路径、参数名、状态枚举与后端保持一致。

前端最怕的是「路径写对了但字段名对不上」——那不会报错，只会静默显示 undefined。
所以这里断言到字段级别。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "frontend/src/api/resourceSquare.ts"
TYPES = ROOT / "frontend/src/types/resourceSquare.ts"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_square_endpoints_match_backend():
    api = _read(API)
    # 路径统一由 BASE 拼出来，避免三处各写一遍前缀写错
    assert "const BASE = '/api/portal'" in api
    assert "/resource-square/knowledge" in api
    assert "/resource-square/datasets" in api
    assert "/access-requests" in api
    assert "/knowledge/${encodeURIComponent(datasetId)}" in api


def test_request_status_enum_is_closed_set():
    types = _read(TYPES)
    for value in ("pending", "approved", "rejected", "cancelled"):
        assert f"'{value}'" in types
    assert "has_access" in types
    assert "request_status" in types


def test_resource_type_distinguishes_knowledge_from_dataset():
    types = _read(TYPES)
    assert "resource_type: 'dataset' | 'metadata'" in types
    # 注释里要点明这个反直觉的映射，否则下一个人一定会传错
    assert "= 知识库" in types and "= 数据集" in types


def test_readonly_types_exclude_field_details():
    types = _read(TYPES)
    dataset_block = types.split("export interface DatasetReadonly")[1]
    assert "physical_name" in dataset_block
    # 只读边界：不给字段明细与样例值
    assert "columns" not in dataset_block
    assert "examples" not in dataset_block
