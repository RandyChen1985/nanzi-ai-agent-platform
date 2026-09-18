"""前端不得用 document.cookie 增删 admin_token（HttpOnly，JS 操作一律无效）。

## 依据

后端自项目初始化提交 `7c1a9cdc` 起，下发 `admin_token` 时即带 `httponly=True`
（见 `_issue_admin_token_cookie`）。HttpOnly Cookie 对 `document.cookie` 完全不可见：
读不到、也写不了。因此 `document.cookie = 'admin_token=; max-age=0'` 这种"删除"
是**空操作**——既不会报错，也不产生任何效果，只会让后来排查登出问题的人误以为
前端已经让会话失效。

## 会话失效的正确路径

1. **主动登出**：前端 `POST /api/portal/auth/logout`（`Dashboard.vue`），后端吊销
   Redis 会话并 `delete_cookie`——这是唯一能真正终结会话的前端可触发操作。
2. **自然过期**：会话令牌在 Redis 中按 86400 秒滑动过期。
3. **前端清 localStorage** 只影响本地缓存快照（`user_info` 等），与登出**无关**，
   清完浏览器在服务端仍是登录态。

故前端源码中不应再出现对 `admin_token` 的 `document.cookie` 操作。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend/src"


def test_no_document_cookie_manipulation_of_admin_token():
    """全前端不得用 document.cookie 增删 admin_token。

    需跳过注释行——说明「此处不可用 document.cookie 操作 admin_token」的注释本身
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
            if "document.cookie" in line and "admin_token" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")

    assert not offenders, (
        "admin_token 是 HttpOnly，document.cookie 既读不到也写不了，"
        f"这些赋值是空操作，应移除（会话失效请走后端 logout）：{offenders}"
    )
