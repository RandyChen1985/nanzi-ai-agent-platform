"""前端调用的每个 portal 接口，后端必须真的存在。

文本契约能验证「按钮在、handler 在、接口路径写对了」，但验证不了**那个路径
在后端存不存在**——一条写错一个字符的 URL 会让功能运行时才炸，而所有源码断言
照样全绿。

这里做交叉验证：扫前端 axios 调用，展开后端 FastAPI 路由表，逐条比对。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend/src"

#: 反引号、单引号、双引号分开匹配。**反引号里允许出现引号**——模板字符串的
#: `${cond ? 'approve' : 'reject'}` 就是这种，统一用 [^`'"] 会把路径截断。
_CALL_PATTERNS = [
    re.compile(r"axios\.\w+\s*[<(]?\s*`([^`]+)`"),
    re.compile(r"axios\.\w+\s*[<(]?\s*'([^']+)'"),
    re.compile(r'axios\.\w+\s*[<(]?\s*"([^"]+)"'),
]


def _frontend_paths() -> set[str]:
    found: set[str] = set()
    for path in list(FRONTEND.rglob("*.ts")) + list(FRONTEND.rglob("*.vue")):
        text = path.read_text(encoding="utf-8")
        for pattern in _CALL_PATTERNS:
            for match in pattern.finditer(text):
                url = match.group(1)
                if url.startswith("/api/portal/"):
                    found.add(url)
    return found


def _flatten(router, prefix: str = "") -> list[str]:
    """展开 include_router。

    新版 FastAPI 的 include 是延迟对象（``_IncludedRouter``），路由不在
    ``.routes`` 里，要顺着 ``original_router`` 往下走。
    """
    out: list[str] = []
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            out.append(prefix + route.path)
            continue
        sub = getattr(route, "original_router", None)
        if sub is None:
            continue
        context = getattr(route, "include_context", None)
        out.extend(_flatten(sub, prefix + (getattr(context, "prefix", "") or "")))
    return out


def _normalize(path: str) -> str:
    """把 ``/a/${id}/b`` 和 ``/a/{secret_id}/b`` 归一成同一个形状。

    **先去模板变量，再去 query**。反过来会把三元表达式里的 ``?`` 当成查询串
    分隔符——``${approve ? "approve" : "reject"}`` 会被截成 ``${approve `。
    """
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = path.split("?")[0]
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def _matches(front: str, back: set[str]) -> bool:
    """段数一致、静态段相等，动态段（``{}``）两边都能顶任意值。"""
    fs = front.split("/")
    for candidate in back:
        cs = candidate.split("/")
        if len(fs) != len(cs):
            continue
        if all(x == y or "{}" in (x, y) for x, y in zip(fs, cs)):
            return True
    return False


@pytest.mark.no_infrastructure
def test_backend_routes_are_discoverable():
    """守卫：展不开就等于这个测试永远绿，必须让它响。"""
    from app.api.portal import api as portal_api

    paths = _flatten(portal_api.portal_router)
    assert len(paths) > 100, f"只展开出 {len(paths)} 条路由，_flatten 可能失效了"
    assert any("/secrets" in p for p in paths), "凭据相关路由没展出来"


@pytest.mark.no_infrastructure
def test_every_frontend_call_hits_a_real_route():
    from app.api.portal import api as portal_api

    backend = {_normalize("/api/portal" + p) for p in _flatten(portal_api.portal_router)}
    frontend = {_normalize(p) for p in _frontend_paths()}

    assert frontend, "一个前端调用都没扫到，提取正则可能失效了"
    missing = sorted(p for p in frontend if not _matches(p, backend))
    assert not missing, "这些路径后端不存在：\n  " + "\n  ".join(missing)


@pytest.mark.no_infrastructure
def test_credential_endpoints_are_covered():
    """凭据中心的前端调用尤其容易漏——它跨了三个页面和四个组件。"""
    backend = None
    from app.api.portal import api as portal_api

    backend = {_normalize("/api/portal" + p) for p in _flatten(portal_api.portal_router)}
    front = {_normalize(p) for p in _frontend_paths() if "secret" in p}

    assert front, "凭据相关调用一条都没扫到"
    missing = sorted(p for p in front if not _matches(p, backend))
    assert not missing, "凭据接口对不上：\n  " + "\n  ".join(missing)
