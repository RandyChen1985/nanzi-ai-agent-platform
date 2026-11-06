"""用户手选的数据集被智能体绑定覆盖时，必须让用户看得见。

原来只有一条 `logger.warning`：用户明明在输入框选了数据集，结果查的是智能体绑定的
另一批，界面上没有任何提示。这里锁住发布链路：装配阶段算差异 → execution_step
随 meta 事件下发 → 前端渲染琥珀色提示条。
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[2]
_ASSEMBLE = (_REPO / "app/services/ai/pipeline/steps/assemble_step.py").read_text(encoding="utf-8")
_EXECUTION = (_REPO / "app/services/ai/pipeline/steps/execution_step.py").read_text(encoding="utf-8")
_EMBED_CHAT = (_REPO / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def test_assemble_step_detects_override() -> None:
    """装配阶段读工具级绑定，与手选范围比对后写入 shared_state。"""
    assert 'shared_state["metadata_scope_override"]' in _ASSEMBLE
    assert '"get_dataset_schema"' in _ASSEMBLE
    assert "normalize_bound_dataset_ids" in _ASSEMBLE
    # 只在两边都有值且不一致时才提示（绑定优先是既有语义，不改行为）
    assert "set(_bound_metadata_ids) != set(_mounted_metadata_ids)" in _ASSEMBLE


def test_execution_step_emits_notice_in_meta_event() -> None:
    """提示随 meta 事件下发，前端才知道要渲染。"""
    assert '"metadata_scope_notice"' in _EXECUTION
    assert "_scope_override" in _EXECUTION
    assert "已被该智能体绑定的数据集覆盖" in _EXECUTION


def test_frontend_renders_scope_notice() -> None:
    """前端两条 meta 分支都要接，且渲染成可见的提示条。"""
    # 两条流处理分支（普通流 + 续写/恢复）都要接
    assert "msg.metadataScopeNotice = data.metadata_scope_notice" in _EMBED_CHAT
    assert "agentMsg.value.metadataScopeNotice = data.metadata_scope_notice" in _EMBED_CHAT
    assert "metadataScopeNotice?:" in _EMBED_CHAT
    assert 'v-if="msg.metadataScopeNotice"' in _EMBED_CHAT
    # 与绿色「已按权限过滤」区分开，用琥珀色警示
    assert "border-amber-200 bg-amber-50/80" in _EMBED_CHAT
