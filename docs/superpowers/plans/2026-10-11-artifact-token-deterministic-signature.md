# 产物下载 token 改确定性签名（修「打开抽屉踢掉正文链接」） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已分发出去的产物下载链接不再被「我的产出」列表接口吊销 —— 把随机 token 换成由 `artifact_id + expires_at` 确定性派生的签名 token，列表接口直接重算同一个 token 而不再改写 `token_hash`。

**Architecture:** token 不再是「随机值 + 单槽哈希」，而是 `HMAC-SHA256(密钥, "{artifact_id}:{expires_at 秒}")` 的 base64url 结果。同一产物、同一过期时间必然得到同一个 token，所以列表接口无需写库即可返回可用地址，也不会吊销登记时已经写进消息正文的那个 token。校验优先验签，失败再回退历史 `token_hash` 比对（兼容已签发的随机 token，且这些链接只要哈希未被覆盖就继续有效）。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 异步 / Pydantic 2 / `hmac` + `hashlib` + `base64`（标准库）；测试 pytest + `pytest.mark.no_infrastructure` 的 fake session。

**根因证据（本次修复针对的事实）：**

| 事实 | 位置 |
| --- | --- |
| 登记时签发**一次**随机 token，DB 只存哈希 | `app/services/ai/tools/generated_file_service.py:247-260` |
| 列表接口**每列一条就重新签发并覆盖** `token_hash`/`expires_at` | `app/api/v1/endpoints/chat.py:327-333` |
| 下载端点严格比对哈希 → 哈希被覆盖后旧 token 必失败 | `app/services/ai/tools/generated_file_service.py:404` |
| `token_hash` 全仓库只有上述两处写入点 | `grep -rn "token_hash\s*=" app` |
| 真实库只读取证：文件在、路径归属正常、未过期，唯一失败项是 token | `ai_artifacts.id=f1fefcf6995e4b22a48e9e95ab1128a1`，`expires_at-created_at` 比 30 天多 21.7 分钟（被列表接口重写过的痕迹） |

**不在本次范围：** 已被轮换掉的**历史**随机 token 无法还原（明文从未落库），本次不提供「点击时实时换取新地址」的补丁；`publish()` 的 manifest 路径不经过 `ai_artifacts`，无轮换问题，不动。

---

## File Structure

| 文件 | 职责 | 动作 |
| --- | --- | --- |
| `app/core/config.py` | 新增可选 `ARTIFACT_TOKEN_SECRET`，未配置时回退 `ENCRYPTION_KEY` | Modify（约 `:90`） |
| `app/services/ai/tools/generated_file_service.py` | token 派生（`build_artifact_token`）与校验（`verify_artifact_token`）；登记时改用签名 token | Modify |
| `app/api/v1/endpoints/chat.py` | 列表接口改为重算签名 token，**不再**改写 `token_hash` | Modify `:324-352` |
| `tests/ai/tools/test_generated_file_service.py` | 派生确定性、验签/回退、登记-解析闭环 | Modify |
| `tests/api/v1/test_chat_artifacts.py` | 把「轮换」断言改为「token 稳定且哈希不被改写」 | Modify `:85-118` |

---

### Task 1: 确定性签名 token 的派生与校验

**Files:**
- Modify: `app/core/config.py:88-95`
- Modify: `app/services/ai/tools/generated_file_service.py:112-120`（`_token_hash` 附近新增三个函数）
- Test: `tests/ai/tools/test_generated_file_service.py`

- [x] **Step 1: 写失败测试**

追加到 `tests/ai/tools/test_generated_file_service.py` 末尾（该文件已 import `generated_file_service`、`datetime/timezone`、`pytest`；若缺 `re` 请在文件头补 `import re`）：

