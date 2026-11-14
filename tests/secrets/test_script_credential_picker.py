from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.portal.endpoints import secrets


def row(identifier, *, structured=False, scenes=None):
    return SimpleNamespace(
        id=identifier, owner_scope="user", owner_id="101", secret_key=identifier,
        name=identifier, secret_type="secret", category=None, status="active",
        allowed_scenarios=scenes or ["script_env"], value_preview="must-not-return",
        credential_format="structured_v1" if structured else "legacy",
    )


@pytest.mark.asyncio
async def test_script_picker_includes_structured_metadata_without_payload(monkeypatch):
    listing = AsyncMock(return_value=[row("scalar"), row("mail", structured=True), row("api", scenes=["api_tool"])])
    monkeypatch.setattr(secrets.center, "list_visible_secrets", listing)
    monkeypatch.setattr(secrets, "_structured_metadata", AsyncMock(return_value={"mail": {
        "type_key": "mail", "type_version": 1, "account": "alice",
        "secret_fields_set": ["password"], "extra_fields": {}, "value_revision": 2,
    }}))
    result = await secrets.picker(type_key=None, type_version=None, scene="script_env", user_info={"user_id": "101"}, db=object())
    assert listing.call_args.kwargs["legacy_only"] is False
    items = result["data"]["groups"]["mine"]
    assert [item["id"] for item in items] == ["scalar", "mail"]
    assert items[1]["type_key"] == "mail" and items[1]["type_version"] == 1
    assert items[1]["credential_format"] == "structured_v1"
    assert items[1]["owner_scope"] == "user"
    assert "must-not-return" not in str(result)


@pytest.mark.asyncio
async def test_default_picker_remains_legacy_only(monkeypatch):
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(secrets.center, "list_visible_secrets", listing)
    metadata = AsyncMock()
    monkeypatch.setattr(secrets, "_structured_metadata", metadata)
    await secrets.picker(type_key=None, type_version=None, scene=None, user_info={"user_id": "101"}, db=object())
    assert listing.call_args.kwargs["legacy_only"] is True
    metadata.assert_not_awaited()
