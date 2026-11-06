"""契约测试：数据集卡片上要能直接看到授权情况，不用点进去。

背景：数据集权限改成三层（数据集/表/行）之后，卡片上看不出「这个数据集到底授给了谁」。
管理员排查「为什么某人进不来」时得逐个点进详情，来回跳。这里锁住卡片摘要的三件事：

1. 卡片请求批量摘要接口，而不是逐个数据集打授权接口（一屏 9 个卡片就是 9 个请求）；
2. 摘要只对能改权限的人显示——授权对象名单本身是管理信息；
3. 后端路由必须定义在 ``/datasets/{dataset_id}`` 之前，否则 ``permission-summary``
   会被当成 dataset_id 去转 int，直接 422。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = "frontend/src/views/MetadataDatasets.vue"
API = "app/api/portal/endpoints/metadata.py"


def _view() -> str:
    return (ROOT / VIEW).read_text(encoding="utf-8")


def _api() -> str:
    return (ROOT / API).read_text(encoding="utf-8")


def test_card_requests_batch_summary_endpoint() -> None:
    view = _view()
    assert "/api/portal/metadata/datasets/permission-summary" in view
    # ids 逗号拼接一次请求，不能 N+1
    assert "ids: ids.join(',')" in view
    # 列表刷新时顺带刷新摘要
    assert "void fetchPermissionSummary()" in view


def test_summary_only_for_users_who_can_edit_permissions() -> None:
    view = _view()
    assert "canSeePermissionSummary" in view
    assert "element:metadata:edit" in view
    # 卡片徽标本身也受该门禁控制
    assert 'v-if="canSeePermissionSummary"' in view


def test_card_shows_tone_text_and_tooltip() -> None:
    view = _view()
    assert "permissionText" in view
    assert "permissionTooltip" in view
    assert "permissionToneClass" in view
    # 未授权也要有明确文案，别显示成 0
    assert "未授权" in view
    # hover 能看到收窄/行级信息
    assert "条只授权了部分表" in view


def test_backend_route_declared_before_dataset_id_path() -> None:
    api = _api()
    summary_at = api.index('"/datasets/permission-summary"')
    dataset_id_at = api.index('@router.get("/datasets/{dataset_id}"')
    assert summary_at < dataset_id_at, "静态路由必须写在 /datasets/{dataset_id} 之前"


def test_backend_route_requires_metadata_edit() -> None:
    api = _api()
    block = api[api.index('"/datasets/permission-summary"') :][:400]
    assert "element:metadata:edit" in block
