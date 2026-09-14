# 数据集质量治理分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个数据集计算并展示一个 0–100 的「数据资产质量治理分」，支撑治理优先级排序与资产质量展示，对所有角色只读可见，**不接入** AI 检索/编排权重。

**Architecture:** 新增一个纯函数评分服务 `metadata_quality_score_service`，只消费巡检已经产出的汇总计数（类型不匹配数、字段缺失/新增数、备注缺失数、整表缺失数），不引入任何新的数据内容扫描。评分在巡检结算时写入 `meta_datasets.quality_score / quality_breakdown / quality_scored_at`，由现有 `GET /datasets` 接口经 Pydantic `from_attributes` 自动带出，前端做排序与徽标展示。

**Tech Stack:** Python 3.11 / SQLAlchemy 2.x async / Pydantic 2 / FastAPI / Vue 3 + TypeScript + Vite 7 + Tailwind CSS 3 / pytest / MySQL(`db-prod/`) + PostgreSQL(`db-prod-pg/`) 双迁移。

---

## 背景与基线约束

- **基线**：本计划在 `dev-agentscope` 分支当前工作区之上继续。工作区此时已有一批**未提交**的 metadata 漂移治理改动（新增字段 AI 补全 / 备注缺失 `missing_comment` 等）。开工前先 `git status` 确认；若只想隔离本功能，可先在当前改动上提交或 stash 后再开始。
- **前置修复建议**：该批已提交改动当前 `pytest` 为红（`test_normalize_column_type_mapping`、`test_batch_resolve_alerts_add_column_auto_ai` 失败）。本计划不依赖它们，但**合入前应先修绿**，避免测试套件持续失真。
- **迁移纪律**（AGENTS.md）：只新增迁移 SQL 文件，**不**直接改本地/线上数据库。MySQL 用 `db-prod/`，PostgreSQL 用 `db-prod-pg/`，两套不可混用。
- **执行纪律**（AGENTS.md）：Agent **不**运行 `./dev.sh`、部署脚本或生产库操作；服务启停由用户在控制台执行。
- **口径固化**：本版分数仅作展示与人工排序，**不**作为 AI 检索/编排的排序权重，**不**做质量门禁拦截。

## 评分口径（v1，权重公开可解释）

分数 = 100 分制加权平均，四个维度（权重合计 100）。每个维度分数 = `round((1 - 问题占比) * 100)`，问题占比 = `问题计数 / 分母`，分母为 0 时该维度按满分处理。

| 维度 key | 名称 | 权重 | 问题计数 | 分母 |
|---|---|---|---|---|
| `consistency` | 类型一致性 | 30 | `mismatch_count` | `columns_scanned` |
| `coverage` | 结构覆盖 | 25 | `stale_count + new_count` | `columns_scanned` |
| `documentation` | 备注完备度 | 25 | `missing_comment_count` | `columns_scanned` |
| `table_integrity` | 表完整性 | 20 | `missing_tables_count` | `tables_scanned` |

等级：`≥90 优秀(excellent)` / `≥75 良好(good)` / `≥60 一般(fair)` / `<60 较差(poor)`。

---

## File Structure

**新增：**
- `app/services/metadata_quality_score_service.py` — 纯函数评分服务，唯一职责：把巡检计数换算成分数与维度明细。
- `tests/services/test_metadata_quality_score_service.py` — 评分函数单元测试（不需要 DB）。
- `db-prod/V155-add_metadata_quality_score.sql` — MySQL 迁移。
- `db-prod-pg/V56-add_metadata_quality_score.sql` — PostgreSQL 迁移。
- `docs/superpowers/plans/2026-09-14-dataset-quality-score.md` — 本计划。

**修改：**
- `app/models/metadata.py` — `MetaDataset` 新增 3 个字段。
- `app/schemas/metadata.py` — `DatasetResponse` 暴露 3 个字段。
- `app/services/metadata_inspection_service.py` — 两条巡检结算路径写入分数。
- `frontend/src/api/metadata.ts` — `Dataset` 接口补类型。
- `frontend/src/views/MetadataDatasets.vue` — 排序字段、排序逻辑、卡片徽标、列表列。
- `tests/CHECKLIST.md` — 记录本次交付与验证方式。

---

## Task 1: 纯函数评分服务

**Files:**
- Create: `app/services/metadata_quality_score_service.py`
- Test: `tests/services/test_metadata_quality_score_service.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/services/test_metadata_quality_score_service.py`：

