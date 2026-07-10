import pytest
from pydantic import ValidationError


pytestmark = pytest.mark.no_infrastructure


def test_permission_update_normalizes_forbidden_policy_entries():
    from app.schemas.permission import PermissionUpdate

    update = PermissionUpdate(
        forbidden_tools=[" exec_command ", "exec_command", "Bash"],
        forbidden_commands=[" rm ", "RM", "shutdown"],
    )

    assert update.forbidden_tools == ["exec_command", "Bash"]
    assert update.forbidden_commands == ["rm", "shutdown"]


def test_permission_update_rejects_policy_entries_longer_than_storage_limit():
    from app.schemas.permission import PermissionUpdate

    with pytest.raises(ValidationError):
        PermissionUpdate(forbidden_commands=["x" * 101])
