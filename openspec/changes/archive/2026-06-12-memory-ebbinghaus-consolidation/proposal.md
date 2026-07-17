## Why

当前系统的长期记忆（LTM）在进行 Redis 向量检索时，仅单纯依赖语义相似度分值进行召回。这导致两个关键瓶颈：
1. **老旧记忆干扰**：某些已经很久不再使用、且处于陈旧语境中的老旧记忆，仅因相似度较高就被频繁重复召回，不符合“人类大脑遗忘及时间衰减”的自然特征。
2. **记忆冗余与 Token 膨胀**：随着交互周期的拉长，用户的偏好及会话摘要碎片线性增加，冗余的上下文信息不仅稀释了大模型的意图匹配焦点，也带来了大量的 Token 浪费。

## What Changes

本变更拟在平台长期记忆引擎中引入“艾宾浩斯遗忘重排机制”和“后台异步记忆归并降噪管道”：
- **艾宾浩斯动态重排**：修改记忆召回逻辑，基于记忆的流逝时间 $t$（`last_active`）与引用频次（`reference_count`）所决定的记忆强度 $S$，套用艾宾浩斯保留度公式对 KNN 粗筛的记忆进行实时加权计算并重排。
- **夜间异步记忆归并**：在分布式调度任务中心配置凌晨 Cron 任务，扫描近期相似度极高的记忆碎片，利用大模型进行归类、提炼并合并重构为精简版记忆，同时物理删除冗余碎片，实现无感 Token 瘦身。
- **参数动态可调**：在系统设置中支持动态调整“基础遗忘半衰期”与“记忆降噪相似度阈值”。

## Capabilities

### New Capabilities
- `ebbinghaus-memory-ranking`: 引入艾宾浩斯动态记忆遗忘重排打分算法，结合时间差与使用频次优化 LTM 召回顺序。
- `memory-consolidation-pipeline`: 引入后台静默执行的记忆合并、重构与垃圾清理降噪管道。

### Modified Capabilities
<!-- 无现有规格变更，全为新增行为 -->

## Impact

- **后端服务**：
  - [memory_index_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/memory_index_service.py)：增加后置重排算法，并在向量搜索召回时进行最终加权。
  - [scheduler_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/scheduler_service.py)：注册并管理凌晨运行的记忆降噪后台定时任务。
- **数据结构与存储**：
  - 影响 Redis HASH `MEMORY_REDIS_INDEX_NAME` 中 `last_active` 和 `reference_count` 字段的读取、统计与物理更新。
- **系统设置 (前端)**：
  - [SystemConfig.vue](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/frontend/src/views/SystemConfig.vue)：添加记忆基础半衰期及降噪聚类相似度阈值的表单参数配置。
