"""元数据模式的 RAGFlow 探活必须读「元数据 & RAG」那组配置。

系统配置页把 RAGFlow 分成两套，键名和用途都不同：

| 分组 | 配置项 | 用途 |
|---|---|---|
| 元数据 & RAG | `ragflow_api_url` / `ragflow_api_key` | 元数据同步、语义检索（`RagFlowClient()` 默认前缀 `ragflow`） |
| 知识库 | `knowledge_ragflow_api_url` / `knowledge_ragflow_api_key` | 知识库问答检索（`RagFlowClient(config_prefix="knowledge_ragflow")`） |

用户只在「元数据 & RAG」里填了地址，元数据管理页却报
`RAGFlow configuration (knowledge_ragflow_api_url, knowledge_ragflow_api_key) is missing.`
—— 探活复用了知识库接口 `/api/portal/ragflow/datasets`（前缀 `knowledge_ragflow`），
报的是根本不该由元数据关心的那组配置。

这里锁住三件事：

1. 探针只读 `ragflow_*`，不碰 `knowledge_ragflow_*`；
2. `metadata_provider` 的默认值全库一致（漏一处就会让未配置的项目被误判成 RAGFlow 模式）；
3. 元数据页的探活指向元数据侧接口，不再拿知识库接口做探活。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
METADATA_ENDPOINT = ROOT / "app/api/portal/endpoints/metadata.py"
METADATA_VIEW = ROOT / "frontend/src/views/MetadataDatasets.vue"

pytestmark = pytest.mark.no_infrastructure


def test_health_probe_reads_metadata_ragflow_config():
    source = METADATA_ENDPOINT.read_text(encoding="utf-8")
    start = source.index('@router.get("/ragflow/health")')
    # 只截到下一个路由装饰器，避免把整个文件算进来
    end = source.index("@router.", start + 10)
    handler = source[start:end]

    assert 'ConfigService.get("ragflow_api_url")' in handler
    assert 'ConfigService.get("ragflow_api_key")' in handler
    # 注释里可以提到知识库配置做对比，但**不许真去读它**
    assert 'ConfigService.get("knowledge_ragflow_api_url")' not in handler
    assert 'ConfigService.get("knowledge_ragflow_api_key")' not in handler
    assert 'RagFlowClient(config_prefix="ragflow")' in handler


def test_health_probe_returns_structured_result_instead_of_raising():
    """探活失败是预期状态：返回 200 + reachable=False，别让前端靠异常分支判断。"""
    source = METADATA_ENDPOINT.read_text(encoding="utf-8")
    start = source.index('@router.get("/ragflow/health")')
    end = source.index("@router.", start + 10)
    handler = source[start:end]

    assert '"reachable": False' in handler
    assert '"reachable": True' in handler
    assert "except Exception" in handler


def test_metadata_provider_default_is_consistent_across_codebase():
    """默认值必须是 local —— 与 schema / metadata_service / examples 等保持一致。"""
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if 'metadata_provider' in line and 'default=' in line and '"ragflow"' in line:
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")
    assert not offenders, "metadata_provider 的默认值应为 local：" + "; ".join(offenders)


def test_metadata_page_probes_metadata_side_endpoint():
    source = METADATA_VIEW.read_text(encoding="utf-8")
    assert "axios.get('/api/portal/metadata/ragflow/health')" in source
    # 探活不能再打知识库接口（注释里说明背景不算）
    assert "axios.get('/api/portal/ragflow/datasets'" not in source


def test_config_cache_ignores_blank_string():
    """空串缓存不能当命中：否则同一个配置项会「首次有默认值、之后变空」。"""
    source = (ROOT / "app/services/config_service.py").read_text(encoding="utf-8")
    assert 'cached_val is not None and cached_val != ""' in source