```python
def test_build_artifact_token_is_deterministic_and_binds_id_and_expiry():
    """同一 artifact_id + 同一 expires_at 必须恒得同一 token；任一变化都必须变。"""
    expires = datetime(2026, 11, 10, 5, 32, 43, tzinfo=timezone.utc)
    first = generated_file_service.build_artifact_token("a" * 32, expires)
    second = generated_file_service.build_artifact_token("a" * 32, expires)
    assert first == second, "同一产物同一过期时间必须派生同一 token（列表接口靠它免写库）"
    assert first != generated_file_service.build_artifact_token("b" * 32, expires)
    assert first != generated_file_service.build_artifact_token("a" * 32, expires.replace(hour=6))
    # 必须能安全放进 ?token=...（现有 URL 正则只允许 [A-Za-z0-9_-]）
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    # 秒级以内的微秒差异不应改变 token（DB 列不保存微秒）
    assert first == generated_file_service.build_artifact_token(
        "a" * 32, expires.replace(microsecond=999999)
    )


def test_build_artifact_token_requires_expiry():
    with pytest.raises(ValueError):
        generated_file_service.build_artifact_token("a" * 32, None)


def test_verify_artifact_token_prefers_signature_and_falls_back_to_legacy_hash():
    expires = datetime(2026, 11, 10, 5, 32, 43, tzinfo=timezone.utc)
    artifact_id = "a" * 32
    signed = generated_file_service.build_artifact_token(artifact_id, expires)

    # 新机制：验签通过（即便库里没有历史哈希）
    assert generated_file_service.verify_artifact_token(artifact_id, expires, signed, None) is True

    # 历史随机 token：签名必然不匹配，但哈希匹配时仍放行（兼容升级前已发出的链接）
    legacy = "legacy-random-token"
    assert generated_file_service.verify_artifact_token(
        artifact_id, expires, legacy, generated_file_service._token_hash(legacy)
    ) is True

    # 两种都不匹配 → 拒绝
    assert generated_file_service.verify_artifact_token(
        artifact_id, expires, "wrong-token", generated_file_service._token_hash(legacy)
    ) is False
    # 空 token 直接拒绝
    assert generated_file_service.verify_artifact_token(artifact_id, expires, "", None) is False
    # 用别的产物的签名 token 来访问本产物 → 拒绝（artifact_id 参与派生）
    other = generated_file_service.build_artifact_token("b" * 32, expires)
    assert generated_file_service.verify_artifact_token(artifact_id, expires, other, None) is False
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/ai/tools/test_generated_file_service.py -q -k "artifact_token"`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_artifact_token'`

- [x] **Step 3: 加配置项**

`app/core/config.py`，在 `ENCRYPTION_KEY: str` 之后插入：

```python
    # 产物下载 token 的签名密钥（可选）。
    # 未配置时回退 ENCRYPTION_KEY —— 单机/开发环境无需额外配置。
    # 说明：签名 token 由本密钥派生，同一产物在同一过期时间内 token 恒定，
    # 因此列表接口可以重算而不写库，也不会吊销已分发到消息正文里的链接。
    ARTIFACT_TOKEN_SECRET: Optional[str] = None
```

- [x] **Step 4: 实现派生与校验**

`app/services/ai/tools/generated_file_service.py`：文件头 `import hashlib` 之前补 `import base64`；在 `_token_hash`（`:118`）之后新增：

```python
def _artifact_token_secret() -> bytes:
    """签名密钥：优先专用配置，回退平台加密密钥。"""
    dedicated = str(getattr(settings, "ARTIFACT_TOKEN_SECRET", "") or "").strip()
    return (dedicated or settings.ENCRYPTION_KEY).encode("utf-8")


def _as_utc(value: datetime) -> datetime:
    """naive 时间按既有习惯视为 UTC（与 resolve_workspace_artifact 的过期判断一致）。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_artifact_token(artifact_id: str, expires_at: datetime | None) -> str:
    """确定性派生下载 token：同一 artifact_id + 同一 expires_at 恒得同一 token。

    为什么不用随机 token：`ai_artifacts` 只存 token 哈希、无法还原明文，列表接口
    为了给出可用的 download_url 只能「重新签发并覆盖哈希」—— 那会把登记时已经发到
    消息正文里的链接全部吊销（正文里的 token 与抽屉里的 token 是两批）。
    改为确定性签名后，列表接口直接重算同一个 token，无需写库，链接不再互相吊销。
    """
    if expires_at is None:
        raise ValueError("缺少过期时间，无法派生产物下载 token")
    message = f"{artifact_id}:{int(_as_utc(expires_at).timestamp())}".encode("utf-8")
    digest = hmac.new(_artifact_token_secret(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_artifact_token(
    artifact_id: str,
    expires_at: datetime | None,
    token: str,
    stored_hash: str | None,
) -> bool:
    """先验签名（新机制），失败再回退历史哈希比对（兼容升级前签发的随机 token）。"""
    if not token:
        return False
    if expires_at is not None:
        if hmac.compare_digest(build_artifact_token(artifact_id, expires_at), token):
            return True
    if stored_hash:
        return hmac.compare_digest(str(stored_hash), _token_hash(token))
    return False
```

- [x] **Step 5: 跑测试确认通过**

Run: `pytest tests/ai/tools/test_generated_file_service.py -q -k "artifact_token"`
Expected: 3 passed

---

### Task 2: 登记产物时改用签名 token

**Files:**
- Modify: `app/services/ai/tools/generated_file_service.py:246-264`
- Test: `tests/ai/tools/test_generated_file_service.py`

