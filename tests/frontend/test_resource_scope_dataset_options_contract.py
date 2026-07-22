from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_embed_resource_scope_loads_accessible_dataset_options():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert "/api/portal/metadata/datasets/accessible" in source
    assert "axios.get('/api/portal/metadata/datasets')" not in source or "datasets/accessible" in source
    # 会话资源候选列表不再走重的全量 metadata 列表
    assert "loadResourceOptions" in source
    assert source.count("/api/portal/metadata/datasets/accessible") >= 1


def test_accessible_datasets_endpoint_exists():
    source = _source("app/api/portal/endpoints/metadata.py")
    assert '/datasets/accessible"' in source or "/datasets/accessible" in source
    assert "list_accessible_dataset_options" in source
