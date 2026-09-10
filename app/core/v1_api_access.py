from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from starlette.routing import Match

V1_API_PREFIX = "/api/v1"

# 权限页可分配的外部 API 静态列表
ASSIGNABLE_V1_API_RESOURCES: list[dict[str, str]] = [
    {
        "id": "GET:/api/v1/users/profile",
        "name": "获取用户画像",
        "description": "获取当前或指定用户的详细信息（包括角色和权限），并允许代表他人签发嵌入式 Ticket。",
        "group": "V1 用户服务",
        "method": "GET",
        "path": "/api/v1/users/profile",
    },
    {
        "id": "POST:/api/v1/schema",
        "name": "检索元数据 Schema",
        "description": "统一 Schema 检索接口，根据配置路由到 Local Service 或 RAGFlow。",
        "group": "V1 Schema服务",
        "method": "POST",
        "path": "/api/v1/schema",
    },
    {
        "id": "POST:/api/v1/chatbi/sql/execute",
        "name": "执行 ChatBI 只读 SQL",
        "description": "校验 API Key 与权限后执行只读 SELECT 语句。",
        "group": "V1 ChatBI",
        "method": "POST",
        "path": "/api/v1/chatbi/sql/execute",
    },
]


def get_assignable_v1_api_resources() -> list[dict[str, str]]:
    """Return the static list of permission-assignable V1 external APIs."""
    return [dict(item) for item in ASSIGNABLE_V1_API_RESOURCES]


def build_api_resource_id(method: str, path: str) -> str:
    return f"{method.upper()}:{path}"


def expand_api_permission_candidates(resource_id: str) -> set[str]:
    """Return canonical ID plus legacy short-path aliases for permission lookup."""
    method, _, path = resource_id.partition(":")
    method = method.upper()
    if not method or not path:
        return {resource_id}

    candidates = {resource_id, build_api_resource_id(method, path)}

    if path.startswith(f"{V1_API_PREFIX}/"):
        suffix = path[len(V1_API_PREFIX):] or "/"
        candidates.add(build_api_resource_id(method, suffix))
        tail = suffix.rstrip("/").split("/")[-1]
        if tail:
            candidates.add(build_api_resource_id(method, f"/{tail}"))
    elif path.startswith("/") and not path.startswith(V1_API_PREFIX):
        candidates.add(build_api_resource_id(method, f"{V1_API_PREFIX}{path}"))

    return candidates


def resolve_v1_api_resource_id(request: Request) -> tuple[str, str]:
    """Resolve canonical permission ID and display path for the current V1 request."""
    method = request.method.upper()
    route = request.scope.get("route")

    if isinstance(route, APIRoute) and route.path.startswith(V1_API_PREFIX):
        return build_api_resource_id(method, route.path), route.path

    matched_path = _match_v1_route_path(request)
    if matched_path:
        return build_api_resource_id(method, matched_path), matched_path

    route_path = getattr(route, "path", "") if route else ""
    url_path = request.url.path

    if url_path.startswith(V1_API_PREFIX):
        return build_api_resource_id(method, url_path), url_path

    if route_path.startswith(V1_API_PREFIX):
        return build_api_resource_id(method, route_path), route_path

    if route_path.startswith("/"):
        guessed = f"{V1_API_PREFIX}{route_path}"
        return build_api_resource_id(method, guessed), guessed

    fallback = route_path or url_path
    return build_api_resource_id(method, fallback), fallback


def _match_v1_route_path(request: Request) -> str | None:
    scope = dict(request.scope)
    for route in request.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(V1_API_PREFIX):
            continue
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route.path
    return None


def is_v1_api_whitelisted(path: str) -> bool:
    if "/chat" in path and not path.startswith(f"{V1_API_PREFIX}/chatbi"):
        return True
    if "/tasks" in path:
        return True
    if "/embed" in path:
        return True
    if "/sandbox/docker/workspace" in path:
        return True
    if "/sandbox/k8s/workspace" in path:
        return True
    return False


def build_api_permission_alias_map(app: FastAPI) -> dict[str, str]:
    """Map any known API permission alias to its canonical resource ID."""
    del app
    alias_map: dict[str, str] = {}
    for resource in get_assignable_v1_api_resources():
        canonical_id = str(resource.get("id") or "")
        if not canonical_id:
            continue
        for alias in expand_api_permission_candidates(canonical_id):
            alias_map[alias] = canonical_id
    return alias_map


def normalize_api_permission_ids(app: FastAPI, api_ids: list[str]) -> list[str]:
    """Normalize stored API permission IDs to canonical GET:/api/v1/... format."""
    alias_map = build_api_permission_alias_map(app)
    normalized: list[str] = []
    seen: set[str] = set()
    for api_id in api_ids or []:
        canonical = alias_map.get(api_id, api_id)
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
    return normalized
