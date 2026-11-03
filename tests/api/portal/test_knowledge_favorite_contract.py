"""知识库收藏的契约。

「我的收藏」从前端占位换成真实数据，需要一条按人记录的收藏表：
- 收藏只要求「可读」——它是个人视图，不是权限，收藏别人共享的库不应该被拒
- 收藏/取消都要幂等，否则双击就报唯一键冲突
- 列表接口带 is_favorited，前端过滤即可，不必再开一套收藏列表接口
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "app/api/portal/endpoints/ragflow.py"
MODEL = ROOT / "app/models/knowledge.py"
MYSQL_MIGRATION = ROOT / "db-prod/V202-create-knowledge-base-favorites.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V102-create-knowledge-base-favorites.sql"


@pytest.fixture(scope="module")
def source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def _endpoint_block(source: str, func: str) -> str:
    start = source.index(f"async def {func}(")
    end = source.find("\n@router.", start)
    return source[start: end if end != -1 else len(source)]


def test_favorite_table_uses_person_and_dataset_unique_key() -> None:
    """一个人对一个库只能收藏一次，唯一键放库里兜底。"""
    model = MODEL.read_text(encoding="utf-8")
    assert 'UniqueConstraint("user_id", "dataset_id", name="uix_kbf_user_dataset")' in model
    for migration in (MYSQL_MIGRATION, PG_MIGRATION):
        assert migration.exists()
        assert "uix_kbf_user_dataset" in migration.read_text(encoding="utf-8")


def test_favorite_requires_read_not_manage(source: str) -> None:
    """收藏是个人视图：能看就能收藏，不能要求管理权。"""
    block = _endpoint_block(source, "favorite_dataset")
    assert "require_dataset_access" in block
    assert "can_manage_dataset" not in block


def test_favorite_and_unfavorite_are_idempotent(source: str) -> None:
    """重复收藏不能抛唯一键冲突；取消没收藏过的也要成功。"""
    fav = _endpoint_block(source, "favorite_dataset")
    assert "if not exists:" in fav
    unfav = _endpoint_block(source, "unfavorite_dataset")
    assert "delete(KnowledgeBaseFavorite)" in unfav


def test_list_exposes_is_favorited(source: str) -> None:
    """列表项带 is_favorited，前端按它算角标和过滤，不开第二套列表接口。"""
    assert "async def _attach_favorites" in source
    assert 'item["is_favorited"]' in source
    assert source.count("_attach_favorites(db, merged") == 1
