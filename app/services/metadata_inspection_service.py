"""元数据物理数据库一致性巡检服务。

通过数据源适配器直连真实物理数据库，对比各表的 information_schema/物理列与平台元数据列；
复用 Redis Stream 架构向前端实时流式输出阶段、进度和逐行日志，
并将发现的 Schema 漂移差异（缺失/新增列）自动沉淀至漂移告警表，供管理员人工决策。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.data_adapter.factory import get_adapter
from app.services.metadata_drift_service import MetadataDriftService
from app.services.metadata_service import MetadataService
from app.services.metadata_sync_log_service import metadata_sync_log_service

logger = logging.getLogger(__name__)


# 整型族：覆盖 MySQL int/bigint、PostgreSQL integer/bigint、Oracle NUMBER(0,0) 外、ClickHouse Int64/UInt128、
# SQLAlchemy/BI 风格 Integer/BigInteger/Int64 等写法。仅同一大类的写法差异不视为漂移。
_INT_PREFIXES = (
    "int", "uint", "tinyint", "smallint", "mediumint", "bigint",
)
_INT_EXACT = {"int", "integer", "bigint", "smallint", "tinyint", "mediumint", "int2", "int4", "int8"}

# 数值（浮点 / 高精度）族：decimal/numeric/float/double/real/Oracle NUMBER、ClickHouse Float64/Decimal、SQLAlchemy Float/Numeric/Double
_NUM_PREFIXES = (
    "float", "double", "decimal", "numeric", "real", "number",
    "binary_double", "binary_float",
)

# 字符串／文本族：char/varchar/text/nvarchar、PostgreSQL character varying、Oracle VARCHAR2/CLOB、ClickHouse String、SQLAlchemy String/Text
_STR_PREFIXES = ("varchar", "nvarchar", "char", "nchar", "character", "clob", "nclob")
_STR_EXACT = {"text", "string", "tinytext", "mediumtext", "longtext", "citext"}

# 二进制族
_BIN_PREFIXES = ("blob", "binary", "bytea", "varbinary")
_BIN_EXACT = {"image", "raw", "tinyblob", "mediumblob", "longblob"}


def _normalize_col_type(t: str) -> str:
    """提取列类型大类，剔除 (长度)、unsigned、Nullable 等修饰符，实现跨方言鲁棒比对。

    各分支解析：int → int、bigint、integer、int64/uint64/Int128、BigInteger、tinyint 等统一归为 integer；
    同族写法差异（如 date 与 datetime/timestamp、Int64 与 int、varchar 与 text、decimal 与 double）不视为漂移；
    仅跨大类（如字符串 text 与日期 date、整型 int 与字符串 varchar）视为不一致。
    """
    t = (t or "").strip().lower()
    # 逐层剥掉 Nullable/LowCardinality/Nested 包装，取其最内层真实类型
    for _ in range(4):
        m = re.match(r"^(nullable|lowcardinality|nested)\s*\((.+)\)$", t)
        if m:
            t = m.group(2).strip().lower()
        else:
            break
    # 去掉 (长度)/(精度)，如 varchar(50)、decimal(18,4)、int(11) unsigned
    if "(" in t:
        t = t.split("(", 1)[0].strip()
    t = t.replace("unsigned", "").replace("zerofill", "").strip()

    if t == "interval":
        return "datetime"
    if t in _INT_EXACT or t.startswith(_INT_PREFIXES):
        return "integer"
    if t.startswith(_NUM_PREFIXES):
        return "numeric"
    if t in _STR_EXACT or t.startswith(_STR_PREFIXES):
        return "string"
    # 日期时间族：date 与 datetime/timestamp/timestamptz/time 视为同一大类，避免误报
    if "timestamp" in t or "datetime" in t or "timetz" in t or t.startswith(("time", "date")):
        return "datetime"
    if t in {"bool", "boolean", "bit"}:
        return "boolean"
    if t in _BIN_EXACT or t.startswith(_BIN_PREFIXES):
        return "binary"
    # 不确定类型归为 other，避免同名异写误报
    return "other"


def _is_string_date_type_mismatch(meta_t: str, phys_t: str) -> bool:
    """判断两侧列类型是否构成「字符串 ↔ 日期」类的类型不一致。

    说人话：只有 string 大类与 date/datetime 大类之间发生跨越才记为 type_mismatch，
    其余跨大类（integer↔numeric、integer↔string、boolean↔integer、binary↔string 等）
    与同族写法差异（Int64 vs int、varchar vs text、NUMBER vs decimal、date vs datetime）
    一律不算。原因：string ↔ date 是导致 SQL 生成/执行出错最强的两类（varchar 存日期 vs
    真实 date 列，字面量与写法完全不同），而其他跨大类差异对生成 SQL 影响很小，反而会因
    两侧写法不对称产生大量干扰告警。
    """
    a = _normalize_col_type(meta_t)
    b = _normalize_col_type(phys_t)
    return (a, b) in (("string", "datetime"), ("datetime", "string"))


class MetadataInspectionService:
    """物理结构巡检执行引擎。"""

    @classmethod
    async def _scan_dataset_tables(
        cls,
        db: AsyncSession,
        dataset: Any,
        adapter: Any,
        emit: Any,
        *,
        progress_base: int = 30,
        progress_range: int = 60,
        prefix: str = "",
    ) -> Dict[str, Any]:
        """内部单数据集逐表物理结构扫描比对逻辑。"""
        tables = dataset.tables or []
        if not tables:
            await emit(
                message=f"{prefix}数据集【{dataset.name}】下暂无纳管的表，跳过扫描。",
                progress=progress_base + progress_range,
            )
            return {"tables_scanned": 0, "columns_scanned": 0, "stale_count": 0, "new_count": 0, "diff_summary": []}

        total_tables = len(tables)
        total_columns_scanned = 0
        total_missing_tables = 0
        total_stale = 0
        total_new = 0
        total_mismatch = 0
        diff_summary: List[Dict[str, Any]] = []

        # 优先批量获取物理库现存表集合，实现表级缺失快速探测
        phys_tables_set: Optional[Set[str]] = None
        try:
            raw_tables = await adapter.get_tables()
            if isinstance(raw_tables, (list, tuple, set)):
                phys_tables_set = {
                    str(t.get("name") or "").lower().strip()
                    for t in raw_tables
                    if isinstance(t, dict) and t.get("name")
                }
        except Exception as ex:
            logger.warning(f"[Schema Inspection] 获取物理库表列表失败，降级为逐表探测: {ex}")

        for idx, table in enumerate(tables, start=1):
            phys_name = table.physical_name or ""
            meta_cols = table.columns or []
            meta_col_names = {c.physical_name.lower().strip() for c in meta_cols if c.physical_name}
            total_columns_scanned += len(meta_cols)

            pct = progress_base + int((idx / total_tables) * progress_range)

            # 1. 检查物理表是否在物理库中已不存在（整表缺失）
            if phys_tables_set is not None and phys_name.lower().strip() not in phys_tables_set:
                total_missing_tables += 1
                await emit(
                    progress=pct,
                    stage="scanning",
                    message=f"{prefix}[表 {idx}/{total_tables}] ⚠️ 发现物理表已不存在: {phys_name}（物理数据库中已删除此表）",
                )
                await MetadataDriftService.record_drift_alert_core(
                    db,
                    dataset_id=dataset.id,
                    table_id=table.id,
                    table_name=phys_name,
                    column_name="*",
                    drift_type="table_missing_in_db",
                    source="manual_inspection",
                    error_sample=f"巡检发现：物理数据库中已无此数据表 {phys_name}（整表缺失）",
                )
                diff_summary.append({
                    "table_name": phys_name,
                    "table_missing": True,
                    "stale_columns": [],
                    "new_columns": [],
                    "type_mismatches": [],
                })
                continue

            await emit(
                progress=pct,
                stage="scanning",
                message=f"{prefix}[表 {idx}/{total_tables}] 对比表: {phys_name} (声明 {len(meta_cols)} 列)...",
            )

            try:
                physical_columns = await adapter.get_columns(table_name=phys_name)
            except Exception as ex:
                err_str = str(ex).lower()
                # 兼容降级模式下的表不存在报错识别
                if any(kw in err_str for kw in ["doesn't exist", "does not exist", "not found", "unknown table", "no such table"]):
                    total_missing_tables += 1
                    await emit(
                        progress=pct,
                        stage="scanning",
                        message=f"{prefix}[表 {idx}/{total_tables}] ⚠️ 发现物理表已不存在: {phys_name}（物理数据库中已删除此表）",
                    )
                    await MetadataDriftService.record_drift_alert_core(
                        db,
                        dataset_id=dataset.id,
                        table_id=table.id,
                        table_name=phys_name,
                        column_name="*",
                        drift_type="table_missing_in_db",
                        source="manual_inspection",
                        error_sample=f"巡检发现：读取物理列报错，表不存在: {str(ex)[:150]}",
                    )
                    diff_summary.append({
                        "table_name": phys_name,
                        "table_missing": True,
                        "stale_columns": [],
                        "new_columns": [],
                        "type_mismatches": [],
                    })
                    continue

                logger.warning(f"[Schema Inspection] 表 {phys_name} 读取物理列失败: {ex}")
                await emit(
                    progress=pct,
                    stage="scanning",
                    message=f"{prefix}[表 {idx}/{total_tables}] ⚠️ 表 {phys_name} 读取物理列失败: {str(ex)[:200]}",
                )
                continue

            phys_col_map: Dict[str, Dict[str, Any]] = {
                str(c.get("name") or "").lower().strip(): c for c in physical_columns if c.get("name")
            }
            phys_col_names = set(phys_col_map.keys())

            stale_cols = sorted(meta_col_names - phys_col_names)
            new_cols = sorted(phys_col_names - meta_col_names)
            common_cols = sorted(meta_col_names & phys_col_names)

            meta_col_objs: Dict[str, Any] = {
                c.physical_name.lower().strip(): c for c in meta_cols if c.physical_name
            }

            mismatch_cols: List[str] = []
            for col in common_cols:
                meta_c = meta_col_objs.get(col)
                phys_c = phys_col_map.get(col, {})
                meta_t = str(getattr(meta_c, "type", "") or "").strip()
                phys_t = str(phys_c.get("type") or "").strip()
                if meta_t and phys_t and _is_string_date_type_mismatch(meta_t, phys_t):
                    mismatch_cols.append(col)

            if not stale_cols and not new_cols and not mismatch_cols:
                await emit(
                    progress=pct,
                    stage="scanning",
                    message=f"{prefix}[表 {idx}/{total_tables}] ✓ 表 {phys_name} 物理结构一致 ({len(phys_col_names)} 列正常)",
                )
            else:
                table_diff: Dict[str, Any] = {
                    "table_name": phys_name,
                    "stale_columns": stale_cols,
                    "new_columns": new_cols,
                    "type_mismatches": mismatch_cols,
                }
                diff_summary.append(table_diff)

                if stale_cols:
                    total_stale += len(stale_cols)
                    for col in stale_cols:
                        await emit(
                            progress=pct,
                            stage="scanning",
                            message=f"{prefix}[表 {idx}/{total_tables}] ⚠️ 发现物理缺失字段: {phys_name}.{col}（物理库已删除）",
                        )
                        await MetadataDriftService.record_drift_alert_core(
                            db,
                            dataset_id=dataset.id,
                            table_name=phys_name,
                            column_name=col,
                            drift_type="missing_in_db",
                            source="manual_inspection",
                            error_sample=f"巡检发现：物理表 {phys_name} 中已无此字段",
                        )

                if new_cols:
                    total_new += len(new_cols)
                    for col in new_cols:
                        meta_info = phys_col_map.get(col, {})
                        col_type = meta_info.get("type") or "unknown"
                        await emit(
                            progress=pct,
                            stage="scanning",
                            message=f"{prefix}[表 {idx}/{total_tables}] ℹ️ 发现物理新增字段: {phys_name}.{col} (类型: {col_type})",
                        )
                        await MetadataDriftService.record_drift_alert_core(
                            db,
                            dataset_id=dataset.id,
                            table_name=phys_name,
                            column_name=col,
                            drift_type="new_in_db",
                            source="manual_inspection",
                            error_sample=f"巡检发现：物理表新增列，类型 {col_type}",
                        )

                if mismatch_cols:
                    total_mismatch += len(mismatch_cols)
                    for col in mismatch_cols:
                        meta_c = meta_col_objs.get(col)
                        phys_c = phys_col_map.get(col, {})
                        meta_t = str(getattr(meta_c, "type", "") or "").strip()
                        phys_t = str(phys_c.get("type") or "").strip()
                        await emit(
                            progress=pct,
                            stage="scanning",
                            message=f"{prefix}[表 {idx}/{total_tables}] ⚠️ 发现字段类型不一致: {phys_name}.{col}（元数据: {meta_t} vs 物理库: {phys_t}）",
                        )
                        await MetadataDriftService.record_drift_alert_core(
                            db,
                            dataset_id=dataset.id,
                            table_name=phys_name,
                            column_name=col,
                            drift_type="type_mismatch",
                            source="manual_inspection",
                            error_sample=f"巡检发现类型不匹配：元数据声明为 {meta_t}，物理库实际为 {phys_t}",
                        )

        return {
            "tables_scanned": total_tables,
            "columns_scanned": total_columns_scanned,
            "missing_tables_count": total_missing_tables,
            "stale_count": total_stale,
            "new_count": total_new,
            "mismatch_count": total_mismatch,
            "diff_summary": diff_summary,
        }

    @classmethod
    async def inspect_dataset(
        cls,
        db: AsyncSession,
        dataset_id: int,
        task_id: str,
    ) -> Dict[str, Any]:
        """执行单数据集物理结构巡检主流程，包含实时日志推流。"""

        async def emit(
            *,
            event: str = "progress",
            stage: str = "inspecting",
            message: str,
            progress: Optional[int] = None,
            error_detail: Optional[str] = None,
        ):
            try:
                await metadata_sync_log_service.publish(
                    task_id,
                    event=event,
                    stage=stage,
                    message=message,
                    progress=progress,
                    error_detail=error_detail,
                )
            except Exception:
                logger.warning("[Schema Inspection] 日志推流失败", exc_info=True)

        # 1. 加载数据集与表定义
        await emit(progress=5, stage="loading", message="正在加载数据集配置与元数据表定义...")
        dataset = await MetadataService.get_dataset_by_id(db, dataset_id, is_admin=True)
        if not dataset:
            await emit(event="failed", stage="failed", message="数据集不存在", error_detail="数据集不存在")
            return {"success": False, "error": "数据集不存在"}

        data_source = dataset.data_source or ""
        if not data_source:
            await emit(
                event="failed",
                stage="failed",
                message=f"数据集【{dataset.name}】未绑定有效数据源",
                error_detail="未绑定数据源",
            )
            return {"success": False, "error": "未绑定数据源"}

        # 2. 初始化数据源适配器
        await emit(progress=15, stage="connecting", message=f"正在连接数据源【{data_source}】物理数据库...")
        try:
            adapter = await get_adapter(data_source)
        except Exception as e:
            err_msg = f"连接数据源失败: {str(e)}"
            logger.exception(f"[Schema Inspection] {err_msg}")
            await emit(event="failed", stage="failed", message=err_msg, error_detail=err_msg)
            return {"success": False, "error": err_msg}

        await emit(progress=25, stage="connected", message=f"数据源【{data_source}】连接成功，准备巡检物理表结构...")

        tables = dataset.tables or []
        if not tables:
            await emit(
                event="completed",
                stage="completed",
                message="当前数据集下暂无纳管的表，巡检完成（0 张表）。",
                progress=100,
            )
            return {"success": True, "tables_scanned": 0, "stale_count": 0, "new_count": 0}

        await emit(
            progress=30,
            stage="scanning",
            message=f"开始扫描数据集纳管的 {len(tables)} 张数据表物理定义...",
        )

        scan_res = await cls._scan_dataset_tables(
            db, dataset, adapter, emit, progress_base=30, progress_range=60
        )

        # 4. 提交告警变更
        await db.commit()

        # 5. 巡检完成报告
        total_missing_tables = scan_res.get("missing_tables_count", 0)
        total_tables = scan_res["tables_scanned"]
        total_columns_scanned = scan_res["columns_scanned"]
        total_stale = scan_res["stale_count"]
        total_new = scan_res["new_count"]
        total_mismatch = scan_res.get("mismatch_count", 0)

        summary_msg = (
            f"巡检完成！共扫描 {total_tables} 张表、{total_columns_scanned} 个元数据列。"
        )
        if total_missing_tables == 0 and total_stale == 0 and total_new == 0 and total_mismatch == 0:
            summary_msg += " 物理库结构完全一致，未发现任何漂移。"
        else:
            diff_parts = []
            if total_missing_tables > 0:
                diff_parts.append(f"{total_missing_tables} 张表物理缺失")
            if total_stale > 0:
                diff_parts.append(f"{total_stale} 处字段物理缺失")
            if total_new > 0:
                diff_parts.append(f"{total_new} 处物理新增")
            if total_mismatch > 0:
                diff_parts.append(f"{total_mismatch} 处类型不一致")
            summary_msg += f" 检出 {'、'.join(diff_parts)}，已汇总至待处理告警。"

        await emit(
            progress=100,
            event="completed",
            stage="completed",
            message=summary_msg,
        )

        return {
            "success": True,
            "tables_scanned": total_tables,
            "columns_scanned": total_columns_scanned,
            "missing_tables_count": total_missing_tables,
            "stale_count": total_stale,
            "new_count": total_new,
            "mismatch_count": total_mismatch,
            "diff_summary": scan_res["diff_summary"],
        }

    @classmethod
    async def inspect_all_datasets(
        cls,
        db: AsyncSession,
        task_id: str,
        *,
        active_only: bool = True,
    ) -> Dict[str, Any]:
        """执行全库数据集的批量物理结构巡检，统一输出推流进度与体检报告。

        :param active_only: 是否仅巡检开启状态 (status == 1) 的数据集，默认为 True（跳过维护/禁用数据集）。
        """

        async def emit(
            *,
            event: str = "progress",
            stage: str = "inspecting",
            message: str,
            progress: Optional[int] = None,
            error_detail: Optional[str] = None,
        ):
            try:
                await metadata_sync_log_service.publish(
                    task_id,
                    event=event,
                    stage=stage,
                    message=message,
                    progress=progress,
                    error_detail=error_detail,
                )
            except Exception:
                logger.warning("[Schema Inspection] 全局日志推流失败", exc_info=True)

        scope_title = "开启状态" if active_only else "全量"
        await emit(progress=5, stage="loading", message=f"正在获取系统内{scope_title}数据集清单...")
        from app.models.metadata import MetaDataset
        from sqlalchemy import select

        stmt = select(MetaDataset).order_by(MetaDataset.id.asc())
        if active_only:
            stmt = stmt.where(MetaDataset.status == 1)
        datasets = list((await db.execute(stmt)).scalars().all())

        if not datasets:
            empty_msg = "系统内暂无开启状态的数据集，巡检结束。" if active_only else "系统内暂无任何数据集，巡检结束。"
            await emit(
                event="completed",
                stage="completed",
                message=empty_msg,
                progress=100,
            )
            return {"success": True, "datasets_scanned": 0}

        total_ds = len(datasets)
        logger.info(f"🔍 [元数据全库巡检] 开始批量物理结构比对 | 范围: {scope_title} | 数据集总数: {total_ds} 个")
        await emit(
            progress=10,
            stage="queued",
            message=f"已就绪，准备依次对 {scope_title} {total_ds} 个数据集开展物理结构一致性巡检...",
        )

        total_tables_all = 0
        total_columns_all = 0
        total_missing_tables_all = 0
        total_stale_all = 0
        total_new_all = 0
        total_mismatch_all = 0
        drift_datasets_count = 0
        failed_datasets_count = 0

        for idx, ds in enumerate(datasets, start=1):
            ds_name = ds.name
            ds_prefix = f"[{idx}/{total_ds} 数据集: {ds_name}] "
            ds_base_pct = 10 + int(((idx - 1) / total_ds) * 85)
            ds_range_pct = max(1, int((1 / total_ds) * 85))

            logger.info(f"  ↳ [{idx}/{total_ds}] 开始比对数据集 '{ds_name}' (ID: {ds.id}, 数据源: {ds.data_source or '未配置'})...")
            await emit(
                progress=ds_base_pct,
                stage="scanning",
                message=f"{ds_prefix}开始巡检（数据源: {ds.data_source or '无'}，状态: {'正常' if ds.status == 1 else '维护期'}）...",
            )

            data_source = ds.data_source or ""
            if not data_source:
                failed_datasets_count += 1
                logger.warning(f"  ⚠️ [{idx}/{total_ds}] 数据集 '{ds_name}' (ID: {ds.id}) 跳过：未配置关联数据源")
                await emit(
                    progress=ds_base_pct + ds_range_pct,
                    stage="scanning",
                    message=f"{ds_prefix}⚠️ 跳过：未配置关联数据源",
                )
                continue

            try:
                adapter = await get_adapter(data_source)
            except Exception as ex:
                failed_datasets_count += 1
                logger.warning(f"  ❌ [{idx}/{total_ds}] 数据集 '{ds_name}' (ID: {ds.id}) 数据源 {data_source} 连接失败: {ex}")
                await emit(
                    progress=ds_base_pct + ds_range_pct,
                    stage="scanning",
                    message=f"{ds_prefix}❌ 数据源连接失败: {str(ex)[:150]}",
                )
                continue

            # 加载完整表结构
            full_ds = await MetadataService.get_dataset_by_id(db, ds.id, is_admin=True)
            if not full_ds:
                continue

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
            c_scanned = scan_res["columns_scanned"]
            missing_tables = scan_res.get("missing_tables_count", 0)
            stale = scan_res["stale_count"]
            new = scan_res["new_count"]
            mismatch = scan_res.get("mismatch_count", 0)

            total_tables_all += t_scanned
            total_columns_all += c_scanned
            total_missing_tables_all += missing_tables
            total_stale_all += stale
            total_new_all += new
            total_mismatch_all += mismatch

            if missing_tables > 0 or stale > 0 or new > 0 or mismatch > 0:
                drift_datasets_count += 1
                diff_desc = []
                if missing_tables > 0:
                    diff_desc.append(f"{missing_tables} 张表缺失")
                if stale > 0:
                    diff_desc.append(f"{stale} 处字段缺失")
                if new > 0:
                    diff_desc.append(f"{new} 处新增")
                if mismatch > 0:
                    diff_desc.append(f"{mismatch} 处类型不一致")
                logger.warning(
                    f"  ⚠️ [{idx}/{total_ds}] 数据集 '{ds_name}' 检出差异: {'、'.join(diff_desc)} (表: {t_scanned}, 字段: {c_scanned})"
                )
                await emit(
                    progress=ds_base_pct + ds_range_pct,
                    stage="scanning",
                    message=f"{ds_prefix}⚠️ 巡检完毕: 检出 {'、'.join(diff_desc)}",
                )
            else:
                logger.info(
                    f"  ✅ [{idx}/{total_ds}] 数据集 '{ds_name}' 结构完全一致 (扫描 {t_scanned} 张表, {c_scanned} 个字段)"
                )
                await emit(
                    progress=ds_base_pct + ds_range_pct,
                    stage="scanning",
                    message=f"{ds_prefix}✓ 巡检完毕: {t_scanned} 张表结构均与物理库一致",
                )

        # 提交所有沉淀的告警记录
        await db.commit()

        logger.info(
            f"🏁 [元数据全库巡检] 批量比对结束: 共扫描 {total_ds} 个数据集、{total_tables_all} 张物理表、{total_columns_all} 个字段 | "
            f"表缺失: {total_missing_tables_all} | 字段缺失: {total_stale_all} | 新增: {total_new_all} | 类型不匹配: {total_mismatch_all} | 数据源连接失败: {failed_datasets_count}"
        )

        # 汇总全库巡检报告
        summary_msg = (
            f"全库批量巡检完成！共扫描 {total_ds} 个数据集、{total_tables_all} 张物理表、{total_columns_all} 个元数据列。"
        )
        if total_missing_tables_all == 0 and total_stale_all == 0 and total_new_all == 0 and total_mismatch_all == 0 and failed_datasets_count == 0:
            summary_msg += " 恭喜！全库物理表结构完全一致，未发现任何漂移差异。"
        else:
            diff_parts = []
            if total_missing_tables_all > 0:
                diff_parts.append(f"{total_missing_tables_all} 张表物理缺失")
            if total_stale_all > 0:
                diff_parts.append(f"{total_stale_all} 处字段物理缺失")
            if total_new_all > 0:
                diff_parts.append(f"{total_new_all} 处物理新增")
            if total_mismatch_all > 0:
                diff_parts.append(f"{total_mismatch_all} 处类型不一致")

            summary_msg += (
                f" 累计在 {drift_datasets_count} 个数据集中检出 {'、'.join(diff_parts)}"
            )
            if failed_datasets_count > 0:
                summary_msg += f"（另有 {failed_datasets_count} 个数据集因数据源连接失败未能比对）"
            summary_msg += "，已全部收拢至全局漂移治理大盘供人机协同处置。"

        await emit(
            progress=100,
            event="completed",
            stage="completed",
            message=summary_msg,
        )

        return {
            "success": True,
            "datasets_scanned": total_ds,
            "tables_scanned": total_tables_all,
            "columns_scanned": total_columns_all,
            "missing_tables_count": total_missing_tables_all,
            "stale_count": total_stale_all,
            "new_count": total_new_all,
            "mismatch_count": total_mismatch_all,
            "drift_datasets_count": drift_datasets_count,
            "failed_datasets_count": failed_datasets_count,
        }

