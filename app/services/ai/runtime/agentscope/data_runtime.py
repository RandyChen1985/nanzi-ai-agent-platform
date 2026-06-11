"""Shared ChatBI AgentScope runtime constants."""

# 数据查询 ReAct 轮次上限（与全局 agent_max_iterations 取较小值，避免长时间空转）
DATA_QUERY_MAX_STEPS_CAP = 20
