"""契约：生成产物的分享与保存，凭 artifact_id 走通，不再要求「先保存」。

背景（线上反馈）：Qwen-Image 出图后打开画布，`⋯ → 分享文件` 一直是灰的、写着
「需先保存」。但那张图其实早就在工作区里了：

    data/agent_workspaces/admin__1/generated/
      26399a7a80ed41e58b2431a2ca949a34_generated-image-20261121-004916-1-7f3ecd.png

链路是这样断的：`publish()` 把图复制进 `<user_key>/generated/`，`register_artifact`
只登记元信息、不移动文件；但 `_publish_image` 交给模型/前端的 payload 只有
`filename` / `download_url`，`PublishedArtifact` 里压根没有 `storage_path`。模型按
提示写成 `![描述](download_url)`，前端渲染出 `<img src=...>`，点开画布时
`MessageRenderer` 只传 `{ type: 'image', content: src }`——于是 `effectiveSourcePath`
为空，`canShareFile` 为 false。

现在改成：前端从 download_url 抠出 artifact_id，后端凭它反查 `ai_artifacts.storage_path`，
所有权与目录边界照旧校验。同一份凭据也让「保存到目录」从「取 blob 再上传」变成真正的移动。

这些测试锁的是接线：任何一环退回旧写法，分享又会静默变灰。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANVAS = ROOT / "frontend/src/components/embed/ChatCanvas.vue"
DIALOG = ROOT / "frontend/src/components/ShareFileDialog.vue"
API = ROOT / "frontend/src/api/fileShare.ts"
PARSER = ROOT / "frontend/src/utils/generatedArtifact.ts"
PREVIEW = ROOT / "frontend/src/utils/workspaceFilePreview.ts"
BACKEND_SERVICE = ROOT / "app/services/file_share_service.py"
BACKEND_RELOCATE = ROOT / "app/services/ai/tools/generated_file_service.py"
BACKEND_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canvas_derives_artifact_id_from_download_url():
    """画布必须从 content 里解析产物 ID，否则又回到「前端不知道路径」的老路。"""
    source = _read(CANVAS)
    assert "import { extractArtifactId } from '@/utils/generatedArtifact';" in source
    assert "const artifactRef = ref('');" in source
    # 初始化时同步解析：只写在 openShare 里会导致首次打开画布时门控仍是灰的
    sync_block = re.search(r"const syncEditorFromData = \(\) => \{(.+?)\n\};", source, re.S)
    assert sync_block, "找不到 syncEditorFromData"
    assert "artifactRef.value = extractArtifactId(" in sync_block.group(1)


def test_share_gate_accepts_artifact_without_source_path():
    """`canShareFile` 只看 sourcePath 就是这次的 bug 本身。"""
    source = _read(CANVAS)
    gate = re.search(r"const canShareFile = computed\((.+?)\);", source, re.S)
    assert gate, "找不到 canShareFile"
    body = gate.group(1)
    assert "effectiveSourcePath.value" in body
    assert "artifactRef.value" in body


def test_open_share_passes_artifact_id_through():
    source = _read(CANVAS)
    open_block = re.search(r"const openShare = \(\) => \{(.+?)\n\};", source, re.S)
    assert open_block, "找不到 openShare"
    body = open_block.group(1)
    # 空路径但有产物 ID 时必须继续，不能提前 return
    assert "if (!path && !artifactId)" in body
    assert "artifactId," in body, "shareTarget 必须带上 artifactId"
    # 弹窗挂载处要透传
    assert ':artifact-id="shareTarget?.artifactId || \'\'"' in source


def test_share_payload_carries_artifact_id_to_backend():
    assert "artifact_id?: string;" in _read(API)
    dialog = _read(DIALOG)
    assert "artifactId?: string" in dialog, "弹窗必须接受产物 ID"
    assert "artifact_id: props.artifactId || undefined," in dialog
    # 提交前校验要看两个来源，否则产物场景会被自己拦下
    assert "if (!props.filePath && !props.artifactId)" in dialog


def test_saving_image_artifact_moves_instead_of_reuploading():
    """产物已在 generated/ 里，保存到目录必须是移动，不能再复制一份。"""
    source = _read(CANVAS)
    save_block = re.search(r"const handleDirectorySave = async \((.+?)\n\};", source, re.S)
    assert save_block, "找不到 handleDirectorySave"
    body = save_block.group(1)
    assert "relocateGeneratedArtifact(" in body
    relocate_at = body.index("relocateGeneratedArtifact(")
    upload_at = body.find("uploadToWorkspaceDir(")
    assert upload_at > relocate_at, "迁址要排在回退上传之前"
    assert "if (artifactRef.value) {" in body


def test_image_overwrite_never_writes_text_into_the_png():
    """图片撞名时若走「写文本覆盖」，会把 PNG 写成一段文本——必须走迁址覆盖。"""
    source = _read(CANVAS)
    assert "kind: 'relocate'" in source
    confirm = re.search(r"const confirmOverwrite = async \(\) => \{(.+?)\n\};", source, re.S)
    assert confirm, "找不到 confirmOverwrite"
    body = confirm.group(1)
    assert "if (pending.kind === 'relocate')" in body
    assert "{ overwrite: true }" in body
    # 迁址分支必须在写内容之前 return 掉
    relocate_at = body.index("pending.kind === 'relocate'")
    write_at = body.index("saveWorkspaceFileContent(")
    assert relocate_at < write_at


def test_frontend_api_posts_to_the_relocate_endpoint():
    assert "/api/v1/chat/generated-files/${artifactId}/relocate" in _read(PREVIEW)


def test_backend_accepts_artifact_id_and_still_checks_ownership():
    service = _read(BACKEND_SERVICE)
    assert "artifact_id: Optional[str] = None," in service
    assert "_artifact_storage_path(" in service
    # 产物路径要过与普通路径同一道归属闸
    assert "assert_share_path_belongs_to_owner(workspace_key, owner_key)" in service
    # 查产物时必须带 owner 条件，不能只按 ID 查
    assert "AiArtifact.owner_user_id == owner_id" in service


def test_backend_relocate_keeps_moves_inside_owner_workspace():
    relocate = _read(BACKEND_RELOCATE)
    assert "async def relocate_artifact(" in relocate
    assert "shutil.move(" in relocate, "必须是移动而不是复制"
    assert "目标目录必须是本人工作区内的目录" in relocate
    assert "ArtifactRelocateConflict" in relocate
    endpoint = _read(BACKEND_ENDPOINT)
    assert '"/generated-files/{artifact_id}/relocate"' in endpoint
    assert "overwrite: bool = False" in endpoint


def test_artifact_url_parser_behaviour_via_node():
    """解析器真跑一遍：认错 ID 会把别人的产物做成分享，认不出就回到「需先保存」。"""
    proc = subprocess.run(
        ["node", "tests/frontend/generated_artifact.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "产物链接解析测试全部通过" in proc.stdout