```python
"""数据集质量治理分纯函数单元测试（不依赖数据库）。"""

from app.services.metadata_quality_score_service import (
    DIMENSION_WEIGHTS,
    compute_quality_score,
)


def test_weights_sum_to_100():
    assert sum(DIMENSION_WEIGHTS.values()) == 100


def test_perfect_dataset_scores_100():
    result = compute_quality_score(
        tables_scanned=3,
        columns_scanned=20,
        missing_tables_count=0,
        stale_count=0,
        new_count=0,
        mismatch_count=0,
        missing_comment_count=0,
    )
    assert result["score"] == 100
    assert result["level"] == "excellent"
    assert all(d["score"] == 100 for d in result["dimensions"])
    assert {d["key"] for d in result["dimensions"]} == set(DIMENSION_WEIGHTS)


def test_empty_dataset_is_full_score():
    """空数据集（0 表 0 列且无问题）不应被扣分。"""
    result = compute_quality_score(tables_scanned=0, columns_scanned=0)
    assert result["score"] == 100
    assert result["level"] == "excellent"


def test_mixed_problems_weighted_score():
    # consistency 80*30 + coverage 80*25 + documentation 70*25 + table_integrity 100*20
    # = 2400 + 2000 + 1750 + 2000 = 8150 -> 82 (good)
    result = compute_quality_score(
        tables_scanned=2,
        columns_scanned=10,
        missing_tables_count=0,
        stale_count=1,
        new_count=1,
        mismatch_count=2,
        missing_comment_count=3,
    )
    assert result["score"] == 82
    assert result["level"] == "good"
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["consistency"]["score"] == 80
    assert by_key["coverage"]["score"] == 80
    assert by_key["documentation"]["score"] == 70
    assert by_key["table_integrity"]["score"] == 100


def test_negative_and_overflow_counts_are_clamped():
    """计数负数按 0 处理；问题数超过分母时该维度封底 0 分，总分不为负。"""
    result = compute_quality_score(
        tables_scanned=1,
        columns_scanned=2,
        missing_tables_count=5,
        stale_count=-3,
        new_count=99,
        mismatch_count=99,
        missing_comment_count=99,
    )
    assert 0 <= result["score"] <= 100
    assert result["level"] == "poor"
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["consistency"]["score"] == 0
    assert by_key["coverage"]["score"] == 0
    assert by_key["documentation"]["score"] == 0
    assert by_key["table_integrity"]["score"] == 0


def test_level_thresholds():
    def level_for(score: int) -> str:
        # 通过构造仅 documentation 维度的问题数逼近目标分。
        # 4 个维度同权重难以精确命中，这里直接断言边界函数行为。
        from app.services.metadata_quality_score_service import _level_for

        return _level_for(score)[0]

    assert level_for(100) == "excellent"
    assert level_for(90) == "excellent"
    assert level_for(89) == "good"
    assert level_for(75) == "good"
    assert level_for(74) == "fair"
    assert level_for(60) == "fair"
    assert level_for(59) == "poor"
    assert level_for(0) == "poor"


def test_breakdown_contains_explainable_fields():
    result = compute_quality_score(tables_scanned=1, columns_scanned=4, mismatch_count=1)
    dim = {d["key"]: d for d in result["dimensions"]}["consistency"]
    assert dim["label"] == "类型一致性"
    assert dim["weight"] == 30
    assert dim["problem_count"] == 1
    assert dim["total"] == 4
    assert isinstance(dim["detail"], str) and dim["detail"]
    assert result["level_label"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/test_metadata_quality_score_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.metadata_quality_score_service'`

> 注意：本仓库 `pytest` 不在 PATH，且裸跑缺 `app` 模块；统一使用 `PYTHONPATH=. .venv/bin/python -m pytest`。

- [ ] **Step 3: Write minimal implementation**

创建 `app/services/metadata_quality_score_service.py`：

