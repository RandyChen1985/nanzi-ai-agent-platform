import pytest

pytestmark = pytest.mark.no_infrastructure


def test_schema_validation_rejects_references_and_secret_defaults():
    from app.services.secrets.credential_schema import CredentialSchemaError, validate_schema

    invalid = [
        {"type": "object", "properties": {"x": {"$ref": "https://example.test/schema"}}},
        {"type": "object", "properties": {"password": {"type": "string", "default": "fixture"}}},
        {"type": "object", "properties": {"credential_account": {"type": "string"}}},
    ]
    for schema in invalid:
        with pytest.raises(CredentialSchemaError):
            validate_schema(schema)


def test_schema_uses_strict_types_and_derives_public_projection():
    from app.services.secrets.credential_schema import (
        BUILTIN_SCHEMAS, CredentialSchemaError, validate_payload,
    )

    schema = BUILTIN_SCHEMAS["imap_password"][1]
    prepared = validate_payload(schema, {"host": "mail.example.test", "username": "alice", "password": "fixture"})
    assert prepared.public == {"host": "mail.example.test", "username": "alice", "port": 993}
    assert prepared.secret_fields == ["password"]
    with pytest.raises(CredentialSchemaError, match="port"):
        validate_payload(schema, {"host": "mail.example.test", "username": "alice", "password": "fixture", "port": True})


def test_encrypted_payload_is_bound_to_credential_identity():
    from app.services.secrets.payload_crypto import PayloadCipher, PayloadCryptoError

    cipher = PayloadCipher({"old": bytes(32)}, "old")
    plaintext = {"password": "synthetic-only"}
    ciphertext = cipher.encrypt(plaintext, aad=["id-1", "user", "owner", "imap_password", 1, 1])
    assert "synthetic-only" not in ciphertext
    assert cipher.decrypt(ciphertext, aad=["id-1", "user", "owner", "imap_password", 1, 1]) == plaintext
    with pytest.raises(PayloadCryptoError):
        cipher.decrypt(ciphertext, aad=["id-2", "user", "owner", "imap_password", 1, 1])


def test_schema_validates_enum_text_arrays_and_nested_credentials():
    from app.services.secrets.credential_schema import CredentialSchemaError, validate_payload, validate_schema

    schema = {
        "type": "object",
        "x-icon": "☸️",
        "properties": {
            "region": {"type": "string", "enum": ["east", "west"], "default": "east"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "cluster": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "token": {"type": "string"},
                },
                "required": ["host", "token"],
            },
        },
        "required": ["labels", "cluster"],
    }
    normalized = validate_schema(schema)
    assert validate_schema(normalized) == normalized
    assert normalized["x-icon"] == "☸️"
    assert normalized["properties"]["cluster"]["properties"]["token"]["writeOnly"] is True
    values = {"labels": ["test"], "cluster": {"host": "cluster.example.test", "token": "synthetic-only"}}
    prepared = validate_payload(normalized, values)
    assert prepared.values == {**values, "region": "east"}
    assert prepared.public == {"labels": ["test"], "region": "east"}
    assert prepared.secret_fields == ["cluster"]

    for changes, path in [
        ({"region": "north"}, "region"),
        ({"labels": ["ok", 1]}, r"labels\[1\]"),
        ({"cluster": {"host": "cluster.example.test"}}, r"cluster\.token"),
        ({"cluster": {**values["cluster"], "unknown": "not declared"}}, "cluster"),
    ]:
        with pytest.raises(CredentialSchemaError, match=path):
            validate_payload(normalized, {**values, **changes})


@pytest.mark.parametrize("field", [
    {"type": "string", "enum": ["east", "east"]},
    {"type": "string", "enum": ["east"], "default": "west"},
    {"type": "string", "enum": ["east"], "writeOnly": True},
    {"type": "array", "items": {"$ref": "https://example.test/schema"}},
    {"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": True},
    {"type": "object", "properties": {"name": {"type": "string", "default": "metadata"}}, "writeOnly": True},
])
def test_schema_rejects_invalid_constraints_on_composite_fields(field):
    from app.services.secrets.credential_schema import CredentialSchemaError, validate_schema

    with pytest.raises(CredentialSchemaError):
        validate_schema({"type": "object", "properties": {"cluster": field}})


def test_nested_fields_share_the_existing_schema_field_limit():
    from app.services.secrets.credential_schema import CredentialSchemaError, validate_schema

    nested = {"type": "object", "properties": {f"item_{i}": {"type": "string"} for i in range(32)}}
    with pytest.raises(CredentialSchemaError, match="32"):
        validate_schema({"type": "object", "properties": {"cluster": nested}})
