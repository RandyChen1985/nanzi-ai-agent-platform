"""智能体「复制」能力的前后端契约测试。

锁定关键实现片段，防止后续改动把复制入口、权限约束、
系统智能体可复制的规则或弹窗预填逻辑删掉。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW_PATH = "frontend/src/views/AgentManagement.vue"
API_PATH = "frontend/src/api/agent.ts"
BACKEND_PATH = "app/api/portal/endpoints/agents.py"
SERVICE_PATH = "app/services/ai/agent_manager.py"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _template(view: str) -> str:
    """只取 <template> 部分。

    本文件的 <script setup> 在 <template> 之前，直接在全文里找中文片段会先命中
    script 中的注释，导致按钮定位错位。
    """
    return view[view.index("<template>"):]


def _button_tag_before(source: str, marker: str, occurrence: int = 0) -> str:
    """返回第 occurrence 次出现 marker 之前、最近一个 <button 到 marker 之间的文本。"""
    index = -1
    for _ in range(occurrence + 1):
        index = source.index(marker, index + 1)
    start = source.rindex("<button", 0, index)
    return source[start:index]


def test_card_menu_offers_duplicate():
    view = _source(VIEW_PATH)
    tpl = _template(view)

    assert "复制智能体" in tpl
    assert "openDuplicateModal" in tpl

    button_tag = _button_tag_before(tpl, "复制智能体")
    assert "v-has-perm=\"'element:agent:create'\"" in button_tag
    # 复制不修改源智能体，因此不得受 is_editable 限制——系统智能体也要能复制
    assert "is_editable" not in button_tag


def test_compact_row_also_offers_duplicate():
    view = _source(VIEW_PATH)
    tpl = _template(view)

    # 紧凑列表视图的复制按钮（图标按钮，title 标注）
    assert 'title="复制智能体"' in tpl
    button_tag = _button_tag_before(tpl, 'title="复制智能体"')
    assert "v-has-perm=\"'element:agent:create'\"" in button_tag
    assert "is_editable" not in button_tag


def test_duplicate_modal_collects_name_and_display_name():
    view = _source(VIEW_PATH)
    tpl = _template(view)

    assert "showDuplicateModal" in tpl
    assert "duplicateForm.name" in tpl
    assert "duplicateForm.display_name" in tpl

    # 预填规则：标识符加 -copy，显示名加 -副本
    assert "`${agent.name}-copy`" in view
    assert "`${agent.display_name}-副本`" in view

    # 弹窗必须说明会复制已发布版本配置且副本立即可用
    assert "已发布版本" in tpl
    assert "立即可用" in tpl
    assert "源智能体不受任何影响" in tpl


def test_duplicate_submit_flow_is_guarded():
    view = _source(VIEW_PATH)
    tpl = _template(view)

    assert "submitDuplicate" in tpl
    assert "duplicating" in tpl
    assert "duplicateError" in tpl
    # 空值就地拦截，不发无效请求
    assert "物理标识符与显示名称都不能为空" in view
    # 后端错误（如标识符重名）要落到 duplicateError 展示
    catch_block = view.split("const submitDuplicate = async () => {", 1)[1].split(
        "const startAgentCreation", 1
    )[0]
    assert "duplicateError.value =" in catch_block


def test_duplicate_api_wired_to_backend_endpoint():
    api = _source(API_PATH)
    assert "duplicateAgent" in api
    assert "/api/portal/agents/${id}/duplicate" in api

    backend = _source(BACKEND_PATH)
    assert '"/{agent_id}/duplicate"' in backend
    assert "element:agent:create" in backend

    handler = backend.split("async def duplicate_agent", 1)[1]
    assert "AgentManagerService.duplicate_agent" in handler
    assert "404" in handler


def test_service_enforces_duplicate_invariants():
    service = _source(SERVICE_PATH)
    body = service.split("async def duplicate_agent", 1)[1].split(
        "async def _pick_duplicate_source_version", 1
    )[0]

    # 副本永远是普通智能体、默认启用，且头像直接复用而非转移待绑定文件
    assert "is_system=False" in body
    assert "is_enabled=True" in body
    assert "avatar_url=source.avatar_url" in body
    assert "adopt_pending_avatar" in body  # 注释说明为何不走该函数
    assert "status=\"PUBLISHED\"" in body
    assert "version_number=1" in body
    # 物理标识符唯一性复用创建路径的判定
    assert "find_agent_name_conflict" in body


def test_source_version_picker_prefers_published():
    service = _source(SERVICE_PATH)
    picker = service.split("async def _pick_duplicate_source_version", 1)[1]

    assert 'AIAgentVersion.status == "PUBLISHED"' in picker
    assert "version_number.desc()" in picker
