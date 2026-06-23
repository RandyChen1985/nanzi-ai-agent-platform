from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from starlette.routing import Match

V1_API_PREFIX = "/api/v1"


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
    return False


def build_api_permission_alias_map(app: FastAPI) -> dict[str, str]:
    """Map any known API permission alias to its canonical resource ID."""
    from app.services.api_discovery_service import ApiDiscoveryService

    alias_map: dict[str, str] = {}
    for resource in ApiDiscoveryService.get_v1_api_resources(app):
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
