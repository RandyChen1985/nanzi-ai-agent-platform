import logging
import os
from typing import Optional, Dict, List
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis
from app.core.config import settings

logger = logging.getLogger(__name__)

CACHE_PREFIX = "sys_config:"
CACHE_TTL = 300  # 5 minutes

class ConfigService:
    
    # Memory Cache for get_all_from_db
    _all_configs_cache: Optional[Dict[str, dict]] = None
    _all_configs_last_fetched: float = 0
    _ALL_CONFIGS_TTL = 60.0  # 60 seconds

    @staticmethod
    async def get_all_from_db() -> Dict[str, dict]:
        """
        Retrieves all configs from DB.
        Returns a dict: { key: { value, description, category, is_secret } }
        Now uses short-term memory cache (60s).
        """
        import time
        if ConfigService._all_configs_cache and (time.time() - ConfigService._all_configs_last_fetched < ConfigService._ALL_CONFIGS_TTL):
            return ConfigService._all_configs_cache

        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT `key`, `value`, `description`, `category`, `is_secret` FROM system_configs"))
            rows = result.fetchall()
            configs = {}
            for row in rows:
                configs[row[0]] = {
                    "value": row[1],
                    "description": row[2],
                    "category": row[3],
                    "is_secret": bool(row[4])
                }
            
            # Update Cache
            ConfigService._all_configs_cache = configs
            ConfigService._all_configs_last_fetched = time.time()
            
            return configs

    @staticmethod
    async def get(key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get configuration value.
        Strategy:
        1. Check Redis Cache
        2. Check DB
        3. Return Default
        """
        # 1. Check Redis
        redis = await get_redis()
        if redis:
            cached_val = await redis.get(f"{CACHE_PREFIX}{key}")
            if cached_val is not None:
                return cached_val
        
        # 2. Check DB
        db_val = None
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("SELECT value FROM system_configs WHERE `key` = :key"), 
                    {"key": key}
                )
                row = result.fetchone()
                if row:
                    db_val = row[0]
                    # Cache it if found (even if empty string)
                    await redis.set(f"{CACHE_PREFIX}{key}", db_val, ex=CACHE_TTL)
        except Exception as e:
            logger.error(f"Failed to fetch config '{key}' from DB: {e}")
        
        if db_val is not None and db_val != "":
             return db_val
             
        # 3. Default
        return default

    @staticmethod
    async def set_config(
        key: str, 
        value: str, 
        description: Optional[str] = None, 
        category: str = "general", 
        is_secret: bool = False,
        changed_by: str = "system",
        change_reason: Optional[str] = None,
        db: Optional[AsyncSession] = None
    ) -> bool:
        """
        Set configuration value in DB and update Redis.
        Now supports Audit Logging.
        """
        from app.services.auth_service import AuthService
        session, is_local = await AuthService._get_session(db)
        try:
            # 1. Fetch old value for audit
            result = await session.execute(
                text("SELECT value FROM system_configs WHERE `key` = :key"), 
                {"key": key}
            )
            row = result.fetchone()
            old_value = row[0] if row else None
            change_type = "UPDATE" if row else "CREATE"

            # 2. Upsert config
            # Note: ON DUPLICATE KEY UPDATE is specific to MySQL
            sql = """
                INSERT INTO system_configs (`key`, `value`, `description`, `category`, `is_secret`)
                VALUES (:key, :value, :description, :category, :is_secret)
                ON DUPLICATE KEY UPDATE
                    `value` = VALUES(`value`),
                    `description` = COALESCE(VALUES(`description`), system_configs.`description`),
                    `category` = COALESCE(VALUES(`category`), system_configs.`category`),
                    `is_secret` = COALESCE(VALUES(`is_secret`), system_configs.`is_secret`)
            """
            await session.execute(text(sql), {
                "key": key, 
                "value": value, 
                "description": description, 
                "category": category, 
                "is_secret": is_secret
            })
            
            # 3. Insert Audit Log
            # Only log if value changed or it's a new key
            if old_value != value:
                audit_sql = """
                    INSERT INTO system_config_history 
                    (config_key, old_value, new_value, description, changed_by, change_type)
                    VALUES (:key, :old_value, :new_value, :audit_desc, :changed_by, :change_type)
                """
                # Use provided change_reason if available, else description, else generic
                audit_desc = change_reason or description or "Config updated via set_config"
                
                await session.execute(text(audit_sql), {
                    "key": key, 
                    "old_value": old_value, 
                    "new_value": value, 
                    "audit_desc": audit_desc, 
                    "changed_by": changed_by, 
                    "change_type": change_type
                })
            
            await session.commit()
            
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()
        
        redis = await get_redis()
        if redis:
            await redis.set(f"{CACHE_PREFIX}{key}", value, ex=CACHE_TTL)
            logger.info(f"Config '{key}' updated in DB and Redis. Audit log created.")
        
        # Invalidate Memory Cache
        ConfigService._all_configs_cache = None
            
        return old_value != value

    @staticmethod
    async def get_all_configs_grouped() -> Dict[str, List[dict]]:
        """
        Get all configs grouped by category for UI display.
        Secrets are masked.
        """
        configs = await ConfigService.get_all_from_db()
        grouped = {}
        
        for key, data in configs.items():
            cat = data['category']
            if cat == 'branding':
                continue
            if cat not in grouped:
                grouped[cat] = []
            
            # Mask secret
            display_value = data['value']
            if data['is_secret'] and display_value:
                if len(display_value) > 8:
                    display_value = display_value[:3] + "****" + display_value[-4:]
                else:
                    display_value = "****"
            
            grouped[cat].append({
                "key": key,
                "value": display_value, # Masked for display
                "description": data['description'],
                "is_secret": data['is_secret']
            })
            
        return grouped

    @staticmethod
    async def update_config_value(key: str, value: str, changed_by: str = "system", change_reason: Optional[str] = None) -> bool:
        """
        Update ONLY the configuration value in DB and Redis.
        Preserves other fields (category, description, is_secret).
        """
        old_value = None # Initialize old_value outside the session block
        async with AsyncSessionLocal() as session:
            try:
                # 1. Fetch old value
                result = await session.execute(
                    text("SELECT value FROM system_configs WHERE `key` = :key"), 
                    {"key": key}
                )
                row = result.fetchone()
                old_value = row[0] if row else None
                
                if old_value is None:
                    # Key doesn't exist, we must use set_config but since we are in async context manager here,
                    # we should probably close, or just handle logic here.
                    # Simpler is to return and let caller handle, or recursive call?
                    # Since we're inside a session, calling set_config (which creates new session) is safe but inefficient.
                    # Ideally we replicate logic. But for now, let's just use recursive call after exiting session?
                    pass 
                
                # Logic if exists:
                if old_value is not None:
                    # 2. Update value
                    await session.execute(
                        text("UPDATE system_configs SET value = :value WHERE `key` = :key"),
                        {"value": value, "key": key}
                    )
                    
                    # 3. Audit Log
                    if old_value != value:
                        audit_sql = """
                            INSERT INTO system_config_history 
                            (config_key, old_value, new_value, description, changed_by, change_type)
                            VALUES (:key, :old_value, :value, :audit_desc, :changed_by, 'UPDATE')
                        """
                        audit_desc = change_reason or "Value updated"
                        await session.execute(text(audit_sql), {
                            "key": key, 
                            "old_value": old_value, 
                            "value": value, 
                            "audit_desc": audit_desc, 
                            "changed_by": changed_by
                        })
                    
                    await session.commit()
            except Exception:
                await session.rollback()
                raise

        if old_value is None:
             # Fallback to set_config outside of the session block
             return await ConfigService.set_config(key, value, changed_by=changed_by, change_reason=change_reason)
        
        redis = await get_redis()
        if redis:
            await redis.set(f"{CACHE_PREFIX}{key}", value, ex=CACHE_TTL)
            logger.info(f"Config '{key}' value updated. Audit log created.")
        
        # Invalidate Memory Cache
        ConfigService._all_configs_cache = None
            
        return old_value != value

    @staticmethod
    async def bulk_update(updates: List[dict], changed_by: str = "system"):
        """
        Bulk update configs.
        updates: [{key, value}, ...]
        """
        for item in updates:
            key = item.get('key')
            val = item.get('value')
            if key is not None and val is not None:
                await ConfigService.update_config_value(key, val, changed_by=changed_by, change_reason="Bulk update")

    @staticmethod
    async def get_config_history(key: str, limit: int = 50) -> List[dict]:
        """
        Get history of a specific configuration key.
        """
        async with AsyncSessionLocal() as session:
            sql = """
                SELECT id, config_key, old_value, new_value, description, changed_by, change_type, created_at
                FROM system_config_history
                WHERE config_key = :key
                ORDER BY created_at DESC
                LIMIT :limit
            """
            result = await session.execute(text(sql), {"key": key, "limit": limit})
            rows = result.fetchall()
            
            history = []
            for r in rows:
                history.append({
                    "id": r[0],
                    "config_key": r[1],
                    "old_value": r[2],
                    "new_value": r[3],
                    "description": r[4],
                    "changed_by": r[5],
                    "change_type": r[6],
                    "created_at": r[7].strftime("%Y-%m-%d %H:%M:%S") if r[7] else ""
                })
            return history