```python
"""数据资产质量治理分计算服务。

纯函数实现：仅消费巡检产出的汇总计数，不访问数据库、不扫描数据内容，
便于单元测试，并可在巡检结算处直接复用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 维度权重（合计 100）。口径公开，前端据此展示分数构成。
DIMENSION_WEIGHTS: Dict[str, int] = {
    "consistency": 30,       # 类型一致性：元数据声明类型与物理库一致的字段占比
    "coverage": 25,          # 结构覆盖：元数据字段与物理字段无缺失、无多出
    "documentation": 25,     # 备注完备：字段有有效中文业务备注的占比
    "table_integrity": 20,   # 表完整性：元数据表在物理库中存在的占比
}

# 分数等级：(最低分, 等级键, 中文标签)，按分数从高到低匹配。
LEVEL_THRESHOLDS: List[Tuple[int, str, str]] = [
    (90, "excellent", "优秀"),
    (75, "good", "良好"),
    (60, "fair", "一般"),
    (0, "poor", "较差"),
]

DIMENSION_META: Dict[str, Dict[str, str]] = {
    "consistency": {"label": "类型一致性", "detail": "元数据字段类型与物理库不一致"},
    "coverage": {"label": "结构覆盖", "detail": "元数据与物理库存在字段缺失或多出"},
    "documentation": {"label": "备注完备度", "detail": "字段缺少有效中文业务备注"},
    "table_integrity": {"label": "表完整性", "detail": "元数据表在物理库中已不存在"},
}


def _rate(problem_count: int, total: int) -> float:
    """计算问题占比并 clamp 到 [0, 1]；分母 <= 0 时按无问题处理。"""
    if total <= 0:
        return 0.0
    return min(max(problem_count / total, 0.0), 1.0)


def _level_for(score: int) -> Tuple[str, str]:
    """返回 (等级键, 中文标签)。"""
    for threshold, key, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return key, label
    return "poor", "较差"


def compute_quality_score(
    *,
    tables_scanned: int,
    columns_scanned: int,
    missing_tables_count: int = 0,
    stale_count: int = 0,
    new_count: int = 0,
    mismatch_count: int = 0,
    missing_comment_count: int = 0,
) -> Dict[str, Any]:
    """基于巡检验出的问题计数，计算数据集级质量治理分（0-100）。

    返回 {"score", "level", "level_label", "dimensions"}；
    dimensions 每项含 key/label/weight/score/problem_count/total/detail，供前端解释扣分原因。
    """
    col_total = max(columns_scanned, 0)
    tbl_total = max(tables_scanned, 0)

    problem_counts: Dict[str, int] = {
        "consistency": max(mismatch_count, 0),
        "coverage": max(stale_count, 0) + max(new_count, 0),
        "documentation": max(missing_comment_count, 0),
        "table_integrity": max(missing_tables_count, 0),
    }
    totals: Dict[str, int] = {
        "consistency": col_total,
        "coverage": col_total,
        "documentation": col_total,
        "table_integrity": tbl_total,
    }

    dimensions: List[Dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0
    for key, weight in DIMENSION_WEIGHTS.items():
        count = problem_counts[key]
        rate = _rate(count, totals[key])
        dim_score = int(round((1.0 - rate) * 100))
        dim_score = min(max(dim_score, 0), 100)
        weighted_sum += dim_score * weight
        weight_total += weight
        dimensions.append(
            {
                "key": key,
                "label": DIMENSION_META[key]["label"],
                "weight": weight,
                "score": dim_score,
                "problem_count": count,
                "total": totals[key],
                "detail": DIMENSION_META[key]["detail"],
            }
        )

    overall = int(round(weighted_sum / weight_total)) if weight_total else 100
    overall = min(max(overall, 0), 100)
    level_key, level_label = _level_for(overall)

    return {
        "score": overall,
        "level": level_key,
        "level_label": level_label,
        "dimensions": dimensions,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/test_metadata_quality_score_service.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/metadata_quality_score_service.py tests/services/test_metadata_quality_score_service.py
git commit -m "feat(metadata): 新增数据集质量治理分纯函数评分服务与单测"
```

---

## Task 2: 模型字段 + 双库迁移 SQL

**Files:**
- Modify: `app/models/metadata.py` (`MetaDataset`，约 6-33 行)
- Create: `db-prod/V155-add_metadata_quality_score.sql`
- Create: `db-prod-pg/V56-add_metadata_quality_score.sql`

- [ ] **Step 1: 为 `MetaDataset` 增加字段**

在 `app/models/metadata.py` 的 `MetaDataset` 类中，`row_filter_config` 之后、`# RAGFlow Integration Fields` 之前插入：

