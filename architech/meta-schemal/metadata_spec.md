# RAG Text-to-SQL 元数据规范设计 (Metadata Specification)

为了极大提升 Text-to-SQL 的准确率，单纯依赖数据库 Schema（DDL）是远远不够的。我们需要构建一个富含“业务语义”的元数据层 (Semantic Layer)。

## 1. 核心设计理念

元数据应回答以下问题：
*   **这是什么？** (Tables/Columns 的业务定义)
*   **有什么关系？** (Explicit Relationships，甚至跨库关联)
*   **有哪些值？** (Categorical Values 的枚举，帮助 LLM 理解 where 条件)
*   **怎么计算？** (Common Metrics，如 PUE = Total Power / IT Power)
*   **常见叫法？** (Synonyms，用户把 "IDC" 叫 "机房"，把 "Power" 叫 "电量")

## 2. 推荐元数据结构 (YAML/JSON)

为了维护方便，建议使用 YAML 格式，易于人类阅读和编辑。

### 结构概览

```yaml
version: "1.0"
domain: "IDC_Energy" # 领域划分
entities:           # 实体（通常对应表）
  - name: "metrics_realtime"
    term: "实时能耗指标表"
    description: "存储各机房、设备的实时电力、温度等指标数据，每分钟更新。"
    synonyms: ["实时数据", "监控表"]
    columns:
      - name: "pue"
        term: "PUE值"
        type: "float"
        description: "Power Usage Effectiveness，电能利用效率，越低越好。"
        examples: [1.2, 1.5, 1.8] # Few-shot 示例值
        
      - name: "room_id"
        term: "机房ID"
        description: "关联机房表的唯一标识"
        foreign_key: "rooms.id" # 显式外键指引

      - name: "metric_type"
        term: "指标类型"
        description: "区分电量、电压、电流等"
        enums:            # 重要！即使数据库未定义 enum，这里也要列出业务枚举
          - value: "power_active"
            description: "有功功率"
          - value: "voltage"
            description: "电压"

relationships:      # 复杂关联或逻辑关联
  - source: "metrics_realtime.room_id"
    target: "rooms.id"
    type: "many_to_one"
    description: "每个指标数据属于一个特定机房"

metrics:            # 预定义计算公式 (Computed Metrics)
  - name: "daily_pue_avg"
    description: "日均PUE"
    sql_formula: "AVG(pue)"
    group_by: ["room_id", "date(created_at)"]
```

## 3. 具体字段规范

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| **Entities (Tables)** | List | Yes | 数据实体列表 |
| `name` | String | Yes | 数据库中的物理表名 |
| `term` | String | Yes | 业务通用名称 (中文) |
| `description` | String | Yes | 详细描述，包含数据粒度（如：按天聚合）、更新频率等 |
| `synonyms` | List | No | 同义词列表，用于向量检索增强 (RAG) |
| **Columns** | List | Yes | 关键字段列表（无需列出所有，仅列出业务相关） |
| `name` | String | Yes | 物理字段名 |
| `term` | String | Yes | 业务字段名 |
| `enums` | List | No | **关键**：枚举值及其业务含义。极大帮助 LLM 生成正确的 where a='b' |
| `examples` | List | No | **关键**：典型值示例，防止 LLM 幻觉生成不存在的值 |
| `foreign_key` | String | No | 显式关联指针 `TargetTable.TargetColumn` |

## 4. 文件组织策略 (File Organization Strategy)

关于“一个文件包含多少个表”，我们推荐 **按业务域 (Domain-Driven) 分组** 的策略，而不是“一表一文件”或“全库一文件”。

### 推荐方案：按业务域分组 (多表一文件)
将逻辑紧密相关的表定义在同一个 YAML 文件中。

*   **优点**：
    *   **上下文完整**：便于 LLM 理解表之间的关联（Foreign Keys 通常发生在同域表之间）。
    *   **管理方便**：避免产生数千个碎片小文件，也避免一个 10MB 的巨大文件。
*   **示例**：
    *   `meta-schemal/resources.yaml`: 包含 `cmdb_resources`, `cmdb_rooms`, `cmdb_racks`。
    *   `meta-schemal/energy.yaml`: 包含 `metrics_power`, `metrics_pue`, `billing_records`。

### 替代方案：一表一文件 (One Table Per File)
*   **适用场景**：当表数量极大（>1000张）且表之间耦合度极低时。
*   **缺点**：难以直观查看 ER 关系，需要额外的索引文件。

---

## 5. 实施策略


1.  **文件管理**：在 `architech/meta-schemal` 目录下，按业务域拆分文件，例如：
    *   `resource_meta.yaml` (资源类：机房、机柜)
    *   `metric_meta.yaml` (指标类：能耗、PUE)
2.  **RAG 增强**：
    *   **Retrieval**: 当用户提问 "查看南孜机房的PUE" 时，先检索 Meta Schema 中的 Synonyms 和 Description。
    *   **Prompting**: 将检索到的 Top-K 表结构 + Enums + Examples 注入到 Prompt 中，而非 Dump 整个数据库结构。

## 6. LLM 是如何读取这些文件的？ (Retrieval Strategy)

当元数据分散在多个文件（甚至几十个文件）中时，我们**不能也不应该**把所有内容一次性塞给 LLM（Context 也就是上下文长度是有限且昂贵的）。

我们需要采用 **RAG (检索增强生成)** 策略：

1.  **索引阶段 (Indexing)**：
    *   系统启动时，读取 `meta-schemal/` 下的所有 YAML 文件。
    *   将每个 `entity` (表) 和 `synonyms` (同义词) 及其描述，转换为向量并存入向量数据库（如 ChromaDB/FAISS）。

2.  **检索阶段 (Retrieval)**：
    *   **User Query**: "帮我查一下上海机房昨天的平均PUE"
    *   **Search**: 系统根据关键词 "上海机房", "PUE" 在向量库中检索。
    *   **Match**: 命中 `ck_fact_nanzi_resroom_hbase` (因为有同义词 "机房") 和 `ck_fact_donghuan_real_metric_hbase` (因为有同义词 "PUE")。

3.  **构建提示词 (Prompt Construction)**：
    *   系统仅将 **命中的这 2 张表** 的元数据（YAML片段）注入到 Prompt 中。
    *   LLM 看到的上下文是精简且高度相关的，因此生成的 SQL 最准确。

**总结**：文件怎么拆分主要为了**人**好管理（按业务域拆分）；而**机器** (LLM) 是通过按需检索（Search-on-Demand）来读取的，不管你拆成多少个文件，它只看和当前问题相关的那部分。

## 7. 示例 (Draft)

请参考同级目录下的 `sample_idc_meta.yaml` 进行编写。
