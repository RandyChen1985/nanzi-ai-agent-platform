from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_types_are_defined_in_single_source():
    source = _source("frontend/src/types/canvas.ts")

    assert "export type WorkspaceCanvasType" in source
    assert "'document'" in source
    assert "export interface CanvasDocumentMeta" in source
    assert "export interface CanvasPanelData" in source
    assert "sourcePath?: string" in source
    assert "documentMeta?: CanvasDocumentMeta" in source
    assert "downloadPath?: string" in source


def test_canvas_consumers_import_types_instead_of_redeclaring():
    consumers = {
        "ChatCanvas.vue": "frontend/src/components/embed/ChatCanvas.vue",
        "useWorkspaceCanvas.ts": "frontend/src/composables/chat/useWorkspaceCanvas.ts",
        "workspaceFilePreview.ts": "frontend/src/utils/workspaceFilePreview.ts",
        "MessageRenderer.vue": "frontend/src/components/MessageRenderer.vue",
    }

    for label, path in consumers.items():
        source = _source(path)
        assert (
            "from '@/types/canvas'" in source or 'from "@/types/canvas"' in source
        ), label
