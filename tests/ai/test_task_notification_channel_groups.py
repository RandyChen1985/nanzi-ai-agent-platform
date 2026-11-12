"""定时任务的「发到哪个群」：配置归一 + 投递降级。

产品口径（用户确认）：
- 任务可以给每个渠道单独指定群；不指定就发个人中心里的默认群；
- 指定的群被删除/停用后，定时任务**不能静默丢消息** —— 改发默认群，
  并在投递记录里写明「原本要发哪个群、为什么没发成」。

与对话场景刻意不同：那边 AI 指定的群失效就直接不发（用户当场能看到回执并纠正），
这边无人值守，丢了就没人知道。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services.notification_service import NotificationService
from app.services.task_notification_channels import (
    NOTIFICATION_CHANNEL_GROUPS_KEY,
    build_notification_delivery_supplement,
    channel_groups_from_task_config,
    merge_notification_channel_groups_into_config,
    normalize_channel_groups,
)
from app.services.task_notification_delivery import _deliver_channel

pytestmark = pytest.mark.no_infrastructure


class TestNormalizeChannelGroups:
    def test_keeps_valid_entries(self):
        assert normalize_channel_groups(
            {"dingtalk": 12, "feishu": "13", "wechat_work": 14}
        ) == {"dingtalk": 12, "feishu": 13, "wechat_work": 14}

    def test_drops_channels_without_group_semantics(self):
        """邮件发的是收件人列表、站内消息没有群，都不该出现「群 ID」。"""
        assert normalize_channel_groups({"email": 5, "portal": 6}) == {}

    def test_drops_dirty_values(self):
        assert normalize_channel_groups(
            {"dingtalk": 0, "feishu": -3, "wechat_work": None, "x": True}
        ) == {}

    def test_drops_unknown_channels(self):
        assert normalize_channel_groups({"slack": 9, "dingtalk": 12}) == {"dingtalk": 12}

    @pytest.mark.parametrize("raw", [None, [], "dingtalk=12", 12])
    def test_non_dict_returns_empty(self, raw: Any):
        assert normalize_channel_groups(raw) == {}


class TestTaskConfigRoundTrip:
    def test_reads_groups(self):
        config = {NOTIFICATION_CHANNEL_GROUPS_KEY: {"dingtalk": "7"}}
        assert channel_groups_from_task_config(config) == {"dingtalk": 7}

    def test_missing_key_is_empty(self):
        assert channel_groups_from_task_config({"notification_channels": ["portal"]}) == {}
        assert channel_groups_from_task_config(None) == {}

    def test_merge_round_trip(self):
        config = merge_notification_channel_groups_into_config({}, {"dingtalk": 12})
        assert config[NOTIFICATION_CHANNEL_GROUPS_KEY] == {"dingtalk": 12}
        cleared = merge_notification_channel_groups_into_config(config, {})
        assert NOTIFICATION_CHANNEL_GROUPS_KEY not in cleared

    def test_legacy_list_config_still_readable(self):
        """存量任务只有 List[str] 的 notification_channels，不能被新键影响。"""
        legacy = {"notification_channels": ["portal", "dingtalk"]}
        assert channel_groups_from_task_config(legacy) == {}


class TestPromptSupplement:
    def test_mentions_group_when_specified(self):
        text = build_notification_delivery_supplement(
            ["portal", "dingtalk"], {"dingtalk": 12}
        )
        assert "钉钉群 id=12" in text

    def test_no_group_line_when_unspecified(self):
        text = build_notification_delivery_supplement(["portal", "dingtalk"])
        assert "指定的发送目标" not in text

    def test_keeps_existing_prohibition(self):
        text = build_notification_delivery_supplement(["dingtalk"], {"dingtalk": 12})
        assert "不应调用" in text


def _patch_sender(monkeypatch, sink: List[Dict[str, Any]], *, ok: bool = True, err: str = ""):
    async def fake_send(db, user_id, title, body, *, channel_id=None, channel_name=None):
        sink.append({"user_id": user_id, "title": title, "channel_id": channel_id})
        return ok, err

    monkeypatch.setattr(NotificationService, "send_dingtalk", fake_send)


def _patch_resolve(monkeypatch, target, err: str = ""):
    async def fake_resolve(db, user_id, channel_type, group_id=None, group_name=None):
        return target, err

    monkeypatch.setattr(NotificationService, "resolve_channel", fake_resolve)


class TestDeliverChannelWithGroup:
    @pytest.mark.asyncio
    async def test_no_group_means_default(self, monkeypatch):
        calls: List[Dict[str, Any]] = []
        _patch_sender(monkeypatch, calls)

        ok, note = await _deliver_channel(
            None, user_id=1, channel="dingtalk", title="t", body="b",
            trace_id=None, task_name="任务",
        )
        assert ok is True and note == "dingtalk:ok"
        assert calls[0]["channel_id"] is None

    @pytest.mark.asyncio
    async def test_explicit_group_is_used_and_note_names_it(self, monkeypatch):
        calls: List[Dict[str, Any]] = []
        _patch_sender(monkeypatch, calls)
        target = type("R", (), {"id": 12, "name": "运维告警群"})()
        _patch_resolve(monkeypatch, target)

        ok, note = await _deliver_channel(
            None, user_id=1, channel="dingtalk", title="t", body="b",
            trace_id=None, task_name="任务", group_id=12,
        )
        assert ok is True
        assert calls[0]["channel_id"] == 12
        # 投递记录要能看出消息进了哪个群，否则出问题时无从核对
        assert "运维告警群" in note and "12" in note

    @pytest.mark.asyncio
    async def test_stale_group_falls_back_to_default_and_says_so(self, monkeypatch):
        """群被删/停用后仍要送达：改发默认群，并把原因留在 note 里。"""
        calls: List[Dict[str, Any]] = []
        _patch_sender(monkeypatch, calls)
        _patch_resolve(monkeypatch, None, "群 id=12 已停用，消息未发送。当前可用钉钉群：id=13")

        ok, note = await _deliver_channel(
            None, user_id=1, channel="dingtalk", title="t", body="b",
            trace_id=None, task_name="任务", group_id=12,
        )
        assert ok is True, "定时任务不能因为群失效就丢掉结果"
        # 第二次调用走默认群（channel_id=None）
        assert len(calls) == 1 and calls[0]["channel_id"] is None
        assert "已失效" in note and "改发默认群" in note

    @pytest.mark.asyncio
    async def test_stale_group_and_default_also_fails(self, monkeypatch):
        calls: List[Dict[str, Any]] = []

        async def fake_send(db, user_id, title, body, *, channel_id=None, channel_name=None):
            calls.append({"channel_id": channel_id})
            return False, "webhook 404"

        monkeypatch.setattr(NotificationService, "send_dingtalk", fake_send)
        _patch_resolve(monkeypatch, None, "群 id=12 不存在")

        ok, note = await _deliver_channel(
            None, user_id=1, channel="dingtalk", title="t", body="b",
            trace_id=None, task_name="任务", group_id=12,
        )
        assert ok is False
        assert "webhook 404" in note and "群 id=12 不存在" in note

    @pytest.mark.asyncio
    async def test_email_ignores_group_id(self, monkeypatch):
        """邮件没有群的概念：即便配置里混进了 email 的群 ID 也不该去解析。"""
        calls: List[Tuple[str, Any]] = []

        async def fake_email(db, user_id, title, body, **kwargs):
            calls.append(("email", kwargs))
            return True, ""

        monkeypatch.setattr(NotificationService, "send_email", fake_email)

        async def boom(*args, **kwargs):
            raise AssertionError("邮件渠道不应该调用 resolve_channel")

        monkeypatch.setattr(NotificationService, "resolve_channel", boom)

        ok, note = await _deliver_channel(
            None, user_id=1, channel="email", title="t", body="b",
            trace_id=None, task_name="任务", group_id=5,
        )
        assert ok is True and note == "email:ok"
        assert calls[0][1] == {}


class TestToolSuccessDetection:
    """调度侧靠工具返回文案判断「模型已经自己发过了」，判断错了就重复发送。

    多群改造把成功文案从 "Successfully sent ..." 改成
    「已发送到钉钉「运维告警群」(id=12)」，而判定函数只认前者 ——
    结果模型明明发出去了，调度侧仍判未送达再发一遍，群里收到两条。
    """

    @pytest.mark.parametrize(
        "channel_word",
        ["钉钉", "企业微信", "飞书"],
    )
    def test_new_success_wording_is_recognized(self, channel_word):
        from app.services.task_notification_delivery import notification_tool_succeeded

        assert notification_tool_succeeded(
            "send_dingtalk_message",
            f"已发送到{channel_word}「运维告警群」(id=12)",
        ) is True

    @pytest.mark.parametrize(
        "output",
        [
            "钉钉消息未发送：用户未启用钉钉通知",
            "Error executing DingTalk tool: boom",
            "failed to send",
        ],
    )
    def test_failures_are_not_counted_as_delivered(self, output):
        from app.services.task_notification_delivery import notification_tool_succeeded

        assert notification_tool_succeeded("send_dingtalk_message", output) is False

    def test_legacy_wording_still_recognized(self):
        from app.services.task_notification_delivery import notification_tool_succeeded

        assert notification_tool_succeeded(
            "send_dingtalk_message", "Successfully sent message to DingTalk"
        ) is True


class TestListChannelsToolRegistration:
    """不登记只读，默认权限是 ask：每次查群都要人工审批，定时任务里直接用不了。"""

    def test_permission_scope_is_read(self):
        from app.services.ai.runtime.agentscope.tools import infer_runtime_permission_scope

        assert infer_runtime_permission_scope("list_notification_channels", "native") == "read"

    def test_evidence_registered(self):
        from app.services.ai.tools.registry import TOOL_EVIDENCE_POLICY, TOOL_EVIDENCE_TYPES
        from app.services.ai.grounding.models import EvidenceType

        assert EvidenceType.RUNTIME_STATE in TOOL_EVIDENCE_TYPES["list_notification_channels"]
        assert TOOL_EVIDENCE_POLICY["list_notification_channels"] == "allow_empty_success"


class TestListChannelsDefaultReporting:
    """工具告诉模型的「默认群」必须和 send_* 的真实兜底一致。

    真实兜底是 get_config_by_type_raw（已启用优先 → 默认群优先 → id 升序）：
    默认群停用时，不指定群会自动落到最早启用的群。工具原先只认
    「is_default 且 is_enabled」，默认群一停用就回「没有已启用的群」——
    模型据此让用户去配群，可群本来就是配好的。
    """

    @pytest.mark.asyncio
    async def test_reports_fallback_group_instead_of_claiming_none(self, monkeypatch):
        from unittest.mock import patch

        from app.services.ai.tools import notification_tools as tools

        grouped = {
            "dingtalk": [
                {"id": 1, "name": "已停用的默认群", "is_enabled": False, "is_default": True},
                {"id": 2, "name": "运维告警群", "is_enabled": True, "is_default": False},
            ]
        }

        class _Ctx:
            user_id = 7

        async def fake_list(db, user_id, channel_type=None, **kwargs):  # noqa: ANN001
            return grouped

        class _Session:
            async def __aenter__(self):  # noqa: ANN201
                return self

            async def __aexit__(self, *args):  # noqa: ANN002
                return None

        # get_current_agent_context / AsyncSessionLocal 是函数内 import，
        # 只能 patch 源模块，patch 工具模块本身会 AttributeError
        with patch("app.core.context.get_current_agent_context", return_value=_Ctx()), \
             patch("app.core.orm.AsyncSessionLocal", lambda: _Session()), \
             patch.object(NotificationService, "list_channels", fake_list):
            raw = await tools.list_notification_channels()._arun(channel_type="dingtalk")

        payload = json.loads(raw)
        dingtalk = payload["channels"][0]
        assert dingtalk["default_id"] == 2, "默认群停用时应指向实际会用的启用群"
        assert dingtalk["default_is_fallback"] is True
        assert "没有已启用的群" not in payload["note"]
        assert "最早启用" in payload["note"]
