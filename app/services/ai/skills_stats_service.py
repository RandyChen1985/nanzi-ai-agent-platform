import logging
import datetime
from typing import Set, List, Dict, Any, Union
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

class SkillsStatsService:
    TOTAL_KEY = "nanzi:skills:stats:total"
    DAILY_KEY_PREFIX = "nanzi:skills:stats:daily:"
    DAILY_TTL = 60 * 86400  # 60 days

    async def record_activations(self, skill_ids: Union[Set[str], List[str]]) -> None:
        """
        累加记录技能激活数据
        """
        redis = await get_redis()
        if not redis or not skill_ids:
            return
        
        today = datetime.date.today().isoformat()
        daily_key = f"{self.DAILY_KEY_PREFIX}{today}"
        
        try:
            for skill_id in skill_ids:
                if not skill_id:
                    continue
                # 累加总计
                await redis.hincrby(self.TOTAL_KEY, skill_id, 1)
                # 累加每日
                await redis.hincrby(daily_key, skill_id, 1)
            # 延长今日 Key 的有效期至 60 天
            await redis.expire(daily_key, self.DAILY_TTL)
        except Exception as e:
            logger.error(f"[SkillsStats] Failed to record skill stats: {e}")

    async def get_stats(self, days: int = 30) -> Dict[str, Any]:
        """
        拉取前 days 天的每日趋势及累计分布
        """
        redis = await get_redis()
        total_stats = {}
        trend_stats = {}
        
        if not redis:
            return {"total": total_stats, "trend": trend_stats}
            
        try:
            # 1. 获取累计
            raw_total = await redis.hgetall(self.TOTAL_KEY) or {}
            for k, v in raw_total.items():
                skill_id = k.decode("utf-8") if isinstance(k, bytes) else k
                count = int(v) if v else 0
                total_stats[skill_id] = count
                
            # 2. 获取趋势（最近 30 天，由远及近）
            today = datetime.date.today()
            for i in range(days - 1, -1, -1):
                d = today - datetime.timedelta(days=i)
                date_str = d.isoformat()
                daily_key = f"{self.DAILY_KEY_PREFIX}{date_str}"
                
                trend_stats[date_str] = {}
                raw_daily = await redis.hgetall(daily_key) or {}
                for k, v in raw_daily.items():
                    skill_id = k.decode("utf-8") if isinstance(k, bytes) else k
                    count = int(v) if v else 0
                    trend_stats[date_str][skill_id] = count
                    
        except Exception as e:
            logger.error(f"[SkillsStats] Failed to retrieve stats: {e}")
            
        return {
            "total": total_stats,
            "trend": trend_stats
        }

skills_stats_service = SkillsStatsService()
