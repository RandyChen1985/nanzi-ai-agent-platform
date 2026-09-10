import logging
import json
from typing import Optional
from app.services.ai.tools.tool_compat import tool
from app.services.chatbi_example_service import ExampleService

logger = logging.getLogger(__name__)

@tool
async def search_qa_examples(query: str, top_k: Optional[int] = 5) -> str:
    """
    检索「问答经验库」中经过人工审核的高质量历史问答案例，作为回答参考。

    该库同时收录「取数 SQL 型案例」与「通用问答/知识型案例」两类经验：
    - 取数型案例提供已验证的 SQL 实现、字段表关联口径；
    - 通用型案例提供标准参考回答（ai_answer）与业务背景（context_summary）。

    适合在以下场景主动检索：
    1. 用户询问指标口径、同比/环比、复杂取数逻辑，需参考历史验证过的 SQL 写法与表关联方式；
    2. 通用智能体或知识库问答需要参考以往的高标准回答表述与解题思路；
    3. 首次面对陌生业务问题，想先看看库里是否有相似的历史处理经验。

    Args:
        query: 用户问题或检索关键词，用于在经验库中检索相似历史案例。
        top_k: (可选) 最多返回的案例条数，默认 5。
    """
    try:
        logger.info(f"[ExampleSearchTool] Called with query='{query}', top_k={top_k}")
        
        # 通用检索：不限定数据集、不做历史代词的意图改写；
        # require_sql=False 让无 SQL 的通用问答/知识型案例也能被检索到。
        examples = await ExampleService.search_examples(
            query=query, 
            dataset_id=None, 
            top_k=top_k, 
            history=None,
            require_sql=False,
        )
        
        if not examples:
            return json.dumps({
                "status": "empty",
                "message": f"未在经验库中找到与 '{query}' 相关的优质案例。"
            }, ensure_ascii=False)
            
        # Format the result nicely for the LLM
        formatted_context = "【检索到的经验库案例】:\n\n"
        for i, ex in enumerate(examples):
            question = ex.get('question', '未知问题')
            sql = ex.get('sql', '')
            ai_answer = ex.get('ai_answer') or ''
            context_summary = ex.get('context_summary') or ''
            dataset_name = ex.get('dataset_name', '通用')
            similarity = ex.get('similarity', 0)
            
            formatted_context += f"--- 案例 {i+1} [相似度: {similarity:.2f} | 数据集: {dataset_name}] ---\n"
            formatted_context += f"用户问题: {question}\n"
            if context_summary:
                formatted_context += f"业务背景: {context_summary}\n"
            if ai_answer:
                formatted_context += f"标准参考回答: {ai_answer}\n"
            if sql:
                formatted_context += f"优质 SQL（如为取数需求可参考其表关联与口径）:\n```sql\n{sql}\n```\n"
            formatted_context += "\n"
            
        # Return structured JSON so that if needed, other layers can parse it
        result = {
            "status": "success",
            "content": formatted_context,
            "count": len(examples)
        }
        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        err_msg = str(e)
        logger.error(f"[ExampleSearchTool] Failed to search examples: {e}", exc_info=True)
        return f"[Tool Error] 经验库检索失败: {err_msg}"
