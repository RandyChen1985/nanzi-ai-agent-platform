# 需求分析与设计提案：智能指标推荐 (Smart Metric Recommendation)

## 1. 背景与目标
**现状**：目前“业务指标(Metrics)”需要用户手动逐个创建，填写物理字段、计算逻辑、业务描述等，门槛较高且操作繁琐。
**目标**：利用 AI 自动分析数据表结构（Schema）和内容，批量推荐高价值的业务指标和常用查询逻辑，用户只需勾选确认即可入库。

## 2. 核心需求拆解 (基于用户反馈)

用户希望 AI 能覆盖以下几种“指标”类型：

1.  **聚合型指标 (Aggregations)**
    *   **定义**：数值型统计，用于回答“多少”、“总量”等问题。
    *   **示例**：
        *   `机房总数` (`COUNT(rowkey)`)
        *   `机架使用率` (`COUNT(used) / COUNT(total)`)
        *   `PUE平均值` (`AVG(pue)`)
    *   **AI 策略**：扫描数值字段、主键计数。

2.  **维度分布型指标 (Distributions/Group By)**
    *   **定义**：按类别分组统计，用于回答“分布情况”、“占比”等问题。
    *   **示例**：
        *   `辖区分布` (`SELECT gxqy, COUNT(*) FROM ... GROUP BY gxqy`)
        *   `设备类型占比`
    *   **AI 策略**：扫描枚举值(Enum)字段、外键引用字段。

3.  **常用数据视图 (Data Views/Snippets)**
    *   **定义**：高频查询的字段组合，用于回答“列出...”、“查询...详情”等问题。
    *   **示例**：
        *   `机房列表` (`SELECT jfmc, jfbm, dz FROM ...`) —— **用户提到的重点**。
    *   **AI 策略**：识别业务表的核心描述字段 (Name, Code, Status)。

## 3. 功能交互设计 (UI Flow)

### 入口
在 `MetadataTables` -> `业务指标 (Metrics)` 标签页，在“新建指标”按钮旁新增 **“✨ 智能发现指标”** 按钮。

### 交互流程
1.  **点击“智能发现”**：
    *   弹出一个分析窗口（类似导入向导）。
    *   **输入上下文 (可选)**：用户可以输入一句话提示，例如“我想看机房相关的容量统计”，或者什么都不输（全量扫描）。
2.  **AI 分析中**：
    *   后端将当前数据集的所有表结构 (`Tables` + `Columns`) + 部分采样数据（可选）发送给 LLM。
    *   Prompt 核心：`"你是一个数据分析师，请根据以下表结构，推荐 5-10 个最有业务价值的分析指标..."`。
3.  **推荐列表确认 (Preview)**：
    *   界面展示 AI 推荐的指标卡片列表。
    *   每个卡片包含：
        *   **名称**：(e.g., "机房辖区分布")
        *   **逻辑SQL**：(`SELECT gxqy, count(*)...`)
        *   **解释**：(用于回答各区域机房数量对比)
        *   **推荐理由**：(e.g., "发现 'gxqy' 字段只有 5 个唯一值，适合做维度分析")
    *   **操作**：用户勾选想要的指标，或者修改名称/SQL。
4.  **一键入库**：
    *   点击“保存选中项”，批量写入 `metrics` 表。

## 4. 技术方案验证

### 输入 (Prompt Context)
```yaml
tables:
  - name: ck_fact_nanzi_resroom_hbase
    columns:
      - name: jfmc (机房名称)
      - name: gxqy (管辖区域)
      - name: jgzs (机柜总数)
```

### 输出 (Expected JSON)
```json
[
  {
    "name": "room_distribution_by_region",
    "display_name": "机房辖区分布",
    "calculation": "SELECT gxqy, count(1) as total FROM ck_fact_nanzi_resroom_hbase GROUP BY gxqy",
    "description": "统计不同管辖区域内的机房数量",
    "unit": "个",
    "type": "dimension"
  },
  {
    "name": "total_racks",
    "display_name": "机柜总规模",
    "calculation": "SELECT sum(jgzs) FROM ck_fact_nanzi_resroom_hbase",
    "description": "所有机房的机柜设计总数",
    "unit": "个",
    "type": "measure"
  }
]
```

## 5. 待讨论问题
1.  **SQL 片段 vs 完整 SQL**：目前的 `Metric` 定义通常只包含 `calculation` 逻辑（通常是 SQL 表达式部分）。如果包含 `GROUP BY`，系统执行时需要特殊处理（ChatBI 引擎目前是用 `SELECT {calculation} FROM ...` 还是完全由 LLM 拼装？）。
    *   *现状确认*：ChatBI 主要是让 LLM 参考 `Metrics` 里的逻辑来写 SQL。所以只要我们把 `calculation` 写清楚（可以是完整 SQL 或者表达式），LLM 都能看懂。
2.  **数据隐私**：采样数据发给 AI 是否敏感？(目前是私有部署模型/企业级 API，通常可接受，但需确认)。
