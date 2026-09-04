import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.core.orm import get_db_session
from app.models.ai_model import AIModel
from app.core.dependencies import require_admin
import uuid

# Mock Admin User
@pytest.fixture
def admin_headers(admin_api_key):
    return {"X-API-Key": admin_api_key}

@pytest.mark.asyncio
async def test_model_management_flow(client: AsyncClient, admin_headers):
    # 1. Create Model
    model_id = f"test-gpt-v1-{uuid.uuid4().hex}"
    new_model = {
        "name": "Test GPT",
        "model_id": model_id,
        "provider": "openai",
        "type": "llm",
        "api_base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "is_active": True
    }
    
    response = await client.post("/api/portal/models", json=new_model, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test GPT"
    assert data["has_api_key"] == True
    assert "api_key" not in data
    model_db_id = data["id"]
    
    # 2. List Models
    response = await client.get("/api/portal/models", headers=admin_headers)
    assert response.status_code == 200
    models = response.json()
    assert len(models) >= 1
    found = next((m for m in models if m["id"] == model_db_id), None)
    assert found is not None
    assert found["model_id"] == model_id
    
    # 3. Update Model
    update_data = {"name": "Test GPT Updated"}
    response = await client.put(f"/api/portal/models/{model_db_id}", json=update_data, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Test GPT Updated"
    
    # 4. Filter by Type
    response = await client.get("/api/portal/models?type=llm", headers=admin_headers)
    assert response.status_code == 200
    llms = response.json()
    assert all(m["type"] == "llm" for m in llms)
    
    # 5. Delete Model (Physical Delete)
    response = await client.delete(f"/api/portal/models/{model_db_id}", headers=admin_headers)
    assert response.status_code == 200
    
    # Verify it's gone from list (since list filters is_active=True)
    response = await client.get("/api/portal/models", headers=admin_headers)
    models_after = response.json()
    assert not any(m["id"] == model_db_id for m in models_after)

    # Physical deletion must not leave a hidden row behind either.
    response = await client.get(
        "/api/portal/models?include_inactive=true",
        headers=admin_headers,
    )
    assert not any(m["id"] == model_db_id for m in response.json())


@pytest.mark.asyncio
async def test_model_management_persists_optional_token_limits(
    client: AsyncClient,
    admin_headers,
):
    model_id = f"token-limits-{uuid.uuid4().hex}"
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Token Limit Model",
            "model_id": model_id,
            "provider": "openai",
            "type": "llm",
            "context_size": 262144,
            "max_output_tokens": 65536,
            "api_key": "sk-token-limits-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["context_size"] == 262144
    assert response.json()["max_output_tokens"] == 65536

    updated = await client.put(
        f"/api/portal/models/{response.json()['id']}",
        json={"context_size": None, "max_output_tokens": 16384},
        headers=admin_headers,
    )

    assert updated.status_code == 200
    assert updated.json()["context_size"] is None
    assert updated.json()["max_output_tokens"] == 16384


@pytest.mark.asyncio
async def test_model_management_persists_temperature(
    client: AsyncClient,
    admin_headers,
):
    model_id = f"temperature-{uuid.uuid4().hex}"
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Temperature Model",
            "model_id": model_id,
            "provider": "openai",
            "type": "llm",
            "temperature": 0.35,
            "api_key": "sk-temperature-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["temperature"] == 0.35

    updated = await client.put(
        f"/api/portal/models/{response.json()['id']}",
        json={"temperature": 0.8},
        headers=admin_headers,
    )

    assert updated.status_code == 200
    assert updated.json()["temperature"] == 0.8


@pytest.mark.asyncio
async def test_model_management_defaults_thinking_configuration(
    client: AsyncClient,
    admin_headers,
):
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Default Thinking Config",
            "model_id": f"thinking-default-{uuid.uuid4().hex}",
            "provider": "openai",
            "type": "llm",
            "api_key": "sk-thinking-default",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["thinking_enable"] is False
    assert data["thinking_only"] is False
    assert data["allow_disable_thinking"] is True
    assert data["reasoning_effort"] is None
    assert data["supported_reasoning_efforts"] == ["none", "minimal", "low", "medium", "high", "xhigh"]


@pytest.mark.asyncio
async def test_model_management_persists_thinking_configuration(
    client: AsyncClient,
    admin_headers,
):
    payload = {
        "name": "Thinking Config",
        "model_id": f"thinking-round-trip-{uuid.uuid4().hex}",
        "provider": "openai",
        "type": "llm",
        "api_key": "sk-thinking-round-trip",
        "thinking_enable": True,
        "thinking_only": True,
        "allow_disable_thinking": False,
        "reasoning_effort": "high",
        "supported_reasoning_efforts": ["none", "minimal", "low", "medium", "high", "xhigh"],
    }

    created = await client.post(
        "/api/portal/models",
        json=payload,
        headers=admin_headers,
    )
    assert created.status_code == 200
    model_id = created.json()["id"]
    for field in (
        "thinking_enable",
        "thinking_only",
        "allow_disable_thinking",
        "reasoning_effort",
        "supported_reasoning_efforts",
    ):
        assert created.json()[field] == payload[field]

    updated = await client.put(
        f"/api/portal/models/{model_id}",
        json={"reasoning_effort": "xhigh"},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["reasoning_effort"] == "xhigh"
    assert updated.json()["supported_reasoning_efforts"] == payload["supported_reasoning_efforts"]
    assert updated.json()["thinking_enable"] is True
    assert updated.json()["thinking_only"] is True
    assert updated.json()["allow_disable_thinking"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thinking_config",
    [
        {"reasoning_effort": "unsupported"},
        {"supported_reasoning_efforts": []},
        {
            "reasoning_effort": "high",
            "supported_reasoning_efforts": ["low"],
        },
    ],
)
async def test_model_management_rejects_invalid_thinking_configuration(
    client: AsyncClient,
    admin_headers,
    thinking_config: dict,
):
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Invalid Thinking Config",
            "model_id": f"thinking-invalid-{uuid.uuid4().hex}",
            "provider": "openai",
            "type": "llm",
            "api_key": "sk-thinking-invalid",
            **thinking_config,
        },
        headers=admin_headers,
    )

    assert response.status_code in {400, 422}


@pytest.mark.asyncio
async def test_model_context_size_and_max_output_tokens_can_be_cleared(
    client: AsyncClient,
    admin_headers,
):
    """测试将输入上下文与输出上限修改为数字后再置空/传空字符串，能成功保存并重置为 None。"""
    model_id = f"test-token-clear-{uuid.uuid4().hex}"
    create_payload = {
        "name": "Token Clear Test Model",
        "model_id": model_id,
        "provider": "openai",
        "type": "llm",
        "api_key": "sk-test",
        "context_size": 65536,
        "max_output_tokens": 32768,
    }
    create_res = await client.post("/api/portal/models", json=create_payload, headers=admin_headers)
    assert create_res.status_code == 200
    created_id = create_res.json()["id"]
    assert create_res.json()["context_size"] == 65536
    assert create_res.json()["max_output_tokens"] == 32768

    # 1. 传 None 置空
    update_res1 = await client.put(
        f"/api/portal/models/{created_id}",
        json={"context_size": None, "max_output_tokens": None},
        headers=admin_headers,
    )
    assert update_res1.status_code == 200
    assert update_res1.json()["context_size"] is None
    assert update_res1.json()["max_output_tokens"] is None

    # 重新设置
    await client.put(
        f"/api/portal/models/{created_id}",
        json={"context_size": 131072, "max_output_tokens": 16384},
        headers=admin_headers,
    )

    # 2. 模拟前端输入框清空后传空字符串 "" 或 0 容错清洗置空
    update_res2 = await client.put(
        f"/api/portal/models/{created_id}",
        json={"context_size": "", "max_output_tokens": ""},
        headers=admin_headers,
    )
    assert update_res2.status_code == 200
    assert update_res2.json()["context_size"] is None
    assert update_res2.json()["max_output_tokens"] is None



@pytest.mark.asyncio
async def test_model_id_must_be_globally_unique_on_create(client: AsyncClient, admin_headers):
    model_id = f"unique-create-{uuid.uuid4().hex}"
    payload = {
        "name": "Unique Model A",
        "model_id": model_id,
        "provider": "openai",
        "type": "llm",
        "api_key": "sk-unique-a",
    }

    first = await client.post("/api/portal/models", json=payload, headers=admin_headers)
    assert first.status_code == 200

    duplicate = await client.post(
        "/api/portal/models",
        json={**payload, "name": "Unique Model B"},
        headers=admin_headers,
    )
    assert duplicate.status_code == 409
    assert "model_id" in duplicate.json()["message"]


@pytest.mark.asyncio
async def test_model_id_must_be_globally_unique_on_update(client: AsyncClient, admin_headers):
    first_id = f"unique-update-a-{uuid.uuid4().hex}"
    second_id = f"unique-update-b-{uuid.uuid4().hex}"

    created = []
    for name, model_id in (("Update Model A", first_id), ("Update Model B", second_id)):
        response = await client.post(
            "/api/portal/models",
            json={
                "name": name,
                "model_id": model_id,
                "provider": "openai",
                "type": "llm",
                "api_key": "sk-update-test",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        created.append(response.json()["id"])

    duplicate = await client.put(
        f"/api/portal/models/{created[1]}",
        json={"model_id": first_id},
        headers=admin_headers,
    )
    assert duplicate.status_code == 409
    assert "model_id" in duplicate.json()["message"]


@pytest.mark.asyncio
async def test_model_api_key_is_encrypted_at_rest(client: AsyncClient, admin_headers, db_session):
    plaintext = f"sk-encrypt-{uuid.uuid4().hex}"
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Encrypted Model",
            "model_id": f"encrypted-{uuid.uuid4().hex}",
            "provider": "openai",
            "type": "llm",
            "api_key": plaintext,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200

    model = await db_session.get(AIModel, response.json()["id"])
    assert model is not None
    assert model.api_key != plaintext

    from app.utils.model_credentials import decrypt_model_api_key

    assert model.api_key.startswith("modelkey:v1:")
    assert decrypt_model_api_key(model.api_key) == plaintext


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("provider", "anthropic"), ("type", "unknown")])
async def test_model_api_rejects_unsupported_provider_or_type(
    client: AsyncClient,
    admin_headers,
    field: str,
    value: str,
):
    payload = {
        "name": "Invalid Model",
        "model_id": f"invalid-{uuid.uuid4().hex}",
        "provider": "openai",
        "type": "llm",
        "api_key": "sk-invalid",
    }
    payload[field] = value

    response = await client.post("/api/portal/models", json=payload, headers=admin_headers)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_base_url"),
    [
        ("deepseek", "https://api.deepseek.com"),
        ("kimi", "https://api.moonshot.cn/v1"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
        ("siliconflow", "https://api.siliconflow.cn/v1"),
        ("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ],
)
async def test_model_provider_fills_default_api_base_url(
    client: AsyncClient,
    admin_headers,
    provider: str,
    expected_base_url: str,
):
    response = await client.post(
        "/api/portal/models",
        json={
            "name": f"{provider} default",
            "model_id": f"{provider}-default-{uuid.uuid4().hex}",
            "provider": provider,
            "type": "llm",
            "api_key": "sk-default-url-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["api_base_url"] == expected_base_url


@pytest.mark.asyncio
async def test_model_provider_keeps_custom_api_base_url(
    client: AsyncClient,
    admin_headers,
):
    custom_url = "https://llm-gateway.example.com/v1"
    response = await client.post(
        "/api/portal/models",
        json={
            "name": "Custom DeepSeek Gateway",
            "model_id": f"deepseek-custom-{uuid.uuid4().hex}",
            "provider": "deepseek",
            "type": "llm",
            "api_base_url": custom_url,
            "api_key": "sk-custom-url-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["api_base_url"] == custom_url


@pytest.mark.asyncio
async def test_model_provider_change_updates_preset_but_preserves_custom_url(
    client: AsyncClient,
    admin_headers,
):
    preset_model = await client.post(
        "/api/portal/models",
        json={
            "name": "Preset Provider Model",
            "model_id": f"preset-provider-{uuid.uuid4().hex}",
            "provider": "deepseek",
            "type": "llm",
            "api_key": "sk-provider-change-test",
        },
        headers=admin_headers,
    )
    assert preset_model.status_code == 200

    preset_update = await client.put(
        f"/api/portal/models/{preset_model.json()['id']}",
        json={"provider": "kimi"},
        headers=admin_headers,
    )
    assert preset_update.status_code == 200
    assert preset_update.json()["api_base_url"] == "https://api.moonshot.cn/v1"

    custom_url = "https://private-gateway.example.com/v1"
    custom_model = await client.post(
        "/api/portal/models",
        json={
            "name": "Custom Provider Model",
            "model_id": f"custom-provider-{uuid.uuid4().hex}",
            "provider": "deepseek",
            "type": "llm",
            "api_base_url": custom_url,
            "api_key": "sk-provider-custom-test",
        },
        headers=admin_headers,
    )
    assert custom_model.status_code == 200

    custom_update = await client.put(
        f"/api/portal/models/{custom_model.json()['id']}",
        json={"provider": "kimi"},
        headers=admin_headers,
    )
    assert custom_update.status_code == 200
    assert custom_update.json()["api_base_url"] == custom_url


@pytest.mark.asyncio
async def test_model_list_can_include_inactive_models(client: AsyncClient, admin_headers):
    model_id = f"inactive-list-{uuid.uuid4().hex}"
    created = await client.post(
        "/api/portal/models",
        json={
            "name": "Inactive List Model",
            "model_id": model_id,
            "provider": "openai",
            "type": "llm",
            "api_key": "sk-inactive-list-test",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200
    model_db_id = created.json()["id"]

    disabled = await client.put(
        f"/api/portal/models/{model_db_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200

    active_only = await client.get("/api/portal/models", headers=admin_headers)
    assert not any(model["id"] == model_db_id for model in active_only.json())

    all_models = await client.get(
        "/api/portal/models?include_inactive=true",
        headers=admin_headers,
    )
    found = next(model for model in all_models.json() if model["id"] == model_db_id)
    assert found["is_active"] is False


@pytest.mark.asyncio
async def test_model_discovery_uses_provider_default_and_returns_model_options(
    client: AsyncClient,
    admin_headers,
    monkeypatch,
):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-pro", "owned_by": "deepseek"},
                    {"id": "deepseek-v4-flash", "owned_by": "deepseek"},
                ],
            }

    class FakeClient:
        last_url = None
        last_headers = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            FakeClient.last_url = url
            FakeClient.last_headers = headers
            return FakeResponse()

    monkeypatch.setattr("app.api.portal.endpoints.models.httpx.AsyncClient", FakeClient)

    response = await client.post(
        "/api/portal/models/discover",
        json={
            "provider": "deepseek",
            "api_key": "sk-discover-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json() == [
        {"model_id": "deepseek-v4-pro", "name": "deepseek-v4-pro"},
        {"model_id": "deepseek-v4-flash", "name": "deepseek-v4-flash"},
    ]
    assert FakeClient.last_url == "https://api.deepseek.com/models"
    assert FakeClient.last_headers == {"Authorization": "Bearer sk-discover-test"}


@pytest.mark.asyncio
async def test_dashscope_discovery_uses_official_catalog_endpoint(
    client: AsyncClient,
    admin_headers,
    monkeypatch,
):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"models": [{"model_name": "qwen-plus"}]}}

    class FakeClient:
        last_url = None
        last_params = None

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            FakeClient.last_url = url
            FakeClient.last_params = params
            return FakeResponse()

    monkeypatch.setattr("app.api.portal.endpoints.models.httpx.AsyncClient", FakeClient)

    response = await client.post(
        "/api/portal/models/discover",
        json={
            "provider": "dashscope",
            "api_key": "sk-dashscope-discover-test",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json() == [{"model_id": "qwen-plus", "name": "qwen-plus"}]
    assert FakeClient.last_url == "https://dashscope.aliyuncs.com/api/v1/deployments/models"
    assert FakeClient.last_params == {
        "page_no": 1,
        "page_size": 100,
        "version": "v1.0",
        "model_source": "base",
    }
