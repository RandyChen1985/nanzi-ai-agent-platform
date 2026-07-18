from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ScenarioTemplateResourceRequirement(BaseModel):
    type: str
    name: str
    required: bool = True
    description: Optional[str] = None


class ScenarioTemplateResourceOption(BaseModel):
    id: str
    name: str
    label: str
    description: Optional[str] = None
    status: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class ScenarioTemplateResourceOptionsResponse(BaseModel):
    template_id: str
    options: Dict[str, List[ScenarioTemplateResourceOption]] = Field(default_factory=dict)


class ScenarioTemplateSummary(BaseModel):
    id: str
    name: str
    category: str
    description: str
    tags: List[str] = Field(default_factory=list)
    recommended: bool = False
    target_departments: List[str] = Field(default_factory=list)
    delivery_time: Optional[str] = None
    maturity: Optional[str] = None
    included_capabilities: List[str] = Field(default_factory=list)
    deliverables: List[str] = Field(default_factory=list)
    business_goals: List[str] = Field(default_factory=list)
    install_steps: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    required_resources: List[ScenarioTemplateResourceRequirement] = Field(default_factory=list)
    sample_questions: List[str] = Field(default_factory=list)


class ScenarioTemplateDetail(ScenarioTemplateSummary):
    manifest: Dict[str, Any]


class ScenarioTemplateInstallRequest(BaseModel):
    instance_name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    resource_bindings: Dict[str, Any] = Field(default_factory=dict)
    publish: bool = True

    @field_validator("instance_name")
    @classmethod
    def validate_instance_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if any(ch not in allowed for ch in normalized):
            raise ValueError("实例标识仅支持字母、数字、短横线和下划线")
        if len(normalized) > 100:
            raise ValueError("实例标识不能超过 100 个字符")
        return normalized


class ScenarioTemplatePrecheckItem(BaseModel):
    key: str
    label: str
    status: str
    message: str


class ScenarioTemplatePrecheckResponse(BaseModel):
    template_id: str
    target_agent_name: str
    can_install: bool
    checks: List[ScenarioTemplatePrecheckItem]


class ScenarioTemplateInstallResponse(BaseModel):
    template_id: str
    created: bool
    instance: Dict[str, Any]
    run: Dict[str, Any]
    agent: Dict[str, Any]
    version: Dict[str, Any]
    resource_bindings: Dict[str, Any] = Field(default_factory=dict)
    missing_resources: List[ScenarioTemplateResourceRequirement] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    enabled_tools: List[str] = Field(default_factory=list)
    sample_questions: List[str] = Field(default_factory=list)
    resource_summary: List[Dict[str, Any]] = Field(default_factory=list)


class ScenarioTemplateInstanceSummary(BaseModel):
    id: str
    template_id: str
    template_name: str
    status: str
    owner: Optional[str] = None
    agent: Dict[str, Any]
    latest_run: Optional[Dict[str, Any]] = None
    resource_summary: List[Dict[str, Any]] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    sample_questions: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
