"""知识库引用详情的 Redis 旁路存储。

背景：引用详情（citations）此前只存在于实时 SSE 流与前端内存中，刷新页面后历史
消息里的 ``[ID:n]`` 徽章点不开、引用列表整块消失。落库会让
``ai_agent_execution_history`` 随切片全文明显膨胀，因此改为独立 Redis key 按
trace_id 旁路保存，TTL 跟随会话历史。

命名空间刻意选用 ``conversation:`` 前缀：Redis 浏览器分组规则表（受
``tests/frontend/test_redis_browser_grouping_contract.py`` 约束，45 条前缀必须按
长度严格降序）已把该前缀映射到「会话记忆与上下文」，新增 key 无需改动规则表。

旁路定位意味着：任何读写失败都不得影响对话主流程与历史接口可用性，失败只告警。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# 只保留弹层展示与「查看原档」反查必需的字段，其余（RAGFlow 原始大对象等）一律丢弃。
_ALLOWED_FIELDS = (
    "id",
    "chunk_id",
    "doc_id",
    "dataset_id",
    "doc_name",
    "document_name",
    "content",
    "similarity",
    "page_no",
    "source_type",
    "link",
    "positions",
)


class KnowledgeCitationStore:
    """按 trace_id 保存单轮引用详情的旁路缓存。"""

    KEY_PREFIX = "conversation"
    KEY_SUFFIX = "citation_detail"
    MAX_ITEMS = 100
    MAX_CONTENT_CHARS = 8000
    MAX_PAYLOAD_BYTES = 1024 * 1024

    @classmethod
    def _key(cls, trace_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{cls.KEY_SUFFIX}:{trace_id}"

    @classmethod
    def _dedupe_key(cls, item: Dict[str, Any]) -> str:
        chunk_id = str(item.get("chunk_id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        head = str(item.get("content") or "")[:200]
        return f"body:{item.get('doc_name') or ''}|{head}"

    def sanitize(self, citations: Any) -> List[Dict[str, Any]]:
        """去重、限条数、截断正文，保证写入 value 有界。纯函数，便于单测。"""
        if not isinstance(citations, list):
            return []

        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in citations:
            if not isinstance(raw, dict):
                continue
            key = self._dedupe_key(raw)
            if key in seen:
                continue
            seen.add(key)
            item = {field: raw[field] for field in _ALLOWED_FIELDS if field in raw}
            content = item.get("content")
            if isinstance(content, str) and len(content) > self.MAX_CONTENT_CHARS:
                item["content"] = content[: self.MAX_CONTENT_CHARS]
            deduped.append(item)
            if len(deduped) >= self.MAX_ITEMS:
                break
        return self._fit_payload(deduped)

    @staticmethod
    def _serialize(items: List[Dict[str, Any]]) -> str | None:
        try:
            return json.dumps(items, ensure_ascii=False)
        except (TypeError, ValueError):
            return None

    def _fit_payload(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按序列化后大小从尾部削减；不可序列化时整体放弃（旁路数据，宁缺勿错）。"""
        while items:
            payload = self._serialize(items)
            if payload is not None and len(payload.encode("utf-8")) <= self.MAX_PAYLOAD_BYTES:
                return items
            items = items[:-1]
        return []

    async def save(self, trace_id: str, citations: Any) -> bool:
        """保存单轮引用详情；任何失败只告警并返回 False。"""
        tid = str(trace_id or "").strip()
        if not tid:
            return False
        items = self.sanitize(citations)
        if not items:
            return False
        payload = self._serialize(items)
        if payload is None:
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            # TTL 跟随会话历史，避免引用详情比会话本身活得更久或更短
            from app.services.ai.memory_service import memory_service

            await redis.set(self._key(tid), payload, ex=memory_service.ttl)
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路数据失败不得影响主流程
            logger.warning("[KnowledgeCitationStore] save failed trace=%s: %s", tid, exc)
            return False

    async def load_many(self, trace_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        """按 trace_id 批量取回；单条损坏只跳过该条。"""
        unique = list(dict.fromkeys(str(t).strip() for t in trace_ids if str(t or "").strip()))
        if not unique:
            return {}
        try:
            redis = await get_redis()
            if not redis:
                return {}
            raw_values = await redis.mget([self._key(t) for t in unique])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[KnowledgeCitationStore] load failed: %s", exc)
            return {}

        result: Dict[str, List[Dict[str, Any]]] = {}
        for tid, raw in zip(unique, raw_values or []):
            if not raw:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("[KnowledgeCitationStore] decode failed trace=%s", tid)
                continue
            if isinstance(parsed, list):
                result[tid] = parsed
        return result

    async def delete_many(self, trace_ids: Iterable[str]) -> int:
        """删除引用详情（会话/历史被删除时调用）；失败只告警。"""
        keys = [self._key(t) for t in (str(x).strip() for x in trace_ids) if t]
        if not keys:
            return 0
        try:
            redis = await get_redis()
            if not redis:
                return 0
            return int(await redis.delete(*keys) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[KnowledgeCitationStore] delete failed: %s", exc)
            return 0


knowledge_citation_store = KnowledgeCitationStore()


def accumulate_citation_details(state: Dict[str, Any], chunk: Dict[str, Any]) -> bool:
    """把 ``type == "citation"`` 事件里的切片累积进 shared_state，按 chunk_id 去重。

    返回该事件是否被消费。抽成纯函数有两个目的：让 execution_step 只留一行接入，
    以及让去重与容错可以脱离 pipeline 单测。
    """
    if not isinstance(chunk, dict) or chunk.get("type") != "citation":
        return False

    data = chunk.get("data")
    if not isinstance(data, list):
        # 载荷不可用也算消费：事件不该再落到下游正文分支
        return True

    bucket = state.get("knowledge_citations")
    if not isinstance(bucket, list):
        bucket = []
        state["knowledge_citations"] = bucket

    known = {
        KnowledgeCitationStore._dedupe_key(item)
        for item in bucket
        if isinstance(item, dict)
    }
    for item in data:
        if not isinstance(item, dict):
            continue
        key = KnowledgeCitationStore._dedupe_key(item)
        if key in known:
            continue
        known.add(key)
        bucket.append(item)
    return True
