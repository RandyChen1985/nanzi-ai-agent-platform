"""智能体检索知识库时的作用域守卫。

智能体绑定的库会跳过使用者权限校验（作者决定了这个智能体的知识范围），
但 `private` 不能被代理放行 —— 否则「A 被授权过某库」就能靠建智能体把它
二次分发给全体使用者。这组测试锁住那条收紧逻辑的位置与口径。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_TOOL = ROOT / "app/services/ai/tools/knowledge_tool.py"
PERMISSION_SERVICE = ROOT / "app/services/permission_service.py"
MCP_SERVICE = ROOT / "app/api/portal/endpoints/mcp_service.py"
PLATFORM_MCP = ROOT / "app/services/mcp/platform_mcp.py"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_private_datasets_are_stripped_before_agent_bound_bypass():
    """必须在算「智能体绑定放行集合」时就摘掉私有库，而不是事后再判断。"""
    src = _read(KNOWLEDGE_TOOL)
    assert "find_private_datasets_not_owned" in src

    strip_at = src.index("find_private_datasets_not_owned")
    restricted_at = src.index("restricted_datasets = [")
    assert strip_at < restricted_at, "摘私有库必须发生在 restricted_datasets 计算之前"

    # 摘完之后仍然走「智能体绑定 ∪ 用户可读」的并集，语义不变
    assert "dataset_id in agent_granted_ids or dataset_id in allowed_restricted_set" in src


def test_permission_service_exposes_the_guard_with_creator_exemption():
    src = _read(PERMISSION_SERVICE)
    assert "async def find_private_datasets_not_owned" in src
    # 创建人本人必须被豁免，否则作者检索自己绑定的私有库也会被拦
    assert "str(created_by) == str(user_name)" in src
    # 只针对 private：公有/部门库的判定不能在这里重复实现
    assert "!= VISIBILITY_PRIVATE" in src


def test_mcp_knowledge_search_needs_no_extra_guard():
    """MCP 的检索范围本来就只取用户权限，不存在智能体绑定放行，不需要这层。"""
    mcp = _read(MCP_SERVICE)
    platform = _read(PLATFORM_MCP)
    assert "find_private_datasets_not_owned" not in mcp
    assert "find_private_datasets_not_owned" not in platform
    # 它们直接按用户权限取交集
    assert "get_knowledge_base_access" in platform
    assert "intersect_authorized_ids" in platform
