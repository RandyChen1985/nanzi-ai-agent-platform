"""响应信封判定：聊天面用的是 StandardResponse，**没有** `status` 字段。

## 背景（真实事故）

后端存在两套成功信封：

- `/api/v1/**`（含全部 `/chat/*`）走 `StandardResponse`：`{code, message, data, timestamp, trace_id, execution_mode}`；
- 门户部分历史接口（`/api/portal/auth/user_apikey`、票据兑换等）走遗留的 `{status: "success", data}`。

会话归属校验曾经按遗留信封判定成功（`res.data?.status !== "success"`），在聊天面上**永远为假**。
后果不是报错而是**静默降级**：三处会话采用点全部得到「判不出来」，于是侧栏点历史打不开、
刷新后像开了新会话（本地指针与服务端活跃指针都不被采用）。这类「判空字段」的错误不会被
类型检查发现（`any` 信封），必须用真实信封形状的行为测试钉住。
"""

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(module_path: str, expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


# 与 app/schemas/response.py 的 StandardResponse 逐字段对应：**没有 status**。
CHAT_ENVELOPE = {
    "code": 200,
    "message": "success",
    "data": {"owned": True, "foreign": False},
    "timestamp": "2026-10-10T23:50:00",
    "trace_id": "5f2f7b1e-0000-4000-8000-000000000000",
    "execution_mode": None,
}


def test_standard_response_envelope_is_success_even_without_a_status_field():
    assert "status" not in CHAT_ENVELOPE, "聊天面信封没有 status；有的话这条用例就失去意义了"

    result = _run_typescript(
        "frontend/src/utils/apiEnvelope.ts",
        f"return {{ ok: api.isApiSuccess({json.dumps(CHAT_ENVELOPE)}) }};",
    )

    assert result["ok"] is True


def test_legacy_portal_envelope_is_still_recognised():
    result = _run_typescript(
        "frontend/src/utils/apiEnvelope.ts",
        "return { ok: api.isApiSuccess({ status: 'success', data: { user_id: 1 } }) };",
    )

    assert result["ok"] is True


def test_failures_and_malformed_envelopes_are_not_success():
    result = _run_typescript(
        "frontend/src/utils/apiEnvelope.ts",
        """return {
  unauthorized: api.isApiSuccess({ code: 401, message: 'Missing API Key or Token', data: null }),
  failing_status: api.isApiSuccess({ status: 'error', data: null }),
  missing_code: api.isApiSuccess({ data: { owned: true, foreign: false } }),
  nullish: api.isApiSuccess(null),
  undefined_payload: api.isApiSuccess(undefined),
  text: api.isApiSuccess('success')
};""",
    )

    assert result == {
        "unauthorized": False,
        "failing_status": False,
        "missing_code": False,
        "nullish": False,
        "undefined_payload": False,
        "text": False,
    }


def test_session_adoption_paths_judge_success_by_the_envelope_helper():
    """会话采用相关的三处判定都必须走信封 helper，不得再读不存在的 `status` 字段。"""
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "if (!isApiSuccess(res.data)) continue;" in embed
    assert "isApiSuccess(activeRes.data)" in embed
    assert 'if (!isApiSuccess(res.data)) return "unknown";' in debug
    for source, name in ((embed, "EmbedChat"), (debug, "AgentDebug")):
        assert "res.data?.status" not in source, f"{name} 仍在按不存在的信封 status 判定成功"
        assert "activeRes.data?.status" not in source
