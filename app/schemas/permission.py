from pydantic import BaseModel, Field, field_validator
from typing import List, Optional

class ResourcePermissionBase(BaseModel):
    resource_type: str
    resource_id: str
    enabled: bool = True

class PermissionSet(BaseModel):
    """Represents a set of permissions grouped by resource type"""
    agents: List[str] = Field(default_factory=list, description="List of allowed Agent IDs")
    datasets: List[str] = Field(default_factory=list, description="List of allowed RagFlow Dataset IDs")
    apis: List[str] = Field(default_factory=list, description="List of allowed External API Routes")
    metadata: List[str] = Field(default_factory=list, description="List of allowed Metadata Dataset IDs")
    menus: List[str] = Field(default_factory=list, description="List of allowed Menu IDs")
    elements: List[str] = Field(default_factory=list, description="List of allowed Action/Element IDs")
    forbidden_tools: List[str] = Field(default_factory=list, description="List of forbidden tool names")
    forbidden_commands: List[str] = Field(default_factory=list, description="List of forbidden shell command keywords")

    @field_validator("forbidden_tools", "forbidden_commands", mode="before")
    @classmethod
    def normalize_forbidden_policy_entries(cls, value):
        if value is None:
            return []
        normalized = []
        seen = set()
        for raw_entry in value:
            entry = str(raw_entry).strip()
            if not entry:
                continue
            if len(entry) > 100:
                raise ValueError("Forbidden policy entries cannot exceed 100 characters")
            dedupe_key = entry.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(entry)
        return normalized

class ResourceDetail(BaseModel):
    id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None

class PermissionSetDetail(BaseModel):
    """Detailed view of permissions with names and descriptions"""
    agents: List[ResourceDetail] = Field(default_factory=list)
    datasets: List[ResourceDetail] = Field(default_factory=list)
    apis: List[ResourceDetail] = Field(default_factory=list)
    metadata: List[ResourceDetail] = Field(default_factory=list)
    menus: List[ResourceDetail] = Field(default_factory=list)
    elements: List[ResourceDetail] = Field(default_factory=list)
    forbidden_tools: List[ResourceDetail] = Field(default_factory=list)
    forbidden_commands: List[ResourceDetail] = Field(default_factory=list)

class UserPermissionsResponse(BaseModel):
    roles: List[str] = Field(default_factory=list, description="Assigned roles (reserved)")
    permissions: PermissionSet
    details: Optional[PermissionSetDetail] = None

class PermissionUpdate(PermissionSet):
    """Payload for updating user permissions"""
    pass
