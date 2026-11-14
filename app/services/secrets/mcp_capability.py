"""供模型查看的非敏感 MCP 凭据能力摘要。"""
import logging

from sqlalchemy import or_, select

from app.models.secret import SecretBinding
from app.services.secrets.injection import mcp_arg_slots

MCP_CREDENTIAL_INFO = "mcp_credential"
_MODES = frozenset({"fixed", "infer", "ask", "rule", "mixed", "parameter_bindings", "none"})
logger = logging.getLogger(__name__)


def public_credential_info(value) -> dict:
    """仅复制公开的模式与类型信息，不透传凭据绑定引用。"""
    if not isinstance(value, dict) or not isinstance(value.get("mode"), str) or value["mode"] not in _MODES:
        return {}
    result = {"mode": value["mode"], "candidate_lookup_required": value["mode"] == "infer"}
    raw_type = value.get("credential_type")
    if isinstance(raw_type, dict):
        kind = {
            key: raw_type[key] for key in ("name", "type_key")
            if isinstance(raw_type.get(key), str) and raw_type[key].strip()
        }
        version = raw_type.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version > 0:
            kind["version"] = version
        if kind:
            result["credential_type"] = kind
    return result


async def load_parameter_binding_modes(session, tool_record, user_id: str) -> dict[str, str]:
    """仅查询策略元数据，遵循解析器的个人优先、同层规则优先顺序。

    这里生成配置说明，不代表已获授权或能成功解析；实际调用仍独立解析并
    校验绑定。查询失败时不猜测固定模式，也不阻止工具进入原有调用校验链路。
    """
    pairs = mcp_arg_slots(tool_record)
    if not pairs:
        return {}
    try:
        rows = (await session.execute(select(
            SecretBinding.slot, SecretBinding.strategy, SecretBinding.bound_by_user_id,
        ).where(
            SecretBinding.consumer_type == "mcp_server",
            SecretBinding.consumer_id == tool_record.server_id,
            SecretBinding.slot.in_([slot for _, slot in pairs]),
            or_(SecretBinding.bound_by_user_id.is_(None), SecretBinding.bound_by_user_id == "",
                SecretBinding.bound_by_user_id == user_id),
        ))).all()
    except Exception:
        logger.warning("MCP 参数绑定模式查询暂不可用，保留未确定的模式说明")
        return {}

    modes = {}
    for parameter, slot in pairs:
        applicable = [row for row in rows if row.slot == slot]
        personal = [row for row in applicable if user_id and row.bound_by_user_id == user_id]
        layer = personal or [row for row in applicable if not row.bound_by_user_id]
        strategies = {row.strategy or "fixed" for row in layer}
        if "rule" in strategies:
            modes[parameter] = "rule"
        elif strategies == {"fixed"}:
            modes[parameter] = "fixed"
    return modes


def credential_info(profile, credential_type_name, parameters, binding_modes) -> dict:
    if profile is not None:
        return public_credential_info({
            "mode": profile.selection_mode,
            "credential_type": {
                "name": credential_type_name, "type_key": profile.type_key, "version": profile.type_version,
            },
        })
    modes = {(binding_modes or {}).get(parameter, "parameter_bindings") for parameter in parameters}
    if not modes:
        mode = "none"
    elif not modes <= {"fixed", "rule"}:
        mode = "parameter_bindings"
    elif len(modes) == 1:
        mode = next(iter(modes))
    else:
        mode = "mixed"
    return public_credential_info({"mode": mode})


def model_credential_description(tool_name: str, info: dict) -> str:
    # 仅精确匹配本地操作白名单，不把远端名称或描述直接拼入模型说明。
    operation = str(tool_name).split(":", 1)[-1].lower()
    purpose = "服务健康检查。" if operation in {"health", "health_check", "healthcheck"} else "调用 MCP 工具。"
    kind = info.get("credential_type", {})
    label = kind.get("name") or kind.get("type_key")
    if label:
        purpose += f"使用的凭据类型为{label}。"
    instruction = {
        "fixed": "使用固定凭据，由平台自动注入。无需填写 credential_account，无需查询凭据候选，直接调用工具。",
        "infer": "业务账号可以与平台用户名不同。使用 credential_account 选择账号；不确定时调用 list_mcp_credential_candidates 查询本工具的候选。",
        "ask": "凭据由服务端交互选择。直接调用工具并按服务端提示完成选择，无需查询凭据候选。",
        "rule": "凭据按参数绑定规则由平台选择并注入。仅在规则允许模型兜底时使用可选凭据编号，无需查询结构化凭据候选。",
        "mixed": "凭据包含固定绑定与参数规则，由平台选择并注入。仅规则允许模型兜底的参数使用可选凭据编号，无需查询结构化凭据候选。",
        "parameter_bindings": "凭据由平台按参数绑定配置提供，具体策略在调用时核验。无需填写 credential_account，无需查询结构化凭据候选。",
        "none": "未配置工具参数凭据。",
    }.get(info.get("mode"), "凭据配置由平台在调用时核验。")
    return purpose + instruction + "实际调用仍受服务端权限和配置校验约束。"
