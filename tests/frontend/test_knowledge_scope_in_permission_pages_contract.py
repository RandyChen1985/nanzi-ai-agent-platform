"""用户/角色的权限配置页：知识库必须显示可见范围，且个人私有不可勾选。

背景：知识库「可见范围」落地三档后，个人私有库在判定侧会被直接跳过——
授权给它永远不会生效。但用户/角色的权限配置页仍把私有库列成可勾选项，
管理员勾了半天、对方依然看不到。这里锁住三件事：
1. 每项知识库都显示「授权可见 / TEAM 级 / 个人私有」标签；
2. 私有项禁用勾选并说明原因；
3. 回显与保存都剔除私有库 id，不留下永不生效的授权记录。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"
ROLES = ROOT / "frontend/src/views/Roles.vue"
PERMISSION_LISTS = ROOT / "frontend/src/utils/permissionLists.ts"
PERMISSION_SERVICE = ROOT / "app/services/permission_service.py"


def _read(path: Path) -> str:
    assert path.exists(), f"{path} 不存在"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [USERS, ROLES])
def test_knowledge_scope_badge_covers_three_scopes(path):
    page = _read(path)
    # 三档标签与知识库页口径一致，避免同一档位在两处叫不同名字
    assert "KNOWLEDGE_SCOPE_BADGE" in page
    assert "授权可见" in page
    assert "TEAM 级" in page
    assert "个人私有" in page
    # 徽标只作用于知识库页签，不能污染智能体/模型列表
    assert "activeResTab === 'datasets'" in page or 'activeResTab === "datasets"' in page


@pytest.mark.parametrize("path", [USERS, ROLES])
def test_private_knowledge_base_is_not_selectable(path):
    page = _read(path)
    assert "isPrivateKnowledgeBase" in page
    # 勾选框必须被「个人私有」禁用。
    # 失联库另有一套规则（已授权的允许取消），见 test_missing_dataset_toggle_contract.py
    assert "isPrivateKnowledgeBase(res) ||" in page
    assert "!isSelectedDatasetResource(res)" in page
    # 并且要说明为什么不能选，而不是默默禁用
    assert "个人私有知识库不参与授权" in page


@pytest.mark.parametrize("path", [USERS, ROLES])
def test_private_dataset_ids_are_stripped_on_echo_and_save(path):
    page = _read(path)
    assert "collectPrivateDatasetIds" in page
    # 至少要出现两次：一次用于回显剔除，一次用于保存剔除
    assert page.count("collectPrivateDatasetIds") >= 3  # 定义 1 次 + 使用 2 次


def test_payload_builder_supports_excluded_ids():
    utils = _read(PERMISSION_LISTS)
    assert "excludedDatasetIds" in utils
    # 私有库与失联库走同一条过滤通道，不要各写一套
    assert "excluded.has(id)" in utils


def test_backend_write_path_drops_private_datasets():
    service = _read(PERMISSION_SERVICE)
    assert "_exclude_private_datasets" in service
    # 用户与角色两条写入路径都要过滤
    assert service.count("await self._exclude_private_datasets(updates.datasets)") == 2
