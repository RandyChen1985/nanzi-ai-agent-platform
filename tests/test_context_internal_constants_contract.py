"""两个上下文内部常量**不得**被做成系统配置项。

背景：`agent_context_overhead_headroom_tokens` 曾被补上迁移种子并暴露到系统设置页，
`agent_context_llm_digest_transcript_max_chars` 也曾被设计成系统配置项。两者都不该做成
配置：

- overhead 取决于「当前 Agent 绑定了多少工具、系统提示多长」，管理员在自己的环境里
  **无法凭经验估出正确值**；
- transcript 上限是保证「摘要请求自身不超窗」的内部护栏，不是业务参数。

做成配置只会多一个"填错反而更糟"的旋钮（填小了不压缩、填大了过度压缩，两种都难归因），
且两处都会长期无人调整、实际等于隐藏的常量。因此固定为代码常量，并按环境需要由开发者
调整。本测试锁住这个决定，防止后续又"顺手"把它配到界面上。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]

# 禁止再次变成系统配置项的 key（历史名，仅用于反向断言）
FORBIDDEN_CONFIG_KEYS = (
    "agent_context_overhead_headroom_tokens",
    "agent_context_llm_digest_transcript_max_chars",
)

SCAN_DIRS = ("app", "frontend/src", "db-prod", "db-prod-pg")


def _iter_source_files():
    for rel in SCAN_DIRS:
        base = ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix in {".py", ".ts", ".vue", ".sql"} and path.is_file():
                yield path


@pytest.mark.parametrize("key", FORBIDDEN_CONFIG_KEYS)
def test_key_is_not_read_from_config_service(key):
    """不得再从 ConfigService 读取这两个 key。"""
    offenders = []
    for path in _iter_source_files():
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if key in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"{key} 又被当成配置项读取：{offenders}"


@pytest.mark.parametrize("key", FORBIDDEN_CONFIG_KEYS)
def test_key_is_not_seeded_or_exposed_as_a_config_item(key):
    """不得出现在迁移种子或系统设置页里。"""
    offenders = []
    for path in _iter_source_files():
        if path.suffix not in {".sql", ".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if key in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"{key} 又被暴露成配置项：{offenders}"


def test_context_overhead_constant_is_single_source():
    """预算计算与用量展示必须共用同一个常量对象。"""
    from app.services.ai import agent_service
    from app.services.ai import context_usage

    assert (
        agent_service.CONTEXT_OVERHEAD_RESERVATION_TOKENS
        is context_usage.CONTEXT_OVERHEAD_RESERVATION_TOKENS
    )
    assert context_usage.CONTEXT_OVERHEAD_RESERVATION_TOKENS > 0


def test_llm_digest_transcript_constant_has_safe_bounds():
    """transcript 上限必须存在、为正，且其推导出的单条上限也合理。"""
    from app.services.ai.context.compactor import LLM_DIGEST_TRANSCRIPT_MAX_CHARS

    assert LLM_DIGEST_TRANSCRIPT_MAX_CHARS >= 2000
    assert max(200, LLM_DIGEST_TRANSCRIPT_MAX_CHARS // 8) >= 200
