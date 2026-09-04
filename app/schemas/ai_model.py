import json
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Literal, Optional, Any
from datetime import datetime
from pydantic import field_validator

ModelProvider = Literal[
    "openai",
    "azure",
    "deepseek",
    "kimi",
    "zhipu",
    "siliconflow",
    "dashscope",
    "volcengine",
    "ollama",
    "other",
]
ModelType = Literal["llm", "embedding", "multimodal"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

REASONING_EFFORT_VALUES = ("none", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_SUPPORTED_REASONING_EFFORTS = list(REASONING_EFFORT_VALUES)


def _strip_required_text(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


def normalize_supported_reasoning_efforts(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_SUPPORTED_REASONING_EFFORTS)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("supported_reasoning_efforts must be a JSON array") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("supported_reasoning_efforts must be a non-empty array")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("supported_reasoning_efforts must contain strings")
    unknown = sorted(set(value) - set(REASONING_EFFORT_VALUES))
    if unknown:
        raise ValueError(f"unsupported reasoning effort: {', '.join(unknown)}")
    selected = set(value)
    return [effort for effort in REASONING_EFFORT_VALUES if effort in selected]


def normalize_legacy_reasoning_effort(value: Any) -> Optional[str]:
    """Normalize removed reasoning values when reading legacy records."""
    if value is None or value == "" or value == "auto":
        return None
    if value == "max":
        return "xhigh"
    return value if value in REASONING_EFFORT_VALUES else None


def normalize_legacy_supported_reasoning_efforts(value: Any) -> list[str]:
    """Return safe reasoning options for legacy records without mutating storage."""
    if value is None:
        return list(DEFAULT_SUPPORTED_REASONING_EFFORTS)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return list(DEFAULT_SUPPORTED_REASONING_EFFORTS)
    if not isinstance(value, (list, tuple, set)):
        return list(DEFAULT_SUPPORTED_REASONING_EFFORTS)

    normalized = {
        normalize_legacy_reasoning_effort(item)
        for item in value
    }
    normalized.discard(None)
    if not normalized:
        return list(DEFAULT_SUPPORTED_REASONING_EFFORTS)
    return [effort for effort in REASONING_EFFORT_VALUES if effort in normalized]


def validate_reasoning_configuration(
    reasoning_effort: Optional[str],
    supported_reasoning_efforts: Any,
) -> list[str]:
    supported = normalize_supported_reasoning_efforts(supported_reasoning_efforts)
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORT_VALUES:
        raise ValueError("reasoning_effort must be none, minimal, low, medium, high, or xhigh")
    if reasoning_effort is not None and reasoning_effort not in supported:
        raise ValueError("reasoning_effort must be included in supported_reasoning_efforts")
    return supported

class AIModelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Display Name")
    model_id: str = Field(..., min_length=1, max_length=255, description="Actual Model ID for API")
    provider: ModelProvider = Field(..., description="OpenAI-compatible provider")
    type: ModelType = Field(..., description="Type: llm, embedding, multimodal")
    api_base_url: Optional[str] = None
    context_size: Optional[int] = Field(
        default=None,
        gt=0,
        le=10_000_000,
        description="Model context window in tokens",
    )
    max_output_tokens: Optional[int] = Field(
        default=None,
        gt=0,
        le=10_000_000,
        description="Maximum output tokens per request",
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0,
        le=2,
        description="Model test/default temperature, from 0 to 2",
    )
    thinking_enable: bool = False
    thinking_only: bool = False
    allow_disable_thinking: bool = True
    reasoning_effort: Optional[ReasoningEffort] = None
    supported_reasoning_efforts: list[ReasoningEffort] = Field(
        default_factory=lambda: list(DEFAULT_SUPPORTED_REASONING_EFFORTS)
    )
    is_active: bool = True

    @field_validator("context_size", "max_output_tokens", mode="before")
    @classmethod
    def normalize_optional_tokens(cls, value: Any) -> Optional[int]:
        if value is None or value == "" or value == 0:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
            try:
                val_int = int(val_str)
                return val_int if val_int > 0 else None
            except ValueError:
                return None
        return value

    @field_validator("supported_reasoning_efforts", mode="before")
    @classmethod
    def normalize_reasoning_efforts(cls, value: Any) -> list[str]:
        return normalize_supported_reasoning_efforts(value)

    @model_validator(mode="after")
    def validate_reasoning_settings(self):
        validate_reasoning_configuration(
            self.reasoning_effort,
            self.supported_reasoning_efforts,
        )
        return self

    @field_validator("name", "model_id")
    @classmethod
    def normalize_text(cls, value: str, info):
        return _strip_required_text(value, info.field_name)

class AIModelCreate(AIModelBase):
    api_key: Optional[str] = None

class AIModelUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=255)
    provider: Optional[ModelProvider] = None
    type: Optional[ModelType] = None
    api_base_url: Optional[str] = None
    context_size: Optional[int] = Field(default=None, gt=0, le=10_000_000)
    max_output_tokens: Optional[int] = Field(default=None, gt=0, le=10_000_000)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    thinking_enable: Optional[bool] = None
    thinking_only: Optional[bool] = None
    allow_disable_thinking: Optional[bool] = None
    reasoning_effort: Optional[ReasoningEffort] = None
    supported_reasoning_efforts: Optional[list[ReasoningEffort]] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("context_size", "max_output_tokens", mode="before")
    @classmethod
    def normalize_optional_tokens(cls, value: Any) -> Optional[int]:
        if value is None or value == "" or value == 0:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
            try:
                val_int = int(val_str)
                return val_int if val_int > 0 else None
            except ValueError:
                return None
        return value

    @field_validator("name", "model_id")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str], info):
        if value is None:
            return value
        return _strip_required_text(value, info.field_name)

    @field_validator("supported_reasoning_efforts", mode="before")
    @classmethod
    def normalize_optional_reasoning_efforts(cls, value: Any) -> list[str] | None:
        if value is None:
            return value
        return normalize_supported_reasoning_efforts(value)

    @model_validator(mode="after")
    def validate_partial_reasoning_settings(self):
        if self.reasoning_effort is not None and self.supported_reasoning_efforts is not None:
            validate_reasoning_configuration(
                self.reasoning_effort,
                self.supported_reasoning_efforts,
            )
        return self


