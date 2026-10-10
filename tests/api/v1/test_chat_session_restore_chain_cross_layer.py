"""跨层回归：真实后端响应必须被**真实前端判定代码**认下来。

## 为什么要跨层

第三～七轮连续踩的是同一类错配：**前端读了后端不存在的字段**。

- 会话归属校验写成 `res.data?.status !== "success"`，而 `/chat/*` 用的是
  `StandardResponse`（`code/message/data`，没有 `status`）⇒ 判定永远为真 ⇒ 三处会话采用点
  全部退化成「判不出来」，历史打不开、刷新后像开了新会话；
- `/chat/active`（GET）的活跃会话判定同样问题，「跨设备/无痕模式同步活跃会话」自引入起从未生效。

两端各自的自测都是绿的：后端用例只看 `data` 载荷、不看信封外壳；前端契约只断言源码字符串，
甚至把错误的 `status !== "success"` 当成正确实现钉住。**只有把真实响应喂给真实前端代码**才能
覆盖这条接缝，所以这里跨层：httpx+ASGI 打真实后端，抽出 `EmbedChat.vue` 里真实的
`checkConversationAdoption`（连同真实的 `isApiSuccess`）用 node 执行，断言它判成 `adoptable`。

没有 node 的环境自动跳过（本仓库前端测试已依赖 node，此处只是不强制后端作业也装）。
"""

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.main import app
from app.models.audit import AgentExecutionHistory
from app.models.user import User
from app.services.auth_service import AuthService

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("node") is None, reason="跨层用例需要 node 执行前端判定代码"),
]

ROOT = Path(__file__).resolve().parents[3]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
API_ENVELOPE = ROOT / "frontend/src/utils/apiEnvelope.ts"


async def _create_user(db, prefix: str, role: str = "user") -> dict:
    uniq = uuid.uuid4().hex[:10]
    user_name = f"{prefix}_{uniq}"
    api_key = await AuthService.generate_api_key(user_name, role=role, db=db)
    result = await db.execute(select(User).where(User.user_name == user_name))
    user = result.scalar_one()
    return {"headers": {"X-API-Key": api_key}, "user_id": str(user.id), "user_name": user_name}


async def _seed_turn(db, user: dict, conversation_id: str, query: str) -> None:
    db.add(
        AgentExecutionHistory(
            agent_id="agent-restore-probe",
            trace_id=f"trace-{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            user_id=user["user_id"],
            username=user["user_name"],
            query=query,
            summary=f"{query}-summary",
            status="success",
        )
    )
    await db.commit()


def _real_frontend_adoption_function() -> str:
    """抽出 EmbedChat.vue 里真实的 checkConversationAdoption（含函数自身收尾）。"""
    source = EMBED_CHAT.read_text(encoding="utf-8")
    start = source.index("const checkConversationAdoption = async (")
    end = source.index("\n};", start) + len("\n};")
    function_source = source[start:end]
    assert "isApiSuccess(res.data)" in function_source, (
        "抽取到的不是修复后的实现：成功判定必须走信封 helper"
    )
    return function_source


def _run_real_frontend_decision(payload: dict) -> dict:
    """把真实响应信封喂给真实前端判定，返回 {decision, calls}。"""
    module_source = "\n".join(
        [
            API_ENVELOPE.read_text(encoding="utf-8"),
            _real_frontend_adoption_function(),
            "module.exports = { checkConversationAdoption };",
        ]
    )
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False, encoding="utf-8") as handle:
        handle.write(module_source)
        module_path = handle.name
    payload_js = json.dumps(payload, ensure_ascii=False)
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const code = ts.transpileModule(fs.readFileSync({json.dumps(module_path)}, 'utf8'), {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
let calls = 0;
const axios = {{ get: async () => {{ calls += 1; return {{ data: {payload_js} }}; }} }};
const consoleStub = {{ warn: () => {{}}, log: () => {{}}, error: () => {{}} }};
new Function('module', 'exports', 'require', 'axios', 'console', 'activeConversationRequestParams', 'embedAuthHeaders', code)(
  moduleRef, moduleRef.exports, require, axios, consoleStub,
  () => ({{ instance_id: 'probe-inst' }}), () => ({{}})
);
const decision = await moduleRef.exports.checkConversationAdoption('conv-probe');
process.stdout.write(JSON.stringify({{ decision, calls }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    try:
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        Path(module_path).unlink(missing_ok=True)
    return json.loads(completed.stdout)


async def test_session_restore_chain_accepts_real_backend_responses(db_session):
    """混杂会话（既有他人轮次、也有我的轮次）在两个采用来源上都要被判成可采用。"""
    mine = await _create_user(db_session, "restore_probe_mine")
    other = await _create_user(db_session, "restore_probe_other")
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    await _seed_turn(db_session, other, conversation_id, "别人的轮次")
    await _seed_turn(db_session, mine, conversation_id, "我的轮次")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ownership = (
                await client.get(
                    f"/api/v1/chat/conversation/{conversation_id}/ownership",
                    headers=mine["headers"],
                )
            ).json()
            registered = (
                await client.post(
                    "/api/v1/chat/active",
                    json={"conversation_id": conversation_id},
                    # 与前端 updateActiveConversationOnServer 一致：带上实例参数
                    params={"instance_id": "probe-inst"},
                    headers=mine["headers"],
                )
            ).json()
            active = (
                await client.get(
                    "/api/v1/chat/active",
                    params={"instance_id": "probe-inst"},
                    headers=mine["headers"],
                )
            ).json()
            history = (
                await client.get(
                    "/api/v1/chat/history",
                    params={"conversation_id": conversation_id},
                    headers=mine["headers"],
                )
            ).json()

        ownership_decision = _run_real_frontend_decision(ownership)
        active_decision = _run_real_frontend_decision(active)

        # 1) 真实信封必须被真实前端判定认下来（第三～七轮的错配就出在这条接缝上）
        assert ownership_decision == {"decision": "adoptable", "calls": 1}, ownership_decision
        # 2) 服务端活跃会话（跨设备/无痕同步）同样要能被采用，且实例桶两侧一致
        assert registered.get("code") == 200, registered
        assert active["data"]["conversation_id"] == conversation_id, active
        assert active_decision == {"decision": "adoptable", "calls": 1}, active_decision
        # 3) 读侧只回我自己的轮次：混杂会话也不泄露他人内容
        assert [item["query"] for item in history["data"]["items"]] == ["我的轮次"], history
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(AgentExecutionHistory).where(
                    AgentExecutionHistory.conversation_id == conversation_id
                )
            )
            for user in (mine, other):
                await session.execute(delete(User).where(User.user_name == user["user_name"]))
            await session.commit()
