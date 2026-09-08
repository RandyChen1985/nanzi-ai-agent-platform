"""Build a small, permission-filtered resource catalog for model context."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Optional

from app.core.orm import AsyncSessionLocal
from app.services.ai.knowledge_catalog import (
    AuthorizedKnowledgeCatalog,
    fetch_authorized_knowledge_catalog,
)
from app.services.metadata_service import MetadataService
from app.services.permission_service import PermissionService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessibleResourceSnapshot:
    """请求内的目录摘要，无 ORM/Session；不能作为工具执行授权凭据。"""

    user_id: Optional[int]
    user_name: Optional[str]
    is_admin: bool
    status: str
    dataset_count: int
    knowledge_base_count: int
    prompt: str

    @property
    def counts(self) -> dict[str, int | str]:
        return {"status": self.status, "datasets": self.dataset_count, "knowledge_bases": self.knowledge_base_count}

    def matches(self, *, user_id: Optional[int], user_name: Optional[str], is_admin: bool) -> bool:
        return (self.user_id, self.user_name, self.is_admin) == (user_id, user_name, is_admin)

DEFAULT_MAX_ITEMS = 20
DEFAULT_MAX_CHARS = 4000
_CATALOG_HEADER = "## 当前用户可访问的内部资源摘要"
_CATALOG_NOTICE = (
    "以下是经过权限过滤的目录级元数据，仅用于辅助判断问题来源；"
    "具体内容必须通过对应工具获取，资源名称不代表绕过服务端权限校验。"
)
_TRUNCATED_NOTICE = "- 更多资源未展示，请按需调用资源目录工具。"
_NON_KNOWLEDGE_BASE_RESOURCE_NAMES = frozenset({"chatbi-example-meta"})


def _clean_catalog_value(value: Any) -> str:
    """Keep database metadata on one line so it cannot create prompt sections."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _short_description(value: Any, *, max_chars: int = 160) -> str:
    """Limit each description so one verbose record cannot consume the catalog."""
    text = _clean_catalog_value(value)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}…"


def _resource_line(row: Any, *, resource_type: str) -> str:
    """Render only a resource name and short description for model routing."""
    name = _clean_catalog_value(getattr(row, "name", None))
    display_name = _clean_catalog_value(getattr(row, "display_name", None))
    description = _short_description(getattr(row, "description", None))
    if not name and not display_name:
        return ""

    if resource_type == "dataset":
        if display_name and name and display_name != name:
            label = f"{display_name}（{name}）"
        else:
            label = display_name or name
    else:
        label = name or display_name

    return f"- {label}：{description}" if description else f"- {label}"


def _append_section(
    sections: list[tuple[str, list[str]]],
    *,
    title: str,
    rows: Iterable[Any],
    resource_type: str,
    max_items: int,
) -> bool:
    """Add normalized rows and report whether the item cap discarded rows."""
    source_rows = list(rows)
    rendered = [
        line
        for line in (
            _resource_line(row, resource_type=resource_type)
            for row in source_rows[:max_items]
        )
        if line
    ]
    if rendered:
        sections.append((title, rendered))
    return len(source_rows) > max_items


def _knowledge_bases_for_prompt(rows: Iterable[Any]) -> list[Any]:
    """Exclude resources reserved for structured-data examples from the KB catalog."""
    return [
        row
        for row in rows
        if _clean_catalog_value(getattr(row, "name", None))
        not in _NON_KNOWLEDGE_BASE_RESOURCE_NAMES
    ]


async def fetch_accessible_resource_counts(
    db: Any,
    *,
    user_id: Optional[int],
    user_name: Optional[str] = None,
    is_admin: bool = False,
) -> dict[str, int | str]:
    """Return permission-filtered counts for the user-facing execution trace."""
    snapshot = await fetch_accessible_resource_snapshot(
        db, user_id=user_id, user_name=user_name, is_admin=is_admin,
    )
    return snapshot.counts