- [x] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_register_artifact_issues_deterministic_signed_token(tmp_path, monkeypatch):
    """登记返回的 token 必须是签名 token：可由 (id, expires_at) 重算出来。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    source = workspace / "报告.docx"
    source.write_bytes(b"docx-bytes")

    session = _FakeArtifactSession()
    monkeypatch.setattr(generated_file_service, "_workspace_root", lambda: _async_value(workspace))
    monkeypatch.setattr(generated_file_service, "AsyncSessionLocal", lambda: session)

    published = await generated_file_service.register_artifact(
        source_path=source,
        filename="报告.docx",
        owner_user_id=7,
        artifact_type="word",
    )

    assert published.token == generated_file_service.build_artifact_token(
        published.artifact_id, published.expires_at
    )
    # 哈希列仍要写（模型非空约束），但只是历史回退用的备份
    assert session.added[0].token_hash == generated_file_service._token_hash(published.token)
```

`_FakeArtifactSession` 与 `_async_value` 若文件中没有则补（放在该测试上方）：

```python
class _FakeArtifactSession:
    """满足 register_artifact 的 add/commit 的最小会话。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


def _async_value(value):
    async def _inner():
        return value

    return _inner
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/ai/tools/test_generated_file_service.py -q -k "signed_token"`
Expected: FAIL — 断言失败（当前是随机 token，与重算结果不等）

- [x] **Step 3: 改实现**

`app/services/ai/tools/generated_file_service.py:246-264`：

```python
    expires_at = datetime.now(timezone.utc) + ttl
    artifact_id = uuid.uuid4().hex
    # 签名 token 由 (artifact_id, expires_at) 派生：列表接口稍后能重算同一个值，
    # 因此不需要（也不允许）在列表时重新签发并覆盖哈希。
    token = build_artifact_token(artifact_id, expires_at)

    async with AsyncSessionLocal() as session:
        artifact = AiArtifact(
            id=artifact_id,
            owner_user_id=int(owner_user_id),
            ...
            token_hash=_token_hash(token),
            expires_at=expires_at,
        )
```

（`token_hash` 保留写入：列非空，且作为历史回退路径的备份。）

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/ai/tools/test_generated_file_service.py -q`
Expected: 全绿

- [x] **Step 5: 让解析路径走验签**

`resolve_workspace_artifact` 中把哈希比对那一行（`:404`）：

```python
        if not hmac.compare_digest(record.token_hash or "", _token_hash(token)):
            return None
```

替换为：

```python
        if not verify_artifact_token(artifact_id, record.expires_at, token, record.token_hash):
            return None
```

- [x] **Step 6: 跑该文件的既有「错误 token 必须被拒」用例**

Run: `pytest tests/ai/tools/test_generated_file_service.py -q -k "resolve_workspace_artifact"`
Expected: 3 passed（含 `..._rejects_wrong_token`）

---

### Task 3: 列表接口停止轮换（本次根因的修复点）

**Files:**
- Modify: `app/api/v1/endpoints/chat.py:324-352`
- Test: `tests/api/v1/test_chat_artifacts.py:85-118`

- [x] **Step 1: 改测试为「稳定且不改写」（先让它红）**

把 `tests/api/v1/test_chat_artifacts.py::test_list_artifacts_returns_items_and_rotates_token` 整段替换为：

```python
@pytest.mark.asyncio
async def test_list_artifacts_returns_stable_signed_token_without_rotating():
    """列表必须重算签名 token 且**不得**改写 token_hash。

    这一条是回归锁：旧实现每次列出都重新签发并覆盖哈希，导致登记时已经写进
    消息正文的链接被吊销（用户实测点正文链接 404「文件不存在或已过期」，而抽屉正常）。
    """
    a1 = _make_artifact(artifact_id="aa" * 16, filename="方案.docx")
    a2 = _make_artifact(artifact_id="bb" * 16, artifact_type="excel", filename="报表.xlsx")
    session = _FakeSession(rows=[a2, a1], count=2)

    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7", "role": "user"})
    app.dependency_overrides[get_db_session] = _db_override(session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/artifacts", headers={"X-API-Key": "test-key"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert {item["filename"] for item in data["items"]} == {"方案.docx", "报表.xlsx"}

    for item in data["items"]:
        assert item["download_url"].startswith(f"/api/v1/chat/generated-files/{item['id']}?token=")
        token = item["download_url"].split("token=")[1]
        row = session.rows_by_id[item["id"]]
        # 返回的就是由 (id, expires_at) 派生的签名 token
        assert token == generated_file_service.build_artifact_token(row.id, row.expires_at)
        # 关键回归断言：哈希没有被改写（否则正文里已分发的旧链接会被吊销）
        assert row.token_hash == "old-hash"
```

