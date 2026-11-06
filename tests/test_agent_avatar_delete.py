"""删除智能体时只清理它自己的头像文件。"""
import pytest

from app.core import avatar_assets

pytestmark = pytest.mark.no_infrastructure


def test_delete_agent_avatars_only_removes_own_prefix(tmp_path):
    (tmp_path / "agent-1_1.png").write_bytes(b"x")
    (tmp_path / "agent-10_1.png").write_bytes(b"x")
    (tmp_path / "pending_1.png").write_bytes(b"x")

    removed = avatar_assets.delete_agent_avatars("agent-1", str(tmp_path))

    assert removed == 1
    assert not (tmp_path / "agent-1_1.png").exists()
    assert (tmp_path / "agent-10_1.png").exists()
    assert (tmp_path / "pending_1.png").exists()
