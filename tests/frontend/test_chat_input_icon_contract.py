from pathlib import Path


CHAT_INPUT = Path(__file__).parents[2] / "frontend/src/components/embed/ChatInput.vue"
EMBED_CHAT = Path(__file__).parents[2] / "frontend/src/views/EmbedChat.vue"
AGENT_MANAGEMENT = Path(__file__).parents[2] / "frontend/src/views/AgentManagement.vue"
STATS_CARD = Path(__file__).parents[2] / "frontend/src/components/dashboard/StatsCard.vue"
AGENT_DEBUG = Path(__file__).parents[2] / "frontend/src/views/AgentDebug.vue"
WELCOME_DASHBOARD = Path(__file__).parents[2] / "frontend/src/components/embed/WelcomeDashboard.vue"
EXECUTION_TIMELINE = Path(__file__).parents[2] / "frontend/src/components/chat/ChatExecutionTimeline.vue"
MESSAGE_ACTION_MENUS = Path(__file__).parents[2] / "frontend/src/components/chat/MessageActionMenus.vue"


def test_plus_menu_uses_consistent_outline_icons_instead_of_emoji() -> None:
    source = CHAT_INPUT.read_text(encoding="utf-8")
    plus_menu = source.split("<!-- Menu Dropdown -->", 1)[1].split("<!-- Desktop flyout:", 1)[0]

    for icon in (
        "ChartBarIcon",
        "BookOpenIcon",
        "ComputerDesktopIcon",
        "FolderIcon",
        "CpuChipIcon",
        "Cog6ToothIcon",
        "PuzzlePieceIcon",
        "ChatBubbleLeftRightIcon",
        "ClockIcon",
    ):
        assert f"<{icon}" in plus_menu

    for emoji in ("📊", "📚", "💻", "📁", "🧠", "⚙️", "🔌", "🤖", "💬", "🕒"):
        assert emoji not in plus_menu
    assert "<CommandLineIcon" in source
    assert "<CommandLineIcon class=\"h-3.5 w-3.5 shrink-0 text-gray-400" in source


def test_smart_delegation_uses_group_icon() -> None:
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert "BoltIcon" in source
    assert "<BoltIcon" in source


def test_system_shortcuts_render_icons_separately_from_labels() -> None:
    chat_input = CHAT_INPUT.read_text(encoding="utf-8")
    embed_chat = EMBED_CHAT.read_text(encoding="utf-8")
    agent_debug = AGENT_DEBUG.read_text(encoding="utf-8")
    system_commands = embed_chat.split("const SYSTEM_SLASH_COMMANDS = [", 1)[1].split("];", 1)[0]
    agent_debug_commands = agent_debug.split("const SYSTEM_SLASH_COMMANDS = [", 1)[1].split("];", 1)[0]

    assert "const getSystemCommandIcon" in chat_input
    assert "<component :is=\"getSystemCommandIcon(cmd)\"" in chat_input

    for emoji in ("📊", "📚", "💻", "📁", "📄", "🕒", "🧹", "⚙️", "💬"):
        assert emoji not in system_commands
        assert emoji not in agent_debug_commands


def test_collapsed_shortcuts_hint_uses_svg_lightning_icon() -> None:
    source = EMBED_CHAT.read_text(encoding="utf-8")

    assert "import { CommandLineIcon } from \"@heroicons/vue/24/outline\";" in source
    assert '<CommandLineIcon class="h-4 w-4 shrink-0 text-white"' in source
    assert "<BoltIcon" not in source
    assert 'aria-hidden="true">⚡️</span>' not in source


def test_agent_cards_use_svg_for_default_avatars_but_keep_custom_avatar_urls() -> None:
    source = AGENT_MANAGEMENT.read_text(encoding="utf-8")

    assert "const getAgentIcon" in source
    assert "<component v-else :is=\"getAgentIcon(agent)\"" in source
    assert ":src=\"agent.avatar_url\"" in source
    assert "getAgentEmoji(agent)" not in source


def test_stats_card_aligns_icon_and_content_to_the_top() -> None:
    source = STATS_CARD.read_text(encoding="utf-8")

    assert "class=\"flex items-start gap-3.5\"" in source
    assert "text-[15px] font-semibold text-gray-600" in source


def test_welcome_entry_cards_render_svg_icons() -> None:
    source = WELCOME_DASHBOARD.read_text(encoding="utf-8")

    assert "<component :is=\"welcomeCardIcon(card.icon)\"" in source
    assert "<component :is=\"welcomeCardIcon(cap.icon)\"" in source
    for emoji in ("📊", "📚", "💻", "📄", "⚠️", "💬"):
        assert emoji not in source


def test_thinking_card_renders_step_icons_as_svg_components() -> None:
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")
    template = source.split("<script setup", 1)[0]

    assert "function timelineIconFor" in source
    assert "<component v-else :is=\"timelineIconFor(item)\"" in template
    assert "<SparklesIcon" in template
    assert "<CpuChipIcon" in template
    for emoji in ("✨", "💭", "⚡"):
        assert emoji not in template


def test_thinking_card_uses_only_exported_heroicons() -> None:
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "MapIcon" in source
    assert "CompassIcon" not in source


def test_thinking_card_strips_legacy_leading_sparkle_from_timeline_titles() -> None:
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert 'formatTimelineTitle(item.title || item.tool_name || "执行步骤").replace(/^✨\\s*/, "")' in source


def test_multimodal_model_badges_use_svg_icons() -> None:
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert "PhotoIcon" in source
    assert "<PhotoIcon" in source
    assert "🖼️" not in source


def test_ai_message_actions_have_larger_desktop_hit_areas() -> None:
    source = MESSAGE_ACTION_MENUS.read_text(encoding="utf-8")
    embed_chat = EMBED_CHAT.read_text(encoding="utf-8")

    assert "class=\"flex shrink-0 items-center gap-1.5\"" in source
    assert "min-h-8" in source
    assert "text-[11px]" in source
    assert "class=\"w-3.5 h-3.5\"" in embed_chat
    assert "'p-2'" in embed_chat


def test_timeline_permission_filter_preserves_union_item_type() -> None:
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "items.flatMap((item): ProcessTimelineItem[] =>" in source


def test_report_dataset_resolution_guards_indexed_value() -> None:
    source = (Path(__file__).parents[2] / "frontend/src/components/data-portal/DataPortalReportCreateModal.vue").read_text(encoding="utf-8")

    assert "const matchedDataset = matchedDatasets[0]" in source
    assert "if (matchedDataset)" in source


def test_edit_report_form_keeps_dataset_name() -> None:
    source = EMBED_CHAT.read_text(encoding="utf-8")
    edit_block = source.split("const openEditReportModal", 1)[1].split("const closeSavedReportEditor", 1)[0]

    assert "dataset_name: report.dataset_name || ''" in edit_block
