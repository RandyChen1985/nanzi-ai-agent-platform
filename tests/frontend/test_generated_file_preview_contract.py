"""AI 产物「预览 / 下载」双入口的契约测试。

沿用项目惯例：源码形状断言 + 用 Node 真跑纯函数做行为验证。
行为验证见 tests/frontend/generated_file_preview.mjs。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 行为验证：判定表（含 .pptx / .ppt 子串陷阱、.markdown 这类集合差异）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_generated_preview_plan_behaviour_via_node():
    script = Path("tests/frontend/generated_file_preview.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "产物预览判定测试全部通过" in proc.stdout


def test_plan_module_is_dependency_free_and_uses_explicit_ts_extension():
    source = _source("frontend/src/utils/generatedFilePreviewPlan.ts")

    # 零运行时依赖：判定模块不得引入 axios，否则 Node 无法直接加载（行为测试会静默失去覆盖）
    assert "from 'axios'" not in source
    assert "from '@/utils/axios'" not in source
    # 显式 .ts 扩展名是 Node ESM 解析内部依赖的前提（仓库已有 chatSessionExport.ts 先例）
    assert "from './documentPreviewFormats.ts'" in source
    assert "export function resolveGeneratedPreviewPlan(" in source
    assert "export function buildArtifactDownloadUrl(" in source


def test_content_loader_passes_url_for_office_and_blob_for_images():
    source = _source("frontend/src/utils/generatedFilePreview.ts")

    assert "export async function openGeneratedFileInCanvas(" in source
    # Office / PDF 只传鉴权 URL；图片 / CSV 才取 Blob
    assert "responseType: 'blob'" in source
    assert "type: isPdf ? 'pdf' : 'document'" in source
    # 对象 URL 必须先回收旧值再写回单槽 ref，否则旧值被覆盖后永久泄漏
    assert "activeBlobUrlRef.value = ''" in source
    assert "activeBlobUrlRef.value = blobUrl" in source
    assert source.index("URL.revokeObjectURL(activeBlobUrlRef.value)") < source.index(
        "activeBlobUrlRef.value = blobUrl"
    )


def test_workspace_canvas_exposes_generated_file_preview_on_right_dock():
    source = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")

    # 只看整个文件会误判：showCanvas(false) 在同一个文件的工作区预览里也有，
    # 必须切出 handleGeneratedFilePreview 本体的片段来断言（否则把它改成
    # 不钉住也照样通过 —— 典型假绿）。
    body = source[source.index("const handleGeneratedFilePreview = async (") : source.index("const handleOpenCanvas = async (")]
    # 产物预览是右侧钉住（与消息正文点链接一致）；显式置回 false 是因为上一次
    # 可能是工作区预览（那次会把 canvasFromWorkspace 置为 true）
    assert "canvasFromWorkspace.value = false;" in body
    assert "showCanvas(true);" in body
    assert "showCanvas(false)" not in body
    assert "return openGeneratedFileInCanvas({" in body
    assert "handleGeneratedFilePreview," in source


def test_artifacts_drawer_has_preview_and_download_actions():
    source = _source("frontend/src/components/embed/MyArtifactsDrawer.vue")

    assert "'preview-file': [item: ArtifactListItem]" in source
    assert "const previewArtifact = (it: ArtifactListItem)" in source
    assert "const downloadArtifact = (it: ArtifactListItem)" in source
    assert "const canPreviewArtifact = (it: ArtifactListItem)" in source
    # 下载必须强制 attachment，否则 html/pdf/图片会退化成新标签页预览
    assert "buildArtifactDownloadUrl(resolveGeneratedFileHref(it.download_url))" in source
    # 动作区在卡片主体之外，且点击不得冒泡成预览
    assert '@click.stop="previewArtifact(it)"' in source
    assert '@click.stop="downloadArtifact(it)"' in source
    # 不可预览类型不得渲染预览按钮，主体点击改为直接下载
    assert 'v-if="canPreviewArtifact(it)"' in source
    assert "const activateArtifact = (it: ArtifactListItem)" in source
    assert "if (canPreviewArtifact(it)) previewArtifact(it)" in source
    # previewArtifact 内部必须自己兜住不可预览类型（不能只靠模板隐藏按钮：
    # 消息正文等调用方不经过模板），且兜底动作是下载而不是静默返回
    preview_body = source[source.index("const previewArtifact = (it: ArtifactListItem)") : source.index("const activateArtifact = (it: ArtifactListItem)")]
    assert "if (!canPreviewArtifact(it)) {" in preview_body
    assert "downloadArtifact(it)" in preview_body
    # 旧的「点击即下载」入口不得残留
    assert "const openArtifact = (" not in source


def test_embed_chat_wires_drawer_preview_to_canvas_with_host_binding():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert '@preview-file="previewArtifactInCanvas"' in source
    assert "const previewArtifactInCanvas = async (item: { download_url: string; filename: string })" in source
    # 跨站嵌入：产物地址必须先绑到当前页面 Host
    assert "resolveGeneratedFileHref(item.download_url)" in source
    assert "handleGeneratedFilePreview," in source
    # 画布右侧钉住 → 只有预览成功才收起抽屉（失败时保住抽屉，用户还能改点下载）
    assert "const opened = await handleGeneratedFilePreview({" in source
    assert "if (opened) showMyArtifactsDrawer.value = false;" in source


def test_message_renderer_previews_office_and_offers_explicit_download():
    source = _source("frontend/src/components/MessageRenderer.vue")

    # Office 链接此前没有任何分支，点击落到浏览器默认行为（即下载）
    assert "const isOfficeDoc = ['.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx']" in source
    assert "type = 'document';" in source
    assert "resolveDocumentViewerMime(filename)" in source
    # 「下载」按钮：地址挂在 data 属性上，且必须在 <a> 预览分支之前处理
    assert "data-generated-download" in source
    assert "buildArtifactDownloadUrl(downloadUrl)" in source
    assert source.index("target.closest<HTMLElement>('[data-generated-download]')") < source.index(
        "const linkEl = target.closest('a')"
    )
    # 追加按钮必须发生在 HTML 链接占位符还原之后，否则 markdown 链接会被漏掉
    assert source.index("res = appendGeneratedFileDownloadActions(res);") > source.index(
        "res.replace(/###HTML_LINK_PLACEHOLDER_(\\d+)###/g"
    )


def test_canvas_download_falls_back_to_direct_url_for_artifacts():
    source = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "buildArtifactDownloadUrl(content)" in source
    # 直链兜底必须落在「把 content 当文本下载」之前，否则会下载出一个内容是 URL 的文档
    assert source.index("buildArtifactDownloadUrl(content)") < source.index(
        "a.download = `canvas_export.${extension}`"
    )


# --------------------------------------------------------------------------- #
# 跨层行为验证：抽出 MessageRenderer 里真实的 appendGeneratedFileDownloadActions
# 执行。「占位符还原之后再注入」这个顺序光看源码字符串是验不出来的。
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行抽取出的函数")
def test_download_button_injection_runs_the_real_function(tmp_path):
    source = _source("frontend/src/components/MessageRenderer.vue")
    start = source.index("  const GENERATED_FILE_HREF_PATTERN =")
    end = source.index("  /**\n   * 后处理：修复被 Markdown 引擎", start)
    snippet = source[start:end]
    assert "appendGeneratedFileDownloadActions" in snippet, "抽取片段失效，用例需要同步更新"

    script = tmp_path / "check_download_button.ts"
    script.write_text(
        "import assert from 'node:assert/strict'\n"
        + snippet
        + """
