"""权属人变更入口的契约：谁能看到、谁看不到。

背景很具体——所有者离职或转岗后，这个智能体既没人能编辑、也没人能发布，
只有管理员能碰。所以必须有一个「换个人」的入口，否则只能靠改库。

锁三件事：

1. 管理员能看到「变更权属人」，普通用户看不到（这是高风险管理动作，
   不是普通编辑权限）；
2. **主助手没有这个入口**——它是系统固定入口，不存在「归谁」的概念；
3. 变更要能落进发布历史（事件动作 TRANSFER_OWNER 有中文文案），
   否则事后没人说得清什么时候、因为什么换了人。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend" / "src" / "views" / "AgentManagement.vue"
MODAL = ROOT / "frontend" / "src" / "components" / "agent" / "AgentTransferOwnerModal.vue"
HISTORY = ROOT / "frontend" / "src" / "components" / "agent" / "AgentSystemHistoryDrawer.vue"
API = ROOT / "frontend" / "src" / "api" / "agent.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_transfer_entry_is_admin_only_and_not_for_main_agent():
    src = _read(MANAGEMENT)

    assert "const canTransferOwner" in src, "缺少权属人变更的可见性判定"
    gate = src.split("const canTransferOwner", 1)[1].split("}", 1)[0]
    assert "isAdminViewer" in gate, "变更权属人必须是管理员专属"
    assert "isMainAgent" in gate, "主助手不允许变更权属人"

    # 菜单项真的渲染出来，并且绑定到打开弹窗
    assert 'v-if="canTransferOwner(agent)"' in src, "菜单项没有接入可见性判定"
    assert "openTransferOwner(agent)" in src, "菜单项没有绑定打开动作"
    assert ">变更权属人<" in src or "变更权属人\n" in src, "菜单文案缺失"


def test_transfer_modal_collects_owner_and_reason():
    src = _read(MODAL)

    assert "agentSystemApi.transferOwner" in src, "弹窗没有调用转交接口"
    assert "new_owner" in src, "请求体缺少新权属人"
    assert "reason" in src, "缺少变更原因（会记入发布历史）"

    assert "当前权属人" in src, "应标出谁现在是权属人，避免选到同一个人"
    # 候选人走专用接口，不依赖「用户管理」菜单权限——
    # 复用那套接口时，普通管理员会看到空列表，还以为是查不到人
    assert "transferOwnerCandidates" in src, "候选人应走智能体自己的候选接口"
    assert "getUsers" not in src, "不应依赖用户管理列表接口"
    # 空列表要把失败原因说出来，而不是笼统显示「没有匹配的启用用户」
    assert "errorMessage || " in src, "加载失败时应显示具体原因"


def test_transfer_event_has_human_readable_label():
    src = _read(HISTORY)
    assert "TRANSFER_OWNER" in src, "发布历史缺少权属人变更的文案映射"
    label_line = [line for line in src.splitlines() if "TRANSFER_OWNER" in line][0]
    assert "变更权属人" in label_line, "历史里应显示「变更权属人」而不是原始动作码"


def test_transfer_api_hits_dedicated_endpoint():
    src = _read(API)
    assert "/transfer-owner" in src, "前端应调用专用的转交接口"
    assert "transferOwner:" in src, "agentSystemApi 缺少 transferOwner 封装"


def test_transfer_surfaces_tools_the_new_owner_cannot_use():
    """转交后必须把「新权属人用不了的工具」说出来。

    后端会按新权属人的视角体检一遍并按 ``dropped_tool_refs`` 回带。界面如果只是
    弹一句「转交成功」，新同事接手后只会看到「配置都在、就是跑不起来」——同一类
    问题在新建套模板时已经出现过一次。
    """
    modal = _read(MODAL)
    management = _read(MANAGEMENT)

    assert "dropped_tool_refs" in modal, "弹窗应把体检结果带回给上层"
    assert "emit('done'," in modal, "done 事件需要携带被剔除的引用清单"

    assert "dropped" in management and "无权使用" in management, \
        "转交成功后应提示新权属人有工具不可用"


def test_orphan_owner_filter_and_banner_exist():
    """管理员要能主动发现「权属人已停用」的智能体，而不是靠人想起来。"""
    src = _read(MANAGEMENT)

    assert 'const ownerFilter' in src and 'ownerFilter.value === "orphan"' in src, \
        "缺少权属人筛选逻辑"
    assert '权属人已停用' in src, "筛选下拉/提示条要有可读文案"
    assert "orphanAgents" in src, "缺少孤儿智能体统计"
    # 提示条只给管理员看：普通用户即使看到也没法处理
    banner = src.split("orphanAgents.length", 1)[0]
    assert "isAdminViewer" in banner, "孤儿提示条应仅对管理员展示"