`_FakeSession` 需要补 `rows_by_id`（在 `__init__` 里加一行）：

```python
        self.rows_by_id = {row.id: row for row in self._rows}
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/v1/test_chat_artifacts.py -q -k "stable_signed_token"`
Expected: FAIL — 当前返回的是随机 token，且 `row.token_hash` 被改写

- [x] **Step 3: 改实现**

`app/api/v1/endpoints/chat.py:324-352`，把 for 循环那段替换为：

```python
    now = datetime.now(timezone.utc)
    public_base_url = await get_download_url_prefix()
    items: List[ArtifactListItem] = []
    # 确定性签名：同一产物重算出的 token 与登记时一致，因此这里**不再改写 token_hash**。
    # 旧实现「每列出一次就新签发并覆盖哈希」会把已经发到消息正文里的链接全部吊销
    # （正文用的是登记时那个 token），本接口必须保持只读语义。
    for row in rows:
        expires_at = row.expires_at
        if expires_at is None:
            # 历史记录缺过期时间：补一次（仅此一次写库），否则无法派生 token
            expires_at = (row.created_at or datetime.now()) + DEFAULT_TTL
            row.expires_at = expires_at
        items.append(
            ArtifactListItem(
                id=row.id,
                filename=row.filename,
                artifact_type=row.artifact_type,
                mime_type=row.mime_type,
                size=int(row.size or 0),
                conversation_id=row.conversation_id,
                trace_id=row.trace_id,
                created_at=row.created_at,
                expires_at=expires_at,
                download_url=build_download_url(
                    row.id,
                    build_artifact_token(row.id, expires_at),
                    public_base_url=public_base_url,
                ),
            )
        )
    await db.commit()
```

并把该函数的局部 import（约 `:290-296`）中的 `_token_hash` 换成 `build_artifact_token`：

```python
    from app.services.ai.tools.generated_file_service import (
        DEFAULT_TTL,
        build_artifact_token,
        build_download_url,
        get_download_url_prefix,
    )
```