const url = '/api/v1/chat/generated-files/' + 'a'.repeat(32) + '?token=abc'
const rendered = `<p><a href="${url}">北京到上海高铁车次参考清单.docx</a></p>`
const withBtn = appendGeneratedFileDownloadActions(rendered)
assert.ok(withBtn.includes(`data-generated-download="${url}"`), 'markdown 链接后必须注入下载按钮')
assert.ok(withBtn.includes('>下载</button>'), '按钮文案应为「下载」')
// 幂等：重复处理不得叠加第二个按钮
const occurrences = (appendGeneratedFileDownloadActions(withBtn).match(/data-generated-download/g) || []).length
assert.equal(occurrences, 1, '不得重复注入')
// 非产物链接（工作区预览等）不得被注入
const other = '<a href="/api/v1/chat/fs/preview?path=x.docx">a.docx</a>'
assert.ok(!appendGeneratedFileDownloadActions(other).includes('data-generated-download'), '非产物链接不得注入')
console.log('产物下载按钮注入测试全部通过')
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "产物下载按钮注入测试全部通过" in proc.stdout


def test_message_body_artifact_links_reuse_the_same_plan_as_the_drawer():
    """正文里点产物链接必须走「我的产出」抽屉那套分派器，而不是另一份硬编码扩展名清单。

    回归背景（用户实测）：正文里点《北京到上海高铁车次参考清单.md》直接下载了，
    而同一个文件在抽屉里能预览 —— 因为正文只硬编码了 pdf/csv/html/图片/Office，
    `.md`/`.txt`/`.json`/代码等文本类没有任何分支，直接落到浏览器默认行为。
    """
    source = _source("frontend/src/components/MessageRenderer.vue")

    # 复用抽屉同一套分派器，而不是再维护第二份清单
    assert "resolveGeneratedPreviewPlan" in source
    assert "from '@/utils/generatedFilePreviewPlan'" in source
    # 产物 URL 本身不带扩展名，必须按链接文字（文件名）判定
    assert "resolveGeneratedPreviewPlan(artifactName)" in source
    assert "linkEl.textContent?.trim()" in source
    # 只能用产物 URL 模式限定，避免误伤普通链接
    assert "GENERATED_FILE_HREF_PATTERN.test(href)" in source
    # 只补「原有扩展名分支没覆盖」的格式，不动 pdf/csv/html/图片/Office 的既有行为
    assert "&& !(isPdf || isCsv || isHtml || isImage || isOfficeDoc)" in source
    # 事件契约
    assert "(e: 'preview-generated-file'" in source
    assert "emit('preview-generated-file', { url: href, name: artifactName })" in source


