"""轨迹里的模型名必须来自「真实生效口径」，不能裸读版本配置。

背景：输入框选 `gpt-oss-120b` 时，工具卡片与审计轨迹一度仍写版本配置的
`DeepSeek-V4-Flash`。修掉主链路后做了全仓排查，发现同一根因还散落在三处：
会话历史的 `model_id`（用量报表 / 会话导出读它）、ChatBI 的经验库步骤与
`get_dataset_schema` 预取步骤。这个文件既锁住那三处的行为，也加一条源码护栏，
防止新写的 `AgentExecutionStep` 再裸读 `config.model_name`。
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.context import set_execution_options_context

pytestmark = pytest.mark.no_infrastructure

REPO_ROOT = Path(__file__).resolve().parents[3]
# 轨迹与审计数据（用户可见 / 可查）的产出目录。
SCAN_DIRS = (
    REPO_ROOT / "app" / "services" / "ai" / "runners",
    REPO_ROOT / "app" / "services" / "ai" / "executors",
    REPO_ROOT / "app" / "services" / "ai",
)


@pytest.fixture(autouse=True)
def _clean_execution_options_context():
    set_execution_options_context({})
    yield
    set_execution_options_context({})


def _runner(model_name: str = "deepseek-v4-flash") -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(agent_name="DataAgent", model_name=model_name),
    )


# —— 会话历史 model_id ——


def test_history_model_name_prefers_session_override():
    from app.services.ai.audit import resolve_history_model_name

    set_execution_options_context({"model": "gpt-oss-120b"})

    assert resolve_history_model_name(SimpleNamespace(model_name="deepseek-v4-flash")) == "gpt-oss-120b"


def test_history_model_name_falls_back_to_agent_version():
    from app.services.ai.audit import resolve_history_model_name

    assert resolve_history_model_name(SimpleNamespace(model_name="deepseek-v4-flash")) == "deepseek-v4-flash"


def test_history_model_name_is_none_when_nothing_configured():
    from app.services.ai.audit import resolve_history_model_name

    assert resolve_history_model_name(SimpleNamespace(model_name=None)) is None
    assert resolve_history_model_name(SimpleNamespace(model_name="  ")) is None


# —— ChatBI 经验库 / 预取步骤 ——


def test_few_shot_step_reports_session_override_model():
    from app.services.ai.runners.chatbi.few_shot import _effective_model_name

    set_execution_options_context({"model": "gpt-oss-120b"})

    assert _effective_model_name(_runner()) == "gpt-oss-120b"


def test_schema_prefetch_step_reports_session_override_model():
    from app.services.ai.runners.chatbi.schema_prefetch import _effective_model_name

    set_execution_options_context({"model": "gpt-oss-120b"})

    assert _effective_model_name(_runner()) == "gpt-oss-120b"


def test_chatbi_steps_keep_recorded_effective_model_over_session_alias():
    """真实下发模型一旦记录（注册表解析后的 id），优先于会话别名。"""
    from app.services.ai.runners.chatbi.few_shot import (
        _effective_model_name as few_shot_model,
    )
    from app.services.ai.runners.chatbi.schema_prefetch import (
        _effective_model_name as prefetch_model,
    )

    set_execution_options_context({"model": "gpt-oss-120b"})
    runner = _runner()
    runner._session_effective_model = "gpt-oss-120b@2026-11"

    assert few_shot_model(runner) == "gpt-oss-120b@2026-11"
    assert prefetch_model(runner) == "gpt-oss-120b@2026-11"


# —— 源码护栏 ——

_ALLOWED_HINTS = ("resolve_effective_model_name",)
# 只认 `model=` 这个关键字参数本身：`config_model=` / `tool_model=` 之类是
# `resolve_effective_model_name` 的入参，不是轨迹字段。
_MODEL_ASSIGN = re.compile(r"(?<![A-Za-z0-9_])model\s*=\s*(.+)")

# 「裸读版本配置」的两种写法。`getattr(agent_config, "model_name", None)` 与
# `config.model_name` 是同一件事——把 getattr 一律当合规会漏掉后一种写法
# （这正是压缩记录那处的形态），所以按读取对象判断，而不是按有没有 getattr。
_VERSION_MODEL_READ = re.compile(
    r"(?:(?:self|runner|agent_config)\s*\.\s*)?config\s*\.\s*model_name\b"
    r"|getattr\(\s*(?:self\s*\.\s*config|runner\s*\.\s*config|agent_config|config)"
    r"\s*,\s*[\"']model_name[\"']"
)
# 合规的兜底写法：`getattr(native_model, "model", config.model_name)`——真实句柄
# 优先、版本配置只作 default。判据是 getattr 的第一个参数**不是**配置对象。
_GETATTR_ON_CONFIG = re.compile(
    r"getattr\(\s*(?:self\s*\.\s*config|runner\s*\.\s*config|agent_config|config)\s*,"
)


def _reads_version_model(expression: str) -> bool:
    """表达式是否在庸读版本配置（真实句柄兜底不算）。"""
    if not _VERSION_MODEL_READ.search(expression):
        return False
    if "getattr(" in expression and not _GETATTR_ON_CONFIG.search(expression):
        return False
    return True


def _iter_python_files():
    seen = set()
    for directory in SCAN_DIRS:
        for path in directory.rglob("*.py"):
            if path in seen or "__pycache__" in path.parts:
                continue
            seen.add(path)
            yield path


def test_no_trace_step_reads_version_model_directly():
    """`AgentExecutionStep(model=...)` 不得裸读版本配置。

    合规写法只有两种：走 `resolve_effective_model_name(...)` 统一口径，或从一个
    真实下发句柄上取（表达式里出现 `getattr(`）。
    """
    offenders = []
    for path in _iter_python_files():
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"AgentExecutionStep\(", source):
            # 取这一次构造的窗口：足够覆盖跨行表达式，又不会串到下一个类/函数。
            window = source[match.start(): match.start() + 1600]
            end = window.find("timestamp=")
            window = window[: end if end != -1 else len(window)]
            if any(hint in window for hint in _ALLOWED_HINTS):
                continue
            for line_match in _MODEL_ASSIGN.finditer(window):
                expression = line_match.group(1)
                if not _reads_version_model(expression):
                    continue
                line_no = source[: match.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_no}: {expression.strip()[:90]}"
                )
    assert not offenders, "轨迹模型名裸读版本配置：\n" + "\n".join(offenders)


_SSE_MODEL_FIELD = re.compile(r'"model"\s*:\s*([^,\n}]+)')


def test_no_sse_log_payload_reads_version_model_directly():
    """SSE 日志载荷里的 `"model": ...` 同样不得裸读版本配置。

    这条补上 `AgentExecutionStep` 之外的另一条出口：工具卡片等 log chunk 直接
    字典下发，不走构造器，源码护栏要单独覆盖。
    """
    offenders = []
    for path in _iter_python_files():
        source = path.read_text(encoding="utf-8")
        for match in _SSE_MODEL_FIELD.finditer(source):
            expression = match.group(1)
            if any(hint in expression for hint in _ALLOWED_HINTS):
                continue
            if not _reads_version_model(expression):
                continue
            line_no = source[: match.start()].count("\n") + 1
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{line_no}: {expression.strip()[:90]}"
            )
    assert not offenders, "SSE 日志裸读版本配置：\n" + "\n".join(offenders)


def test_compaction_record_does_not_read_version_model():
    """压缩记录（`stage=resolved_model`）的 model_name 必须是最终目标模型。

    这一阶段的压缩就是「按最终模型窗口重算」的那一次，记版本配置等于把诊断
    数据指到另一个模型上；它走 `_persist_context_compaction_event(...)`，
    不在 `AgentExecutionStep` 里，所以单独锁一条。
    """
    path = REPO_ROOT / "app" / "services" / "ai" / "pipeline" / "steps" / "route_step.py"
    source = path.read_text(encoding="utf-8")
    matches = re.findall(
        r"_persist_context_compaction_event\((.*?)\n\s{16,}\)\n",
        source,
        flags=re.S,
    )
    assert matches, "未找到压缩记录调用点，测试需要随实现更新"
    for call in matches:
        # 版本配置的读法有两种写法：`agent_config.model_name` 与
        # `getattr(agent_config, "model_name", None)`，都要盯住。
        reads_version_model = (
            "config.model_name" in call
            or ('agent_config' in call and '"model_name"' in call)
        )
        if not reads_version_model:
            continue
        assert "effective_model_id" in call, (
            "压缩记录的 model_name 必须取 runtime_model_info.effective_model_id"
        )


def test_chatbi_runner_records_effective_model():
    """ChatBI 链路也必须记录真实句柄模型，否则只能回落到会话别名。"""
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = object.__new__(DataAgentRunner)
    handle = SimpleNamespace(model_name="gpt-oss-120b", temperature=0.2)

    runner._record_effective_model(handle)

    assert runner._session_effective_model == "gpt-oss-120b"