class AIModelDiscoverRequest(BaseModel):
    provider: ModelProvider
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_config_id: Optional[str] = None


class AIModelTestRequest(BaseModel):
    provider: ModelProvider
    type: ModelType
    model_id: str = Field(..., min_length=1, max_length=255)
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    context_size: Optional[int] = Field(default=None, gt=0, le=10_000_000)
    max_output_tokens: Optional[int] = Field(default=None, gt=0, le=10_000_000)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    model_config_id: Optional[str] = None

    @field_validator("context_size", "max_output_tokens", mode="before")
    @classmethod
    def normalize_optional_tokens(cls, value: Any) -> Optional[int]:
        if value is None or value == "" or value == 0:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
            try:
                val_int = int(val_str)
                return val_int if val_int > 0 else None
            except ValueError:
                return None
        return value

    @field_validator("model_id")
    @classmethod
    def normalize_model_id(cls, value: str):
        return _strip_required_text(value, "model_id")


class AIModelOption(BaseModel):
    model_id: str
    name: str

class AIModelResponse(AIModelBase):
    id: str
    created_at: datetime
    updated_at: datetime
    # Existing deployments may contain legacy provider/type values that are
    # still readable. Create/update requests remain strictly validated above.
    provider: str
    type: str
    # We do NOT return the full api_key for security
    has_api_key: bool = False

    model_config = ConfigDict(from_attributes=True)

    @staticmethod
    def from_orm_custom(obj):
        """Custom converter to handle masked API key logic"""
        values = {
            field_name: getattr(obj, field_name, None)
            for field_name in AIModelResponse.model_fields
            if field_name != "has_api_key"
        }
        values["reasoning_effort"] = normalize_legacy_reasoning_effort(
            values.get("reasoning_effort")
        )
        values["supported_reasoning_efforts"] = normalize_legacy_supported_reasoning_efforts(
            values.get("supported_reasoning_efforts")
        )
        data = AIModelResponse.model_validate(values)
        data.has_api_key = bool(getattr(obj, "api_key", None))
        # Ensure API key is never leaked in the default response if Pydantic included it
        # (Though it is not in AIModelBase, better safe)
        return data
