## Context

目前，南孜平台的本地检索模式使用关系型数据库（MySQL/SQLAlchemy）以模糊匹配 SQL (ilike) 方式检索数据集。这种传统匹配方式在处理同义词、自然语言问答时匹配效果差。而在没有部署 RAGFlow 引擎的纯本地环境或外网连通性异常的情况下，RAG 检索又无法提供服务。
因此，需要在本地搭建 RediSearch 向量检索，引入 Embedding 生成元数据特征并存储于本地 Redis 缓存中，以提升本地模式下的语义召回率。同时需要通过前置过滤将安全权限控制在 Redis 检索阶段，并像素级对齐 RAGFlow 格式以维持统一的提示词流输出。

## Goals / Non-Goals

**Goals:**
- **本地语义召回**：通过 Redis 向量 HNSW 索引，实现对本地元数据的快速、精准语义召回。
- **前置权限过滤**：利用 Redis 标签过滤，确保普通用户只能在其有权访问的数据集子集中进行向量检索。
- **RAGFlow 返回对齐**：检索后的连缀上下文与打分形式与 RAGFlow 返回完全一致，保证多态接口无缝切换。

**Non-Goals:**
- **知识库文档分块检索**：本项目仅限对元数据数据集（Schema）进行向量检索，非结构化文件（PDF/DOCX等）的切片与检索仍由 RAGFlow 承担，不在本次本地化改造范围。
- **分布式 Redis 集群适配**：暂不考虑复杂的分布式 Redis 索引同步，默认基于单机/主从 Redis Stack。

## Decisions

### 决策 1: 索引类型与命名空间隔离（对齐 RAGFlow 颗粒度）
- **选择方案**：以“物理表”和“指标文件”为独立文档单元存储于 Redis HASH 中：
  - 元数据专属 RediSearch 索引名：`nanzi:idx:metadata:dataset`
  - 前置扫描范围前缀限定为：`PREFIX 1 metadata:dataset:`
  - 数据集下的**物理表** Redis Key 格式：`metadata:dataset:<dataset_id>:table:<table_physical_name>`
  - 数据集下的**指标** Redis Key 格式：`metadata:dataset:<dataset_id>:metrics`
- **权衡考虑**：您指出的非常对，RAGFlow 侧的同步机制是**一个数据集对应一个 Knowledge Base，内部对每张物理表单独上传为一个文本文件，且指标单独上传为 `_metrics.txt` 文件**。
  因此，为了实现格式的像素级多态对齐，本地 Redis 模式必须改变之前“整个数据集存入一个 Key”的设计，改为**“一个表/指标独立生成 YAML 并对应一个 Redis HASH”**的架构。通过重用 `MetadataRagService` 现有的 YAML 生成方法，确保本地 HASH 里的 `doc_name`（如 `res_server.txt`）和 `content`（单表 YAML）与 RAGFlow 完全对齐。

### 决策 2: 权限前置过滤 (Pre-filtering) vs 后置过滤 (Post-filtering)
- **选择方案**：前置过滤。向 Redis FT.SEARCH 发送请求前，先获取用户有权的数据集 ID 列表，并拼装为 Tag 条件：`(@dataset_id:{1|3|8})`。
- **权衡考虑**：如果进行后置过滤（先搜出 Top K，再在 Python 里过滤没权限的），会导致严重召回流失（如 Top K 均无权限，最终结果为 0 命中）。前置过滤直接在 HNSW 检索树上应用布尔条件限制，在保障绝对水平越权安全的同时，最大化确保了有权数据集的召回质量。

### 决策 3: 参数统一化读取与兜底机制
- **选择方案**：
  1. **构建与向量化参数**：优先对接系统“长期记忆”共用的全局 `EmbeddingClient`（以保持和记忆服务 LTM 的 Embedding 引擎对齐）。如果由于配置缺失导致读取失败，则在 Python 代码中采用 `1536` 维度作为硬编码兜底。
  2. **运行期查询参数**：在 local 检索时，直接读取系统中已有的 RAGFlow 参数项（如 `ragflow_similarity_threshold` 等），保持全平台检索门槛的一致。
- **权衡考虑**：避免为本地模式开发单独的配置项表单，最大程度复用并简化管理员的系统配置工作，对齐已有记忆与 RAG 服务。

## Risks / Trade-offs

- **[Risk] Redis 内存开销过大** → [Mitigation] 元数据结构信息量小，主要由表和核心字段的英文及描述组成。限制参与向量化计算的文本长度，并启用 HNSW 索引而非全量暴力扫描以节约内存。
- **[Risk] Redis 重启导致索引丢失** → [Mitigation] 增加探针机制，在检索或系统启动时，如捕获 `FT.INFO` 报错则自动调用 `ensure_metadata_index()` 重建索引，元数据在 MySQL 中持久化存储，可随时一键重构向量索引。
- **[Risk] Redis 宕机/向量同步失败阻塞主业务** → [Mitigation] **彻底的故障隔离与降级**。通过异步协程（`asyncio.create_task`）将本地 Redis 向量化更新流程从 MySQL 主事务中剥离出去，异步执行；在向量生成和写入最外层包覆 `try-except Exception`。若出现 Redis 连不上或 Embedding 接口超时，仅记录 Warning 日志，绝不向上层 raise 抛出任何异常，确保元数据数据集的新建、编辑、智能导入和删除等主业务流程 100% 顺畅不受任何干扰。