```python
    # 资产质量治理分（巡检结算时写入，0-100）
    quality_score = Column(Integer, nullable=True, comment='数据资产质量治理分 0-100')
    quality_breakdown = Column(JSON, nullable=True, comment='质量分维度明细')
    quality_scored_at = Column(DateTime, nullable=True, comment='最近一次质量评分时间')
```

- [ ] **Step 2: 新增 MySQL 迁移 SQL**

创建 `db-prod/V155-add_metadata_quality_score.sql`：

```sql
-- V155: 数据集质量治理分（巡检结算时写入，仅新增列，不改动既有数据）
ALTER TABLE `meta_datasets`
  ADD COLUMN `quality_score` INT NULL COMMENT '数据资产质量治理分 0-100',
  ADD COLUMN `quality_breakdown` JSON NULL COMMENT '质量分维度明细',
  ADD COLUMN `quality_scored_at` DATETIME NULL COMMENT '最近一次质量评分时间';
```

- [ ] **Step 3: 新增 PostgreSQL 迁移 SQL**

创建 `db-prod-pg/V56-add_metadata_quality_score.sql`：

```sql
-- V56: 数据集质量治理分（巡检结算时写入，仅新增列，不改动既有数据）
ALTER TABLE meta_datasets
  ADD COLUMN IF NOT EXISTS quality_score INTEGER,
  ADD COLUMN IF NOT EXISTS quality_breakdown JSONB,
  ADD COLUMN IF NOT EXISTS quality_scored_at TIMESTAMP;

COMMENT ON COLUMN meta_datasets.quality_score IS '数据资产质量治理分 0-100';
COMMENT ON COLUMN meta_datasets.quality_breakdown IS '质量分维度明细';
COMMENT ON COLUMN meta_datasets.quality_scored_at IS '最近一次质量评分时间';
```

- [ ] **Step 4: 验证模型可导入且字段就位**

Run: `python -c "from app.models.metadata import MetaDataset; print([c.name for c in MetaDataset.__table__.columns if c.name.startswith('quality')])"`
Expected: `['quality_score', 'quality_breakdown', 'quality_scored_at']`

- [ ] **Step 5: Commit**

```bash
git add app/models/metadata.py db-prod/V155-add_metadata_quality_score.sql db-prod-pg/V56-add_metadata_quality_score.sql
git commit -m "feat(metadata): meta_datasets 新增质量治理分字段与 MySQL/PG 迁移"
```

---

## Task 3: Schema 暴露质量分

**Files:**
- Modify: `app/schemas/metadata.py` (`DatasetResponse`，约 92-107 行)

- [ ] **Step 1: 为 `DatasetResponse` 增加字段**

在 `app/schemas/metadata.py` 的 `DatasetResponse` 中，`relationship_count: int = 0` 之后、`model_config = ConfigDict(from_attributes=True)` 之前插入：

```python
    # 资产质量治理分（巡检结算时写入；未巡检过则为 None）
    quality_score: Optional[int] = None
    quality_breakdown: Optional[Dict[str, Any]] = None
    quality_scored_at: Optional[datetime] = None
```

- [ ] **Step 2: 验证 schema 可实例化**

Run: `python -c "from app.schemas.metadata import DatasetResponse; print('quality_score' in DatasetResponse.model_fields)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add app/schemas/metadata.py
git commit -m "feat(metadata): DatasetResponse 暴露质量治理分字段"
```

---

## Task 4: 单数据集巡检结算写入分数

**Files:**
- Modify: `app/services/metadata_inspection_service.py`（导入区约 9-16 行；`inspect_dataset` 结算处约 433-438 行）

- [ ] **Step 1: 增加导入**

在 `app/services/metadata_inspection_service.py` 顶部导入区：

```python
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
```

并在服务导入中加入评分服务（与其他 `app.services.*` 导入放在一起）：

```python
from app.services.metadata_quality_score_service import compute_quality_score
```

- [ ] **Step 2: 在 `inspect_dataset` 的 commit 前写入分数**

定位 `inspect_dataset` 中这段（约 433-438 行）：

```python
        scan_res = await cls._scan_dataset_tables(
            db, dataset, adapter, emit, progress_base=30, progress_range=60
        )

        # 4. 提交告警变更
        await db.commit()
```

替换为：

