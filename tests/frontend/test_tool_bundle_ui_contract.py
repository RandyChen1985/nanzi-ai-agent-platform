"""工具管理 JSON 导入/导出的前端契约。

风格与 ``tests/frontend`` 其他用例一致：读源码断言关键锚点，不跑浏览器。
真正需要浏览器验证的是布局与交互手感，这里只守住「按钮在、接口对、脱敏前缀一致」。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL_API = ROOT / "frontend/src/api/tool.ts"
REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"
SCRIPT_LIST = ROOT / "frontend/src/components/system/ScriptToolList.vue"
IMPORT_MODAL = ROOT / "frontend/src/components/system/ToolBundleImportModal.vue"
BUNDLE_UTILS = ROOT / "frontend/src/utils/toolBundle.ts"
EXPORT_COMPOSABLE = ROOT / "frontend/src/composables/useToolBundleExport.ts"
PROMPT_CONST = ROOT / "frontend/src/constants/toolBundlePrompt.ts"
BACKEND_CREDENTIALS = ROOT / "app/services/ai/tools/tool_credentials.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# API 客户端
# --------------------------------------------------------------------------- #
def test_tool_bundle_api_exposes_three_calls():
    source = _source(TOOL_API)
    assert "export const toolBundleApi" in source
    for method in ("exportBundle", "precheck", "importBundle"):
        assert f"{method}:" in source, method
    assert "/api/portal/tools/export" in source
    assert "/api/portal/tools/import/precheck" in source
    assert "/api/portal/tools/import" in source


def test_export_requests_blob_and_joins_ids():
    source = _source(TOOL_API)
    body = source[source.index("exportBundle:"):]
    body = body[: body.index("precheck:")]
    assert "responseType: 'blob'" in body, "导出必须按 blob 接收，否则拿不到响应头文件名"
    assert "join(',')" in body, "ids 以逗号分隔传给后端"


def test_import_action_type_covers_four_actions():
    source = _source(TOOL_API)
    assert re.search(
        r"export type ToolBundleImportAction\s*=\s*'create'\s*\|\s*'skip'\s*\|\s*'overwrite'\s*\|\s*'rename'",
        source,
    )


# --------------------------------------------------------------------------- #
# 脱敏前缀必须与后端一致
# --------------------------------------------------------------------------- #
def test_credential_placeholder_prefix_matches_backend():
    backend = _source(BACKEND_CREDENTIALS)
    frontend = _source(BUNDLE_UTILS)
    backend_prefix = re.search(r'CREDENTIAL_PLACEHOLDER_PREFIX\s*=\s*"([^"]+)"', backend)
    frontend_prefix = re.search(r"CREDENTIAL_PLACEHOLDER_PREFIX\s*=\s*'([^']+)'", frontend)
    assert backend_prefix and frontend_prefix
    assert backend_prefix.group(1) == frontend_prefix.group(1), "前后端占位符前缀不一致，徽标会永远不亮"


# --------------------------------------------------------------------------- #
# 两个 tab 的入口
# --------------------------------------------------------------------------- #
def test_api_tab_has_export_and_import_buttons():
    source = _source(REGISTRY)
    assert "导出 JSON（" in source
    assert "导入 JSON" in source
    assert "showBundleImport = true" in source
    assert "ToolBundleImportModal" in source
    # 有勾选导勾选，无勾选导当前筛选结果
    assert "selectedToolIds.value.size" in source
    assert "filteredTools.value.map((tool) => tool.id)" in source


def test_api_tab_actions_live_in_search_row_dropdown():
    """按钮跟「脚本工具」页对齐：标题行只留标题，操作在搜索行右侧的下拉里。

    平铺四个按钮会把搜索框挤窄，也不符合两页同一版式。
    """
    source = _source(REGISTRY)
    assert source.index("搜索名称、描述或 URL") < source.index("导出 JSON（"), "导入导出应排在搜索框之后"
    assert "showToolActionsMenu" in source
    assert "更多操作：导入 / 导出 / 分组管理" in source
    assert "document.addEventListener('click', closeToolActionsMenu)" in source
    # 标题行（第一行）不再平铺操作按钮
    first_row = source[source.index("<!-- 第一行") : source.index("<!-- 第二行")]
    for gone in ("openGroupManager()", "showBundleImport = true", "导出 JSON（"):
        assert gone not in first_row, f"标题行仍残留操作：{gone}"


def test_api_rows_show_pending_credential_badge():
    source = _source(REGISTRY)
    assert "待补凭据" in source
    assert "toolPendingCredentials(t)" in source
    assert "pendingCredentialHeaders(tool.headers)" in source


def test_script_tab_has_export_and_import_buttons():
    source = _source(SCRIPT_LIST)
    assert "导出 JSON（" in source
    assert "导入 JSON" in source
    assert "bundleImportVisible = true" in source
    assert "ToolBundleImportModal" in source
    assert "selectedIds.value.size" in source
    assert "filteredTools.value.map((tool) => tool.id)" in source
    # 内置脚本导出要给出提示，不能悄悄变成自定义工具
    assert "按「自定义工具」导出" in source


def test_script_tab_actions_live_in_add_button_dropdown():
    source = _source(SCRIPT_LIST)
    assert source.index("搜索名称、描述或用途") < source.index("导出 JSON（"), "导入导出应排在搜索框之后"
    assert "showActionsMenu" in source
    assert "更多操作：导入 / 导出" in source
    assert "document.addEventListener('click', closeActionsMenu)" in source
    # 沙箱不支持脚本工具时「新建」禁用，但导入/导出仍要能点：箭头按钮不能跟着禁用
    arrow_at = source.index('title="更多操作：导入 / 导出"')
    arrow_tag = source[source.rindex("<button", 0, arrow_at) : arrow_at + 40]
    assert ":disabled" not in arrow_tag, "箭头按钮被禁用会导致无法导入"


# --------------------------------------------------------------------------- #
# 导入弹窗
# --------------------------------------------------------------------------- #
def test_import_modal_prechecks_before_writing():
    source = _source(IMPORT_MODAL)
    assert "toolBundleApi.precheck" in source
    assert "toolBundleApi.importBundle" in source
    # 预检与导入是两个独立步骤：先看清单，再确认落库
    assert "确认导入" in source
    assert "parseBundleText" in source


def test_import_modal_offers_overwrite_and_rename():
    source = _source(IMPORT_MODAL)
    assert "覆盖已有" in source
    assert "另存为新工具" in source
    assert "can_overwrite" in source, "不可覆盖的目标（内置脚本/跨类型同名）不能给出覆盖选项"
    assert "item.status !== 'invalid'" in source


def test_import_modal_reports_pending_credentials_after_import():
    source = _source(IMPORT_MODAL)
    assert "pending_credentials" in source
    assert "还缺凭据" in source


def test_export_composable_parses_blob_error():
    source = _source(EXPORT_COMPOSABLE)
    assert "readBundleError" in source
    assert "resolveDownloadFilename" in source
    assert "downloadBlob" in source


# --------------------------------------------------------------------------- #
# 「让 AI 生成」页签
# --------------------------------------------------------------------------- #
def test_import_modal_has_ai_generation_tab():
    source = _source(IMPORT_MODAL)
    assert "导入 JSON 文件" in source
    assert "让 AI 生成" in source
    assert "SCRIPT_PROMPT_EXAMPLES" in source, "要给用户可点的示例需求"
    assert "copyToClipboard" in source, "必须复用带 HTTP 降级的复制工具"
    assert "buildScriptToolPrompt" in source


def test_prompt_teaches_the_real_runtime_contract():
    """提示词里的每一条都对应平台真实行为，删掉生成的脚本就跑不通。"""
    source = _source(PROMPT_CONST)
    for anchor in (
        "json.load(sys.stdin)",  # 参数从 stdin 读
        "artifacts",  # 可下载产物登记
        "Path.cwd()",  # cwd 即会话工作区
        "sys.exit(2)",  # 失败要非 0 退出
        "parameter_schema",
        "requirements",
        "schema_version",
        "```json",  # 明确要求只输出一个 json 代码块
    ):
        assert anchor in source, f"提示词缺少运行时契约：{anchor}"


def test_prompt_keeps_requirement_placeholder_and_examples():
    source = _source(PROMPT_CONST)
    assert "SCRIPT_PROMPT_PLACEHOLDER" in source
    assert "requirement.trim() || SCRIPT_PROMPT_PLACEHOLDER" in source, "空需求要落回占位符，别复制一段没有需求的提示词"


def test_placeholder_is_a_filled_example_not_an_empty_shell():
    """占位符要示范「需求该写到什么颗粒度」，否则用户只会写「做个 Excel 工具」。"""
    source = _source(PROMPT_CONST)
    for anchor in ("请整体替换成你的需求", "功能：", "输入参数：", "输出：", "特殊要求："):
        assert anchor in source, f"需求占位符缺少四要素：{anchor}"
    assert "默认 80" in source, "示例要示范「可选参数 + 默认值」的写法"
    assert "export function buildScriptToolPrompt" in source


def test_prompt_ships_a_runnable_example():
    """字段表之外必须给一份完整示例：AI 最容易漏的是 artifacts 与 code 的转义。"""
    source = _source(PROMPT_CONST)
    assert "EXAMPLE_SCRIPT_TOOL" in source
    assert "buildExampleBundleJson" in source
    assert "参考示例" in source
    assert "内容不要照抄" in source, "示例太具体时 AI 会照抄业务逻辑，要明确说明只是格式示意"
    # 示例必须示范产物登记，否则 AI 生成的脚本只会 print 路径
    assert "artifacts" in source


def test_prompt_forbids_db_only_fields():
    source = _source(PROMPT_CONST)
    # 这些字段不进导入契约，写进去会被静默忽略，必须明确告知 AI 不要输出
    for field in ("id", "source", "is_overridden", "created_at", "updated_at"):
        assert field in source, field
    assert "不要输出" in source


def test_import_modal_has_paste_tab():
    """三个入口：上传文件、粘贴内容、让 AI 生成。"""
    source = _source(IMPORT_MODAL)
    assert "导入 JSON 文件" in source
    assert "粘贴 JSON" in source
    assert "让 AI 生成" in source
    assert "precheckPastedText" in source
    assert "粘贴的 JSON" in source, "预检表要能看出来源是粘贴而不是文件"


def test_paste_reuses_the_file_precheck_path():
    """粘贴不能自己再写一套解析/预检，否则两条路会各自漂移。"""
    source = _source(IMPORT_MODAL)
    assert source.count("toolBundleApi.precheck") == 1, "预检只应有一个调用点"
    assert "applyBundleText" in source
    assert source.count("applyBundleText(") >= 2, "文件与粘贴都应经由 applyBundleText"
    # 预检表挂在文件页签下，粘贴成功后要切过去才看得到结果
    assert "activeTab.value = 'file'" in source