def test_message_body_preview_falls_back_to_download_without_a_host_listener():
    """宿主没接预览事件时必须退回浏览器默认（下载）。

    MessageRenderer 也用在只展示回答摘要、没有画布的位置；若一律 preventDefault，
    链接会点了没反应 —— 比「点了会下载」更糟。
    """
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "getCurrentInstance()?.vnode.props?.onPreviewGeneratedFile" in source
    # 预览分支必须以此为前提
    assert "plan.kind !== 'download-only' && canPreviewGeneratedFile" in source


def test_embed_chat_wires_message_body_artifact_preview_to_the_canvas():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert '@preview-generated-file="previewGeneratedFileInCanvas"' in source

    start = source.index("const previewGeneratedFileInCanvas")
    body = source[start : source.index("};", start)]
    # 与抽屉共用同一条链路：绑当前 Host → 取内容/URL → 右侧钉住画布
    assert "handleGeneratedFilePreview({" in body
    assert "resolveGeneratedFileHref(payload.url)" in body
    assert "payload.name" in body


def test_workspace_canvas_normalizing_title_keeps_document_meta():
    """`normalizeDirectPayloadTitle` 分支不能把 `documentMeta` 吃掉。

    回归背景：AgentDebug 配了 `normalizeDirectPayloadTitle: true`，而该分支是**白名单式**重建
    对象（type/title/content/langName/runnable），白名单里漏了 `documentMeta` —— Office 预览
    正是靠它（filename + mime）选渲染链路，传不到就预览不出来。

    注意修法是**往白名单里补字段**，而不是改成 `...payload`：既有测试
    （`test_workspace_canvas_keeps_workspace_toggle_and_debug_title_normalization`）固化了
    「normalize 后不夹带 sourcePath 等上下文」的语义。
    """
    source = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")

    start = source.index("canvasData.value = options.normalizeDirectPayloadTitle")
    body = source[start : source.index(": payload;", start)]

    assert "documentMeta" in body, "documentMeta 必须进白名单，否则调试页 Office 预览拿不到 mime"
    assert "title: payload.title || " in body, "只对 title 做缺省兜底"
    assert "content: payload.content" in body, "既有白名单字段保持不动"


