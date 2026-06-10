from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module


DEFAULT_REQUIRED_MODULES = [
    "agentscope.agent",
    "agentscope.model",
    "agentscope.tool",
    "agentscope.event",
    "agentscope.permission",
    "agentscope.workspace",
    "agentscope.app.storage",
    "agentscope.app.message_bus",
]


@dataclass(frozen=True)
class AgentScopeImportCheck:
    ok: bool
    available_modules: list[str]
    missing_modules: list[str]


def verify_agentscope_imports(
    required_modules: list[str] | None = None,
) -> AgentScopeImportCheck:
    available: list[str] = []
    missing: list[str] = []

    for module_name in required_modules or DEFAULT_REQUIRED_MODULES:
        try:
            import_module(module_name)
        except Exception:
            missing.append(module_name)
        else:
            available.append(module_name)

    return AgentScopeImportCheck(
        ok=not missing,
        available_modules=available,
        missing_modules=missing,
    )

