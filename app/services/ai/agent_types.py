"""Primary agent types and their locked orchestration capabilities."""

from enum import StrEnum
from typing import Iterable


class AgentType(StrEnum):
    GENERAL = "GENERAL"
    CHATBI = "CHATBI"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"


LOCKED_CAPABILITY_BY_TYPE = {
    AgentType.GENERAL: "general_chat",
    AgentType.CHATBI: "data_query",
    AgentType.KNOWLEDGE_BASE: "knowledge_base",
}

PRIMARY_CAPABILITIES = frozenset(LOCKED_CAPABILITY_BY_TYPE.values())

EXTERNAL_ENGINE_TYPES = frozenset({"RAGFLOW", "OPENCLAW"})


def _normalize_engine_type(engine_type: AgentType | str | None) -> str:
    return str(getattr(engine_type, "value", engine_type) or "LOCAL").strip().upper()


def resolve_agent_type_for_engine(
    engine_type: AgentType | str | None,
    agent_type: AgentType | str,
) -> AgentType:
    """RAGFlow / OpenClaw 固定为通用助手主类型。"""
    if _normalize_engine_type(engine_type) in EXTERNAL_ENGINE_TYPES:
        return AgentType.GENERAL
    return AgentType(agent_type)

def resolve_agent_type(agent: object) -> AgentType:
    """Resolve persisted type with compatibility for pre-migration records."""
    persisted = getattr(agent, "agent_type", None)
    if persisted:
        try:
            return AgentType(str(persisted))
        except ValueError:
            pass

    capabilities = {
        str(value).strip()
        for value in (getattr(agent, "capabilities", None) or [])
    }
    name = str(getattr(agent, "name", "") or "").strip()
    if "data_query" in capabilities or name == "chat-bi":
        return AgentType.CHATBI
    if "knowledge_base" in capabilities or name == "knowledge-base":
        return AgentType.KNOWLEDGE_BASE
    return AgentType.GENERAL


def normalize_agent_capabilities(
    agent_type: AgentType | str,
    values: Iterable[str] | None,
) -> list[str]:
    """Keep one locked primary capability and normalized extension tags."""
    normalized_type = AgentType(agent_type)
    extensions = sorted(
        {
            str(value).strip()
            for value in values or []
            if str(value).strip()
            and str(value).strip() not in PRIMARY_CAPABILITIES
        }
    )
    return [LOCKED_CAPABILITY_BY_TYPE[normalized_type], *extensions]


def normalize_agent_capabilities_for_agent(
    *,
    engine_type: AgentType | str | None,
    agent_type: AgentType | str,
    values: Iterable[str] | None,
) -> list[str]:
    resolved_type = resolve_agent_type_for_engine(engine_type, agent_type)
    return normalize_agent_capabilities(resolved_type, values)
