"""Contract tests for personal skills experience (center + chat mount scope)."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERSONAL_CENTER = ROOT / "frontend" / "src" / "views" / "PersonalCenter.vue"
SKILLS_MGMT = ROOT / "frontend" / "src" / "views" / "SkillsManagement.vue"
EMBED = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"
DEBUG = ROOT / "frontend" / "src" / "views" / "AgentDebug.vue"
CASCADE = ROOT / "frontend" / "src" / "components" / "embed" / "SkillCascadeMenu.vue"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"
BANNER = ROOT / "frontend" / "src" / "components" / "chat" / "SkillCreatedBanner.vue"
SKILL_CREATED_UTIL = ROOT / "frontend" / "src" / "utils" / "skillCreated.ts"
EXEC_TOOLS = ROOT / "app" / "services" / "ai" / "tools" / "system_executive_tools.py"


def test_personal_center_exposes_skills_tab():
    text = PERSONAL_CENTER.read_text(encoding="utf-8")
    assert "'skills'" in text or '"skills"' in text
    assert "personal-only" in text or "personalOnly" in text
    assert "SkillsManagement" in text
    assert "skill_id" in text


def test_skills_management_supports_personal_only_mode():
    text = SKILLS_MGMT.read_text(encoding="utf-8")
    assert "personalOnly" in text
    assert "initialSkillId" in text
    assert "v-if=\"!personalOnly\"" in text


def test_chat_mount_includes_skill_scope():
    for path in (EMBED, DEBUG, CHAT_INPUT):
        text = path.read_text(encoding="utf-8")
        assert "scope," in text or "scope:" in text
        assert 'scope === "personal"' in text or "scope === 'personal'" in text
    for path in (EMBED, DEBUG):
        text = path.read_text(encoding="utf-8")
        assert "SkillCreatedBanner" in text


def test_skill_cascade_empty_state_points_to_personal_center():
    text = CASCADE.read_text(encoding="utf-8")
    assert "/dashboard/personal?tab=skills" in text
    assert "SkillCascadeMenu" in CHAT_INPUT.read_text(encoding="utf-8") or "skill-cascade" in CHAT_INPUT.read_text(encoding="utf-8").lower()
    assert "SkillCascadeMenu" in CHAT_INPUT.read_text(encoding="utf-8")


def test_create_skills_emits_machine_marker():
    text = EXEC_TOOLS.read_text(encoding="utf-8")
    assert "NANZI_SKILL_CREATED" in text
    assert BANNER.exists()
    assert "parseSkillCreatedMarker" in SKILL_CREATED_UTIL.read_text(encoding="utf-8")
