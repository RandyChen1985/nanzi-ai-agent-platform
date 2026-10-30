"""工具包导出/导入的契约测试。

不连库：``FakeSession`` 只按「表名 + 绑定值」返回预置行，足以覆盖查重与覆盖分支。
真正落库（事务、唯一约束）由 ``sys_api_tools`` / ``sys_script_tools`` 的既有集成
测试负责，这里盯的是**不能出错的两条硬约束**：

1. 凭据永不进文件；
2. 覆盖导入不能把对方已有的真实凭据冲成占位符。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.tools.generic_api import (
    GenericApiToolFactory,
    UnresolvedCredentialError,
)
from app.services.ai.tools.tool_credentials import (
    credential_placeholder,
    is_credential_header,
    is_credential_placeholder,
    mask_headers,
    unresolved_credential_headers,
)
from app.services.tool_bundle_service import (
    BUNDLE_SCHEMA,
    BUNDLE_SCHEMA_VERSION,
    ToolBundleError,
    build_bundle,
    merge_headers_for_import,
    normalize_decisions,
    parse_bundle,
    plan_import,
    PrecheckItem,
)

pytestmark = pytest.mark.no_infrastructure


# --------------------------------------------------------------------------- #
# 假会话：按表名 + 绑定值返回预置行
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


def _table_of(statement) -> str:
    text = str(statement)
    for name in ("sys_api_tools", "sys_script_tools", "mcp_tool_cache"):
        if name in text:
            return name
    return ""


def _bound_values(statement) -> set[str]:
    try:
        params = statement.compile().params
    except Exception:  # noqa: BLE001 - 编译失败就当作无过滤条件
        return set()
    return {str(value) for value in params.values() if isinstance(value, str)}


class _FakeSession:
    def __init__(self, api_rows=(), script_rows=(), mcp_names=()):
        self.api_rows = list(api_rows)
        self.script_rows = list(script_rows)
        self.mcp_names = set(mcp_names)
        self.added = []

    async def execute(self, statement):
        table = _table_of(statement)
        values = _bound_values(statement)
        if table == "sys_api_tools":
            rows = [row for row in self.api_rows if not values or row.name in values]
            return _FakeResult(rows)
        if table == "sys_script_tools":
            rows = [row for row in self.script_rows if not values or row.name in values]
            return _FakeResult(rows)
        if table == "mcp_tool_cache":
            hit = [name for name in self.mcp_names if not values or name in values]
            return _FakeResult([SimpleNamespace(id="mcp") for _ in hit])
        return _FakeResult([])

    def add(self, entity):
        self.added.append(entity)

    async def flush(self):
        return None


def _api_row(**overrides):
    base = dict(
        id="api-1",
        name="ttt",
        description="testtest",
        method="POST",
        url_template="https://api.example.com/v1/orders/{order_id}/refund",
        headers=json.dumps({"Content-Type": "application/json", "Authorization": "Bearer sk-live-123"}),
        parameter_schema="{}",
        signing_enabled=False,
        signing_key_id="abcdef0123456789",
        signing_secret_encrypted="cipher-text",
        signing_algorithm="hmac-sha256",
        group_name="test",
        is_active=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _script_row(**overrides):
    base = dict(
        id="script-1",
        name="demo_script",
        display_name="演示脚本",
        description=None,
        source="custom",
        language="python",
        code="import json\nprint(json.dumps({}))\n",
        parameter_schema="{}",
        requirements="[]",
        timeout_seconds=30,
        group_name=None,
        is_active=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _bundle(*tools):
    return {
        "schema": BUNDLE_SCHEMA,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "exported_at": "2026-10-30T21:50:00+08:00",
        "exported_by": "chenxiaolong",
        "tools": list(tools),
    }


def _api_tool(**overrides):
    tool = {
        "kind": "api",
        "name": "ttt",
        "description": "testtest",
        "method": "POST",
        "url_template": "https://api.example.com/v1/orders/{order_id}/refund",
        "headers": {"Authorization": credential_placeholder("Authorization")},
        "parameter_schema": {},
        "signing_enabled": False,
        "signing_algorithm": "hmac-sha256",
        "group_name": "test",
        "is_active": True,
        "requires_credentials": ["Authorization"],
    }
    tool.update(overrides)
    return tool


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
def test_credential_headers_are_recognised_by_name():
    for name in (
        "Authorization",
        "authorization",
        "X-Api-Key",
        "Cookie",
        "X-Csrf-Token",
        "X-Client-Secret",
        "Proxy-Authorization",
    ):
        assert is_credential_header(name), name
    # 业务 header 不能被误伤
    for name in ("Content-Type", "Accept", "X-Tenant-Id", "X-Author"):
        assert not is_credential_header(name), name


def test_mask_headers_never_leaks_original_value():
    token = "Bearer sk-live-123456"
    masked, requires = mask_headers(
        {"Authorization": token, "Content-Type": "application/json", "X-Api-Key": "abc"}
    )
    assert masked["Authorization"] == "${CREDENTIAL:authorization}"
    assert masked["X-Api-Key"] == "${CREDENTIAL:x-api-key}"
    assert masked["Content-Type"] == "application/json"
    assert sorted(requires) == ["Authorization", "X-Api-Key"]
    assert token not in json.dumps(masked)


def test_built_bundle_contains_no_credentials_and_no_signing_secret():
    bundle = build_bundle(api_rows=[_api_row()])
    dumped = json.dumps(bundle, ensure_ascii=False)
    assert "sk-live-123" not in dumped
    assert "cipher-text" not in dumped
    assert "abcdef0123456789" not in dumped
    item = bundle["tools"][0]
    assert item["headers"]["Authorization"] == "${CREDENTIAL:authorization}"
    assert item["requires_credentials"] == ["Authorization"]
    # 导出契约里根本没有这些字段
    for forbidden in ("id", "signing_key_id", "signing_secret_encrypted", "created_at", "updated_at"):
        assert forbidden not in item


def test_built_bundle_marks_builtin_script_as_custom():
    effective = SimpleNamespace(
        id="builtin-script-file-digest",
        name="file_digest",
        display_name="文件摘要",
        description="内置",
        source="builtin",
        language="python",
        code="print(1)",
        parameter_schema={},
        requirements=[],
        timeout_seconds=10,
        group_name=None,
        is_active=True,
    )
    bundle = build_bundle(script_effectives=[effective])
    item = bundle["tools"][0]
    assert item["kind"] == "script"
    assert item["source"] == "custom", "内置脚本导出后必须按自定义工具流通"


# --------------------------------------------------------------------------- #
# 文件级校验
# --------------------------------------------------------------------------- #
def test_parse_rejects_wrong_schema_and_newer_version():
    with pytest.raises(ToolBundleError, match="schema"):
        parse_bundle({"schema": "other.bundle", "schema_version": 1, "tools": []})

    newer = _bundle(_api_tool())
    newer["schema_version"] = BUNDLE_SCHEMA_VERSION + 1
    with pytest.raises(ToolBundleError, match="更新版本"):
        parse_bundle(newer)


def test_parse_rejects_empty_tools():
    with pytest.raises(ToolBundleError, match="没有工具"):
        parse_bundle({"schema": BUNDLE_SCHEMA, "schema_version": 1, "tools": []})


def test_parse_keeps_valid_items_when_one_is_broken():
    broken = _api_tool(name="broken_url", url_template="")
    parsed = parse_bundle(_bundle(_api_tool(), broken, {"kind": "mystery", "name": "x"}))
    assert parsed.items[0].is_valid
    assert not parsed.items[1].is_valid
    assert "url_template" in (parsed.items[1].error or "")
    assert not parsed.items[2].is_valid
    assert "未知的工具类型" in (parsed.items[2].error or "")


def test_prompt_spec_sample_is_importable():
    """按「让 AI 生成」提示词写出来的条目必须能过校验。

    提示词（``frontend/src/constants/toolBundlePrompt.ts``）是给外部 AI 的规范，
    它一旦和 ``SysScriptToolCreate`` 漂移，用户拿到的 JSON 就会导入失败。这里用一份
    「完全照着提示词写」的样例当活文档。
    """
    tool = {
        "kind": "script",
        "name": "csv_group_summary",
        "display_name": "CSV 分组统计",
        "description": "按列分组计数与求和，结果以 JSON 输出。",
        "language": "python",
        "code": (
            "import json\nimport sys\n\n"
            "def fail(message):\n"
            "    print(message, file=sys.stderr)\n"
            "    sys.exit(2)\n\n"
            "def main():\n"
            "    params = json.load(sys.stdin)\n"
            "    json.dump({'ok': True, 'artifacts': []}, sys.stdout, ensure_ascii=False)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        "parameter_schema": {
            "type": "object",
            "properties": {"csv": {"type": "string"}, "group_by": {"type": "string"}},
            "required": ["csv", "group_by"],
        },
        "requirements": [],
        "timeout_seconds": None,
        "group_name": "数据处理",
        "is_active": True,
    }
    parsed = parse_bundle(_bundle(tool))
    item = parsed.items[0]
    assert item.is_valid, item.error
    assert item.payload.language == "python"
    assert item.payload.timeout_seconds is None


def test_script_item_requires_code():
    parsed = parse_bundle(
        _bundle({"kind": "script", "name": "ok_script", "language": "python", "code": ""})
    )
    assert not parsed.items[0].is_valid
    assert "code" in (parsed.items[0].error or "")


# --------------------------------------------------------------------------- #
# 覆盖时的 header 合并
# --------------------------------------------------------------------------- #
def test_overwrite_keeps_existing_real_credential():
    merged, pending = merge_headers_for_import(
        {"Authorization": credential_placeholder("Authorization")},
        {"Authorization": "Bearer real-token"},
    )
    assert merged["Authorization"] == "Bearer real-token"
    assert pending == []


def test_overwrite_reports_pending_when_target_has_no_credential():
    merged, pending = merge_headers_for_import(
        {"Authorization": credential_placeholder("Authorization")},
        {"Content-Type": "application/json"},
    )
    assert is_credential_placeholder(merged["Authorization"])
    assert pending == ["Authorization"]


def test_unresolved_headers_helper():
    assert unresolved_credential_headers(
        {"Authorization": "${CREDENTIAL:authorization}", "Accept": "application/json"}
    ) == ["Authorization"]
    assert unresolved_credential_headers({"Authorization": "Bearer x"}) == []


# --------------------------------------------------------------------------- #
# 决策
# --------------------------------------------------------------------------- #
def test_decisions_reject_unknown_action():
    with pytest.raises(ToolBundleError, match="action 非法"):
        normalize_decisions([{"index": 0, "action": "destroy"}])


def _check(index=0, status="new", can_overwrite=False, name="ttt", suggested="ttt_2"):
    return PrecheckItem(
        index=index,
        kind="api",
        name=name,
        status=status,
        can_overwrite=can_overwrite,
        suggested_name=suggested,
    )


def test_plan_rejects_create_on_conflict():
    parsed = parse_bundle(_bundle(_api_tool()))
    decisions = normalize_decisions([{"index": 0, "action": "create"}])
    with pytest.raises(ToolBundleError, match="同名已存在"):
        plan_import(parsed, [_check(status="conflict", can_overwrite=True)], decisions)


def test_plan_rejects_overwrite_when_target_not_overwritable():
    parsed = parse_bundle(_bundle(_api_tool()))
    decisions = normalize_decisions([{"index": 0, "action": "overwrite"}])
    with pytest.raises(ToolBundleError, match="不能覆盖"):
        plan_import(parsed, [_check(status="conflict", can_overwrite=False)], decisions)


def test_plan_rejects_rename_to_same_name():
    parsed = parse_bundle(_bundle(_api_tool()))
    decisions = normalize_decisions([{"index": 0, "action": "rename", "rename_to": "ttt"}])
    with pytest.raises(ToolBundleError, match="另存名与原名称相同"):
        plan_import(parsed, [_check(status="conflict")], decisions)


def test_plan_defaults_to_skip_and_keeps_valid_create():
    parsed = parse_bundle(_bundle(_api_tool(), _api_tool(name="ttt2")))
    plan = plan_import(parsed, [_check(index=0), _check(index=1, name="ttt2")], [])
    assert plan == [], "没有决策时一律跳过，不能默默导入"


def test_plan_create_rename_and_overwrite_are_allowed():
    parsed = parse_bundle(_bundle(_api_tool()))
    plan = plan_import(parsed, [_check(status="new")], normalize_decisions([{"index": 0, "action": "create"}]))
    assert len(plan) == 1 and plan[0][2].action == "create"

    plan = plan_import(
        parsed,
        [_check(status="conflict", can_overwrite=True)],
        normalize_decisions([{"index": 0, "action": "overwrite"}]),
    )
    assert len(plan) == 1 and plan[0][2].action == "overwrite"

    plan = plan_import(
        parsed,
        [_check(status="conflict")],
        normalize_decisions([{"index": 0, "action": "rename", "rename_to": "ttt_9"}]),
    )
    assert len(plan) == 1 and plan[0][2].rename_to == "ttt_9"


# --------------------------------------------------------------------------- #
# 运行时兜底
# --------------------------------------------------------------------------- #
async def test_runtime_blocks_unresolved_credential_placeholder():
    config = SimpleNamespace(
        name="imported_tool",
        method="POST",
        url_template="https://api.example.com/v1/echo",
        headers=json.dumps({"Authorization": credential_placeholder("Authorization")}),
        parameter_schema="{}",
        signing_enabled=False,
        signing_key_id=None,
        signing_secret_encrypted=None,
        signing_algorithm="hmac-sha256",
    )
    with pytest.raises(UnresolvedCredentialError) as exc:
        await GenericApiToolFactory._perform_request(config, {}, user_info={})
    assert "Authorization" in str(exc.value)
    assert "imported_tool" in str(exc.value)


# --------------------------------------------------------------------------- #
# 提示词里的参考示例必须真的能导入
# --------------------------------------------------------------------------- #
def test_prompt_example_bundle_is_importable(tmp_path):
    """跨语言校验：把前端提示词里的示例 JSON 取出来，喂给真实的导入校验器。

    只断言「提示词里写了 artifacts」是不够的——示例字段一旦和 ``SysScriptToolCreate``
    漂移（比如 timeout 写成 0），用户照抄就会导入失败，而文本断言发现不了。
    """
    repo_root = Path(__file__).resolve().parents[3]
    frontend = repo_root / "frontend"
    esbuild = frontend / "node_modules/.bin/esbuild"
    node = shutil.which("node")
    if not esbuild.exists() or not node:
        pytest.skip("未安装前端依赖，跳过提示词示例的跨语言校验")

    bundle_js = tmp_path / "prompt.mjs"
    subprocess.run(
        [str(esbuild), str(frontend / "src/constants/toolBundlePrompt.ts"),
         "--format=esm", f"--outfile={bundle_js}", "--log-level=error"],
        check=True, capture_output=True,
    )
    dumped = subprocess.run(
        [node, "--input-type=module", "-e",
         f"import('{bundle_js}').then(m => process.stdout.write(m.buildExampleBundleJson()))"],
        check=True, capture_output=True, text=True,
    )
    bundle = json.loads(dumped.stdout)
    parsed = parse_bundle(bundle)
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.is_valid, f"提示词示例本身不合法，照抄的用户会导入失败：{item.error}"
    assert item.payload.timeout_seconds is None
    assert item.payload.requirements == []
