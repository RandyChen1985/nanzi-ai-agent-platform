"""会话级项目资源范围：仅保存用户主动挂载的资源。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.redis import get_redis


class ResourceScopeStorageError(RuntimeError):
    """会话资源范围存储不可用，调用方必须显式失败，禁止降级为空范围。"""


class ConversationResourceService:
    KEY_PREFIX = "conversation"
    SUFFIX = "resource_scope_v1"

    @classmethod
    def _key(cls, user_id: Any, conversation_id: str) -> str:
        if user_id is None or not str(user_id).strip():
            raise ValueError("会话资源范围缺少有效 user_id")
        return f"{cls.KEY_PREFIX}:{user_id}:{conversation_id}:{cls.SUFFIX}"

    @staticmethod
    def dataset_names(scope: Dict[str, Any] | None) -> set[str]:
        """提取会话挂载数据集的规范名称集合。"""
        return {
            str(item.get("dataset_name") or item.get("name") or "").strip()
            for item in (scope or {}).get("datasets", [])
            if isinstance(item, dict)
            and str(item.get("dataset_name") or item.get("name") or "").strip()
        }

    @staticmethod
    def has_dataset_scope(scope: Dict[str, Any] | None) -> bool:
        """是否显式配置了数据集范围（即使条目格式已损坏）。"""
        return bool((scope or {}).get("datasets"))

    @staticmethod
    def dataset_ids(scope: Dict[str, Any] | None) -> list[int]:
        """提取会话挂载数据集的整数 ID，供 Schema 工具做硬过滤。"""
        result: list[int] = []
        for item in (scope or {}).get("datasets", []):
            if not isinstance(item, dict):
                continue
            try:
                dataset_id = int(str(item.get("id") or "").strip())
            except (TypeError, ValueError):
                continue
            if dataset_id not in result:
                result.append(dataset_id)
        return result

    @classmethod
    async def get(cls, user_id: Any, conversation_id: str) -> Dict[str, Any]:
        try:
            redis = await get_redis()
        except Exception as exc:
            raise ResourceScopeStorageError("会话资源范围存储不可用") from exc
        if not redis:
            raise ResourceScopeStorageError("会话资源范围存储不可用")
        try:
            raw = await redis.get(cls._key(user_id, conversation_id))
        except Exception as exc:
            raise ResourceScopeStorageError("读取会话资源范围失败") from exc
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            value = json.loads(raw or "{}")
            if not isinstance(value, dict):
                raise ValueError("scope 根节点必须是对象")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ResourceScopeStorageError("会话资源范围数据损坏") from exc
        result: Dict[str, Any] = {"project_name": str(value.get("project_name") or "").strip()}
        result.update({
            key: [item for item in value.get(key, []) if isinstance(item, dict)]
            for key in ("datasets", "knowledge_bases", "skills")
        })
        return result

    @classmethod
    async def normalize_for_user(
        cls,
        db: Any,
        *,
        user_info: Dict[str, Any],
        scope: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按当前用户真实可见资源规范化 scope，拒绝客户端伪造名称和路径。"""
        from app.services.metadata_service import MetadataService
        from app.services.permission_service import PermissionService
        from app.services.ai.skill_resolver import list_skill_metas

        raw_user_id = user_info.get("user_id") or user_info.get("id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("缺少有效用户身份") from exc
        is_admin = user_info.get("role") == "admin" or user_info.get("is_admin") is True

        accessible_datasets = await MetadataService.list_accessible_dataset_options(
            db,
            user_id=user_id,
            is_admin=is_admin,
            status=1,
        )
        dataset_catalog = {str(item.id): item for item in accessible_datasets}
        datasets: list[Dict[str, Any]] = []
        for item in scope.get("datasets", []):
            if not isinstance(item, dict):
                continue
            dataset = dataset_catalog.get(str(item.get("id") or "").strip())
            if dataset is None:
                raise ValueError(f"数据集无权访问或不存在: {item.get('id')}")
            datasets.append(
                {
                    "id": str(dataset.id),
                    "name": dataset.display_name or dataset.name,
                    "dataset_name": dataset.name,
                }
            )

        requested_kb_ids = [
            str(item.get("id") or "").strip()
            for item in scope.get("knowledge_bases", [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        permission_service = PermissionService(db)
        allowed_kb_ids = set(
            await permission_service.filter_knowledge_dataset_ids(
                user_id,
                user_info.get("user_name") or user_info.get("username"),
                requested_kb_ids,
            )
        )
        if set(requested_kb_ids) - allowed_kb_ids:
            raise ValueError("知识库无权访问或不存在")
        knowledge_bases = [
            {
                "id": dataset_id,
                "name": next(
                    (
                        str(item.get("name") or dataset_id)
                        for item in scope.get("knowledge_bases", [])
                        if isinstance(item, dict) and str(item.get("id") or "").strip() == dataset_id
                    ),
                    dataset_id,
                ),
            }
            for dataset_id in requested_kb_ids
        ]

        skill_catalog = {
            (str(item.get("scope") or "global"), str(item.get("id") or "")): item
            for item in list_skill_metas(user_info=user_info)
            if item.get("id")
        }
        skills: list[Dict[str, Any]] = []
        for item in scope.get("skills", []):
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("id") or "").strip()
            requested_scope = str(item.get("scope") or "").strip().lower()
            candidates = [
                meta
                for (skill_scope, candidate_id), meta in skill_catalog.items()
                if candidate_id == skill_id and (not requested_scope or skill_scope == requested_scope)
            ]
            if len(candidates) != 1:
                raise ValueError(f"技能不存在、无权访问或 scope 不明确: {skill_id}")
            meta = candidates[0]
            skills.append(
                {
                    "id": skill_id,
                    "name": meta.get("name") or skill_id,
                    "description": meta.get("description") or "",
                    "scope": meta.get("scope") or "global",
                }
            )

        return {
            "project_name": str(scope.get("project_name") or "").strip()[:100],
            "datasets": datasets,
            "knowledge_bases": knowledge_bases,
            "skills": skills,
        }

    @classmethod
    async def replace(cls, user_id: Any, conversation_id: str, scope: Dict[str, Any]) -> Dict[str, Any]:
        """持久化已规范化的会话资源范围；失败时显式报错。"""
        normalized: Dict[str, Any] = {"project_name": str(scope.get("project_name") or "").strip()[:100]}
        normalized.update({
            key: [item for item in scope.get(key, []) if isinstance(item, dict) and item.get("id")]
            for key in ("datasets", "knowledge_bases", "skills")
        })
        try:
            redis = await get_redis()
            if not redis:
                raise ResourceScopeStorageError("会话资源范围存储不可用")
            # scope 与历史会话同生命周期，删除会话时由 clear_history 主动清理。
            await redis.set(cls._key(user_id, conversation_id), json.dumps(normalized, ensure_ascii=False))
        except ResourceScopeStorageError:
            raise
        except Exception as exc:
            raise ResourceScopeStorageError("保存会话资源范围失败") from exc
        return normalized

    @classmethod
    async def delete(cls, user_id: Any, conversation_id: str) -> None:
        try:
            redis = await get_redis()
            if redis:
                await redis.delete(cls._key(user_id, conversation_id))
        except Exception:
            pass
