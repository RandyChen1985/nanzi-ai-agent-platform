"""元数据 Schema 漂移异常告警与人机协同处置服务。

汇聚运行时报错反哺（方案 A）与物理结构巡检发现的 Schema 漂移差异，
为管理员提供差异看板与安全受控的人机处置（一键下线/忽略，绝不擅自修改线上元数据）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import MetaColumn, MetaDataset, MetaSchemaDriftAlert, MetaTable

logger = logging.getLogger(__name__)

# 处置动作与漂移类型的合法组合白名单。
# 防止跨类型误处置，例如把「整表物理缺失」告警当作「新增字段」录入，
# 从而向元数据写入 physical_name='*' 之类的垃圾字段并掩盖真实漂移。
ACTION_DRIFT_TYPE_MAP: Dict[str, set] = {
    "table_missing_in_db": {"drop_table", "ignore"},
    "missing_in_db": {"drop_column", "ignore"},
    "new_in_db": {"add_column", "ignore"},
    "type_mismatch": {"sync_type", "ignore"},
    "missing_comment": {"update_comment", "ignore"},
}


def _is_action_allowed(drift_type: Optional[str], action: str) -> bool:
    """校验处置动作是否适用于该漂移类型；未知类型（历史数据）放行以保持向后兼容。"""
    if action == "ignore":
        return True
    allowed = ACTION_DRIFT_TYPE_MAP.get(drift_type or "")
    if allowed is None:
        return True
    return action in allowed


def _parse_llm_json_response(raw_text: str) -> Dict[str, Any]:
    """剥离 LLM 输出的 Markdown 代码块围栏并解析为 JSON 字典。"""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            text = "\n".join(lines[1:])
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
    try:
        res = json.loads(text.strip())
        return res if isinstance(res, dict) else {}
    except Exception:
        return {}


def normalize_column_type(raw_type: Optional[str]) -> str:
    """将物理数据库原生类型（跨方言）归一化映射为平台 6 大通用标准类型：
    String, Int64, Float64, DateTime, Boolean, JSON。

    映射规则：
    - JSON: json, jsonb, map, array, struct, object, nested(...)
    - DateTime: date, datetime, timestamp, timestamptz, time, year, interval
    - Boolean: bool, boolean, bit
    - Float64: float, double, decimal, numeric, real, number(带小数/精度), money
    - Int64: int, integer, bigint, tinyint, smallint, mediumint, int2/4/8, uint, serial, number(整型)
    - String: varchar, varchar2, char, text, longtext, clob, nvarchar, point, 及所有文本/未知几何/兜底类型
    """
    if not raw_type:
        return "String"

    raw_lower = str(raw_type).strip().lower()
    t = raw_lower

    # 1. 剔除 Nullable / LowCardinality 等 ClickHouse / PG 包装器
    while True:
        if t.startswith("nullable(") and t.endswith(")"):
            t = t[9:-1].strip()
        elif t.startswith("lowcardinality(") and t.endswith(")"):
            t = t[15:-1].strip()
        else:
            break

    # 2. JSON / 复合/嵌套结构体优先判断 (如 ClickHouse nested(...), postgres jsonb)
    if t.startswith(("json", "object", "map", "array", "struct", "nested")):
        return "JSON"

    # 3. Oracle NUMBER(p, s) 预判：若含有逗号说明定义了小数标度，归为 Float64
    if t.startswith("number") and "," in t:
        return "Float64"

    # 4. 剔除 (长度/精度)，例如 varchar(255), decimal(18,2), int(11), bit(1)
    if "(" in t:
        t = t.split("(", 1)[0].strip()

    t = t.replace("unsigned", "").replace("zerofill", "").strip()

    # 5. 日期时间族
    if t.startswith(("date", "time", "year", "interval")) or any(
        t.startswith(k) for k in ("timestamp", "datetime", "timestamptz")
    ):
        return "DateTime"

    # 6. 布尔族
    if t in ("bool", "boolean", "bit"):
        return "Boolean"

    # 7. 浮点 / 高精度数值族
    if any(
        t.startswith(k)
        for k in ("float", "double", "decimal", "numeric", "real", "binary_double", "binary_float", "money")
    ):
        return "Float64"

    # 8. 整型族 (以整型单词前缀严格匹配，避免 point, longtext 等因包含 int/long 误命中)
    if t.startswith(("int", "uint", "tinyint", "smallint", "mediumint", "bigint", "serial", "number")):
        return "Int64"

    # 9. 字符串与其它所有文本/大字段/未知几何类型，统统归为 String
    return "String"


class MetadataDriftService:
    """Schema 漂移告警服务。"""

    @staticmethod
    async def record_runtime_stale_alert(
        *,
        dataset_id: Optional[int] = None,
        dataset_name: Optional[str] = None,
        table_name: str,
        column_name: str,
        error_sample: str = "",
    ) -> None:
        """从运行时异步记录一个未知列报错告警（非阻塞）。

        设计为独立打开数据库 Session，保证独立于外部长请求生命周期，并吞掉所有内部异常。
        支持传入 dataset_id 或 dataset_name，若均无则根据 table_name 智能反查。
        """
        try:
            from app.core.orm import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                await MetadataDriftService.record_drift_alert_core(
                    session,
                    dataset_id=dataset_id,
                    dataset_name=dataset_name,
                    table_name=table_name,
                    column_name=column_name,
                    drift_type="missing_in_db",
                    source="runtime",
                    error_sample=error_sample,
                )
                await session.commit()
        except Exception:
            logger.warning(
                f"[MetadataDrift] 异步记录运行时漂移告警失败 (dataset_name={dataset_name}, {table_name}.{column_name})",
                exc_info=True,
            )

    @staticmethod
    async def record_drift_alert_core(
        db: AsyncSession,
        *,
        dataset_id: Optional[int] = None,
        dataset_name: Optional[str] = None,
        table_id: Optional[int] = None,
        table_name: str,
        column_name: str,
        drift_type: str = "missing_in_db",
        source: str = "runtime",
        error_sample: str = "",
    ) -> Optional[MetaSchemaDriftAlert]:
        """核心告警录入逻辑：同表同字段若已存在待处理告警，则累加频次；否则新增。"""
        resolved_dataset_id = dataset_id
        if not resolved_dataset_id and dataset_name:
            ds_stmt = select(MetaDataset.id).where(
                (func.lower(MetaDataset.name) == dataset_name.lower().strip())
                | (func.lower(MetaDataset.display_name) == dataset_name.lower().strip())
            )
            resolved_dataset_id = (await db.execute(ds_stmt)).scalar()

        if not resolved_dataset_id:
            # 根据物理表名反查 dataset_id
            t_stmt = select(MetaTable.dataset_id).where(
                func.lower(MetaTable.physical_name) == table_name.lower().strip()
            )
            resolved_dataset_id = (await db.execute(t_stmt)).scalar()

        if not resolved_dataset_id:
            logger.warning(
                f"[MetadataDrift] 无法关联有效数据集，跳过告警记录: {table_name}.{column_name}"
            )
            return None

        # 查询是否有同名同类型待处理 (status=0) 的告警 (I1)
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == resolved_dataset_id,
            func.lower(MetaSchemaDriftAlert.table_name) == table_name.lower().strip(),
            func.lower(MetaSchemaDriftAlert.column_name) == column_name.lower().strip(),
            MetaSchemaDriftAlert.drift_type == drift_type,
            MetaSchemaDriftAlert.status == 0,
        )
        existing = (await db.execute(stmt)).scalars().first()

        if existing:
            existing.hit_count += 1
            if error_sample:
                existing.error_sample = str(error_sample)[:1000]
            existing.updated_at = datetime.now()
            # 尝试补充 table_id
            if not existing.table_id:
                t_stmt = select(MetaTable.id).where(
                    MetaTable.dataset_id == resolved_dataset_id,
                    func.lower(MetaTable.physical_name) == table_name.lower().strip(),
                )
                existing.table_id = (await db.execute(t_stmt)).scalar()
            return existing

        matched_table_id = table_id
        if not matched_table_id:
            t_stmt = select(MetaTable.id).where(
                MetaTable.dataset_id == resolved_dataset_id,
                func.lower(MetaTable.physical_name) == table_name.lower().strip(),
            )
            matched_table_id = (await db.execute(t_stmt)).scalar()

        alert = MetaSchemaDriftAlert(
            dataset_id=resolved_dataset_id,
            table_id=matched_table_id,
            table_name=table_name.strip(),
            column_name=column_name.strip(),
            drift_type=drift_type,
            source=source,
            error_sample=str(error_sample)[:1000] if error_sample else None,
            hit_count=1,
            status=0,
        )
        db.add(alert)
        return alert

    @staticmethod
    async def get_dataset_drift_alerts(
        db: AsyncSession,
        dataset_id: int,
        *,
        status: Optional[int] = None,
    ) -> List[MetaSchemaDriftAlert]:
        """获取指定数据集的漂移告警列表，默认按待处理优先、检出时间倒序。"""
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == dataset_id
        )
        if status is not None:
            stmt = stmt.where(MetaSchemaDriftAlert.status == status)

        stmt = stmt.order_by(
            MetaSchemaDriftAlert.status.asc(),
            MetaSchemaDriftAlert.hit_count.desc(),
            MetaSchemaDriftAlert.updated_at.desc(),
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_drift_summary(db: AsyncSession) -> Tuple[int, Dict[int, int]]:
        """获取全局待处理漂移告警总数及各数据集的待处理计数。"""
        stmt = (
            select(
                MetaSchemaDriftAlert.dataset_id,
                func.count(MetaSchemaDriftAlert.id).label("cnt"),
            )
            .where(MetaSchemaDriftAlert.status == 0)
            .group_by(MetaSchemaDriftAlert.dataset_id)
        )
        rows = (await db.execute(stmt)).all()
        dataset_counts: Dict[int, int] = {int(r[0]): int(r[1]) for r in rows}
        total_pending = sum(dataset_counts.values())
        return total_pending, dataset_counts

    @staticmethod
    async def _call_llm_for_new_column_semantic(
        llm: Any,
        dataset_name: Optional[str],
        data_source: Optional[str],
        table_name: str,
        column_name: str,
        physical_type: Optional[str],
        comment: Optional[str],
        sibling_terms: List[str],
        sample_values: List[Any],
    ) -> Tuple[str, str, List[str], bool, Optional[str]]:
        """调用 LLM 推断新增字段的中文术语与描述。不依赖 db session，适合并发执行。"""
        from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage

        sibling_text = "、".join(sibling_terms) if sibling_terms else "（无）"
        sample_text = (", ".join(f"'{s}'" for s in sample_values[:3])) if sample_values else "（无样例，请仅依据字段名推断）"

        system_prompt = (
            "你是一个精通数据资产治理的数据库专家。请针对某个数据库中物理新增（元数据尚未收录）的单个字段，"
            "推断其中文业务含义，给出建议录入元数据的业务术语。\n\n"
            "要求：\n"
            "1. term：2~12 字的中文业务术语/备注名，必须清晰描述字段业务含义（如 '用户手机号'、'订单状态'），不得使用英文原样照抄。\n"
            "2. description：不超过 100 字的中文业务描述，说明该字段存储什么、代表什么含义。\n"
            "3. synonyms：1~3 个便于检索的同义词（可为英文缩写、别名、口语说法），用于增强后续检索命中。\n\n"
            "只需返回一个可被 json.loads 解析的 JSON 对象，不要 Markdown，不要多余解释，结构如下：\n"
            '{"term": "中文业务术语", "description": "中文业务描述", "synonyms": ["同义词1"]}'
        )
        user_prompt = (
            f"所属数据集: {dataset_name or '未知'}\n"
            f"数据源类型: {data_source or '未知'}\n"
            f"物理表名: {table_name}\n"
            f"待分析字段名: {column_name}\n"
            f"物理字段类型: {physical_type or '未知'}\n"
            f"物理字段注释: {comment or '（无）'}\n"
            f"同表其它字段已有中文术语参考: {sibling_text}\n"
            f"该字段部分样例值: {sample_text}"
        )
        try:
            response = await llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            raw = getattr(response, "content", "") or str(response)
            data = _parse_llm_json_response(raw)
            term = str(data.get("term") or "").strip()
            description = str(data.get("description") or "").strip()
            raw_syn = data.get("synonyms") or []
            synonyms = [str(s).strip() for s in raw_syn if str(s).strip()]
            llm_succeeded = bool(term)
            ai_error = None if term else "LLM 未返回有效的中文术语"
            return term, description, synonyms, llm_succeeded, ai_error
        except Exception as ex:
            logger.warning(f"[MetadataDrift] 新增字段 LLM 语义分析失败: {ex}", exc_info=True)
            return "", "", [], False, str(ex)[:300]

    @staticmethod
    async def analyze_new_column_ai(
        db: AsyncSession,
        alert_id: int,
        *,
        with_samples: bool = True,
    ) -> Dict[str, Any]:
        """对单个物理新增字段（new_in_db 告警）进行 LLM 语义分析，返回建议的中文业务术语供管理员确认。

        分析上下文：同表其它字段的中文业务术语 + 物理类型/注释 + 可选的该字段样例值。
        LLM 失败时返回 llm_succeeded=False，由调用方降级为默认（英文物理名）补录，不阻塞流程。
        """
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.id == alert_id)
        alert = (await db.execute(stmt)).scalars().first()
        if not alert:
            raise ValueError(f"告警不存在: ID {alert_id}")

        dataset_id = alert.dataset_id
        table_name = alert.table_name
        column_name = alert.column_name

        ds_stmt = select(MetaDataset).where(MetaDataset.id == dataset_id)
        ds = (await db.execute(ds_stmt)).scalars().first()
        dataset_name = ds.name if ds else None
        data_source = (ds.data_source if ds else None) or ""

        # ── 1. 收集物理列定义（类型 / 注释）+ 同表其它字段中文术语 ──
        physical_type: Optional[str] = None
        comment: Optional[str] = None
        sibling_terms: List[str] = []
        adapter = None
        if data_source:
            try:
                from app.services.data_adapter.factory import get_adapter

                adapter = await get_adapter(data_source)
                phys_cols = await adapter.get_columns(table_name=table_name)
                for pc in phys_cols:
                    col_name = str(pc.get("name") or "").strip().lower()
                    if col_name == column_name.strip().lower():
                        physical_type = normalize_column_type(pc.get("type"))
                        comment = pc.get("comment")
                # 同表其它已有字段（含映射库里的中文术语）
                sibling_stmt = (
                    select(MetaTable)
                    .options(selectinload(MetaTable.columns))
                    .where(
                        MetaTable.dataset_id == dataset_id,
                        MetaTable.physical_name == table_name,
                    )
                )
                meta_table = (await db.execute(sibling_stmt)).scalars().first()
                if meta_table:
                    for c in (meta_table.columns or []):
                        if c.physical_name and c.physical_name.lower() != column_name.lower():
                            if c.term and c.term != c.physical_name:
                                sibling_terms.append(f"{c.physical_name}={c.term}")
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 分析新增字段时读取物理列失败: {ex}", exc_info=True)

        # ── 2. 可选：读取该字段样例值（失败自动降级为无样例） ──
        sample_values: List[Any] = []
        if with_samples and adapter:
            try:
                from app.services.sql_query_execution_service import dialect_from_data_source

                sql_dialect = dialect_from_data_source(data_source)
                # 跨方言取样例：仅取 3 行以控制体积，避免泄露无关数据
                if sql_dialect == "oracle":
                    sample_sql = 'SELECT "{col}" FROM "{tbl}" WHERE ROWNUM <= 3'
                elif sql_dialect == "tsql":
                    sample_sql = "SELECT TOP 3 \"{col}\" FROM \"{tbl}\""
                else:
                    sample_sql = 'SELECT "{col}" FROM "{tbl}" LIMIT 3'
                res = await adapter.execute_sql(
                    sample_sql.replace("{col}", column_name).replace("{tbl}", table_name), {}
                )
                items = res.get("items") or []
                for row in items[:3]:
                    if row and len(row) > 0:
                        val = row[0]
                        if val is not None:
                            sample_values.append(val)
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 读取新增字段样例值失败，降级为无样例: {ex}")

        # ── 3. 调用 LLM 生成中文业务术语 ──
        term, description, synonyms, llm_succeeded, ai_error = "", "", [], False, None
        try:
            from app.services.ai.config import AgentConfigProvider

            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            term, description, synonyms, llm_succeeded, ai_error = (
                await MetadataDriftService._call_llm_for_new_column_semantic(
                    llm=llm,
                    dataset_name=dataset_name,
                    data_source=data_source,
                    table_name=table_name,
                    column_name=column_name,
                    physical_type=physical_type,
                    comment=comment,
                    sibling_terms=sibling_terms,
                    sample_values=sample_values,
                )
            )
        except Exception as ex:
            ai_error = str(ex)[:300]
            logger.warning(f"[MetadataDrift] 初始化 LLM 失败: {ex}", exc_info=True)

        return {
            "alert_id": alert.id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "table_name": table_name,
            "column_name": column_name,
            "physical_type": normalize_column_type(physical_type) if physical_type else "String",
            "comment": comment,
            "sample_values": sample_values,
            "term": term,
            "description": description,
            "synonyms": synonyms,
            "llm_succeeded": llm_succeeded,
            "ai_error": ai_error,
            "sibling_terms": sibling_terms,
        }

    @staticmethod
    async def _fetch_physical_comment(
        db: AsyncSession,
        dataset_id: int,
        table_name: str,
        column_name: str,
    ) -> str:
        """从物理库读取某列的注释；注释为空或等于列名时视为无有效备注，返回空串。

        供 missing_comment 检查与批量补备注时判断物理库是否存在可回填的中文备注。
        """
        try:
            ds_stmt = select(MetaDataset).where(MetaDataset.id == dataset_id)
            ds = (await db.execute(ds_stmt)).scalars().first()
            if not ds or not ds.data_source:
                return ""
            from app.services.data_adapter.factory import get_adapter

            adapter = await get_adapter(ds.data_source)
            phys_cols = await adapter.get_columns(table_name=table_name)
            col_l = column_name.strip().lower()
            for pc in phys_cols:
                if str(pc.get("name") or "").lower().strip() == col_l:
                    c = str(pc.get("comment") or "").strip()
                    # 注释等于字段名同样视为占位无效
                    if c and c.lower() != col_l:
                        return c
                    return ""
        except Exception as ex:
            logger.warning(f"[MetadataDrift] 读取物理列注释失败 {table_name}.{column_name}: {ex}")
        return ""

    @staticmethod
    async def analyze_update_comment_ai(
        db: AsyncSession,
        alert_id: int,
        *,
        with_samples: bool = True,
    ) -> Dict[str, Any]:
        """对备注缺失的字段（missing_comment 告警）进行补充分析，返回建议的字段描述。

        策略：优先采用物理库已有非空备注（免 LLM）；物理库也无有效备注时，调用 LLM
        依据该字段当前业务术语、类型、同表兄弟术语与可选样例值，推断中文业务描述。
        仅生成/完善 description（与可选的同义词），不改动已确认的 term。
        LLM 失败时返回 from_source='none' 与 ai_error，不阻塞流程。
        """
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.id == alert_id)
        alert = (await db.execute(stmt)).scalars().first()
        if not alert:
            raise ValueError(f"告警不存在: ID {alert_id}")

        dataset_id = alert.dataset_id
        table_name = alert.table_name
        column_name = alert.column_name

        ds_stmt = select(MetaDataset).where(MetaDataset.id == dataset_id)
        ds = (await db.execute(ds_stmt)).scalars().first()
        dataset_name = ds.name if ds else None
        data_source = (ds.data_source if ds else "") or ""

        # ── 1. 读取该字段在元数据中的现状（保留 term）与物理定义 ──
        current_term: Optional[str] = None
        current_description: Optional[str] = None
        physical_type: Optional[str] = None
        comment = ""
        adapter = None
        if data_source:
            try:
                from app.services.data_adapter.factory import get_adapter

                adapter = await get_adapter(data_source)
                phys_cols = await adapter.get_columns(table_name=table_name)
                for pc in phys_cols:
                    col_name = str(pc.get("name") or "").strip().lower()
                    if col_name == column_name.strip().lower():
                        physical_type = pc.get("type")
                        comment = str(pc.get("comment") or "").strip()
                        break
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 分析备注缺失字段时读取物理列失败: {ex}", exc_info=True)

        meta_table_stmt = (
            select(MetaTable)
            .options(selectinload(MetaTable.columns))
            .where(
                MetaTable.dataset_id == dataset_id,
                MetaTable.physical_name == table_name,
            )
        )
        meta_table = (await db.execute(meta_table_stmt)).scalars().first()
        sibling_terms: List[str] = []
        if meta_table:
            for c in (meta_table.columns or []):
                if c.physical_name and c.physical_name.lower() == column_name.lower():
                    current_term = c.term
                    current_description = c.description
                elif c.physical_name and c.term and c.term != c.physical_name:
                    sibling_terms.append(f"{c.physical_name}={c.term}")

        # ── 2. 物理库已有有效备注 → 直接采用，免 LLM ──
        if comment and comment.lower() != column_name.strip().lower():
            return {
                "alert_id": alert.id,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "table_name": table_name,
                "column_name": column_name,
                "physical_type": physical_type,
                "comment": comment,
                "sample_values": [],
                "current_term": current_term,
                "current_description": current_description,
                "description": comment,
                "synonyms": [],
                "from_source": "physical",
                "llm_succeeded": False,
                "ai_error": None,
                "sibling_terms": sibling_terms,
            }

        # ── 3. 物理库无有效备注 → 读取样例值后调用 LLM 推断 ──
        sample_values: List[Any] = []
        if with_samples and adapter:
            try:
                from app.services.sql_query_execution_service import dialect_from_data_source

                sql_dialect = dialect_from_data_source(data_source)
                if sql_dialect == "oracle":
                    sample_sql = 'SELECT "{col}" FROM "{tbl}" WHERE ROWNUM <= 3'
                elif sql_dialect == "tsql":
                    sample_sql = "SELECT TOP 3 \"{col}\" FROM \"{tbl}\""
                else:
                    sample_sql = 'SELECT "{col}" FROM "{tbl}" LIMIT 3'
                res = await adapter.execute_sql(
                    sample_sql.replace("{col}", column_name).replace("{tbl}", table_name), {}
                )
                items = res.get("items") or []
                for row in items[:3]:
                    if row and len(row) > 0 and row[0] is not None:
                        sample_values.append(row[0])
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 读取备注缺失字段样例值失败，降级为无样例: {ex}")

        description, synonyms, llm_succeeded, ai_error = "", [], False, None
        from_source = "none"
        try:
            from app.services.ai.config import AgentConfigProvider

            llm = await AgentConfigProvider.get_configured_llm(streaming=False)

            sibling_text = "、".join(sibling_terms) if sibling_terms else "（无）"
            sample_text = (", ".join(f"'{s}'" for s in sample_values[:3])) if sample_values else "（无样例，请仅依据字段名与业务术语推断）"

            system_prompt = (
                "你是一个精通数据资产治理的数据库专家。请为某个数据库表中一个「已在元数据收录，但字段备注缺失」的字段"
                "补充中文业务描述。该字段已有业务术语 term，你需要为其生成准确的 description。\n\n"
                "要求：\n"
                "1. description：不超过 120 字的中文业务描述，说明该字段实际存储什么、代表什么业务含义、可能的取值范围或口径，务必具体准确，不要空话套话。\n"
                "2. synonyms：1~3 个便于检索的同义词（可为英文缩写、别名、口语说法），用于增强后续检索命中；若不确定可留空数组。\n\n"
                "只需返回一个可被 json.loads 解析的 JSON 对象，不要 Markdown，不要多余解释，结构如下：\n"
                '{"description": "中文业务描述", "synonyms": ["同义词1"]}'
            )
            user_prompt = (
                f"所属数据集: {dataset_name or '未知'}\n"
                f"数据源类型: {data_source or '未知'}\n"
                f"物理表名: {table_name}\n"
                f"字段名: {column_name}\n"
                f"物理字段类型: {physical_type or '未知'}\n"
                f"字段已有业务术语: {current_term or '（无）'}\n"
                f"字段已有备注: {current_description or '（无）'}\n"
                f"同表其它字段已有中文术语参考: {sibling_text}\n"
                f"该字段部分样例值: {sample_text}"
            )
            from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage

            response = await llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            raw = getattr(response, "content", "") or str(response)
            data = _parse_llm_json_response(raw)
            description = str(data.get("description") or "").strip()
            raw_syn = data.get("synonyms") or []
            synonyms = [str(s).strip() for s in raw_syn if str(s).strip()]
            llm_succeeded = bool(description)
            from_source = "ai" if description else "none"
            if not description:
                ai_error = "LLM 未返回有效的中文业务描述"
        except Exception as ex:
            ai_error = str(ex)[:300]
            logger.warning(f"[MetadataDrift] 备注缺失字段 LLM 语义分析失败: {ex}", exc_info=True)

        return {
            "alert_id": alert.id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "table_name": table_name,
            "column_name": column_name,
            "physical_type": physical_type,
            "comment": comment,
            "sample_values": sample_values,
            "current_term": current_term,
            "current_description": current_description,
            "description": description,
            "synonyms": synonyms,
            "from_source": from_source,
            "llm_succeeded": llm_succeeded,
            "ai_error": ai_error,
            "sibling_terms": sibling_terms,
        }

    @staticmethod
    async def _resolve_single_alert_core(
        db: AsyncSession,
        alert: MetaSchemaDriftAlert,
        action: str,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        column_term: Optional[str] = None,
        column_description: Optional[str] = None,
        column_synonyms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """核心单项告警处置逻辑，记录变更日志，不执行 db.commit()。

        add_column 场景可传入管理员确认/修改后的业务信息
        （column_term / column_description / column_synonyms），缺省沿用物理注释 → 英文物理名兜底。
        """
        column_dropped = False
        table_dropped = False
        column_added = False
        column_updated = False
        message = ""

        # 处置动作必须与漂移类型匹配，否则拒绝（避免跨类型误处置造成元数据污染）
        if not _is_action_allowed(alert.drift_type, action):
            allowed = "、".join(sorted(ACTION_DRIFT_TYPE_MAP.get(alert.drift_type or "", set())))
            raise ValueError(
                f"告警类型 {alert.drift_type} 不支持处置动作 {action}（允许：{allowed}）"
            )

        if action == "drop_table" or (action == "drop_column" and (alert.drift_type == "table_missing_in_db" or alert.column_name == "*")):
            # 下线整张表及其所有字段
            t_stmt = (
                select(MetaTable)
                .options(selectinload(MetaTable.columns))
                .where(
                    MetaTable.dataset_id == alert.dataset_id,
                    func.lower(MetaTable.physical_name) == alert.table_name.lower(),
                )
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if table:
                old_data = {
                    "physical_name": table.physical_name,
                    "term": table.term,
                    "description": table.description,
                    "synonyms": table.synonyms,
                    "columns": [
                        {"physical_name": col.physical_name, "term": col.term, "type": col.type}
                        for col in (table.columns or [])
                    ],
                }
                table_id_str = f"{alert.dataset_id}:{table.physical_name}"
                await db.delete(table)
                table_dropped = True

                try:
                    from app.services.changelog_service import ChangelogService
                    from app.services.metadata_service import MetadataService

                    await ChangelogService.log_change(
                        db=db,
                        resource_type="table",
                        resource_id=table_id_str,
                        operation="delete",
                        user_id=user_id,
                        user_name=user_name,
                        old_data=old_data,
                        new_data=None,
                        reason=f"元数据巡检：下线物理缺失表 {table.physical_name}",
                    )
                    await MetadataService._mark_dataset_as_modified(db, alert.dataset_id)
                except Exception as ex:
                    logger.warning(f"[MetadataDrift] 记录下线整表变更日志失败: {ex}")

            # 将该表下所有其它待处理告警一并标记为已解决（因为表已经彻底下线）
            other_alerts_stmt = select(MetaSchemaDriftAlert).where(
                MetaSchemaDriftAlert.dataset_id == alert.dataset_id,
                func.lower(MetaSchemaDriftAlert.table_name) == alert.table_name.lower(),
                MetaSchemaDriftAlert.status == 0,
            )
            other_alerts = (await db.execute(other_alerts_stmt)).scalars().all()
            for oa in other_alerts:
                oa.status = 1
                oa.updated_at = datetime.now()

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功从元数据中下线整表 {alert.table_name}"
            if table_dropped:
                message += "（已自动同步向量知识库）"

        elif action == "drop_column":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if table:
                c_stmt = select(MetaColumn).where(
                    MetaColumn.table_id == table.id,
                    func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
                )
                col = (await db.execute(c_stmt)).scalars().first()
                if col:
                    old_data = {
                        "physical_name": table.physical_name,
                        "columns": [{"physical_name": col.physical_name, "term": col.term, "type": col.type}],
                    }
                    new_data = {
                        "physical_name": table.physical_name,
                        "columns": [],
                    }
                    table_id_str = f"{alert.dataset_id}:{table.physical_name}"

                    await db.delete(col)
                    column_dropped = True

                    try:
                        from app.services.changelog_service import ChangelogService
                        from app.services.metadata_service import MetadataService

                        await ChangelogService.log_change(
                            db=db,
                            resource_type="table",
                            resource_id=table_id_str,
                            operation="update",
                            user_id=user_id,
                            user_name=user_name,
                            old_data=old_data,
                            new_data=new_data,
                            reason=f"元数据巡检：下线物理缺失字段 {table.physical_name}.{alert.column_name}",
                        )
                        await MetadataService._mark_dataset_as_modified(db, alert.dataset_id)
                    except Exception as ex:
                        logger.warning(f"[MetadataDrift] 记录下线字段变更日志失败: {ex}")

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功从元数据中下线字段 {alert.table_name}.{alert.column_name}"
            if column_dropped:
                message += "（已自动同步向量知识库）"

        elif action == "add_column":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if not table:
                raise ValueError(f"元数据中未找到表 {alert.table_name}，无法直接添加字段")

            c_stmt = select(MetaColumn).where(
                MetaColumn.table_id == table.id,
                func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
            )
            existing_col = (await db.execute(c_stmt)).scalars().first()
            if not existing_col:
                # 补齐物理列的原始定义（类型 / 注释），作为语义与类型兜底来源
                col_type = "String"
                col_desc = ""
                try:
                    ds_stmt = select(MetaDataset).where(MetaDataset.id == alert.dataset_id)
                    ds = (await db.execute(ds_stmt)).scalars().first()
                    if ds and ds.data_source:
                        from app.services.data_adapter.factory import get_adapter

                        adapter = await get_adapter(ds.data_source)
                        phys_cols = await adapter.get_columns(table_name=alert.table_name)
                        for pc in phys_cols:
                            if str(pc.get("name") or "").lower().strip() == alert.column_name.lower().strip():
                                col_type = normalize_column_type(pc.get("type"))
                                col_desc = pc.get("comment") or ""
                                break
                except Exception as ex:
                    logger.warning(f"[MetadataDrift] 尝试获取物理列类型失败，使用默认值: {ex}")

                col_type = normalize_column_type(col_type)

                # 优先级：管理员确认/修改的术语 > 物理注释 > 英文物理名兜底
                new_term = (column_term or "").strip() or (col_desc if col_desc else alert.column_name)
                new_desc = (column_description or "").strip() if column_description is not None else (col_desc if col_desc else None)
                new_synonyms = column_synonyms if column_synonyms else []

                new_col = MetaColumn(
                    table_id=table.id,
                    physical_name=alert.column_name,
                    term=new_term,
                    type=col_type,
                    description=new_desc or None,
                    synonyms=new_synonyms or None,
                    is_primary=0,
                )
                db.add(new_col)
                column_added = True

                old_data = {
                    "physical_name": table.physical_name,
                    "columns": [],
                }
                new_data = {
                    "physical_name": table.physical_name,
                    "columns": [{"physical_name": new_col.physical_name, "term": new_col.term, "type": new_col.type}],
                }
                table_id_str = f"{alert.dataset_id}:{table.physical_name}"

                try:
                    from app.services.changelog_service import ChangelogService
                    from app.services.metadata_service import MetadataService

                    await ChangelogService.log_change(
                        db=db,
                        resource_type="table",
                        resource_id=table_id_str,
                        operation="update",
                        user_id=user_id,
                        user_name=user_name,
                        old_data=old_data,
                        new_data=new_data,
                        reason=f"元数据巡检：录入物理新增字段 {table.physical_name}.{alert.column_name}",
                    )
                    await MetadataService._mark_dataset_as_modified(db, alert.dataset_id)
                except Exception as ex:
                    logger.warning(f"[MetadataDrift] 记录录入字段变更日志失败: {ex}")

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功将字段 {alert.table_name}.{alert.column_name} 录入元数据表"
            if column_added:
                message += "（已自动同步向量知识库）"

        elif action == "sync_type":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if not table:
                raise ValueError(f"元数据中未找到表 {alert.table_name}，无法同步字段类型")

            c_stmt = select(MetaColumn).where(
                MetaColumn.table_id == table.id,
                func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
            )
            col = (await db.execute(c_stmt)).scalars().first()
            if not col:
                raise ValueError(f"元数据表 {alert.table_name} 中未找到字段 {alert.column_name}")

            # 优先直连物理库获取最新列类型
            phys_type = None
            try:
                ds_stmt = select(MetaDataset).where(MetaDataset.id == alert.dataset_id)
                ds = (await db.execute(ds_stmt)).scalars().first()
                if ds and ds.data_source:
                    from app.services.data_adapter.factory import get_adapter

                    adapter = await get_adapter(ds.data_source)
                    phys_cols = await adapter.get_columns(table_name=alert.table_name)
                    for pc in phys_cols:
                        if str(pc.get("name") or "").lower().strip() == alert.column_name.lower().strip():
                            phys_type = pc.get("type")
                            break
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 尝试从物理库获取列类型失败，尝试从巡检记录解析: {ex}")

            # 若无法连通外部库，从 error_sample 解析实际物理类型
            if not phys_type and alert.error_sample:
                m = re.search(r"物理库实际为\s*([a-zA-Z0-9_()]+)", alert.error_sample)
                if m:
                    phys_type = m.group(1).strip()

            phys_type = normalize_column_type(phys_type)

            old_type = col.type
            col.type = phys_type
            column_updated = True

            old_data = {
                "physical_name": table.physical_name,
                "columns": [{"physical_name": col.physical_name, "term": col.term, "type": old_type}],
            }
            new_data = {
                "physical_name": table.physical_name,
                "columns": [{"physical_name": col.physical_name, "term": col.term, "type": col.type}],
            }
            table_id_str = f"{alert.dataset_id}:{table.physical_name}"

            try:
                from app.services.changelog_service import ChangelogService
                from app.services.metadata_service import MetadataService

                await ChangelogService.log_change(
                    db=db,
                    resource_type="table",
                    resource_id=table_id_str,
                    operation="update",
                    user_id=user_id,
                    user_name=user_name,
                    old_data=old_data,
                    new_data=new_data,
                    reason=f"元数据巡检：同步字段类型 {table.physical_name}.{alert.column_name} ({old_type} -> {col.type})",
                )
                await MetadataService._mark_dataset_as_modified(db, alert.dataset_id)
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 记录同步类型变更日志失败: {ex}")

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功将字段 {alert.table_name}.{alert.column_name} 类型从 {old_type} 同步为物理库实际类型 {col.type}（已自动同步向量知识库）"

        elif action == "update_comment":
            t_stmt = select(MetaTable).where(
                MetaTable.dataset_id == alert.dataset_id,
                func.lower(MetaTable.physical_name) == alert.table_name.lower(),
            )
            table = (await db.execute(t_stmt)).scalars().first()
            if not table:
                raise ValueError(f"元数据中未找到表 {alert.table_name}，无法更新字段备注")

            c_stmt = select(MetaColumn).where(
                MetaColumn.table_id == table.id,
                func.lower(MetaColumn.physical_name) == alert.column_name.lower(),
            )
            col = (await db.execute(c_stmt)).scalars().first()
            if not col:
                raise ValueError(f"元数据表 {alert.table_name} 中未找到字段 {alert.column_name}")

            old_desc = col.description
            old_synonyms = col.synonyms

            # 优先级：管理员确认/修改的描述 > 物理库注释 > 保持现有
            if column_description is not None and (column_description or "").strip():
                new_desc = column_description.strip()
            else:
                phys_desc = await MetadataDriftService._fetch_physical_comment(
                    db, alert.dataset_id, alert.table_name, alert.column_name
                )
                new_desc = phys_desc or old_desc or None

            desc_changed = (new_desc or "") != (old_desc or "")
            syn_changed = (column_synonyms is not None) and (column_synonyms != old_synonyms)

            # 若描述与同义词均无任何实际变化（如物理库无注释且未提供新描述），跳过无意义处置 (I3)
            if not desc_changed and not syn_changed:
                return {
                    "alert_id": alert.id,
                    "status": alert.status,
                    "column_dropped": False,
                    "table_dropped": False,
                    "column_added": False,
                    "column_updated": False,
                    "message": f"字段 {alert.table_name}.{alert.column_name} 物理库无有效注释且未指定新备注，跳过处置",
                }

            if column_synonyms is not None:
                col.synonyms = column_synonyms or None
            col.description = new_desc
            column_updated = desc_changed or syn_changed

            old_data = {
                "physical_name": table.physical_name,
                "columns": [{"physical_name": col.physical_name, "term": col.term, "type": col.type,
                             "description": old_desc, "synonyms": old_synonyms}],
            }
            new_data = {
                "physical_name": table.physical_name,
                "columns": [{"physical_name": col.physical_name, "term": col.term, "type": col.type,
                             "description": col.description, "synonyms": col.synonyms}],
            }
            table_id_str = f"{alert.dataset_id}:{table.physical_name}"

            try:
                from app.services.changelog_service import ChangelogService
                from app.services.metadata_service import MetadataService

                await ChangelogService.log_change(
                    db=db,
                    resource_type="table",
                    resource_id=table_id_str,
                    operation="update",
                    user_id=user_id,
                    user_name=user_name,
                    old_data=old_data,
                    new_data=new_data,
                    reason=f"元数据巡检：补充字段备注 {table.physical_name}.{alert.column_name}",
                )
                await MetadataService._mark_dataset_as_modified(db, alert.dataset_id)
            except Exception as ex:
                logger.warning(f"[MetadataDrift] 记录补充字段备注变更日志失败: {ex}")

            alert.status = 1  # resolved
            alert.updated_at = datetime.now()
            message = f"已成功补充字段 {alert.table_name}.{alert.column_name} 的中文业务备注（已自动同步向量知识库）"

        elif action == "ignore":
            alert.status = 2  # ignored
            alert.updated_at = datetime.now()
            message = f"已忽略字段 {alert.table_name}.{alert.column_name} 的漂移提醒"

        else:
            raise ValueError(f"不支持的漂移处置操作: {action}")

        return {
            "alert_id": alert.id,
            "status": alert.status,
            "column_dropped": column_dropped,
            "table_dropped": table_dropped,
            "column_added": column_added,
            "column_updated": column_updated,
            "message": message,
        }

    @staticmethod
    async def resolve_alert(
        db: AsyncSession,
        alert_id: int,
        action: str,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        column_term: Optional[str] = None,
        column_description: Optional[str] = None,
        column_synonyms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """管理员对单条告警进行人机协同处置（下线字段 / 录入元数据 / 忽略）。

        add_column 场景可传入管理员确认/修改后的 column_term / column_description / column_synonyms。
        """
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.id == alert_id)
        alert = (await db.execute(stmt)).scalars().first()
        if not alert:
            raise ValueError(f"告警不存在: ID {alert_id}")

        res = await MetadataDriftService._resolve_single_alert_core(
            db,
            alert,
            action,
            user_id=user_id,
            user_name=user_name,
            column_term=column_term,
            column_description=column_description,
            column_synonyms=column_synonyms,
        )
        await db.commit()
        if res.get("column_dropped") or res.get("table_dropped") or res.get("column_added") or res.get("column_updated"):
            await MetadataDriftService._try_sync_local_vector({alert.dataset_id})
        return res

    @staticmethod
    async def _prepare_add_column_ai_metadata(
        db: AsyncSession,
        alerts: List[MetaSchemaDriftAlert],
        auto_ai_complete: bool = True,
    ) -> Dict[int, Dict[str, Any]]:
        """在批量执行 add_column 前，智能预判物理注释与并发 AI 补齐。

        返回 mapping: alert_id -> {
            "term": str,
            "description": Optional[str],
            "synonyms": List[str],
            "is_ai": bool,
            "is_physical": bool,
        }
        """
        metadata_map: Dict[int, Dict[str, Any]] = {}
        if not alerts:
            return metadata_map

        # 1. 在主 session 中串行收集物理注释（安全无并发冲突）
        pending_ai_alerts: List[MetaSchemaDriftAlert] = []
        for alert in alerts:
            phys_desc = await MetadataDriftService._fetch_physical_comment(
                db, alert.dataset_id, alert.table_name, alert.column_name
            )
            if phys_desc:
                metadata_map[alert.id] = {
                    "term": phys_desc,
                    "description": phys_desc,
                    "synonyms": [],
                    "is_ai": False,
                    "is_physical": True,
                }
            elif auto_ai_complete:
                pending_ai_alerts.append(alert)
            else:
                metadata_map[alert.id] = {
                    "term": alert.column_name,
                    "description": None,
                    "synonyms": [],
                    "is_ai": False,
                    "is_physical": False,
                }

        if not pending_ai_alerts:
            return metadata_map

        # 2. 预先读取相关 dataset 与同表 sibling_terms（按 dataset_id / table_name 缓存）
        ds_cache: Dict[int, Optional[MetaDataset]] = {}
        sibling_cache: Dict[Tuple[int, str], List[str]] = {}
        for a in pending_ai_alerts:
            if a.dataset_id not in ds_cache:
                ds_obj = (await db.execute(select(MetaDataset).where(MetaDataset.id == a.dataset_id))).scalars().first()
                ds_cache[a.dataset_id] = ds_obj
            tbl_key = (a.dataset_id, a.table_name.lower())
            if tbl_key not in sibling_cache:
                t_obj = (await db.execute(
                    select(MetaTable).options(selectinload(MetaTable.columns))
                    .where(MetaTable.dataset_id == a.dataset_id, func.lower(MetaTable.physical_name) == a.table_name.lower())
                )).scalars().first()
                sibs: List[str] = []
                if t_obj and t_obj.columns:
                    for c in t_obj.columns:
                        if c.physical_name and c.term and c.term != c.physical_name:
                            sibs.append(f"{c.physical_name}={c.term}")
                sibling_cache[tbl_key] = sibs

        # 3. 预先初始化 adapter 缓存（串行无竞态 M2），随后在信号量控制下有界并发（I4）
        try:
            from app.services.ai.config import AgentConfigProvider

            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            adapter_cache: Dict[str, Any] = {}
            for a in pending_ai_alerts:
                ds_obj = ds_cache.get(a.dataset_id)
                ds_src = (ds_obj.data_source if ds_obj else "") or ""
                if ds_src and ds_src not in adapter_cache:
                    try:
                        from app.services.data_adapter.factory import get_adapter
                        adapter_cache[ds_src] = await get_adapter(ds_src)
                    except Exception as e:
                        logger.debug(f"[MetadataDrift] 批量预加载适配器跳过 {ds_src}: {e}")

            sem = asyncio.Semaphore(4)

            async def _infer_single(alert_item: MetaSchemaDriftAlert) -> Tuple[int, Dict[str, Any]]:
                # 将外部数据源查询与 LLM 推断均置于信号量内，保护外部库连接池不被打爆 (I4)
                async with sem:
                    ds_obj = ds_cache.get(alert_item.dataset_id)
                    ds_name = ds_obj.name if ds_obj else None
                    ds_src = (ds_obj.data_source if ds_obj else "") or ""
                    sibs = sibling_cache.get((alert_item.dataset_id, alert_item.table_name.lower())) or []

                    col_type = "String"
                    sample_vals: List[Any] = []
                    if ds_src and ds_src in adapter_cache:
                        try:
                            adp = adapter_cache[ds_src]
                            p_cols = await adp.get_columns(table_name=alert_item.table_name)
                            for pc in p_cols:
                                if str(pc.get("name") or "").lower().strip() == alert_item.column_name.lower().strip():
                                    col_type = normalize_column_type(pc.get("type"))
                                    break

                            col_clean = alert_item.column_name.strip()
                            tbl_clean = alert_item.table_name.strip()
                            # 校验表名与列名合法性，防止 SQL 注入风险 (M5)
                            if re.match(r"^[A-Za-z0-9_]+$", col_clean) and re.match(r"^[A-Za-z0-9_]+$", tbl_clean):
                                from app.services.sql_query_execution_service import dialect_from_data_source

                                sql_dialect = dialect_from_data_source(ds_src)
                                if sql_dialect == "oracle":
                                    s_sql = f'SELECT "{col_clean}" FROM "{tbl_clean}" WHERE ROWNUM <= 3'
                                elif sql_dialect == "tsql":
                                    s_sql = f'SELECT TOP 3 "{col_clean}" FROM "{tbl_clean}"'
                                else:
                                    s_sql = f'SELECT "{col_clean}" FROM "{tbl_clean}" LIMIT 3'
                                s_res = await adp.execute_sql(s_sql, {})
                                for row in (s_res.get("items") or [])[:3]:
                                    if row and len(row) > 0 and row[0] is not None:
                                        sample_vals.append(row[0])
                        except Exception as e:
                            logger.debug(f"[MetadataDrift] 批量采样跳过 {alert_item.table_name}.{alert_item.column_name}: {e}")

                    try:
                        term, desc, syns, ok, err = await asyncio.wait_for(
                            MetadataDriftService._call_llm_for_new_column_semantic(
                                llm=llm,
                                dataset_name=ds_name,
                                data_source=ds_src,
                                table_name=alert_item.table_name,
                                column_name=alert_item.column_name,
                                physical_type=col_type,
                                comment=None,
                                sibling_terms=sibs,
                                sample_values=sample_vals,
                            ),
                            timeout=12.0,
                        )
                        if ok and term:
                            return alert_item.id, {
                                "term": term,
                                "description": desc,
                                "synonyms": syns,
                                "is_ai": True,
                                "is_physical": False,
                            }
                    except Exception as ex:
                        logger.warning(f"[MetadataDrift] 批量自动 AI 分析失败 alert_id={alert_item.id}: {ex}")

                    return alert_item.id, {
                        "term": alert_item.column_name,
                        "description": None,
                        "synonyms": [],
                        "is_ai": False,
                        "is_physical": False,
                    }

            ai_results = await asyncio.gather(*[_infer_single(item) for item in pending_ai_alerts])
            for aid, meta in ai_results:
                metadata_map[aid] = meta
        except Exception as ex:
            logger.warning(f"[MetadataDrift] 批量 AI 分析环境初始化失败，降级为默认名录入: {ex}")
            for a in pending_ai_alerts:
                metadata_map[a.id] = {
                    "term": a.column_name,
                    "description": None,
                    "synonyms": [],
                    "is_ai": False,
                    "is_physical": False,
                }

        return metadata_map

    @staticmethod
    async def batch_resolve_alerts(
        db: AsyncSession,
        dataset_id: int,
        action: str,
        drift_type: Optional[str] = None,
        alert_ids: Optional[List[int]] = None,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        auto_ai_complete: bool = True,
    ) -> Dict[str, Any]:
        """批量处置告警：可指定 alert_ids，或按 dataset_id + drift_type 批量处置。

        若 action == "add_column"，默认开启 auto_ai_complete：物理库有注释优先采用物理注释；
        无注释字段自动并发调用 AI 推断中文业务名与描述，一步到位录入元数据。
        """
        stmt = select(MetaSchemaDriftAlert).where(
            MetaSchemaDriftAlert.dataset_id == dataset_id,
            MetaSchemaDriftAlert.status == 0,
        )
        if alert_ids:
            stmt = stmt.where(MetaSchemaDriftAlert.id.in_(alert_ids))
        elif drift_type:
            stmt = stmt.where(MetaSchemaDriftAlert.drift_type == drift_type)

        alerts = (await db.execute(stmt)).scalars().all()
        if not alerts:
            return {"processed_count": 0, "message": "没有符合条件的待处理告警"}

        # 若为 add_column 批量处置，预先计算各告警对应的术语、描述与同义词
        add_col_meta_map: Dict[int, Dict[str, Any]] = {}
        if action == "add_column":
            add_col_meta_map = await MetadataDriftService._prepare_add_column_ai_metadata(
                db,
                [a for a in alerts if _is_action_allowed(a.drift_type, action)],
                auto_ai_complete=auto_ai_complete,
            )

        processed_count = 0
        failed_count = 0
        skipped_count = 0
        ai_completed_count = 0
        physical_completed_count = 0

        for alert in alerts:
            # 动作与告警类型不匹配则跳过（不误处置、不计失败）
            if not _is_action_allowed(alert.drift_type, action):
                skipped_count += 1
                continue

            # 批量补备注：仅回填物理库已有有效备注的字段，无备注（或无法连通物理库）则跳过，不标记处置
            if action == "update_comment":
                phys_desc = await MetadataDriftService._fetch_physical_comment(
                    db, alert.dataset_id, alert.table_name, alert.column_name
                )
                if not phys_desc:
                    skipped_count += 1
                    continue
                try:
                    await MetadataDriftService._resolve_single_alert_core(
                        db, alert, action,
                        user_id=user_id, user_name=user_name,
                        column_description=phys_desc,
                    )
                    processed_count += 1
                except Exception as e:
                    logger.warning(f"[MetadataDrift] 批量处理单项失败: alert_id={alert.id}, err={e}")
                    failed_count += 1
                continue

            col_term = None
            col_desc = None
            col_syns = None
            meta_info = add_col_meta_map.get(alert.id)
            if meta_info:
                col_term = meta_info.get("term")
                col_desc = meta_info.get("description")
                col_syns = meta_info.get("synonyms")

            try:
                await MetadataDriftService._resolve_single_alert_core(
                    db,
                    alert,
                    action,
                    user_id=user_id,
                    user_name=user_name,
                    column_term=col_term,
                    column_description=col_desc,
                    column_synonyms=col_syns,
                )
                processed_count += 1
                if meta_info and meta_info.get("is_ai"):
                    ai_completed_count += 1
                elif meta_info and meta_info.get("is_physical"):
                    physical_completed_count += 1
            except Exception as e:
                logger.warning(f"[MetadataDrift] 批量处理单项失败: alert_id={alert.id}, err={e}")
                failed_count += 1

        await db.commit()
        if action in ("drop_column", "drop_table", "add_column", "sync_type", "update_comment") and processed_count > 0:
            await MetadataDriftService._try_sync_local_vector({dataset_id})

        if action == "add_column" and processed_count > 0:
            details = []
            if ai_completed_count:
                details.append(f"{ai_completed_count} 项由 AI 自动生成中文名与描述")
            if physical_completed_count:
                details.append(f"{physical_completed_count} 项采用物理库注释")
            detail_str = f"（{'，'.join(details)}，已自动同步向量知识库）" if details else "（已自动同步向量知识库）"
            if failed_count:
                message = f"成功批量录入 {processed_count} 项字段{detail_str}，{failed_count} 项录入失败"
            else:
                message = f"成功批量录入 {processed_count} 项字段{detail_str}"
        elif failed_count:
            message = f"成功批量处置 {processed_count} 项告警，{failed_count} 项处置失败"
        else:
            message = f"成功批量处置 {processed_count} 项告警（已自动同步向量知识库）"
        if skipped_count:
            message += f"，{skipped_count} 项因物理库无有效备注已跳过"

        return {
            "processed_count": processed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "ai_completed_count": ai_completed_count,
            "physical_completed_count": physical_completed_count,
            "message": message,
        }

    @staticmethod
    async def get_all_drift_alerts(
        db: AsyncSession,
        *,
        status: Optional[int] = None,
        dataset_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取全库或指定数据集的漂移告警列表，并附带 dataset_name。"""
        stmt = (
            select(MetaSchemaDriftAlert, MetaDataset.name.label("dataset_name"))
            .outerjoin(MetaDataset, MetaSchemaDriftAlert.dataset_id == MetaDataset.id)
        )
        if dataset_id is not None and dataset_id > 0:
            stmt = stmt.where(MetaSchemaDriftAlert.dataset_id == dataset_id)
        if status is not None:
            stmt = stmt.where(MetaSchemaDriftAlert.status == status)

        stmt = stmt.order_by(
            MetaSchemaDriftAlert.status.asc(),
            MetaSchemaDriftAlert.hit_count.desc(),
            MetaSchemaDriftAlert.updated_at.desc(),
        )
        result = await db.execute(stmt)
        rows = result.all()
        items = []
        for alert, ds_name in rows:
            items.append({
                "id": alert.id,
                "dataset_id": alert.dataset_id,
                "dataset_name": ds_name or f"数据集 #{alert.dataset_id}",
                "table_id": alert.table_id,
                "table_name": alert.table_name,
                "column_name": alert.column_name,
                "drift_type": alert.drift_type,
                "source": alert.source,
                "error_sample": alert.error_sample,
                "hit_count": alert.hit_count,
                "status": alert.status,
                "created_at": alert.created_at,
                "updated_at": alert.updated_at,
            })
        return items

    @staticmethod
    async def batch_resolve_alerts_global(
        db: AsyncSession,
        action: str,
        drift_type: Optional[str] = None,
        alert_ids: Optional[List[int]] = None,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        auto_ai_complete: bool = True,
    ) -> Dict[str, Any]:
        """跨数据集全局批量处置告警。"""
        stmt = select(MetaSchemaDriftAlert).where(MetaSchemaDriftAlert.status == 0)
        if alert_ids:
            stmt = stmt.where(MetaSchemaDriftAlert.id.in_(alert_ids))
        elif drift_type:
            stmt = stmt.where(MetaSchemaDriftAlert.drift_type == drift_type)

        alerts = (await db.execute(stmt)).scalars().all()
        if not alerts:
            return {"processed_count": 0, "message": "没有符合条件的待处理告警"}

        # 若为 add_column 批量处置，预先计算各告警对应的术语、描述与同义词
        add_col_meta_map: Dict[int, Dict[str, Any]] = {}
        if action == "add_column":
            add_col_meta_map = await MetadataDriftService._prepare_add_column_ai_metadata(
                db,
                [a for a in alerts if _is_action_allowed(a.drift_type, action)],
                auto_ai_complete=auto_ai_complete,
            )

        processed_count = 0
        failed_count = 0
        skipped_count = 0
        ai_completed_count = 0
        physical_completed_count = 0

        for alert in alerts:
            # 动作与告警类型不匹配则跳过（不误处置、不计失败）
            if not _is_action_allowed(alert.drift_type, action):
                skipped_count += 1
                continue

            # 批量补备注：仅回填物理库已有有效备注的字段，无备注则跳过，不标记处置
            if action == "update_comment":
                phys_desc = await MetadataDriftService._fetch_physical_comment(
                    db, alert.dataset_id, alert.table_name, alert.column_name
                )
                if not phys_desc:
                    skipped_count += 1
                    continue
                try:
                    await MetadataDriftService._resolve_single_alert_core(
                        db, alert, action,
                        user_id=user_id, user_name=user_name,
                        column_description=phys_desc,
                    )
                    processed_count += 1
                except Exception as e:
                    logger.warning(f"[MetadataDrift] 全局批量处理单项失败: alert_id={alert.id}, err={e}")
                    failed_count += 1
                continue

            col_term = None
            col_desc = None
            col_syns = None
            meta_info = add_col_meta_map.get(alert.id)
            if meta_info:
                col_term = meta_info.get("term")
                col_desc = meta_info.get("description")
                col_syns = meta_info.get("synonyms")

            try:
                await MetadataDriftService._resolve_single_alert_core(
                    db,
                    alert,
                    action,
                    user_id=user_id,
                    user_name=user_name,
                    column_term=col_term,
                    column_description=col_desc,
                    column_synonyms=col_syns,
                )
                processed_count += 1
                if meta_info and meta_info.get("is_ai"):
                    ai_completed_count += 1
                elif meta_info and meta_info.get("is_physical"):
                    physical_completed_count += 1
            except Exception as e:
                logger.warning(f"[MetadataDrift] 全局批量处理单项失败: alert_id={alert.id}, err={e}")
                failed_count += 1

        await db.commit()
        if action in ("drop_column", "drop_table", "add_column", "sync_type", "update_comment") and processed_count > 0:
            ds_ids = {a.dataset_id for a in alerts if a.dataset_id}
            await MetadataDriftService._try_sync_local_vector(ds_ids)

        if action == "add_column" and processed_count > 0:
            details = []
            if ai_completed_count:
                details.append(f"{ai_completed_count} 项由 AI 自动生成中文名与描述")
            if physical_completed_count:
                details.append(f"{physical_completed_count} 项采用物理库注释")
            detail_str = f"（{'，'.join(details)}，已自动同步向量知识库）" if details else "（已自动同步向量知识库）"
            if failed_count:
                message = f"成功批量录入 {processed_count} 项字段{detail_str}，{failed_count} 项录入失败"
            else:
                message = f"成功批量录入 {processed_count} 项字段{detail_str}"
        elif failed_count:
            message = f"成功批量处置 {processed_count} 项告警，{failed_count} 项处置失败"
        else:
            message = f"成功批量处置 {processed_count} 项告警（已自动同步向量知识库）"
        if skipped_count:
            message += f"，{skipped_count} 项因物理库无有效备注已跳过"

        return {
            "processed_count": processed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "ai_completed_count": ai_completed_count,
            "physical_completed_count": physical_completed_count,
            "message": message,
        }

    @staticmethod
    async def _try_sync_local_vector(dataset_ids: Set[int]) -> None:
        """告警处置触发元数据物理列变更后，自动同步相关数据集的本地 Redis 向量与 Schema 缓存。"""
        if not dataset_ids:
            return
        try:
            from app.services.ai.metadata_index_service import MetadataIndexService
            for ds_id in dataset_ids:
                if ds_id and ds_id > 0:
                    await MetadataIndexService.sync_local_redis_vector(ds_id)
        except Exception as ex:
            logger.warning("[MetadataDrift] 处置后同步本地 Redis 向量失败: %s", ex)

