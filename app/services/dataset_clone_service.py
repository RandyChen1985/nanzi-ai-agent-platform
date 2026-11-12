"""元数据数据集「复制新建」服务。

一次复制 = 一个事务里建出数据集 + 全部子资源，并写入初始化变更日志。

设计要点（与 prototypes/2026-11-06-metadata-dataset-clone.html 对齐）：

- 提交的是向导里的**整份草稿**：表/字段/关系/指标都可以在中间步骤改或删，
  保存时以草稿为准，不再回源库二次读取——否则用户改过的内容会被源数据覆盖。
- 关系用物理表名引用（``source_table`` / ``target_table``），插入表拿到新 id 后
  再做一次名字到 id 的映射；引用不到的关系直接跳过并计入 ``skipped_relationships``。
- 不复制：底层数据、RAGFlow 侧数据集、质量分、成员授权；
  ``rag_sync_status`` 保持 0（未同步），等用户手动同步。
- 行级策略（``enable_data_perm`` / ``row_filter_config``）默认不继承，
  只有显式传 ``copy_permissions=true`` 才带走——行级规则通常绑具体部门/人。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_connection import MetaDbConnectionConfig
from app.models.metadata import (
    MetaColumn,
    MetaDataset,
    MetaMetric,
    MetaRelationship,
    MetaTable,
)
from app.services.changelog_service import ChangelogService

logger = logging.getLogger(__name__)

# 与前端输入提示一致：小写字母开头，只含 a-z / 0-9 / _，4–64 位
DATASET_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{3,63}$")
MAX_DISPLAY_NAME_LENGTH = 100
MAX_TABLES = 200


class CloneValidationError(ValueError):
    """复制参数不合法：endpoint 转成 400，前端直接展示 message。"""


class DatasetCloneService:
    """数据集复制新建。"""

    # ------------------------------------------------------------------
    # 唯一性预检
    # ------------------------------------------------------------------
    @staticmethod
    async def _name_taken(conn: AsyncSession, name: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(MetaDataset.id).where(func.lower(MetaDataset.name) == name.strip().lower())
        if exclude_id:
            stmt = stmt.where(MetaDataset.id != exclude_id)
        return (await conn.execute(stmt.limit(1))).first() is not None

    @staticmethod
    async def _display_name_taken(
        conn: AsyncSession, display_name: str, exclude_id: Optional[int] = None
    ) -> bool:
        stmt = select(MetaDataset.id).where(
            func.lower(MetaDataset.display_name) == display_name.strip().lower()
        )
        if exclude_id:
            stmt = stmt.where(MetaDataset.id != exclude_id)
        return (await conn.execute(stmt.limit(1))).first() is not None

    @staticmethod
    async def check_unique(
        conn: AsyncSession,
        name: str,
        display_name: Optional[str] = None,
        exclude_id: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """向导第 1 步的实时查重。返回结构与智能体 name-availability 对齐。"""
        raw_name = (name or "").strip()
        if not raw_name:
            name_result = {"available": False, "reason": "empty", "message": "请填写元数据 ID"}
        elif not DATASET_NAME_PATTERN.match(raw_name):
            name_result = {
                "available": False,
                "reason": "invalid_format",
                "message": "需小写字母开头，只含 a-z、0-9、_，长度 4–64 位",
            }
        elif await DatasetCloneService._name_taken(conn, raw_name, exclude_id):
            name_result = {"available": False, "reason": "taken", "message": "该 ID 已被其他数据集占用"}
        else:
            name_result = {"available": True, "reason": "ok", "message": "可用，全平台唯一"}

        raw_display = (display_name or "").strip()
        if not raw_display:
            display_result = {"available": False, "reason": "empty", "message": "请填写显示名称"}
        elif len(raw_display) > MAX_DISPLAY_NAME_LENGTH:
            display_result = {
                "available": False,
                "reason": "too_long",
                "message": f"名称最长 {MAX_DISPLAY_NAME_LENGTH} 个字符",
            }
        elif await DatasetCloneService._display_name_taken(conn, raw_display, exclude_id):
            display_result = {
                "available": False,
                "reason": "taken",
                "message": "该名称已被其他数据集占用",
            }
        else:
            display_result = {"available": True, "reason": "ok", "message": "可用，全平台唯一"}

        return {"name": name_result, "display_name": display_result}

    # ------------------------------------------------------------------
    # 复制
    # ------------------------------------------------------------------
    @staticmethod
    async def _resolve_data_source(
        conn: AsyncSession, source: MetaDataset, requested: Optional[str]
    ) -> str:
        """数据源只接受已配置连接的 name。

        沿用源数据集当前值时不校验（历史数据里存在 ``clickhouse`` 这类默认占位，
        它们不在连接表里，复制却必须能带过去）；换库则必须是真实存在的连接。
        """
        candidate = (requested or "").strip() or (source.data_source or "").strip()
        if not candidate:
            raise CloneValidationError("请选择数据源")
        if candidate == (source.data_source or ""):
            return candidate
        exists = await conn.scalar(
            select(MetaDbConnectionConfig.id).where(MetaDbConnectionConfig.name == candidate)
        )
        if not exists:
            raise CloneValidationError("数据源不存在或不可用，请从数据源管理里重新选择")
        return candidate

    @staticmethod
    async def clone_dataset(
        conn: AsyncSession,
        source_id: int,
        payload: Any,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按草稿创建副本。抛 ``LookupError`` 表示源不存在，``CloneValidationError`` 表示参数问题。"""
        source = (
            await conn.execute(select(MetaDataset).where(MetaDataset.id == source_id))
        ).scalar_one_or_none()
        if not source:
            raise LookupError("source_dataset_not_found")

        name = (payload.name or "").strip()
        if not DATASET_NAME_PATTERN.match(name):
            raise CloneValidationError("元数据 ID 需小写字母开头，只含 a-z、0-9、_，长度 4–64 位")
        if await DatasetCloneService._name_taken(conn, name):
            raise CloneValidationError(f"数据集 ID「{name}」已被占用，请换一个")

        display_name = (payload.display_name or "").strip() or name
        if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise CloneValidationError(f"显示名称最长 {MAX_DISPLAY_NAME_LENGTH} 个字符")
        if await DatasetCloneService._display_name_taken(conn, display_name):
            raise CloneValidationError(f"名称「{display_name}」已被占用，请换一个")

        data_source = await DatasetCloneService._resolve_data_source(
            conn, source, payload.data_source
        )

        tables: List[Any] = list(payload.tables or [])
        if not tables:
            raise CloneValidationError("至少要保留一张表")
        if len(tables) > MAX_TABLES:
            raise CloneValidationError(f"单次最多复制 {MAX_TABLES} 张表，请分批处理")

        # 先做完全量校验再落库，避免插到一半才发现重名
        seen_tables: set[str] = set()
        for table in tables:
            physical = (table.physical_name or "").strip()
            if not physical:
                raise CloneValidationError("表的物理名不能为空")
            if physical in seen_tables:
                raise CloneValidationError(f"表「{physical}」重复")
            seen_tables.add(physical)

        dataset = MetaDataset(
            name=name,
            display_name=display_name,
            description=payload.description,
            tags=list(payload.tags or []),
            # 分组默认沿用源数据集（复制出来的库通常仍属于同一个业务域）：只有调用方**完全没提**
            # 这个字段时才沿用。用 `is not None` 判断会把「显式传 null = 副本设为未分组」的意图吞掉。
            group_name=(
                payload.group_name
                if "group_name" in payload.model_fields_set
                else source.group_name
            ),
            data_source=data_source,
            # 沿用源数据集的启停状态：源是停用就复制成停用，由用户在列表里决定何时启用
            status=source.status if source.status is not None else 1,
            enable_data_perm=bool(payload.copy_permissions and source.enable_data_perm),
            row_filter_config=source.row_filter_config if payload.copy_permissions else None,
        )
        conn.add(dataset)
        await conn.flush()

        created_tables: List[MetaTable] = []
        table_id_by_name: Dict[str, int] = {}
        column_count = 0
        for table in tables:
            physical = table.physical_name.strip()
            row = MetaTable(
                dataset_id=dataset.id,
                physical_name=physical,
                term=(table.term or "").strip() or physical,
                description=table.description,
                synonyms=list(table.synonyms or []),
                partition_fields=list(table.partition_fields or []),
                index_fields=list(table.index_fields or []),
                status=1,
            )
            conn.add(row)
            # flush 只为拿到自增 id（子表与关系都要用），列本身随后批量 add
            await conn.flush()
            table_id_by_name[physical] = row.id
            created_tables.append(row)

            for column in table.columns or []:
                conn.add(
                    MetaColumn(
                        table_id=row.id,
                        physical_name=column.physical_name,
                        term=(column.term or "").strip() or column.physical_name,
                        type=column.type or "String",
                        description=column.description,
                        enums=column.enums or [],
                        synonyms=list(column.synonyms or []),
                        # 模型里是 Integer，前端传 bool，这里统一成 0/1
                        is_primary=1 if column.is_primary else 0,
                        dimension_role=column.dimension_role or "none",
                        hierarchy_group=column.hierarchy_group,
                        hierarchy_order=column.hierarchy_order,
                    )
                )
                column_count += 1

        created_relationships: List[MetaRelationship] = []
        skipped_relationships = 0
        for relation in payload.relationships or []:
            source_table_id = table_id_by_name.get((relation.source_table or "").strip())
            target_table_id = table_id_by_name.get((relation.target_table or "").strip())
            if not source_table_id or not target_table_id:
                # 用户在向导里删了表但没删关系：跳过而不是整单失败
                skipped_relationships += 1
                continue
            row = MetaRelationship(
                source_table_id=source_table_id,
                target_table_id=target_table_id,
                join_condition=relation.join_condition or "",
                join_type=(relation.join_type or "LEFT").strip().upper(),
                description=relation.description,
            )
            conn.add(row)
            await conn.flush()
            created_relationships.append(row)

        created_metrics: List[MetaMetric] = []
        seen_metrics: set[str] = set()
        for metric in payload.metrics or []:
            metric_name = (metric.name or "").strip()
            if not metric_name or metric_name in seen_metrics:
                continue
            seen_metrics.add(metric_name)
            row = MetaMetric(
                dataset_id=dataset.id,
                name=metric_name,
                display_name=(metric.display_name or "").strip() or metric_name,
                description=metric.description,
                calculation_logic=metric.calculation_logic or "",
                unit=metric.unit,
                tags=list(metric.tags or []),
            )
            conn.add(row)
            created_metrics.append(row)

        await conn.flush()

        summary = {
            "tables": len(created_tables),
            "columns": column_count,
            "relationships": len(created_relationships),
            "metrics": len(created_metrics),
            "skipped_relationships": skipped_relationships,
        }
        await DatasetCloneService._write_changelogs(
            conn,
            dataset=dataset,
            source=source,
            summary=summary,
            tables=created_tables,
            relationships=created_relationships,
            metrics=created_metrics,
            user_id=user_id,
            user_name=user_name,
            log_mode=(payload.log_mode or "detail"),
        )

        await conn.commit()
        await conn.refresh(dataset)

        dataset.table_count = summary["tables"]
        dataset.metric_count = summary["metrics"]
        dataset.relationship_count = summary["relationships"]

        await DatasetCloneService._after_create(dataset.id)
        return {"dataset": dataset, "summary": summary}

    # ------------------------------------------------------------------
    # 变更日志
    # ------------------------------------------------------------------
    @staticmethod
    async def _write_changelogs(
        conn: AsyncSession,
        *,
        dataset: MetaDataset,
        source: MetaDataset,
        summary: Dict[str, int],
        tables: List[MetaTable],
        relationships: List[MetaRelationship],
        metrics: List[MetaMetric],
        user_id: Optional[int],
        user_name: Optional[str],
        log_mode: str,
    ) -> None:
        source_label = source.display_name or source.name
        reason = f"由数据集「{source_label}」({source.name}) 复制新建"
        await ChangelogService.log_change(
            db=conn,
            resource_type="dataset",
            resource_id=str(dataset.id),
            # 复用既有 create 枚举，避免调用方按 operation 过滤时漏掉复制出来的记录；
            # 「这是一次复制」由 new_data.source_dataset_id / clone 标记表达
            operation="create",
            user_id=user_id,
            user_name=user_name,
            new_data={
                "id": dataset.id,
                "name": dataset.name,
                "display_name": dataset.display_name,
                "description": dataset.description,
                "tags": dataset.tags,
                "data_source": dataset.data_source,
                "status": dataset.status,
                "source_dataset_id": source.id,
                "source_dataset_name": source.name,
                "clone": True,
                "clone_summary": summary,
            },
            reason=reason,
        )

        if log_mode != "detail":
            return

        for table in tables:
            await ChangelogService.log_change(
                db=conn,
                resource_type="table",
                resource_id=f"{dataset.id}:{table.physical_name}",
                operation="create",
                user_id=user_id,
                user_name=user_name,
                new_data={
                    "physical_name": table.physical_name,
                    "term": table.term,
                    "description": table.description,
                },
                reason=f"随数据集复制创建（源表 {table.physical_name}）",
            )
        for relation in relationships:
            await ChangelogService.log_change(
                db=conn,
                resource_type="relationship",
                resource_id=str(relation.id),
                operation="create",
                user_id=user_id,
                user_name=user_name,
                new_data={
                    "source_table_id": relation.source_table_id,
                    "target_table_id": relation.target_table_id,
                    "join_condition": relation.join_condition,
                    "join_type": relation.join_type,
                },
                reason="随数据集复制创建",
            )
        for metric in metrics:
            await ChangelogService.log_change(
                db=conn,
                resource_type="metric",
                resource_id=str(metric.name),
                operation="create",
                user_id=user_id,
                user_name=user_name,
                new_data={
                    "name": metric.name,
                    "display_name": metric.display_name,
                    "calculation_logic": metric.calculation_logic,
                    "unit": metric.unit,
                },
                reason="随数据集复制创建",
            )

    # ------------------------------------------------------------------
    # 副作用（缓存 / 向量），失败不影响已提交的复制结果
    # ------------------------------------------------------------------
    @staticmethod
    async def _after_create(dataset_id: int) -> None:
        try:
            from app.services.ai.config import AgentConfigProvider

            await AgentConfigProvider.refresh_dataset_menu()
        except Exception as exc:  # pragma: no cover - 缓存失败不应回滚已建好的数据集
            logger.warning("[Dataset Clone] refresh menu failed: %s", exc)

        try:
            from app.services.ai.metadata_index_service import MetadataIndexService

            await MetadataIndexService.sync_local_redis_vector(dataset_id)
        except Exception as exc:  # pragma: no cover - 本地向量模式之外会直接跳过
            logger.warning("[Dataset Clone] local vector sync failed: %s", exc)
