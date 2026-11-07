import logging
import json
from typing import Optional
from app.core.context import get_current_agent_context
from app.services.ai.tools.tool_compat import tool
from app.services.ai.memory_service import ltm_service

logger = logging.getLogger(__name__)

# 统一的可理解文案：不暴露内部 user_id，也不告诉模型"该传什么才成功"。
_NO_IDENTITY_ERROR = (
    "无权操作其他用户的长期记忆：当前会话无法确认你的身份，"
    "长期记忆只允许读写本人数据，已拒绝本次操作。"
)


def _current_ltm_user_id() -> Optional[str]:
    """从真实会话上下文取长期记忆归属用户。

    为什么不再接受大模型传入的 user_id：该参数完全由模型生成，用户只要在对话里
    说一句「用 user_id=1 保存」，工具就会去写管理员的长期记忆（读、删同理），这是
    一条纯粹的跨用户越权通道。归属只能由运行时上下文决定，模型无法伪造。

    上下文缺失时返回 None 并让调用方 fail-closed——绝不回退到入参或默认用户，
    否则等于把越权通道换个入口保留下来。
    """
    ctx = get_current_agent_context()
    if ctx is None or ctx.user_id is None:
        return None
    return str(ctx.user_id)


@tool
async def update_user_preference(key: str, value: str) -> str:
    """
    持久化地将用户的个性偏好、行为习惯或关于用户的核心事实，以 Key-Value 键值对的形式异步写入 Redis 长期记忆哈希（Hash）中。
    写入对象固定为当前登录用户本人，无需（也无法）指定 user_id。

    Args:
        key: 长期偏好或事实的键名 (如 theme, language, user_role, custom_fact)。
        value: 对应的具体偏好内容值 (如 dark, Chinese, senior_data_analyst)。
    """
    uid = _current_ltm_user_id()
    if uid is None:
        return _NO_IDENTITY_ERROR
    try:
        success = await ltm_service.update_preference(uid, key, value)
        if success:
            return f"成功持久化保存用户偏好记忆！已记录 '{key}': '{value}'。"
        else:
            return "提示：保存记忆失败，可能是 Redis 服务不可用。"
    except Exception as e:
        return f"持久化记忆操作异常: {str(e)}"

@tool
async def fetch_user_long_term_memory() -> str:
    """
    主动查询并拉取当前用户在 Redis 长期记忆哈希（Hash）中存储的所有历史核心事实和偏好记录。
    查询范围固定为当前登录用户本人，无需（也无法）指定 user_id。

    Args:
        (无参数)
    """
    uid = _current_ltm_user_id()
    if uid is None:
        return _NO_IDENTITY_ERROR
    try:
        data = await ltm_service.fetch_memory(uid)
        if not data:
            return "当前用户没有任何长期记忆或事实偏好记录。"

        return f"查询到用户的长期记忆：\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
    except Exception as e:
        return f"检索长期记忆操作异常: {str(e)}"

@tool
async def delete_user_preference(key: str) -> str:
    """
    持久化地将用户在 Redis 长期记忆哈希（Hash）中存储的某个特定偏好、行为习惯或关于用户的核心事实进行清除。
    清除对象固定为当前登录用户本人，无需（也无法）指定 user_id。

    Args:
        key: 需要清除的长期偏好或事实的键名。
    """
    uid = _current_ltm_user_id()
    if uid is None:
        return _NO_IDENTITY_ERROR
    try:
        success = await ltm_service.delete_preference(uid, key)
        if success:
            return f"成功删除用户偏好记忆！已清除键为 '{key}' 的偏好记录。"
        else:
            return "提示：删除记忆失败，可能是 Redis 服务不可用。"
    except Exception as e:
        return f"清除长期记忆操作异常: {str(e)}"
