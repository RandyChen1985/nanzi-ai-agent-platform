"""Langfuse LLM 链路追踪接入模块（一期）。

对外只暴露 package 名称，具体能力从子模块导入：
- ``config_store``：独立表 ``langfuse_config`` 的读写与校验
- ``credentials``：密钥加解密（``langfusekey:v1:`` 前缀）
- ``gate``：开关与 trace 粒度采样
- ``span_attributes``：gen_ai / langfuse 属性构造
- ``settings``：进程内同步快照与后台刷新
- ``manager``：Langfuse SDK 生命周期与导出钩子
- ``context_middleware``：给 AgentScope span 补平台身份
- ``turn_span``：轮次根 span
"""