```python
        scan_res = await cls._scan_dataset_tables(
            db, dataset, adapter, emit, progress_base=30, progress_range=60
        )

        # 4. 结算数据资产质量治理分（供列表展示与治理优先级排序）
        quality = compute_quality_score(
            tables_scanned=scan_res["tables_scanned"],
            columns_scanned=scan_res["columns_scanned"],
            missing_tables_count=scan_res.get("missing_tables_count", 0),
            stale_count=scan_res.get("stale_count", 0),
            new_count=scan_res.get("new_count", 0),
            mismatch_count=scan_res.get("mismatch_count", 0),
            missing_comment_count=scan_res.get("missing_comment_count", 0),
        )
        dataset.quality_score = quality["score"]
        dataset.quality_breakdown = quality
        dataset.quality_scored_at = datetime.now()

        # 5. 提交告警变更与质量分
        await db.commit()
```

- [ ] **Step 3: 在返回体中带上分数（便于前端巡检完成即时显示）**

将该方法 `return {` 字典中的 `"mismatch_count": total_mismatch,` 之后补一行：

```python
            "quality_score": dataset.quality_score,
```

- [ ] **Step 4: 验证既有单数据集巡检测试未被破坏**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/test_metadata_inspection_service.py -v`
Expected: PASS（全部通过；测试使用 AsyncMock，`scan_res` 计数为 0，分数写入不改变断言）

- [ ] **Step 5: Commit**

```bash
git add app/services/metadata_inspection_service.py
git commit -m "feat(metadata): 单数据集巡检结算写入质量治理分"
```

---

## Task 5: 全库巡检结算写入分数

**Files:**
- Modify: `app/services/metadata_inspection_service.py`（`inspect_all_datasets` 循环内约 594-612 行）

- [ ] **Step 1: 在逐数据集循环中写入分数**

定位 `inspect_all_datasets` 中这段（约 594-612 行）：

```python
            scan_res = await cls._scan_dataset_tables(
                db,
                full_ds,
                adapter,
                emit,
                progress_base=ds_base_pct,
                progress_range=ds_range_pct,
                prefix=ds_prefix,
            )

            t_scanned = scan_res["tables_scanned"]
```

在 `scan_res = ...` 之后、`t_scanned = ...` 之前插入：

```python
            # 结算该数据集的质量治理分（与全库告警提交一并持久化）
            quality = compute_quality_score(
                tables_scanned=scan_res["tables_scanned"],
                columns_scanned=scan_res["columns_scanned"],
                missing_tables_count=scan_res.get("missing_tables_count", 0),
                stale_count=scan_res.get("stale_count", 0),
                new_count=scan_res.get("new_count", 0),
                mismatch_count=scan_res.get("mismatch_count", 0),
                missing_comment_count=scan_res.get("missing_comment_count", 0),
            )
            full_ds.quality_score = quality["score"]
            full_ds.quality_breakdown = quality
            full_ds.quality_scored_at = datetime.now()
```

> 说明：`inspect_all_datasets` 已有的 `await db.commit()`（约 648 行）会一并持久化这些字段，无需新增 commit。

- [ ] **Step 2: 验证全库巡检相关测试仍通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/test_metadata_inspection_service.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app/services/metadata_inspection_service.py
git commit -m "feat(metadata): 全库巡检逐数据集结算质量治理分"
```

---

## Task 6: 前端类型、排序与展示

**Files:**
- Modify: `frontend/src/api/metadata.ts`（`Dataset` 接口，20-40 行）
- Modify: `frontend/src/views/MetadataDatasets.vue`（脚本约 656-725 行；卡片 1573-1583 行；列表表头 1693-1730 行；列表行 1784-1811 行）

- [ ] **Step 1: `Dataset` 接口补类型**

在 `frontend/src/api/metadata.ts` 的 `Dataset` 接口中，`relationship_count?: number;` 之后插入：

```ts
  quality_score?: number | null;
  quality_breakdown?: {
    score: number;
    level: string;
    level_label: string;
    dimensions: {
      key: string;
      label: string;
      weight: number;
      score: number;
      problem_count: number;
      total: number;
      detail: string;
    }[];
  } | null;
  quality_scored_at?: string | null;
```

- [ ] **Step 2: 扩展排序字段与比较逻辑**

在 `frontend/src/views/MetadataDatasets.vue` 中：

将 `DatasetSortField` 类型（约 656 行）改为：

```ts
type DatasetSortField = 'display_name' | 'status' | 'table_count' | 'quality_score' | 'rag_sync_status' | 'updated_at'
```

在 `compareDatasets` 的 `switch` 中，`case 'table_count': { ... }` 之后插入：

