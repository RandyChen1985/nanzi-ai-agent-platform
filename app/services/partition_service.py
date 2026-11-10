import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import AsyncSessionLocal
from app.core.config import settings
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

PARTITION_TABLES = ("ai_agent_access_logs", "ai_agent_execution_traces")

class PartitionService:
    """
    Service to manage MySQL Range Partitions for audit and trace logs.
    Supports auto-pruning, auto-expansion, and checking database partition status.
    """

    @staticmethod
    async def is_table_partitioned(db: AsyncSession, table_name: str) -> bool:
        """
        Check if a given table has active Range partitioning.
        """
        if settings.normalized_database_type == "postgresql":
            # PostgreSQL baseline tables are intentionally unpartitioned. Native
            # PostgreSQL partition lifecycle management is a separate concern.
            return False
        sql = """
            SELECT COUNT(*) 
            FROM information_schema.partitions 
            WHERE table_schema = DATABASE() 
              AND table_name = :table_name 
              AND partition_name IS NOT NULL
        """
        res = await db.execute(text(sql), {"table_name": table_name})
        count = res.scalar() or 0
        # If it returns only 1 partition with partition_name = None, it's not partitioned.
        # Otherwise, if count > 0 it has active partitions.
        return count > 1

    @staticmethod
    async def get_partition_status(db: AsyncSession) -> List[Dict[str, Any]]:
        """
        Retrieve range partition meta details for the monitored tables.
        """
        if settings.normalized_database_type == "postgresql":
            return []
        sql = """
            SELECT 
                TABLE_NAME AS table_name,
                PARTITION_NAME AS partition_name,
                PARTITION_DESCRIPTION AS less_than,
                TABLE_ROWS AS table_rows
            FROM information_schema.partitions
            WHERE table_schema = DATABASE()
              AND table_name IN :tables
              AND partition_name IS NOT NULL
            ORDER BY table_name, PARTITION_ORDINAL_POSITION
        """
        # ``IN :tables`` 必须用 expanding 绑定：否则 SQLAlchemy 只会渲染出一个
        # 占位符，把整个 tuple 当成单个标量丢给驱动。asyncmy 之类驱动会直接抛
        # “tuple can not be used as parameter”，pymysql 则会拼出无引号的 IN
        # (a, b) 造成语法错误。expanding 会展开成 IN (%s, %s)。
        stmt = text(sql).bindparams(bindparam("tables", expanding=True))
        res = await db.execute(stmt, {"tables": list(PARTITION_TABLES)})
        rows = res.fetchall()

        result = []
        for r in rows:
            # Map less_than definition (e.g., '2026-06-01 00:00:00' or MAXVALUE)
            desc = r[2]
            if desc and desc.strip().upper() == "MAXVALUE":
                data_range = "MAXVALUE (兜底)"
            elif desc:
                # Remove quotes if present
                clean_desc = desc.replace("'", "")
                data_range = f"小于 {clean_desc}"
            else:
                data_range = "未知"

            result.append({
                "table_name": r[0],
                "partition_name": r[1],
                "less_than": desc.replace("'", "") if desc else "",
                "data_range": data_range,
                "table_rows": r[3] or 0
            })
        return result

    @staticmethod
    async def expand_partitions(db: AsyncSession) -> Dict[str, List[str]]:
        """
        Examine logs tables and pre-create partitions for the next 2 months.
        Uses ALTER TABLE REORGANIZE PARTITION to avoid MAXVALUE boundary errors.
        """
        if settings.normalized_database_type == "postgresql":
            logger.info("PostgreSQL partition expansion is not enabled; skipping.")
            return {}
        expanded = {}
        for table in PARTITION_TABLES:
            partitioned = await PartitionService.is_table_partitioned(db, table)
            if not partitioned:
                logger.info(f"Table '{table}' is not partitioned. Skipping partition expansion.")
                continue

            expanded[table] = []
            # Calculate months we want: next month and the month after
            # current time
            now = datetime.now()
            target_months = []
            for delta_month in (1, 2):
                # Simple month advance math
                year = now.year
                month = now.month + delta_month
                while month > 12:
                    month -= 12
                    year += 1
                
                # Next boundary date is the 1st of month + 1
                boundary_year = year
                boundary_month = month + 1
                if boundary_month > 12:
                    boundary_month = 1
                    boundary_year += 1
                
                part_name = f"p{year}{month:02d}"
                boundary_str = f"{boundary_year}-{boundary_month:02d}-01 00:00:00"
                target_months.append((part_name, boundary_str))

            # Check existing partitions
            check_sql = """
                SELECT partition_name 
                FROM information_schema.partitions 
                WHERE table_schema = DATABASE() 
                  AND table_name = :table_name
            """
            existing_res = await db.execute(text(check_sql), {"table_name": table})
            existing_parts = {r[0] for r in existing_res.fetchall() if r[0]}

            # Filter to partitions we need to add
            to_add = [item for item in target_months if item[0] not in existing_parts]
            if not to_add:
                logger.info(f"All target partitions for table '{table}' already exist. No expansion needed.")
                continue

            # Build REORGANIZE PARTITION SQL
            # Reorganizing pmax to inject new partitions before MAXVALUE
            reorganize_chunks = []
            for part_name, boundary in to_add:
                reorganize_chunks.append(f"PARTITION {part_name} VALUES LESS THAN ('{boundary}')")
                expanded[table].append(part_name)
            
            reorganize_chunks.append("PARTITION pmax VALUES LESS THAN MAXVALUE")
            reorganize_sql = f"""
                ALTER TABLE `{table}` 
                REORGANIZE PARTITION pmax INTO (
                    {", ".join(reorganize_chunks)}
                )
            """
            try:
                await db.execute(text(reorganize_sql))
                await db.commit()
                logger.info(f"Successfully expanded partitions for table '{table}': {expanded[table]}")
            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to expand partitions for table '{table}': {e}", exc_info=True)
                raise
        
        return expanded

    @staticmethod
    async def prune_expired_logs(db: AsyncSession, retention_days: int) -> Dict[str, Any]:
        """
        Deletes log records older than retention_days.
        - For partitioned tables: Drops partitions where all records are older than threshold.
        - For unpartitioned tables: Performs micro-batch DELETEs.
        """
        if retention_days <= 0:
            logger.info("Retention days <= 0. Skipping log pruning.")
            return {"status": "skipped", "message": "Retention days must be positive"}

        threshold_date = datetime.now() - timedelta(days=retention_days)
        pruned_results = {}

        for table in PARTITION_TABLES:
            partitioned = await PartitionService.is_table_partitioned(db, table)
            if partitioned:
                # 1. Partitioned Table Clean
                # Fetch partitions and check their bounds
                sql = """
                    SELECT partition_name, partition_description 
                    FROM information_schema.partitions 
                    WHERE table_schema = DATABASE() 
                      AND table_name = :table_name 
                      AND partition_name IS NOT NULL
                      AND partition_name != 'pmax'
                """
                res = await db.execute(text(sql), {"table_name": table})
                partitions = res.fetchall()

                dropped = []
                for p_name, less_than_val in partitions:
                    if not less_than_val:
                        continue
                    # Parse boundary date
                    try:
                        # Clean up quotes (e.g., '2026-05-01 00:00:00')
                        clean_val = less_than_val.replace("'", "").strip()
                        # If description represents date (Range Columns)
                        bound_date = datetime.strptime(clean_val, "%Y-%m-%d %H:%M:%S")
                        
                        # If the partition boundary is completely before our threshold date,
                        # it means ALL data inside this partition is expired. We can DROP it!
                        if bound_date < threshold_date:
                            drop_sql = f"ALTER TABLE `{table}` DROP PARTITION {p_name}"
                            await db.execute(text(drop_sql))
                            await db.commit()
                            dropped.append(p_name)
                    except Exception as parse_err:
                        logger.warning(f"Skipped parsing partition bound '{less_than_val}' for table '{table}': {parse_err}")
                        continue
                
                pruned_results[table] = {"type": "partition", "dropped": dropped}
                if dropped:
                    logger.info(f"Dropped expired partitions for table '{table}': {dropped}")

            else:
                # 2. Unpartitioned Table Clean (Batch Delete)
                logger.info(f"Table '{table}' is not partitioned. Falling back to micro-batch DELETE.")
                batch_size = 5000
                total_deleted = 0
                while True:
                    if settings.normalized_database_type == "postgresql":
                        table_ref = f'"{table}"'
                        del_sql = f"""
                            DELETE FROM {table_ref}
                            WHERE ctid IN (
                                SELECT ctid FROM {table_ref}
                                WHERE created_at < :threshold
                                LIMIT :batch_size
                            )
                        """
                    else:
                        del_sql = f"DELETE FROM `{table}` WHERE created_at < :threshold LIMIT :batch_size"
                    del_res = await db.execute(text(del_sql), {"threshold": threshold_date, "batch_size": batch_size})
                    await db.commit()
                    deleted = del_res.rowcount
                    total_deleted += deleted
                    if deleted < batch_size:
                        break
                
                pruned_results[table] = {"type": "delete", "deleted_rows": total_deleted}
                if total_deleted > 0:
                    logger.info(f"Cleaned {total_deleted} rows from unpartitioned table '{table}'")

        return {"status": "success", "threshold_date": threshold_date.strftime("%Y-%m-%d %H:%M:%S"), "details": pruned_results}