同时更新该接口的 `description=`（约 `:277-279`）：把「为每条记录新签发一个下载 token 并回写哈希与过期时间」改为「按 (id, 过期时间) 重算确定性签名 token，不改写已登记的哈希，避免吊销已分发链接」。

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/api/v1/test_chat_artifacts.py -q`
Expected: 全绿（含 `uses_configured_public_url`、过滤、分页、鉴权各用例）

---

### Task 4: 回归、变异验证与文档登记

- [x] **Step 1: 相关回归**

Run: `pytest tests/ai/tools/test_generated_file_service.py tests/ai/tools/test_generated_file_tool.py tests/ai/tools/test_write_file_artifact.py tests/api/v1/test_chat_artifacts.py -q`
Expected: 全绿

- [x] **Step 2: 后端更广回归（改动集中在产物链路）**

Run: `pytest tests/ai/tools tests/api/v1/test_chat_artifacts.py -q`
Expected: 全绿（若个别用例依赖真实基础设施，按仓库既有约定单独对待并记录）

- [x] **Step 3: 变异验证（每条都要先红后还原）**

| 变异 | 期望变红 |
| --- | --- |
| M1 `build_artifact_token` 的 message 去掉 `artifact_id`（只用 expires_at） | Task 1「不同产物 token 必须不同」+ 跨产物验签拒绝 |
| M2 `build_artifact_token` 的 message 去掉 expires_at | Task 1「过期时间变化 token 必须变」 |
| M3 `verify_artifact_token` 去掉签名分支（只留哈希回退） | Task 1 验签通过 + Task 2 登记闭环 |
| M4 `verify_artifact_token` 去掉哈希回退分支 | Task 1 历史 token 兼容 |
| M5 列表接口改回 `row.token_hash = _token_hash(新随机 token)` | Task 3「哈希未被改写」 |
| M6 `register_artifact` 改回随机 token | Task 2 闭环保留 |
| M7 `resolve_workspace_artifact` 改回只比哈希 | Task 2 第 6 步闭环（签名 token 解析失败） |

- [x] **Step 4: 更新 `tests/CHECKLIST.md`**

追加一行（沿用现有五列格式）：需求原话（用户实测「点击显示过期了，但是产物里面点击是正常的」+ 截图证据）、根因证据链（含行号与只读 DB 取证）、实现要点、测试与变异结果、以及「未执行 git commit、未代跑 ./dev.sh」。日期 `2026-10-11`。

- [x] **Step 5: 更新本计划文档**

把完成的步骤勾上，并在末尾追加「实测结果」表（用例数、变异命中数、回归范围）。

---

## Self-Review

**1. Spec coverage**

| 需求 | 落地任务 |
| --- | --- |
| 已分发链接不再被列表接口吊销 | Task 3（不再改写哈希）+ Task 1（可重算） |
| 列表返回的 URL 仍然可用 | Task 3（重算签名 token，格式不变） |
| 登记时签发的 token 与列表重算的一致 | Task 2 + Task 1（确定性） |
| 升级前已发出的随机 token 不因本次改动失效 | Task 1 `verify_artifact_token` 哈希回退 |
| 不引入新表/新迁移 | 全部改动不涉及 schema（`token_hash` 复用、`expires_at` 复用） |
| 密钥不硬编码、可隔离 | Task 1 新增 `ARTIFACT_TOKEN_SECRET`，回退 `ENCRYPTION_KEY` |

**2. Placeholder scan**：无 TBD/TODO；每个改动步骤都给了可直接落地的完整代码。

**3. Type consistency**：`build_artifact_token(artifact_id: str, expires_at: datetime | None) -> str`、`verify_artifact_token(artifact_id, expires_at, token, stored_hash) -> bool` 在 Task 1 定义，Task 2/3 与测试中的调用签名一致；`AiArtifact.expires_at` 可空 → 所有调用点都做了 `None` 处理（Task 3 补值、Task 1 抛 `ValueError`）。

**已知取舍（需在汇报中向用户说明）：**
- 本次不修复「已经被轮换掉的历史随机 token」（明文从未落库，救不回）；未修复前就已失效的那条 `f1fefcf6...` 需要从抽屉重新获取或重新生成。
- 列表接口不再续期：产物回归登记时确定的 30 天 TTL（原先「每次列表续期」会让产物实际上永不过期，与 TTL 语义矛盾）。
- 签名密钥泄露即可离线伪造任意产物的 token（随机 token 时代不可伪造）；`ENCRYPTION_KEY` 本就是平台级密钥，风险可接受，且可配置 `ARTIFACT_TOKEN_SECRET` 隔离。轮换该密钥会让所有已分发链接失效 —— 与轮换 ENCRYPTION_KEY 的既有影响一致。

---

## 实测结果（2026-10-11 执行完毕）

| 项 | 结果 |
| --- | --- |
| RED（实现前） | 5 个签名/闭环用例因 `build_artifact_token` 不存在而失败；列表接口用例精确红在「返回随机 token」与「`token_hash` 被改写」 |
| GREEN（实现后） | `tests/ai/tools/test_generated_file_service.py` 17 passed、`tests/api/v1/test_chat_artifacts.py` 8 passed |
| 更广回归 | `tests/ai/tools` 全量 **202 passed / 4 xfailed** |
| 变异验证 | **8/8 命中且字节级还原**（M1 去掉 artifact_id、M2 去掉 expires_at、M3 去掉验签、M4 去掉哈希回退、M5 列表改回改写哈希、M6 登记改回随机 token、M7 解析改回只比哈希、M8 登记不再规整到整秒） |
| 计划外补强（一） | 补「历史记录哈希为旧值、列表给出签名 token」用例 —— 没有它 M7 无法被捕获（原实现下 register 同步写哈希，验签与哈希校验等价，缺口被掩盖） |
| 计划外补强（二） | **DB 秒精度**：`expires_at` 原带微秒，而 MySQL `DATETIME(0)` 不保存微秒且 .5 秒以上四舍五入 —— 登记时 `int(ts)` 截断到 43 秒、从库里读回 44 秒，同一产物会算出两个 token（正文链接即使哈希未被覆盖也失效）。修法是登记时 `replace(microsecond=0)`，并加用例锁住（fake session 不经过 DB，原本会掩盖这个问题）。这条是复核 diff 时发现的，不是测试先发现的 —— 记在这里作为「fake 掉持久化层就会漏掉精度类缺陷」的教训 |
| 顺手修复 | `test_list_artifacts_uses_configured_public_url` 改为 patch `ConfigService.get`（原先只 patch `APP_PUBLIC_URL`，在配置了系统前缀的环境下**一直红**；已用 `git stash` 在 HEAD 版本复现确认为既有失败）；`known_failures.txt` 删除已重写的旧用例名 |
| 静态检查 | venv 内无 ruff，未跑；改动文件均由 pytest 成功导入 |
| 遗留 | 未跑 `./dev.sh`、未执行 `git commit`（按仓库规约由用户执行） |
