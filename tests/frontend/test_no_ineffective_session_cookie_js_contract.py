"""前端不得用 document.cookie 增删 HttpOnly 会话 Cookie（JS 操作一律无效）。

## 依据

后端自项目初始化提交 `7c1a9cdc` 起，下发门户会话 Cookie 时即带 `httponly=True`
（见 `_issue_portal_session_cookie`）。HttpOnly Cookie 对 `document.cookie` 完全不可见：
读不到、也写不了。因此 `document.cookie = '...=; max-age=0'` 这种"删除"是**空操作**
——既不会报错，也不产生任何效果，只会让后来排查登出问题的人误以为前端已经让会话失效。

涉及的门户/嵌入会话 Cookie 名（`app/api/portal/endpoints/auth.py`）：

- `portal_session`：门户登录态（2026-09 由 `admin_token` 改名而来，旧名一并纳入检查，
  用废弃名做 document.cookie 操作同样是空操作）
- `embed_session`：嵌入页会话

## 会话失效的正确路径

1. **主动登出**：前端 `POST /api/portal/auth/logout`（`Dashboard.vue`），后端吊销
   Redis 会话并 `delete_cookie`——这是唯一能真正终结会话的前端可触发操作。
2. **自然过期**：会话令牌在 Redis 中按 86400 秒滑动过期。
3. **前端清 localStorage** 只影响本地缓存快照（`user_info` 等），与登出**无关**，
   清完浏览器在服务端仍是登录态。

故前端源码中不应再出现对这些 Cookie 名的 `document.cookie` 操作。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend/src"

# 现名 + 已废弃旧名（用旧名操作同样无效，且改名后容易残留）
SESSION_COOKIE_NAMES = ("portal_session", "admin_token", "embed_session")


def test_no_document_cookie_manipulation_of_session_cookies():
    """全前端不得用 document.cookie 增删这些 HttpOnly 会话 Cookie。

    需跳过注释行——说明「此处不可用 document.cookie 操作该 Cookie」的注释本身
    会同时包含这两个关键词，若不排除会被自己的文档绊倒。
    """
    offenders = []
    comment_prefixes = ("//", "*", "/*", "<!--")
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".ts", ".vue"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(comment_prefixes):
                continue
            if "document.cookie" in line and any(
                name in line for name in SESSION_COOKIE_NAMES
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")

    assert not offenders, (
        f"这些会话 Cookie（{', '.join(SESSION_COOKIE_NAMES)}）是 HttpOnly，"
        "document.cookie 既读不到也写不了，这些赋值是空操作，应移除"
        f"（会话失效请走后端 logout）：{offenders}"
    )
