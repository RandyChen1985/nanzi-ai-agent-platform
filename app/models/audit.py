from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Float, BigInteger
from sqlalchemy.dialects import mysql
from datetime import datetime
from app.core.orm import Base

# MySQL 的 TEXT 只有 65,535 字节（utf8mb4 下约 2.1 万汉字），长思考或长回答会被
# 严格模式整行拒绝。对话历史的三处长文本统一升到 MEDIUMTEXT（16MB）；PG 的 TEXT
# 本身无长度上限，保持原样。每列单独构造，避免多列共享同一个类型实例。


def _history_long_text() -> Text:
    return Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class AgentExecutionTrace(Base):
    __tablename__ = "ai_agent_execution_traces"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    trace_id = Column(String(64), nullable=False, index=True, comment='关联 Access Log 的 Trace ID')
    step_number = Column(Integer, nullable=False, comment='步骤序号')
    event_type = Column(String(50), nullable=False, comment='事件类型')
    agent_name = Column(String(50), comment='Agent 名称')
    tool_name = Column(String(100), comment='工具名称')
    tool_input = Column(JSON, comment='工具入参')
    tool_output = Column(JSON, comment='工具出参')
    execution_time_ms = Column(Float, comment='耗时(ms)')
    status = Column(String(50), default='success', comment='状态')
    error_message = Column(Text, comment='错误详情')
    
    model = Column(String(100), comment='实际使用的模型名称')
    temperature = Column(Float, comment='使用的温度系数')
    prompt_tokens = Column(Integer, default=0, comment='输入 Token 消耗数')
    completion_tokens = Column(Integer, default=0, comment='输出 Token 消耗数')
    total_tokens = Column(Integer, default=0, comment='总 Token 消耗数')
    span_id = Column(String(64), nullable=True, comment='当前 Span ID')
    parent_span_id = Column(String(64), nullable=True, comment='父 Span ID')
    meta_info = Column(JSON, comment='额外元数据')

    created_at = Column(DateTime, default=datetime.now)

class AgentExecutionHistory(Base):
    """
    High-level conversation history for Agents.
    Records 'Who asked what' and 'What the Agent replied'.
    """
    __tablename__ = "ai_agent_execution_history"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    agent_id = Column(String(36), index=True, nullable=False, comment='Agent ID')
    trace_id = Column(String(64), unique=True, nullable=False, index=True, comment='关联 Trace ID')
    conversation_id = Column(String(50), nullable=True, index=True, comment='关联会话 ID')
    user_id = Column(String(64), nullable=True, comment='用户 ID')
    username = Column(String(64), nullable=True, comment='用户名')
    query = Column(_history_long_text(), nullable=True, comment='用户提问')
    summary = Column(_history_long_text(), nullable=True, comment='AI 响应/总结')
    reasoning_content = Column(_history_long_text(), nullable=True, comment='模型推理过程')
    process_timeline = Column(JSON, nullable=True, comment='思考卡定稿快照')
    prompt_tokens = Column(Integer, default=0, comment='输入 Token 消耗数')
    completion_tokens = Column(Integer, default=0, comment='输出 Token 消耗数')
    total_tokens = Column(Integer, default=0, comment='总 Token 消耗数')
    has_data_output = Column(Integer, default=0, comment='是否产出了数据表格/文件等结构化输出')
    execution_time_ms = Column(Float, comment='耗时(ms)')
    status = Column(String(50), default='success', comment='状态: success, failed')
    agent_version = Column(String(32), nullable=True, comment='生成该回复的 Agent 版本号')
    model_id = Column(String(255))
    model_config_id = Column(String(36))
    feedback = Column(String(10), nullable=True, comment='用户反馈: up, down')
    # 回答该轮的智能体身份快照。历史表原本只落 agent_id，展示名靠实时查 ai_agents
    # 回填——智能体被删除、或当前用户看不到它时，已发生的对话会回溯性失去署名。
    # 这里在写入时刻固化一份；存量为 NULL 的行仍回落到 ai_agents 查询 + Redis 兜底。
    agent_name = Column(String(255), nullable=True, comment='智能体 slug 标识名快照')
    agent_display_name = Column(String(255), nullable=True, comment='智能体显示名快照')

    created_at = Column(DateTime, default=datetime.now, index=True)

class AccessLog(Base):
    """
    API Access Logs.
    """
    __tablename__ = "ai_agent_access_logs"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    trace_id = Column(String(64), index=True, nullable=True)
    user_name = Column(String(64), index=True, nullable=True)
    feature_name = Column(String(100), index=True, nullable=True, comment='功能点/菜单名称')
    endpoint = Column(String(255), nullable=False)
    method = Column(String(10), nullable=False)
    status_code = Column(Integer, nullable=False)
    process_time_ms = Column(Float)
    client_ip = Column(String(50))
    request_params = Column(Text, nullable=True) # JSON String
    response_body = Column(Text, nullable=True)  # JSON String (truncated)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.now, index=True)
