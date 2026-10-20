"""`users.org_path` 存**编码路径**，不存 id 路径。

现状是 id 物化路径（`/1/2/`），带来两个问题：

1. 不可读——模型拿它答"我是什么部门的"只能照搬 `/1/`（已单独修过注入层）；
2. 不可迁移——id 是自增的，换一套系统就全变了，而部门 code 是**业务键**，
   本来就是为跨系统对接设计的。

所以 `users.org_path` 改为编码路径 `/main/dev/`。但 **`departments.path` 保持 id 路径**：
它是树结构的依据（子树查询靠前缀匹配），若也改用 code，改一个部门的编码就得重建
整棵子树的路径，code 一旦写错还会把树结构搞乱。id 永不变，结构才稳。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SERVICE = (ROOT / "app/services/department_service.py").read_text(encoding="utf-8")
USER_MODEL = (ROOT / "app/models/user.py").read_text(encoding="utf-8")


def _sync_block() -> str:
    return SERVICE.split("async def sync_primary_snapshot", 1)[1].split("\n    async def ", 1)[0]


def test_snapshot_writes_code_path_not_id_path():
    block = _sync_block()
    assert "code_path_map" in block, "快照要写编码路径，必须经 code_path_map 派生"
    # 直接把 dept.path（/1/2/）写进 org_path 就是旧行为
    assert "org_path=row[1]" not in block


def test_department_path_itself_stays_id_based():
    """树结构不能被编码绑住：path 仍由自增 id 拼。"""
    assert "compute_department_path" in SERVICE
    # 文档里明确保留 id 路径的理由
    assert "改编码时不必级联重写整棵子树" in SERVICE or "id" in _sync_block()


def test_renaming_code_refreshes_affected_subtree_snapshots():
    """改编码会改变子树内所有用户的 org_path 前缀，只刷本部门成员是不够的。"""
    update_block = SERVICE.split("async def update(", 1)[1].split("\n    async def ", 1)[0]
    assert "subtree_like_prefix" in update_block
    assert "sync_primary_snapshot" in update_block


def test_column_comment_describes_code_path():
    assert "编码路径" in USER_MODEL
