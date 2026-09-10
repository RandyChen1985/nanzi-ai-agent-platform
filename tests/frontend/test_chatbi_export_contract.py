"""「完整明细导出」通道的前后端契约测试。

约束：直链导出（for_export）与 AI 分析（分析上限 1000）共享同一执行入口但互不干扰；
后端放宽到 10 万行、跳过 2MB 返回体限制；前端在明细面板提供「导出完整明细」按钮。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_export_channel_uses_same_execution_entry_with_for_export_flag():
    data_api = _source("app/services/ai/tools/data_api.py")
    exec_service = _source("app/services/sql_query_execution_service.py")

    # 统一入口声明 for_export 参数（默认 False，保持 AI 分析行为不变量）
    assert "for_export: bool = False" in data_api
    # 导出口径行数上限 10 万
    assert "MAX_EXPORT_SQL_ROWS = 100000" in data_api
    # 导出口径下跳过 2MB 返回体硬限制（AI 分析口径仍保留）
    assert "if not for_export and len(result_json" in data_api
    # 缓存变体隔离，避免污染 AI 查数缓存
    assert 'cache_variant = "export_full"' in data_api
    # execute_sql_query_core 透传 for_export 到统一执行入口
    assert "for_export=for_export" in exec_service


def test_export_endpoint_is_registered_under_chatbi_export_router():
    api = _source("app/api/portal/api.py")
    endpoint = _source("app/api/portal/endpoints/chatbi_export.py")

    assert "chatbi_export" in api
    assert 'prefix="/chatbi-export"' in api

    # 端点：只读 SELECT/WITH 白名单，拒绝联邦查询
    assert '"/result"' in endpoint
    assert "for_export=True" in endpoint
    assert 'execution_mode == "federated"' in endpoint
    # 不喂 AI：不带 include_total 的 COUNT（显式关闭）
    assert "include_total = False" in endpoint
    # 复用权限与文件落盘基础设施
    assert "execute_sql_query_core" in endpoint
    assert "register_artifact" in endpoint
    assert 'artifact_type="export"' in endpoint


def test_frontend_exposes_full_detail_export_button():
    panel = _source("frontend/src/components/chatbi/ChatBIInsightPanel.vue")

    assert "导出完整明细" in panel
    assert "导出中…" in panel
    assert 'axios.post("/api/portal/chatbi-export/result"' in panel
    # 按钮只在有 final_sql + 数据源 + 非联邦查询时可用
    assert "canExportFull" in panel
    assert "openExportMenu" in panel
    assert 'm.execution?.mode === "federated"' in panel
    assert "data_source:" in panel
    assert "execution_mode:" in panel
    # 可选择导出格式（xlsx / csv / md），紧贴按钮下拉
    assert "exportMenuRef" in panel
    assert "pickExportFormat" in panel
    assert 'exportFormatOptions' in panel
    assert 'value: "xlsx"' in panel
    assert 'value: "csv"' in panel
    assert 'value: "md"' in panel
    assert "format: fmt" in panel
    # 首次使用引导气泡（参考「快捷指令折叠」气泡样式），挂在「明细」页签上
    assert "showExportHint" in panel
    assert "triggerExportHint" in panel
    assert "dismissExportHint" in panel
    assert "EXPORT_HINT_STORAGE_KEY" in panel
    assert 'v-if="showExportHint && tab.id === \'table\'"' in panel
    # 联邦查询明确提示不支持导出（按钮禁用原因 + 明细页顶说明）
    assert "federatedNotice" in panel
    assert "联邦查询暂不支持完整明细" in panel
    assert "exportDisabledReason" in panel
    # 已去掉冗余的「导出 Markdown」按钮
    assert "导出 Markdown" not in panel
    assert "exportMarkdown" not in panel
    # 结果直接下载并提示行数
    assert "resolveGeneratedFileHref" in panel
    assert "row_count" in panel