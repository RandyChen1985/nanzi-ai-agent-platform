"""数据集「业务分组」契约：与工具 / 智能体 / MCP 同一套归一口径。

为什么单独立一组：分组在平台里有四处（工具、数据集、智能体、MCP）+ 技能写 frontmatter，
页签是同一个组件、同一套计数规则，所以「什么算没分组」「超长怎么办」只能有一个答案。
工具与智能体那边早就接上了 `app/utils/group_name.py`，数据集这边一直是裸的
`Optional[str] = None`，于是：

* `group_name="   "` 会落库成一个空白分组，前端页签上出现一个看不见的空分组；
* 超长名字在 MySQL 严格模式下直接 500，而不是像别处那样静默截断；
* 复制数据集时用 `payload.group_name if payload.group_name is not None else source.group_name`，
  调用方显式传 `null`（意思是「副本不分组」）会被悄悄改回源分组——用 `is not None` 判断
  永远分不清「没提这个字段」和「明确要清空」。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "app" / "schemas" / "metadata.py"
CLONE_SERVICE = ROOT / "app" / "services" / "dataset_clone_service.py"
GROUP_UTIL = ROOT / "app" / "utils" / "group_name.py"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 一、入参归一（真跑 pydantic）
# --------------------------------------------------------------------------


def test_dataset_base_normalizes_group_name() -> None:
    from app.schemas.metadata import DatasetBase

    # 连续空格 / 制表符 / 换行都折成单空格：页签与导出都是「一行一个分组」，带换行会错行
    assert DatasetBase(name="ds", group_name="  研发\t\t效能  ").group_name == "研发 效能"
    # 空白串一律视为未分组，否则页签上会出现一个看不见的空分组
    assert DatasetBase(name="ds", group_name="   ").group_name is None
    assert DatasetBase(name="ds", group_name=None).group_name is None
    # 超长按 64 截断（与 DB 的 VARCHAR(64) 对齐），不抛错
    assert len(DatasetBase(name="ds", group_name="履" * 65).group_name or "") == 64


def test_dataset_update_keeps_unset_semantics() -> None:
    """局部更新没提这个字段时不能进 `model_fields_set`，否则改别的字段会把分组清空。"""
    from app.schemas.metadata import DatasetUpdate

    assert DatasetUpdate(display_name="x").group_name is None
    assert "group_name" not in DatasetUpdate(display_name="x").model_fields_set
    assert "group_name" in DatasetUpdate(group_name=" 数据平台 ").model_fields_set
    assert DatasetUpdate(group_name=" 数据平台 ").group_name == "数据平台"


def test_clone_request_distinguishes_unset_and_explicit_null() -> None:
    """复制接口：没提 ≠ 传 null。前者沿用源分组，后者是「副本设为未分组」。"""
    from app.schemas.metadata import CloneDatasetRequest

    unset = CloneDatasetRequest(name="副本")
    assert "group_name" not in unset.model_fields_set
    assert unset.group_name is None

    cleared = CloneDatasetRequest(name="副本", group_name=None)
    assert "group_name" in cleared.model_fields_set
    assert cleared.group_name is None

    moved = CloneDatasetRequest(name="副本", group_name=" 数据平台 ")
    assert moved.group_name == "数据平台"


# --------------------------------------------------------------------------
# 二、复制服务按 model_fields_set 取值
# --------------------------------------------------------------------------


def test_clone_service_uses_model_fields_set() -> None:
    source = _read(CLONE_SERVICE)
    assert '"group_name" in payload.model_fields_set' in source
    # 老写法（is not None）会把「显式传 null」吞掉，不允许再回来
    assert "if payload.group_name is not None else source.group_name" not in source


# --------------------------------------------------------------------------
# 三、口径只留一份实现
# --------------------------------------------------------------------------


def test_skill_normalizer_reuses_shared_impl() -> None:
    """技能写 frontmatter 的归一是同一份实现，不另写一遍正则。"""
    skill_utils = _read(ROOT / "app" / "utils" / "skill_metadata.py")
    assert "from app.utils.group_name import GROUP_NAME_MAX_LEN, normalize_group_name" in skill_utils
    assert "return normalize_group_name(raw)" in skill_utils
    # 旧实现只折 [\\r\\n]+，制表符和连续空格会带进页签
    assert 're.sub(r"[\\r\\n]+", " ", str(raw or ""))' not in skill_utils


def test_skill_group_equals_shared_normalizer() -> None:
    from app.utils.group_name import normalize_group_name
    from app.utils.skill_metadata import normalize_skill_group

    for raw in ("  研发\t效能 ", "   ", None, "a" * 100):
        assert normalize_skill_group(raw) == normalize_group_name(raw)


def test_group_max_len_is_single_source() -> None:
    from app.utils.group_name import GROUP_NAME_MAX_LEN
    from app.utils.skill_metadata import SKILL_GROUP_MAX_LEN

    assert GROUP_NAME_MAX_LEN == 64
    assert SKILL_GROUP_MAX_LEN == GROUP_NAME_MAX_LEN


# --------------------------------------------------------------------------
# 四、四处 schema 都接上了归一
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        ("app.schemas.metadata", "DatasetBase"),
        ("app.schemas.metadata", "DatasetUpdate"),
        ("app.schemas.metadata", "CloneDatasetRequest"),
    ],
)
def test_dataset_schemas_declare_validator(module_path: str, class_name: str) -> None:
    import importlib

    module = importlib.import_module(module_path)
    model = getattr(module, class_name)
    field = model.model_fields["group_name"]
    assert field.annotation is not None
    # 空白串经 validator 归一后是 None，说明 validator 真的挂上了
    # CloneDatasetRequest 的 name 最少 2 字符，所以给个合法名字
    assert model(name="xy", group_name="  ").group_name is None


def test_schema_file_imports_shared_util() -> None:
    source = _read(SCHEMA)
    assert "from app.utils.group_name import GROUP_NAME_MAX_LEN, normalize_group_name" in source
    assert source.count('@field_validator("group_name", mode="before")') >= 3


def test_group_util_documents_truncation() -> None:
    """截断而不是报错，是刻意选择的语义：文件里要留着原因。"""
    source = _read(GROUP_UTIL)
    assert "return text[:GROUP_NAME_MAX_LEN]" in source
    assert "VARCHAR(64)" in source
