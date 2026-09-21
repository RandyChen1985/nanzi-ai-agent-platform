import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _run_typescript(module_path: str, expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_chart_card_supports_table_view():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "buildChartTableRows" in source
    assert "getAvailableChartViewModes" in source
    assert "applyChartViewMode" in source
    assert "getChartViewModeLabel" in source
    assert "title=\"切换为表格视图\"" in source or "切换为表格视图" in source
    assert "表格" in source
    assert "getActiveChartMode(segment, idx) === 'table'" in source


def test_message_renderer_chart_switcher_adapts_to_candlestick():
    source = _source("frontend/src/components/MessageRenderer.vue")
    util = _source("frontend/src/utils/chartRenderer.ts")

    assert "CandlestickChart" in source
    assert "getAvailableChartViewModes" in source
    assert "K线" in util
    assert 'return orderChartViewModes(["candlestick", "table"])' in util or '["candlestick", "table"]' in util
    assert "getAvailableChartViewModes" in util
    assert "applyChartViewMode" in util


def test_message_renderer_registers_candlestick_chart():
    source = _source("frontend/src/components/MessageRenderer.vue")
    assert "CandlestickChart" in source
    util = _source("frontend/src/utils/chartRenderer.ts")
    assert '"candlestick"' in util
    assert "supportedChartSeriesTypes" in util


def test_message_renderer_only_classifies_chart_specific_fences():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "(?:chart|echarts)" in source
    assert "(?:chart|echarts|json)" not in source


def test_message_renderer_exposes_actionable_chart_parse_failures():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "parsed.error.code" in source
    assert "图表配置解析失败" in source


def test_message_renderer_supports_clarification_card():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert ":::clarification" in source
    assert "'clarification'" in source
    assert "clarification-card" in source
    assert "需要你确认" in source
    assert "clarification-card__icon" in source


def test_message_renderer_wraps_markdown_tables_with_scroll_container():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "markdown-table-scroll" in source
    assert "const responsiveTable = enhanceMarkdownTablesForMobile(table)" in source
    assert "markdown-table-toolbar" in source


def test_embed_markdown_tables_have_breathing_room_and_mobile_overflow():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert ":deep(.markdown-body .markdown-table-scroll)" in source
    assert ":deep(.markdown-body table)" in source
    for token in (
        "min-width: 680px",
        "overflow-x: auto",
        "border-spacing: 0",
        "display: table",
        "padding: 10px 14px",
        "vertical-align: top",
        "overflow-wrap: anywhere",
    ):
        assert token in source

    assert "display: block;\n  width: 100%;\n  min-width: 680px" not in source


def test_embed_ai_bubble_and_tables_follow_selected_markdown_theme():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "markdown-theme-${config.markdownTheme || 'default'}" in source
    for theme in ("default", "minimal", "academic", "apple", "warm", "compact", "bauhaus", "editorial", "zen"):
        assert f".markdown-theme-{theme}" in source
    assert ".markdown-theme-apple :deep(.markdown-table-scroll)" in source
    assert ".markdown-theme-editorial :deep(.markdown-table-scroll)" in source


def test_ai_message_border_visibility_is_configurable_and_persisted():
    management_source = _source("frontend/src/views/AgentManagement.vue")
    drawer_source = _source("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    embed_source = _source("frontend/src/views/EmbedChat.vue")

    assert "hide_message_border" in management_source
    assert "hide_message_border" in drawer_source
    assert "hideMessageBorder: true" in embed_source
    assert "message-borderless" in embed_source
    assert "config.hideMessageBorder" in embed_source
    borderless_css = embed_source.split(".message-borderless {", 1)[1].split("}", 1)[0]
    assert "border-width: 0 !important;" in borderless_css
    assert "padding-left: 0.75rem !important;" in borderless_css


def test_embed_markdown_table_cells_are_left_aligned():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert ":deep(.markdown-body th)" in source
    assert ":deep(.markdown-body td)" in source
    assert "text-align: left !important;" in source


def test_markdown_tables_get_mobile_layout_metadata():
    source = _source("frontend/src/components/MessageRenderer.vue")
    helper = _source("frontend/src/utils/markdownTableResponsive.ts")

    assert "enhanceMarkdownTablesForMobile" in source
    assert "markdown-table-view-toggle" in source
    assert "markdown-table-view-table" in source
    assert "markdown-table-toolbar" in source
    assert "markdown-table-mobile-compact" in helper
    assert "markdown-table-mobile-cards" in helper
    assert "data-label" in helper
    assert "MOBILE_CARD_COLUMN_THRESHOLD" in helper
    assert "let cellIndex = 0" in helper
    assert "const label = headers[currentIndex]" in helper


def test_embed_mobile_tables_use_compact_and_card_layouts():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "@media (max-width: 639px)" in source
    assert "table-layout: fixed" in source
    assert "min-width: 0" in source
    assert "td::before" in source
    assert "content: attr(data-label)" in source
    assert "grid-template-columns" in source
    assert "padding: 7px 6px !important" in source
    assert "var(--md-table-background)" in source


def test_embed_mobile_table_toggle_restores_scrollable_table_view():
    source = _source("frontend/src/views/EmbedChat.vue")
    renderer = _source("frontend/src/components/MessageRenderer.vue")

    assert "markdown-table-view-cards" in renderer
    assert "markdown-table-view-table" in source
    assert "const viewClass = hasMobileCardView ? ' markdown-table-view-table' : '';" in renderer
    assert 'aria-label="切换到卡片视图" aria-pressed="true" title="切换到卡片视图">卡片' in renderer
    assert "overflow-x: auto" in source
    assert "justify-content: flex-start" in source
    assert ".markdown-table-scroll.markdown-table-view-cards" in source
    card_view_css = source.split(".markdown-table-scroll.markdown-table-view-cards", 1)[1].split("}", 1)[0]
    assert "border: 0 !important;" in card_view_css
    assert "display: inline-flex;" in source
    assert "content: '▦';" in source
    assert ":focus-visible" in source
    assert "padding: 6px 6px 10px;" in source
    assert "border-radius: 0;" in source
    assert "切换到表格视图" in renderer
    assert "切换到卡片视图" in renderer


def test_markdown_table_mobile_metadata_maps_headers_and_falls_back_safely():
    result = _run_typescript(
        "frontend/src/utils/markdownTableResponsive.ts",
        """
const transform = api.enhanceMarkdownTablesForMobile;
return {
  cards: transform('<table><thead><tr><th>名称</th><th>A &quot;标记&quot;</th><th>状态</th><th>备注</th></tr></thead><tbody><tr><td>项目</td><td>甲</td><td>正常</td><td>已完成</td></tr></tbody></table>'),
  compact: transform('<table><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead><tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody></table>'),
  empty: transform('<table><thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead></table>'),
  noHeader: transform('<table><tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></tbody></table>'),
  complex: transform('<table><thead><tr><th rowspan="2">A</th><th>B</th><th>C</th><th>D</th></tr></thead><tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></tbody></table>')
};
""",
    )

    assert "markdown-table-mobile-cards" in result["cards"]
    assert 'data-label="名称"' in result["cards"]
    assert 'data-label="A &quot;标记&quot;"' in result["cards"]
    assert "markdown-table-mobile-compact" in result["compact"]
    assert "data-label" not in result["compact"]
    assert "markdown-table-mobile-compact" in result["empty"]
    assert "markdown-table-mobile-cards" not in result["empty"]
    assert "markdown-table-mobile-compact" in result["noHeader"]
    assert "markdown-table-mobile-compact" in result["complex"]


def test_embed_settings_exposes_and_persists_message_border_preference():
    settings_source = _source("frontend/src/components/embed/ChatSettings.vue")
    embed_source = _source("frontend/src/views/EmbedChat.vue")

    assert "隐藏 AI 消息外框" in settings_source
    assert "handleSetMessageBorder" in settings_source
    assert "config.hideMessageBorder" in settings_source
    assert "yovole_hide_message_border" in embed_source
    assert "user_has_custom_border_preference" in embed_source


def test_bauhaus_message_background_is_white():
    source = _source("frontend/src/views/EmbedChat.vue")

    bauhaus_theme = source.split(".markdown-theme-bauhaus {", 1)[1].split("}", 1)[0]
    assert "--ai-bubble-background: #ffffff;" in bauhaus_theme
    assert "--md-table-background: #ffffff;" in bauhaus_theme


def test_minimal_table_uses_near_white_borders():
    source = _source("frontend/src/views/EmbedChat.vue")

    minimal_theme = source.split(".markdown-theme-minimal {", 1)[1].split("}", 1)[0]
    assert "--md-table-border: #f1f5f9;" in minimal_theme
    assert "--md-table-cell-border: #f8fafc;" in minimal_theme
    assert "--md-table-header: #ffffff;" in minimal_theme


def test_apple_table_matches_minimal_table_style():
    source = _source("frontend/src/views/EmbedChat.vue")

    apple_theme = source.split(".markdown-theme-apple {", 1)[1].split("}", 1)[0]
    for token in (
        "--md-table-background: #ffffff;",
        "--md-table-header: #ffffff;",
        "--md-table-text: #334155;",
        "--md-table-border: #f1f5f9;",
        "--md-table-cell-border: #f8fafc;",
        "--md-table-cell-padding: 9px 12px;",
    ):
        assert token in apple_theme


def test_compact_table_uses_near_white_borders():
    source = _source("frontend/src/views/EmbedChat.vue")

    compact_theme = source.split(".markdown-theme-compact {", 1)[1].split("}", 1)[0]
    assert "--md-table-header: #f8fafc;" in compact_theme
    assert "--md-table-border: #f1f5f9;" in compact_theme
    assert "--md-table-cell-border: #f8fafc;" in compact_theme


def test_thought_steps_waiting_state_uses_status_copy_not_empty_skeleton():
    source = _source("frontend/src/views/EmbedChat.vue")
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 首包到达前用状态文案，不再用空骨架条；进行中步骤用呼吸绿点强调
    assert "正在连接服务" in source
    assert 'class="thought-status-dot shrink-0"' in timeline
    assert "border-l-2 border-primary/55" not in source
    assert "overflow-y-auto border-l border-gray-100" not in source


def test_embed_shows_agent_dispatch_placeholder_before_agent_metadata_arrives():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "智能体正在分配调度中" in source
    assert "animate-bounce-dot" in source
    assert 'name="slide-fade"' in source
    assert "msg.isThinking" in source
    # 调度占位胶囊的灰底灰边仍保留，色值现带透明度后缀（bg-gray-50/70、border-gray-200/50）。
    assert "bg-gray-50/70 border-gray-200/50 text-gray-400" in source
    assert "v-if=\"msg.agentName\"" in source


def test_multi_agent_toggle_has_single_toast_source():
    settings_source = _source("frontend/src/components/embed/ChatSettings.vue")
    embed_source = _source("frontend/src/views/EmbedChat.vue")

    assert "多智能体协同已关闭" in settings_source
    assert "triggerMultiAgentHint" not in embed_source
    assert "watch(() => config.enableMultiAgent" not in embed_source


def test_markdown_code_block_renders_clear_foreground_and_fence_structure():
    md_source = _source("frontend/src/utils/markdown.ts")
    renderer_source = _source("frontend/src/components/MessageRenderer.vue")
    embed_source = _source("frontend/src/views/EmbedChat.vue")

    assert "code-block-wrapper" in md_source
    assert "hljs-code" in md_source
    assert "color: #0f172a !important" in renderer_source
    assert "color: #f1f5f9 !important" in renderer_source
    assert "ui-monospace" in renderer_source
    assert "ui-monospace" in embed_source


def test_message_renderer_html_link_opens_canvas_contract():
    renderer_source = _source("frontend/src/components/MessageRenderer.vue")
    canvas_composable_source = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")
    embed_source = _source("frontend/src/views/EmbedChat.vue")

    # 1. MessageRenderer.vue 识别 .html / .htm 链接并拦截
    assert "isHtml = lowerHref.endsWith('.html') || lowerHref.endsWith('.htm')" in renderer_source
    assert "type = 'html'" in renderer_source
    assert "HTML 交互应用" in renderer_source

    # 2. useWorkspaceCanvas.ts 支持拉取 HTTP/API URL 的 HTML 内容并以 html 模式渲染
    assert "payload.type === \"html\"" in canvas_composable_source
    assert "options.resolveFileUrl(payload.content)" in canvas_composable_source

    # 3. EmbedChat.vue canPreviewFile 支持 html
    assert "ext === 'html' || ext === 'htm'" in embed_source


