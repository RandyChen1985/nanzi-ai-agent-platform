# 实现计划 (Implementation Plan) - 艾宾浩斯长期记忆遗忘曲线与异步记忆降噪

本计划定义了“南孜智能体平台”长期记忆（LTM）召回动态重排与后台降噪的详细开发步骤。

---

## 1. 为什么这么做 (Why)

* **痛点根源**：现有系统的长期记忆粗筛只根据余弦相似度召回。某些老旧无用的记忆仅凭相似度分数高就会被频繁召回，缺乏“人类大脑遗忘及时间衰减”的自然规律。同时，长期交互积累的相似记忆碎片会稀释大模型的注意力焦点，造成 Token 的极大浪费。
* **设计思路**：
  1. **动态重排**：在 KNN 向量检索后，实时结合记忆的流逝时间 $t$ 和引用频次 `reference_count` 计算艾宾浩斯记忆保留度 $R$，与相似度分值进行乘积加权并重排，使近期且高频使用的记忆获得更高的曝光。
  2. **异步降噪**：凌晨 3:00 自动执行后台清理，通过内存向量聚类识别相似记忆，调用全局默认大模型将它们重构合并为单条精简陈述，物理删除旧碎片，实现无感 Token 瘦身。
  3. **前端呈现**：在“记忆管理中心”的“服务配置”表单中展示并允许修改这两个配置项。

---

## 2. 准备怎么搞 (How)

### 涉及修改的文件：
1. **[config.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/core/config.py)** (配置项定义)
2. **[memory_index_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/memory_index_service.py)** (记忆召回、聚类与降噪管道)
3. **[scheduler_service.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/app/services/ai/scheduler_service.py)** (凌晨 3:00 定时任务注册与 Redis 锁保障)
4. **[MemoryManagement.vue](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/frontend/src/views/MemoryManagement.vue)** (前端服务配置渲染挂载)
5. **[tests/test_memory_ebbinghaus_consolidation.py](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/tests/test_memory_ebbinghaus_consolidation.py)** (新增的单元测试)
6. **[tests/CHECKLIST.md](file:///Users/chenxiaolong/workspace/yovole-nanzi-ai-agent-platform/tests/CHECKLIST.md)** (自动化测试验收清单)

### 具体修改步骤：

#### 步骤一：配置参数数据库化与全局常量化
* 在 `db-prod/` 中通过 SQL 将 `memory_base_half_life` 与 `memory_consolidation_threshold` 插入 `memory_service_configs` 表中。
* 在 `app/core/config.py` 的全局设置中添加对应的两个固定默认参数：
  * `MEMORY_BASE_HALF_LIFE: float = 7.0` (以天为单位的基础半衰期)
  * `MEMORY_CONSOLIDATION_THRESHOLD: float = 0.82` (记忆降噪的 Cosine 相似度聚类阈值)

#### 步骤二：重构 `_search_summaries_knn` 重排逻辑
* 在 `app/services/ai/memory_index_service.py` 中：
  * 从 `FT.SEARCH` 结果中提取 `last_active` (时间戳) 和 `reference_count` (被引用的次数)。
  * 引入艾宾浩斯保留度计算：
    * $t = (\text{当前时间} - last\_active) / 86400.0$ (天)
    * $S = MEMORY\_BASE\_HALF\_LIFE \times (1.0 + \ln(1.0 + reference\_count))$
    * $R = e^{-t / S}$
  * 对 KNN 粗筛出来的列表执行：`item['final_score'] = item['score'] * R`。
  * 根据 `final_score` 降序排列并按限制数（`limit`）截断。

#### 步骤三：开发静默聚类算法与大模型合并管道
* 在 `app/services/ai/memory_index_service.py` 中添加 `consolidate_user_memories(user_id: str)` 方法：
  * **聚类**：获取该用户所有的活跃记忆，在内存中计算两两 Embedding 向量的余弦距离，将相似度 $> 0.82$ 的记忆组合成连通图分支。
  * **合并**：将分支内的碎片组合进 Prompt，指示默认全局大模型重构出不超过 50 字的精炼版陈述。
  * **更新与清理**：将新合并的记忆写入 Redis（`reference_count` 继承累加值），并调用 `redis.delete` 物理清除该分支下的所有旧 HASH 键。

#### 步骤四：挂载凌晨分布式调度任务
* 在 `app/services/ai/scheduler_service.py` 的 `scheduler_service.start()` 中：
  * 注册定时任务，执行 Cron 规则：`0 3 * * *` (每天凌晨 3:00 启动)。
  * 任务触发时，尝试在 Redis 中设置互斥锁。
  * 竞夺成功的节点执行聚类降噪逻辑，遍历近 24 小时活跃的用户长期记忆进行合并。

#### 步骤五：前端配置项渲染与表单保存
* 在 `frontend/src/views/MemoryManagement.vue` 中注册新增的配置项：
  * 在 `CONFIG_FIELD_TYPES` 注册字段类型为 `number`；
  * 在 `CONFIG_LABELS` 注册其对应的中文名称；
  * 在 `CONFIG_TIPS` 增加对应的鼠标悬停帮助气泡；
  * 在 `CONFIG_GROUPS` 的 `retrieval` 检索配置组挂载这两个配置键。

---

## 3. 为什么这么修改 (Rationale)

1. **Python 内存重排而非 Lua 脚本重排**：因为 Redis KNN 粗筛返回的数据量极小（仅 5-10 条），在 Python 中计算时间戳差值和指数运算的 CPU 耗时 $< 0.1\text{ms}$。在 Python 层做不仅易于维护，而且规避了在 Redis 内部编写、调试复杂数学 Lua 脚本的痛点。
2. **轻量连通子图聚类而非重度科学计算库**：单个用户的记忆碎片在 500 条以内。用简单的 DFS 连通分量进行阈值聚类，仅需几行 Python 代码即可达到极好的效果，无需在 `requirements.txt` 中引入 scipy/numpy 等庞大的依赖库。
3. **前端动态表单配置自动拉取**：前端采用了高度可配置的 `CONFIG_GROUPS` 的方式渲染 `memory_service_configs` 表，在前端补全映射和键名配置，即可在免去开发定制输入框的情况下，让管理员能够灵活修改并一键保存，大大提高了系统配置的可维护性。
