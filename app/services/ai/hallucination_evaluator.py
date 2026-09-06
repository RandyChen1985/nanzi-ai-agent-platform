import json
import logging
import re
from typing import Dict, Any

from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

logger = logging.getLogger(__name__)


class HallucinationEvaluator:
    """
    Evaluator to detect hallucinated content in AI responses relative to RAG context.
    """

    SYSTEM_PROMPT = """你是一个"AI 回答事实一致性审核助手"。
你的任务是判断"AI 的回答"是否基于"检索出的事实文献"，对可能存在的严重幻觉进行标记。

【判定规则】
1. **整体事实一致性（非逐字匹配）**：AI 回答的核心事实性陈述，应能在"事实文献"中找到支撑或合理推断。允许语言组织和合理归纳，不要求逐字一致。
2. **严禁凭空编造**：仅当 AI 明确断言了文献里完全没有提及的具体数字、特定系统名称或特定限制规定（而非一般性推断）时，才视为幻觉。
3. **综合/摘要类回答宽容**：当用户提出宽泛性问题（如"有哪些功能""大概介绍"），AI 基于多篇文献归纳综合的回答，只要大多数主要观点有文献支撑，即视为可接受（is_hallucinated: false）。
4. **安全容错**：如果 AI 坦白说"文献中没有提到"或"无法回答"，属于客观回答，不属于幻觉。
5. **排除常识与礼貌**：问候语、礼貌用语、逻辑转折词不计入幻觉。

【输出格式】
你的输出必须是一个有效的 JSON 字符串，包含以下两个字段：
- is_hallucinated: 布尔值 (true/false)。仅当存在明确的、严重的、无文献依据的核心事实断言时才返回 true。
- reason: 字符串。如果 is_hallucinated 为 true，必须指出哪些具体句子是文献完全不支持的；如果为 false，设为空字符串。

请只输出 JSON 本身，绝对不要包含任何 Markdown 包裹（如 ```json ... ```），也不要有多余的解释。"""

    @classmethod
    async def evaluate(cls, query: str, context: str, response: str, enabled: bool = True) -> Dict[str, Any]:
        """
        Evaluate if the response has hallucination relative to the context.
        Returns: {"is_hallucinated": bool, "reason": str}
        """
        if not enabled:
            return {"is_hallucinated": False, "reason": "NLI 检测已关闭，跳过"}
        if not context.strip() or not response.strip():
            return {"is_hallucinated": False, "reason": "上下文或回答为空，跳过评估"}

        try:
            llm = await get_llm_async(streaming=False, temperature=0.1)  # Low temp to reduce random judgment
            if not llm:
                logger.warning("[HallucinationEvaluator] Failed to get LLM; bypass check")
                return {"is_hallucinated": False, "reason": "无法获取大模型句柄，跳过 LLM 判定（降级为规则兜底）"}

            chat_client = chat_client_from_handle(llm)
            user_content = (
                f"【用户的问题】\n{query}\n\n"
                f"【事实文献】\n{context}\n\n"
                f"【AI 的回答】\n{response}\n"
            )

            messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=cls.SYSTEM_PROMPT)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=user_content)],
                ),
            ]

            # Generate evaluation response
            raw_text = await chat_client.generate_text(messages, temperature=0.1)
            raw_text = str(raw_text or "").strip()

            # Parse JSON from response
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if match:
                data = json.loads(match.group())
                is_hallucinated = bool(data.get("is_hallucinated", False))
                reason = str(data.get("reason", ""))
                logger.info(
                    f"[HallucinationEvaluator] Check result: is_hallucinated={is_hallucinated}, reason='{reason}'"
                )
                return {"is_hallucinated": is_hallucinated, "reason": reason}
            else:
                logger.warning(
                    f"[HallucinationEvaluator] Model returned invalid format: {raw_text}"
                )

        except Exception as e:
            logger.error(
                f"[HallucinationEvaluator] Evaluation failed: {e}", exc_info=True
            )

        return {"is_hallucinated": False, "reason": "LLM 检测异常/格式非法，未完成评估（降级为规则兜底）"}
