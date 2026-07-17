## Context

当前南孜平台通过 [memory_index_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/memory_index_service.py) 进行用户的长期偏好及会话摘要召回。由于仅仅基于余弦相似度评分，未结合时间敏感度和引用频次，导致了老旧冗余信息的大量堆积。为了让 LTM 召回符合人类遗忘及关联模式，并精简大模型会话上下文 Token，我们需要实现艾宾浩斯动态打分与后台异步合并清理。

## Goals / Non-Goals

**Goals:**
1. 重构记忆检索，基于 `last_active` 和 `reference_count` 实现艾宾浩斯保留度对数放大及重排。
2. 开发静默运行的后台降噪管道，支持基于 Redis 分布式独占锁、相似度阈值过滤的多碎片自动归并与更新。
3. 提供前端可调的 `memory_base_half_life` (记忆基础半衰期，默认 7 天) 与 `memory_consolidation_threshold` (降噪相似度，默认 0.82) 的配置表单。

**Non-Goals:**
1. 本次设计不修改 RAGFlow 本身托管知识库的相关策略。
2. 规避在用户前台对话交互期间进行任何记忆合并与删除操作，保证聊天响应速度不受影响。

## Decisions

### 1. 艾宾浩斯重排执行点
- **选择**：在 [memory_index_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/memory_index_service.py) 中 `_search_summaries_knn` 方法内进行 Python 内存重排。
- **对比**：
  - *方案 A（在 Redis 中通过 Lua 脚本排序）*：计算速度极快，但无法动态读取复杂的系统半衰期配置，且调试和修改极其困难。
  - *方案 B（Python 后置重排，当前选择）*：仅对 FT.SEARCH KNN 粗筛出来的 10 条结果进行 Python 算术计算。对于 $N \le 10$ 的列表排序耗时 $< 0.1\text{ms}$，逻辑清晰易读。
- **结论**：选择方案 B。

### 2. 相似记忆聚类算法
- **选择**：在 Python 内存中计算同用户记忆 Embedding 的 Cosine 距离，并进行大于阈值的强连通图分组（Connected Component Clustering）。
- **对比**：
  - *方案 A（引入 sklearn/scipy 层次聚类）*：算法成熟，但会给项目 requirements.txt 增加数兆的科学计算重度依赖，降低容器打包效率。
  - *方案 B（自研轻量强连通图聚类，当前选择）*：由于单个用户的记忆碎片一般小于 500 条，在 Python 中使用双重循环计算相似矩阵（耗时 $< 10\text{ms}$），利用深度优先搜索（DFS）寻找所有满足 `similarity > threshold` 的连通子图进行分组。
- **结论**：选择方案 B，保持系统依赖轻量与敏捷。

### 3. 定时任务分布式锁保障
- **选择**：利用 Redis 原生 `SET lock_key uuid NX EX 3600` 实现分布式排他锁。
- **对比**：
  - *方案 A（使用 APScheduler 自带的 DBJobStore）*：无法完美解决多服务节点并发抢占的问题，在高并发下仍有微小概率重复调度。
  - *方案 B（Redis 互斥锁，当前选择）*：平台已深度融合 Redis，利用原子性 NX 特性可以直接避免定时任务被多实例重复拉起。
- **结论**：选择方案 B。

## Risks / Trade-offs

- **[Risk 1] 降噪任务频繁请求大模型触发 API 频次限制 (Rate Limit)**
  - *Mitigation*：在后台降噪管道循环合并记忆时，每轮合并之间强制 `asyncio.sleep(0.5)`，平抑请求峰值。
- **[Risk 2] 合并过程导致用户长期记忆的微小细节丢失**
  - *Mitigation*：设计严格的合并 System Prompt，指示大模型必须合并冗余语法，但必须精确保留特定的人名、数据库名、物理表名和关键 PUE 数值，字数卡在 50 字以内。
- **[Risk 3] 半衰期计算引起近期频繁提到的新词被快速淡忘**
  - *Mitigation*：对被频繁引用的新词，其 `reference_count` 迅速增加，其记忆强度 $S$ 呈对数放大，其衰减速度会呈指数级降低，完美防范了被快速遗忘的风险。
