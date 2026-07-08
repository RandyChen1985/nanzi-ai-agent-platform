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

    SYSTEM_PROMPT = """你是一个严格的“AI 回答事实一致性判定助手”。
你的唯一任务是判断“AI 的回答”是否完全忠实于“检索出的事实文献”。

【判定规则】
1. **严格的事实一致性**：AI 回答中的任何事实性陈述（例如特定步骤、数字、流程说明等），必须能在“事实文献”中找到明确的支撑。
2. **严禁编造**：AI 的回答中如果出现了文献里完全没有提及的全新规定、流程、外部系统或具体限制（哪怕听起来很合理），则判定为“有幻觉 (is_hallucinated: true)”。
3. **安全容错**：如果 AI 回答坦白说“文献中没有提到”或“无法回答”，这属于客观回答，不属于幻觉（应判定为 is_hallucinated: false）。
4. **排除常识**：一般的问候语、礼貌用语、转折词不计入幻觉。

【输出格式】
你的输出必须是一个有效的 JSON 字符串，包含以下两个字段：
- is_hallucinated: 布尔值 (true/false)。表示该回答是否包含幻觉或文献中不存在的事实。
- reason: 字符串。详细说明判定的原因。如果 is_hallucinated 为 true，必须明确指出哪些回答句子是文献中完全不支持的；如果为 false，设为空字符串。

请只输出 JSON 本身，绝对不要包含任何 Markdown 包裹（如 ```json ... ```），也不要有多余的解释。"""

    @classmethod
    async def evaluate(cls, query: str, context: str, response: str) -> Dict[str, Any]:
        """
        Evaluate if the response has hallucination relative to the context.
        Returns: {"is_hallucinated": bool, "reason": str}
        """
        if not context.strip() or not response.strip():
            return {"is_hallucinated": False, "reason": "上下文或回答为空，跳过评估"}

        try:
            llm = await get_llm_async(streaming=False, temperature=0.1)  # Low temp to reduce random judgment
            if not llm:
                logger.warning("[HallucinationEvaluator] Failed to get LLM; bypass check")
                return {"is_hallucinated": False, "reason": "无法获取大模型句柄，跳过判定"}

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

        return {"is_hallucinated": False, "reason": "检测异常，默认放行"}
