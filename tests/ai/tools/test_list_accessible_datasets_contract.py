"""`list_accessible_datasets` 的目录契约：入参、体积上限、空结果解释。

现场问题（2026-11-07）：这个工具零参数、返回全量、`description` 原样输出，
数据集一多就把上下文撑爆（实测管理员 13 个数据集 = 3036 字符，其中大半是整段
业务介绍）；空结果只有 ``{"items": [], "count": 0}``，模型无法区分「没授权」
「授权指向的数据集已删除/已停用」「关键字没匹配到」，只能笼统回答「你没有数据集」；
两个数据集展示名相同（都叫「测试数据集」）时模型也无法在回答里指代。

本文件锁住四项：
1. ``keyword`` / ``dataset_id`` / ``limit`` 三个可选入参；
2. 返回体积上限（条数 + 单条介绍截断）；
3. ``note`` 区分空结果的三种成因、截断提示、失效授权提示；
4. 重名条目补 ``label``。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.ai.tools import resource_catalog_tools as module

pytestmark = pytest.mark.no_infrastructure


class _FakeSession:
    """只实现工具用到的那一点点接口；``dataset_row`` 用来伪造存在性查询结果。"""

    def __init__(self, dataset_row=None):
        self._dataset_row = dataset_row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        row = self._dataset_row

        class _Result:
            def first(self):
                return row

        return _Result()


def _entry(dataset_id, name, *, display=None, description="", permission=None):
    return {
        "id": dataset_id,
        "name": name,
        "display_name": display or name,
        "description": description,
        "status": 1,
        "permission": permission,
    }


@pytest.fixture
def install(monkeypatch):
    """把上下文、会话与 MetadataService 都换成假实现，避免碰真实库。"""

    def _install(entries, *, granted=None, is_admin=False, dataset_row=None):
        class _FakeMetadata:
            @staticmethod
            async def list_accessible_dataset_entries(db, **kwargs):
                return entries

            @staticmethod
            async def accessible_dataset_ids(db, **kwargs):
                return granted

        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _FakeSession(dataset_row))
        monkeypatch.setattr(
            module,
            "get_current_agent_context",
            lambda: SimpleNamespace(user_id=42, is_admin=is_admin),
        )
        monkeypatch.setattr(module, "MetadataService", _FakeMetadata)

    return _install


async def _call(**kwargs):
    return json.loads(await module.list_accessible_datasets.func(**kwargs))


# --------------------------------------------------------------------------
# 入参
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_filters_by_name_display_and_description(install):
    install(
        [
            _entry(1, "energy_ds", display="能耗"),
            _entry(2, "sales_ds", display="销售", description="覆盖客户主数据"),
        ]
    )

    assert [item["id"] for item in (await _call(keyword="能耗"))["items"]] == [1]
    # 备注也参与匹配
    assert [item["id"] for item in (await _call(keyword="客户主数据"))["items"]] == [2]
    # 大小写不敏感
    assert [item["id"] for item in (await _call(keyword="ENERGY"))["items"]] == [1]


@pytest.mark.asyncio
async def test_dataset_id_returns_single_entry(install):
    install([_entry(53, "a_ds"), _entry(54, "b_ds")])

    payload = await _call(dataset_id=54)

    assert [item["id"] for item in payload["items"]] == [54]
    assert payload["total"] == 1
    assert "note" not in payload


@pytest.mark.asyncio
async def test_unknown_dataset_id_explains_instead_of_empty_list(install):
    install([_entry(53, "a_ds")])

    payload = await _call(dataset_id=999)

    assert payload["items"] == []
    # 库里也没有这个 id → 明确说是数据资产问题，而不是含糊的「你没权限」
    assert "不存在（可能已被删除）" in payload["note"]


@pytest.mark.asyncio
async def test_missing_dataset_is_reported_as_asset_problem(install):
    """id 查不到且库里也没有 → 数据集已被删除，不是权限问题。"""
    install([_entry(53, "a_ds")], dataset_row=None)

    payload = await _call(dataset_id=1)

    assert "不存在（可能已被删除）" in payload["note"]
    assert "不是权限问题" in payload["note"]


@pytest.mark.asyncio
async def test_disabled_dataset_is_reported_as_status_problem(install):
    """存在但 status != 1 → 停用，启用即可，不是权限问题。"""
    install([_entry(53, "a_ds")], dataset_row=(48, 0))

    payload = await _call(dataset_id=48)

    assert "存在但已停用" in payload["note"]


@pytest.mark.asyncio
async def test_dataset_without_grant_is_reported_as_permission_problem(install):
    """存在且启用、但用户没被授权 → 这才是真正的权限问题。"""
    install([_entry(53, "a_ds")], dataset_row=(54, 1))

    payload = await _call(dataset_id=54)

    assert "没有被授权访问它" in payload["note"]
    assert "权限问题" in payload["note"]


@pytest.mark.asyncio
async def test_invalid_dataset_id_is_reported(install):
    install([_entry(53, "a_ds")])

    payload = await _call(dataset_id="abc")

    assert payload["items"] == []
    assert "不是合法的数据集 ID" in payload["note"]


@pytest.mark.asyncio
async def test_limit_caps_items_and_marks_truncated(install):
    install([_entry(i, f"ds_{i}") for i in range(1, 6)])

    payload = await _call(limit=2)

    assert payload["count"] == 2
    assert payload["total"] == 5
    assert payload["truncated"] is True
    assert "共命中 5 个，只返回前 2 个" in payload["note"]


@pytest.mark.asyncio
async def test_limit_is_clamped_to_max(install):
    install([_entry(i, f"ds_{i}") for i in range(1, 5)])

    # 超上限不报错，按上限截断
    payload = await _call(limit=10_000)

    assert payload["count"] == 4
    assert "truncated" not in payload


# --------------------------------------------------------------------------
# 体积
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_description_is_clipped_and_flagged(install):
    install([_entry(1, "a_ds", description="一" * 400)])

    item = (await _call())["items"][0]

    assert len(item["description"]) == module._DATASET_DESCRIPTION_LIMIT + 1  # 含省略号
    assert item["description"].endswith("…")
    assert item["description_truncated"] is True


@pytest.mark.asyncio
async def test_short_description_is_untouched(install):
    install([_entry(1, "a_ds", description="用于资源广场测试")])

    item = (await _call())["items"][0]

    assert item["description"] == "用于资源广场测试"
    assert "description_truncated" not in item


# --------------------------------------------------------------------------
# 重名
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_display_names_are_explained_without_inflating_items(install):
    """重名只提示一句，不给每个条目加 label —— label 是每轮都付的固定成本。"""
    install(
        [
            _entry(1210, "rs_test_8ddf1efa", display="测试数据集"),
            _entry(1216, "rs_test_eb079d16", display="测试数据集"),
            _entry(53, "meta_pgdemo_ds", display="pgdemo主数据表数据集"),
        ]
    )

    payload = await _call()

    items = {item["id"]: item for item in payload["items"]}
    # 条目结构不被撑大，display_name 也不改写
    assert "label" not in items[1210]
    assert items[1210]["display_name"] == "测试数据集"
    # 但必须提醒模型用 id 区分，否则两行看起来一模一样
    assert "展示名重复" in payload["note"]
    assert "用 id 区分" in payload["note"]


@pytest.mark.asyncio
async def test_no_duplicate_note_when_names_are_unique(install):
    install([_entry(1, "a_ds", display="甲"), _entry(2, "b_ds", display="乙")])

    payload = await _call()

    assert "note" not in payload


# --------------------------------------------------------------------------
# 空结果与失效授权
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_catalog_without_any_grant(install):
    install([], granted=set())

    payload = await _call()

    assert payload["items"] == []
    assert "没有任何启用的数据集授权" in payload["note"]


@pytest.mark.asyncio
async def test_empty_catalog_because_grants_point_to_missing_datasets(install):
    """授权行还在、数据集已删除/停用：目录为空但**不是**没权限。"""
    install([], granted={1, 2})

    payload = await _call()

    assert payload["items"] == []
    assert "已删除或已停用" in payload["note"]


@pytest.mark.asyncio
async def test_stale_grants_are_reported_alongside_normal_results(install):
    install([_entry(53, "a_ds")], granted={53, 1})

    payload = await _call()

    assert [item["id"] for item in payload["items"]] == [53]
    assert "另有 1 条授权" in payload["note"]


@pytest.mark.asyncio
async def test_keyword_miss_suggests_available_names(install):
    install([_entry(1, "energy_ds", display="能耗"), _entry(2, "sales_ds")])

    payload = await _call(keyword="不存在")

    assert payload["items"] == []
    assert "没匹配到数据集" in payload["note"]
    assert "能耗" in payload["note"]


@pytest.mark.asyncio
async def test_admin_skips_stale_grant_note(install):
    """管理员没有「授权行」概念（granted=None），不能提示失效授权。"""
    install([_entry(53, "a_ds")], granted=None, is_admin=True)

    payload = await _call()

    assert payload["items"]
    assert "note" not in payload


# --------------------------------------------------------------------------
# 模型可见的契约
# --------------------------------------------------------------------------


def test_tool_schema_exposes_new_arguments():
    schema = module.list_accessible_datasets.args_schema.model_json_schema()

    assert set(schema["properties"]) == {"keyword", "dataset_id", "limit"}
    assert schema["properties"]["limit"]["default"] == module._DATASET_LIMIT_DEFAULT
    # 三个参数都是可选，模型不传也必须能调用
    assert not schema.get("required")


def test_tool_description_mentions_note_and_truncation():
    description = module.list_accessible_datasets.description

    assert "note" in description
    assert "truncated" in description
    assert "keyword" in description


# --------------------------------------------------------------------------
# 会话挂载范围：账号权限 ≠ 本轮可查
# --------------------------------------------------------------------------


def _patch_mounted(monkeypatch, value):
    from app.services.ai import metadata_dataset_scope

    async def _fake(db):
        return value

    monkeypatch.setattr(metadata_dataset_scope, "resolve_mounted_dataset_id_set", _fake)


@pytest.mark.asyncio
async def test_mounted_datasets_are_marked_and_explained(install, monkeypatch):
    """挂了数据集时，条目标 mounted，并在 note 里说明「其余虽在权限内但本轮查不了」。"""
    install([_entry(53, "a_ds", display="甲"), _entry(54, "b_ds", display="乙")])
    _patch_mounted(monkeypatch, {53})

    payload = await _call()

    items = {item["id"]: item for item in payload["items"]}
    assert items[53].get("mounted") is True
    assert "mounted" not in items[54]
    assert "本轮只挂载了「甲」" in payload["note"]
    assert "只能查挂载的这些" in payload["note"]


@pytest.mark.asyncio
async def test_mounted_scope_not_in_result_is_explained(install, monkeypatch):
    """挂载的数据集被筛选条件排除（或不在权限内）时，也要说清本轮查不了。"""
    install([_entry(53, "a_ds", display="甲")])
    _patch_mounted(monkeypatch, {999})

    payload = await _call(keyword="甲")

    assert "不在本次结果里" in payload["note"]


@pytest.mark.asyncio
async def test_unresolvable_mount_is_flagged(install, monkeypatch):
    """挂载值解析成空集合 = 本轮大概率查不到数据，必须提前说明。"""
    install([_entry(53, "a_ds")])
    _patch_mounted(monkeypatch, set())

    payload = await _call()

    assert "无法解析" in payload["note"]


@pytest.mark.asyncio
async def test_no_mount_means_no_mount_note(install, monkeypatch):
    """未限定范围（None）时不能凭空冒出「只挂载了」的提示。"""
    install([_entry(53, "a_ds")])
    _patch_mounted(monkeypatch, None)

    payload = await _call()

    assert "mounted" not in payload["items"][0]
    assert "note" not in payload
