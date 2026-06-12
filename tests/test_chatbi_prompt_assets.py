from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "architech/prompts/system_agents/chatbi/V8_chatbi_runner_aligned.md"
UPDATE_SCRIPT = ROOT / "scripts/update_chatbi_prompt.py"
INIT_SQL = ROOT / "db-prod/V5_consolidated_agent_system.sql"
MIGRATION_SQL = ROOT / "db-prod/V73-update_chatbi_prompt_v8.sql"


def test_update_chatbi_prompt_script_uses_current_v8_prompt():
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")

    assert "V8_chatbi_runner_aligned.md" in script
    assert '"chatbi.md"' not in script


def test_chatbi_prompt_migration_exists_for_existing_databases():
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    migration = MIGRATION_SQL.read_text(encoding="utf-8")

    assert "UPDATE `ai_agent_versions`" in migration
    assert "WHERE `agent_id` = 'sys-agent-chatbi'" in migration
    assert "AND `status` = 'PUBLISHED'" in migration
    assert "面向 DataAgentRunner 门控分工优化的 ChatBI V8 prompt" in migration
    assert prompt.splitlines()[0] in migration
    assert "DataQueryExecutor / DataAgentRunner 控制" in migration


def test_fresh_init_chatbi_prompt_is_runner_aligned_v8():
    init_sql = INIT_SQL.read_text(encoding="utf-8")

    assert "DataQueryExecutor / DataAgentRunner 控制" in init_sql
    assert "V8: Runner-aligned ChatBI prompt" in init_sql
    assert "本轮请求分类（先判类，再裁剪流程）" not in init_sql