async def fetch_accessible_resource_snapshot(
    db: Any,
    *,
    user_id: Optional[int],
    user_name: Optional[str] = None,
    is_admin: bool = False,
) -> AccessibleResourceSnapshot:
    """同一组权限查询同时生成统计与模型目录，供当前请求复用。"""
    if user_id is None:
        return AccessibleResourceSnapshot(user_id, user_name, is_admin, "empty", 0, 0, "")

    datasets = await MetadataService.list_accessible_dataset_options(
        db,
        user_id=user_id,
        is_admin=is_admin,
        status=1,
    )
    knowledge_catalog = await fetch_authorized_knowledge_catalog(
        db,
        user_id=user_id,
        user_name=user_name,
        is_admin=is_admin,
        permission_service=PermissionService(db),
    )
    return AccessibleResourceSnapshot(
        user_id, user_name, is_admin, knowledge_catalog.status,
        len(datasets), len(_knowledge_bases_for_prompt(knowledge_catalog.items)),
        render_accessible_resource_catalog(datasets=datasets, knowledge_bases=knowledge_catalog.items),
    )


def render_accessible_resource_catalog(
    *,
    datasets: Iterable[Any],
    knowledge_bases: Iterable[Any],
    max_items: int = DEFAULT_MAX_ITEMS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Render permission-filtered names and descriptions within a prompt budget."""
    item_limit = max(1, int(max_items))
    char_limit = max(160, int(max_chars))
    sections: list[tuple[str, list[str]]] = []
    truncated = _append_section(
        sections,
        title="### 知识库",
        rows=_knowledge_bases_for_prompt(knowledge_bases),
        resource_type="knowledge_base",
        max_items=item_limit,
    )
    truncated = (
        _append_section(
            sections,
            title="### 数据集",
            rows=datasets,
            resource_type="dataset",
            max_items=item_limit,
        )
        or truncated
    )
    if not sections:
        return ""

    base_parts = [_CATALOG_HEADER, _CATALOG_NOTICE]
    for title, lines in sections:
        base_parts.extend([title, *lines])
    full_text = "\n".join(base_parts)
    if len(full_text) <= char_limit and not truncated:
        return full_text

    footer = f"\n\n{_TRUNCATED_NOTICE}"
    content_limit = max(1, char_limit - len(footer))
    output_parts = [_CATALOG_HEADER, _CATALOG_NOTICE]
    for title, lines in sections:
        section_header = [title]
        candidate = "\n".join(output_parts + section_header)
        if len(candidate) > content_limit:
            break
        output_parts.append(title)
        for line in lines:
            candidate = "\n".join(output_parts + [line])
            if len(candidate) > content_limit:
                break
            output_parts.append(line)

    rendered = "\n".join(output_parts) + footer
    return rendered[:char_limit]


async def build_accessible_resource_catalog(
    *,
    user_id: Optional[int],
    user_name: Optional[str] = None,
    is_admin: bool = False,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_chars: int = DEFAULT_MAX_CHARS,
    knowledge_catalog: Optional[AuthorizedKnowledgeCatalog] = None,
) -> str:
    """Load and render the current user's authorized resource directory.

    This is advisory model context. Tool-level permission checks remain the
    authority, and a catalog lookup failure therefore produces an empty hint.
    """
    if user_id is None:
        return ""

    try:
        async with AsyncSessionLocal() as db:
            datasets = await MetadataService.list_accessible_dataset_options(
                db,
                user_id=user_id,
                is_admin=is_admin,
                status=1,
            )
            if knowledge_catalog is None:
                knowledge_catalog = await fetch_authorized_knowledge_catalog(
                    db,
                    user_id=user_id,
                    user_name=user_name,
                    is_admin=is_admin,
                    permission_service=PermissionService(db),
                )

        return render_accessible_resource_catalog(
            datasets=datasets,
            knowledge_bases=knowledge_catalog.items,
            max_items=max_items,
            max_chars=max_chars,
        )
    except Exception as exc:  # noqa: BLE001 - catalog is advisory context
        logger.warning("Failed to build accessible resource catalog: %s", exc)
        return ""


__all__ = [
    "build_accessible_resource_catalog",
    "fetch_accessible_resource_counts",
    "render_accessible_resource_catalog",
]