def test_generated_file_href_pattern_accepts_token_after_other_query_params(tmp_path):
    """产物链接正则要兼容 token 不在 query 首位（下载按钮会追加 `download=1`）。

    这里把 .vue 里的真实正则字面量抽出来跑真实用例 —— 不是"源码里写了什么"，而是"它匹配什么"。
    """
    source = _source("frontend/src/components/MessageRenderer.vue")
    pattern_line = next(
        line for line in source.splitlines() if line.strip().startswith("const GENERATED_FILE_HREF_PATTERN")
    )
    literal = pattern_line.split("=", 1)[1].strip().rstrip(";")

    script = tmp_path / "generated_file_pattern.mjs"
    script.write_text(
        f"""
const PATTERN = {literal};
const id = 'a'.repeat(32);
const base = '/api/v1/chat/generated-files/' + id;
const cases = [
  [base + '?token=abc', true],
  [base + '?token=abc&download=1', true],
  ['http://localhost:8001' + base + '?token=abc', true],
  // 放宽点：token 不是第一个参数时也要认出来，否则产物链接会被降级成普通下载
  [base + '?download=1&token=abc', true],
  [base, false],
  [base + '?token=', false],
  ['/api/v1/chat/generated-files/nothex?token=abc', false],
  ['/api/v1/chat/fs/preview?path=/docs/a.docx', false],
];
const failed = cases.filter(([url, expected]) => PATTERN.test(url) !== expected);
if (failed.length) {{
  console.error('未通过:', JSON.stringify(failed));
  process.exit(1);
}}
// 防贪婪：匹配不得跨过标签边界。
// 注意第二个链接要用 `?d=1&token=` 形式 —— 贪婪写法（`\\?.*&token=`）只有在 token 前面
// 存在 `&` 时才会"跨过 </a> 去吃别人的 token"，用 `?token=` 构造的话它根本跨不过来，测不出来。
const crossTag = '<a href="' + base + '?x=1">A</a><a href="' + base + '?d=1&token=zzz">B</a>';
const crossed = PATTERN.exec(crossTag);
if (!crossed) {{
  console.error('跨标签场景没有匹配到第二个链接的 token');
  process.exit(1);
}}
if (crossed[0].includes('</a>') || crossed[0].includes('?x=1')) {{
  console.error('匹配跨过了标签边界:', crossed[0]);
  process.exit(1);
}}
console.log('产物链接正则用例全部通过');
""",
        encoding="utf-8",
    )
    node = subprocess.run(["node", str(script)], cwd=ROOT, capture_output=True, text=True)
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"


def test_agent_debug_wires_message_body_artifact_preview_to_its_canvas():
    """调试页用的是同一份 MessageRenderer，同样要接正文产物预览（否则回落成点击下载）。"""
    source = _source("frontend/src/views/AgentDebug.vue")

    assert "handleGeneratedFilePreview" in source
    start = source.index("const previewGeneratedFileInCanvas")
    body = source[start : source.index("};", start)]
    assert "handleGeneratedFilePreview({" in body
    assert "resolveGeneratedFileHref(payload.url)" in body
    # 每个画布接线点都要接上预览事件，避免"有的消息能预览、有的只能下载"
    assert source.count('@preview-generated-file="previewGeneratedFileInCanvas"') == source.count(
        '@open-canvas="handleOpenCanvas"'
    )