```ts
    case 'quality_score':
      // 未评分（null）排最后：升序时 -1 在前，降序时 -1 在后
      return ((a.quality_score ?? -1) - (b.quality_score ?? -1)) * dir
```

- [ ] **Step 3: 增加分数样式与提示辅助函数**

在 `compareDatasets` 定义之后、`displayDatasets` 之前插入：

```ts
const qualityBadgeClass = (score?: number | null): string => {
  if (score === undefined || score === null) return 'bg-gray-50 text-gray-400 border-gray-200'
  if (score >= 90) return 'bg-emerald-50 text-emerald-600 border-emerald-200'
  if (score >= 75) return 'bg-blue-50 text-blue-600 border-blue-200'
  if (score >= 60) return 'bg-amber-50 text-amber-600 border-amber-200'
  return 'bg-rose-50 text-rose-600 border-rose-200'
}

const qualityTooltip = (ds: Dataset): string => {
  const b = ds.quality_breakdown
  if (!b || !b.dimensions?.length) return '暂无质量评分，执行一次 Schema 巡检后生成'
  const parts = b.dimensions.map(
    (d) => `${d.label} ${d.score} 分（问题 ${d.problem_count}/${d.total}）`
  )
  return `质量治理分 ${b.score}（${b.level_label}）\n${parts.join('\n')}`
}
```

- [ ] **Step 4: 卡片视图增加质量分芯片**

在卡片统计芯片区（约 1573-1583 行）的「关系」芯片之后插入：

```html
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-medium"
              :class="qualityBadgeClass(ds.quality_score)"
              :title="qualityTooltip(ds)"
            >
              质量分 <b>{{ ds.quality_score ?? '—' }}</b>
            </span>
```

- [ ] **Step 5: 列表表头新增「质量分」列并收窄 RAG 列**

将列表表头中 RAG 按钮（约 1715-1721 行）整段替换为下面两块（质量分按钮 + 收窄后的 RAG 按钮）：

```html
          <button type="button" class="col-span-1 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('quality_score')">
            <span>质量分</span>
            <svg v-if="sortField === 'quality_score'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-1 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('rag_sync_status')">
            <span>RAG</span>
            <svg v-if="sortField === 'rag_sync_status'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
```

> 列跨度核对（`grid-cols-12`）：数据集 3 + 状态 2 + 统计 2 + 质量分 1 + RAG 1 + 更新时间 1 + 操作 2 = 12。

- [ ] **Step 6: 列表行新增质量分单元格并收窄 RAG 单元格**

在列表行「Stats」块（`<!-- Stats -->` 到其闭合 `</div>`，约 1783-1794 行）之后、「RAG Status」注释之前插入：

```html
             <!-- Quality Score -->
             <div class="col-span-1">
                <span
                   class="inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-bold"
                   :class="qualityBadgeClass(ds.quality_score)"
                   :title="qualityTooltip(ds)"
                >
                   {{ ds.quality_score ?? '—' }}
                </span>
             </div>
```

将 RAG 单元格（`<!-- RAG Status -->` 下的 `<div class="col-span-2">`，约 1797 行）改为：

```html
             <div class="col-span-1">
```

> 行跨度必须与表头一致：3 + 2 + 2 + 1(质量分) + 1(RAG) + 1 + 2 = 12。

