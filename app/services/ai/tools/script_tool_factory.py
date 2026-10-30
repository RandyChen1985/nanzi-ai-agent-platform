"""脚本工具的配置来源与工具壳。

`ScriptToolConfig` 是执行层唯一的输入（name / code / schema / requirements）。
DB 行与内置定义都先映射成它，执行层不区分来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import Field, create_model

from app.models.script_tool import SysScriptTool
from app.services.ai.tools.parameter_schema import parse_parameter_schema
from app.services.ai.tools.script_tool_builtins import (
    BUILTIN_SCRIPT_TOOLS,
    BuiltinScriptTool,
    get_builtin_script_tool,
)
from app.services.ai.tools.script_tool_runtime import (
    ScriptToolConfig,
    ScriptToolError,
)

PYTHON_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def requirements_of(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = [item.strip() for item in raw.splitlines() if item.strip()]
    if not isinstance(parsed, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in parsed if str(item or "").strip())


def runtime_metadata_from_row(
    row: SysScriptTool, *, builtin: BuiltinScriptTool | None = None
) -> tuple[Any, tuple[str, ...], Optional[int]]:
    """未覆盖的内置行以代码库的 schema/依赖/超时为准。

    迁移里的 seed 是快照，``INSERT IGNORE`` 不会更新已有环境；如果始终让 DB 行优先，
    平台修正内置脚本参数（例如 file_to_base64 的输出上限）后，老环境仍会拿到旧
    schema。用户真正改过参数时会写入 code 标记「已覆盖」，此后才以 DB 行为准。
    """
    if builtin is not None and not row.code:
        return builtin.parameter_schema, tuple(builtin.requirements), builtin.timeout_seconds
    return row.parameter_schema, requirements_of(row.requirements), row.timeout_seconds


def config_from_row(row: SysScriptTool, *, builtin: BuiltinScriptTool | None = None) -> ScriptToolConfig:
    """DB 行 → 执行配置。

    内置行的 ``code`` 默认是 NULL，此时源码取代码库定义；一旦管理员在页面上改过并
    保存，库里就有 code，**以库里的为准**（这是「可编辑内置」的语义）。

    代价必须显式告知用户：库里有覆盖后，平台后续对内置脚本的修复不再生效，
    页面上标「已覆盖」并提供「恢复内置默认」（把 code 置回 NULL）。
    """
    code = (row.code or (builtin.code if builtin is not None else "")) or ""
    if not code.strip():
        raise ScriptToolError(f"脚本工具 {row.name} 没有可用源码")
    schema, requirements, timeout_seconds = runtime_metadata_from_row(row, builtin=builtin)
    return ScriptToolConfig(
        name=row.name,
        code=code,
        parameter_schema=schema,
        requirements=requirements,
        timeout_seconds=timeout_seconds,
        language=row.language or "python",
        display_name=row.display_name,
        description=row.description or (builtin.description if builtin is not None else None),
    )


def config_from_builtin(builtin: BuiltinScriptTool) -> ScriptToolConfig:
    return ScriptToolConfig(
        name=builtin.name,
        code=builtin.code,
        parameter_schema=builtin.parameter_schema,
        requirements=tuple(builtin.requirements),
        timeout_seconds=builtin.timeout_seconds,
        language="python",
        display_name=builtin.display_name,
        description=builtin.description,
    )


def build_args_schema(config: ScriptToolConfig):
    """按 parameter_schema 造一个给模型看的 pydantic args schema。

    与 API 工具同口径：有 ``required`` 列表时只有列表内字段必填；没有时沿用
    「默认必填」的历史行为（工具定义里极少依赖可选参数）。
    """
    schema_def, properties, required_fields = parse_parameter_schema(
        config.parameter_schema,
        tool_name=config.name,
    )
    # 标准 JSON Schema 里 ``required: []`` 是合法的「全部可选」；不能因为集合为空
    # 就退回「默认必填」的历史行为，否则模型看到的 args schema 会与脚本不一致。
    has_explicit_required = (
        isinstance(schema_def, dict)
        and "properties" in schema_def
        and isinstance(schema_def.get("required"), list)
    )
    fields: Dict[str, Any] = {}
    for param_name, definition in properties.items():
        param_type: Any = str
        description = ""
        default_value: Any = ...
        if isinstance(definition, dict):
            param_type = PYTHON_TYPES.get(str(definition.get("type") or "string"), str)
            description = str(definition.get("description") or "")
            is_required = (
                param_name in required_fields
                if has_explicit_required
                else bool(definition.get("required", True))
            )
            if not is_required:
                default_value = definition.get("default")
        else:
            description = str(definition)
        fields[param_name] = (param_type, Field(default=default_value, description=description))
    return create_model(f"{config.name}Args", **fields)


class ScriptToolStub:
    """给 registry / 智能体配置用的工具壳：只有元数据，不负责执行。

    真正执行发生在 ``bind_configured_tools_to_workspace``：那里才知道当前会话的
    沙箱与工作区。未经绑定直接调用会明确报错，而不是悄悄跑在宿主机上。
    """

    def __init__(self, config: ScriptToolConfig) -> None:
        self.config = config
        self.name = config.name
        self.display_name = config.display_name
        self.description = config.description or f"[脚本工具] {config.name}"
        self.args_schema = build_args_schema(config)
        self.is_read_only = False
        self.permission_scope = "ask"
        # registry 会把它透传到 RuntimeToolSpec.metadata，供 workspace 绑定消费
        self.runtime_metadata = {"script_tool_config": config}

    async def ainvoke(self, args: Dict[str, Any] | None = None) -> Any:
        raise ScriptToolError(
            "脚本工具需要在会话沙箱中执行：当前调用缺少会话工作区绑定。"
            "请在智能体对话中调用，或使用页面上的「试运行」。"
        )

    async def arun(self, **kwargs: Any) -> Any:
        return await self.ainvoke(kwargs)


class ScriptToolFactory:
    @staticmethod
    def create_tool_from_row(row: SysScriptTool) -> ScriptToolStub:
        builtin = get_builtin_script_tool(row.name) if row.source == "builtin" else None
        return ScriptToolStub(config_from_row(row, builtin=builtin))

    @staticmethod
    def create_tool_from_builtin(builtin: BuiltinScriptTool) -> ScriptToolStub:
        return ScriptToolStub(config_from_builtin(builtin))


@dataclass(frozen=True)
class EffectiveScriptTool:
    """列表页/智能体配置看到的「一个脚本工具」，来源已折叠。"""

    id: str
    name: str
    display_name: Optional[str]
    description: Optional[str]
    source: str
    language: str
    parameter_schema: Any
    requirements: List[str]
    timeout_seconds: Optional[int]
    group_name: Optional[str]
    is_active: bool
    code: Optional[str]
    # 内置脚本被用户改过：库里存了 code，代码库版本不再生效
    is_overridden: bool = False
    created_at: Any = None
    updated_at: Any = None


def merge_effective_script_tools(rows: List[SysScriptTool]) -> List[EffectiveScriptTool]:
    """把库里行与内置定义合并成一份「最终生效」列表。

    内置工具即使迁移尚未执行也应当出现在页面上（否则一次漏跑迁移就看不到它们），
    但库里显式停用/改了分组的行优先——管理员的选择必须被尊重。
    """
    by_name = {row.name: row for row in rows}
    merged: List[EffectiveScriptTool] = []
    seen: set[str] = set()

    for row in rows:
        builtin = get_builtin_script_tool(row.name)
        merged.append(_effective_from_row(row, builtin))
        seen.add(row.name)

    for builtin in BUILTIN_SCRIPT_TOOLS:
        if builtin.name in seen:
            continue
        merged.append(
            EffectiveScriptTool(
                id=builtin.tool_id,
                name=builtin.name,
                display_name=builtin.display_name,
                description=builtin.description,
                source="builtin",
                language="python",
                parameter_schema=builtin.parameter_schema,
                requirements=list(builtin.requirements),
                timeout_seconds=builtin.timeout_seconds,
                group_name=builtin.group_name,
                is_active=True,
                code=builtin.code,
            )
        )

    merged.sort(key=lambda item: (item.source != "builtin", item.name))
    return merged


def _effective_from_row(row: SysScriptTool, builtin: BuiltinScriptTool | None) -> EffectiveScriptTool:
    schema, requirements, timeout_seconds = runtime_metadata_from_row(row, builtin=builtin)
    if isinstance(schema, str):
        try:
            schema = json.loads(schema) if schema else {}
        except ValueError:
            schema = {}
    return EffectiveScriptTool(
        id=row.id,
        name=row.name,
        display_name=row.display_name or (builtin.display_name if builtin else None),
        description=row.description or (builtin.description if builtin else None),
        source=row.source or "custom",
        language=row.language or "python",
        parameter_schema=schema or (builtin.parameter_schema if builtin else {}),
        requirements=list(requirements),
        timeout_seconds=timeout_seconds,
        group_name=row.group_name,
        is_active=bool(row.is_active),
        code=row.code or (builtin.code if builtin is not None else None),
        is_overridden=bool(row.code) and builtin is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