- [ ] **Step 7: 前端类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误输出（exit 0）

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/metadata.ts frontend/src/views/MetadataDatasets.vue
git commit -m "feat(metadata): 数据集列表与卡片展示质量治理分并支持排序"
```

---

## Task 7: 更新 tests/CHECKLIST.md（⚠️ 已延后，见说明）

> **延后原因**：`tests/CHECKLIST.md` 是**表格结构**（新条目为表格行，插入在表头分隔行 `| :--- |` 之后，最新在上），并非自由段落。且该文件当时正被**另一个并发会话**编辑（已写入 review 批次修复条目），与本次要插入的位置相同，直接提交会与其改动混在一起或产生冲突。
>
> **执行时机**：等并发会话提交 review 批次、`tests/CHECKLIST.md` 回到干净基线后再执行本任务；届时按下面的表格行格式插入到表头分隔行之后并单独提交。

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 在表格顶部（`| :--- | :--- | :--- | :--- | :--- |` 之后）插入一行**

```markdown
| 数据集质量治理分（0-100，只读展示：类型一致性 30 + 结构覆盖 25 + 备注完备度 25 + 表完整性 20，巡检结算写入，仅供展示与治理优先级排序，不接入 AI 检索/编排权重） (Dataset Quality Governance Score, Read-Only, Inspection-Settled) | `app/services/metadata_quality_score_service.py`, `app/models/metadata.py`, `app/schemas/metadata.py`, `app/services/metadata_inspection_service.py`, `frontend/src/api/metadata.ts`, `frontend/src/views/MetadataDatasets.vue`, `db-prod/V155-add_metadata_quality_score.sql`, `db-prod-pg/V56-add_metadata_quality_score.sql`, `tests/services/test_metadata_quality_score_service.py`, `tests/CHECKLIST.md` | **数据集级质量治理分与治理优先级排序闭环**：① **纯函数评分服务 `compute_quality_score`**：仅消费巡检已产出的汇总计数（`mismatch_count` / `stale_count + new_count` / `missing_comment_count` / `missing_tables_count`）换算为 0-100 分与可解释的维度明细（`score/level/level_label/dimensions`），不访问数据库、不扫描数据内容；权重公开（类型一致性 30、结构覆盖 25、备注完备度 25、表完整性 20），等级 ≥90 优秀 / ≥75 良好 / ≥60 一般 / <60 较差；② **持久化字段与双库幂等迁移**：`meta_datasets` 新增 `quality_score` / `quality_breakdown` / `quality_scored_at`，提供 MySQL `V155` 与 PostgreSQL `V56` 迁移；③ **巡检结算写入**：单数据集 `inspect_dataset` 与全库 `inspect_all_datasets` 两条路径在告警提交前结算并写入分数；④ **前端只读展示与排序**：`GET /datasets` 经 Pydantic `from_attributes` 自动带出字段，数据集卡片与列表新增质量分徽标（按分段着色）、悬停展示各维度得分与问题计数、列表支持按质量分排序（未评分排最后）；所有角色只读可见，无新增权限依赖；⑤ **测试**：新增 7 项纯函数单测，巡检 10 项回归全绿，`vue-tsc --noEmit` 0 报错。 | ✅ 7 项评分单测 + 10 项巡检测试全绿；`vue-tsc --noEmit` 0 报错；未代跑真实服务与启动脚本 | 2026-09-14 |
```

- [ ] **Step 2: Commit**

```bash
git add tests/CHECKLIST.md
git commit -m "docs(test): 记录数据集质量治理分交付与验证方式"
```

---

## Self-Review

**1. Spec coverage:**
- 「数据集级综合分」→ Task 1 纯函数 + Task 2 持久化字段 + Task 4/5 巡检结算写入。✔
- 「治理优先级排序」→ Task 6 列表排序字段 `quality_score`。✔
- 「资产质量展示」→ Task 6 卡片芯片 + 列表列 + 可解释 tooltip（维度明细）。✔
- 「不进 AI 检索/编排权重」→ 全程仅新增展示字段，未触碰任何检索/编排代码路径。✔
- 「所有角色只读可见」→ 复用现有 `GET /datasets`（无新增权限依赖），无写接口。✔
- 「只用现有巡检信号、不引入内容级扫描」→ 评分入参全部来自 `_scan_dataset_tables` 的返回计数。✔

**2. Placeholder scan:** 无 TBD/TODO；每个改动步骤均含完整代码块与精确 `file:line` 锚点。

**3. Type consistency:**
- 评分服务返回键 `score / level / level_label / dimensions`，与 Task 6 前端 `quality_breakdown` 类型、tooltip 取值字段（`label/score/problem_count/total`）一致。
- ORM 字段名 `quality_score / quality_breakdown / quality_scored_at` 在模型、schema、巡检写入、前端类型四处完全一致。
- `compute_quality_score` 关键字参数名与两处巡检调用处逐字一致（`tables_scanned / columns_scanned / missing_tables_count / stale_count / new_count / mismatch_count / missing_comment_count`）。
- `_level_for` 在测试中被导入使用，实现中确实存在该函数。✔

**风险与回滚：**
- 迁移仅新增可空列，不改动既有数据，回滚只需 `DROP COLUMN`（由用户按需在控制台执行）。
- 前端列表列跨度有调整（RAG 由 2→1），实施后需人工目视确认窄屏下列对齐；若 RAG 徽标显示拥挤，可将 RAG 保留 2 并把「质量分」并入「统计」单元格。
