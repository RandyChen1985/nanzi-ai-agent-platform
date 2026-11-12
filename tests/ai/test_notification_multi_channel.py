"""多群通知渠道：解析规则与保存校验。

这里钉住的是「消息到底会发到哪个群」这条链路上的判断，它是整个改动里
唯一会在出错时**静默**伤人的地方——发错了群，用户当下未必发现。

规则（产品已确认）：
- 指定了群但找不到 / 已停用 / 不属于该渠道 → **忽略，不兜底默认群**；
- 什么都没指定 → 发默认群；
- 按名称指定时做宽松匹配，但绝不猜：有歧义就拒绝并列出候选。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.models.user_notification_config import UserNotificationConfig
from app.services.notification_service import (
    MAX_CHANNELS_PER_TYPE,
    MULTI_GROUP_CHANNELS,
    NotificationService,
)


def _record(
    record_id: int,
    name: str,
    *,
    channel_type: str = "dingtalk",
    enabled: bool = True,
    is_default: bool = False,
    webhook_url: str = "https://example.com/hook",
    secret: str = "",
) -> UserNotificationConfig:
    """构造一条内存中的渠道记录（不落库）。"""
    record = UserNotificationConfig(
        user_id=1, channel_type=channel_type, name=name, is_default=is_default
    )
    record.id = record_id
    record.config_json = json.dumps(
        {"is_enabled": enabled, "webhook_url": webhook_url, "secret": secret},
        ensure_ascii=False,
    )
    return record


def _patch_records(monkeypatch, records: List[UserNotificationConfig]) -> None:
    async def fake_list(db, user_id, channel_type):  # noqa: ANN001 - 保持与原签名一致
        # 必须像真实实现一样按渠道过滤，否则「拿飞书的 ID 当钉钉群用」这类
        # 用例会被 mock 的宽松行为掩盖过去。
        return [r for r in records if r.channel_type == channel_type]

    monkeypatch.setattr(NotificationService, "list_channel_records", fake_list)


class TestGroupNameNormalization:
    """用户口头说的群名，模型几乎不会原样复述，匹配前必须对齐。"""

    @pytest.mark.parametrize(
        "left,right",
        [
            ("运维告警群", "运维告警群群"),  # 模型把「群」复述成量词叠加
            ("运维告警群", "运维告警"),  # 用户只说核心词
            ("运维告警群", " 运维 告警群 "),  # 多余的空白
            ("ＯＰＳ群", "ops"),  # 全角 + 大小写
        ],
    )
    def test_equivalent_forms_collapse(self, left: str, right: str):
        assert NotificationService._normalize_group_name(
            left
        ) == NotificationService._normalize_group_name(right)

    def test_distinct_names_stay_distinct(self):
        assert NotificationService._normalize_group_name(
            "项目群A"
        ) != NotificationService._normalize_group_name("项目群B")

    def test_single_char_name_survives(self):
        """群名就叫「群」时不能剥成空串，否则所有单字群名都会互相撞上。"""
        assert NotificationService._normalize_group_name("群") == "群"


class TestCoerceGroupId:
    """模型传来的群 ID 不一定就是干净的 int。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [(13, 13), ("13", 13), ("13.0", 13), ("id=13", 13), (" 13 ", 13)],
    )
    def test_parses_common_forms(self, raw: Any, expected: int):
        assert NotificationService._coerce_group_id(raw) == expected

    @pytest.mark.parametrize("raw", [True, False, None, "", "abc", 0, -5])
    def test_rejects_invalid(self, raw: Any):
        assert NotificationService._coerce_group_id(raw) is None


class TestResolveChannel:
    @pytest.mark.asyncio
    async def test_explicit_id_hits_enabled_group(self, monkeypatch):
        records = [_record(12, "运维告警群", is_default=True), _record(13, "日报群")]
        _patch_records(monkeypatch, records)

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_id=13
        )
        assert error == ""
        assert record is not None and record.id == 13

    @pytest.mark.asyncio
    async def test_missing_id_is_ignored_not_fallen_back(self, monkeypatch):
        """不存在的群要直接放弃，绝不能悄悄改发默认群。"""
        _patch_records(monkeypatch, [_record(12, "运维告警群", is_default=True)])

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_id=99
        )
        assert record is None
        assert "不存在" in error
        # 回执必须带上可用群清单，模型才有机会自己改对
        assert "id=12" in error

    @pytest.mark.asyncio
    async def test_disabled_group_is_ignored(self, monkeypatch):
        _patch_records(
            monkeypatch,
            [_record(12, "运维告警群", is_default=True), _record(14, "旧群", enabled=False)],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_id=14
        )
        assert record is None
        assert "已停用" in error

    @pytest.mark.asyncio
    async def test_group_from_another_channel_is_not_found(self, monkeypatch):
        """飞书的群 ID 拿来当钉钉群用，必须当作「不存在」。"""
        _patch_records(monkeypatch, [_record(13, "飞书日报群", channel_type="feishu")])

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_id=13
        )
        assert record is None
        assert "不存在" in error

    @pytest.mark.asyncio
    async def test_name_exact_match(self, monkeypatch):
        _patch_records(
            monkeypatch,
            [_record(12, "运维告警群", is_default=True), _record(13, "日报群")],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_name="运维告警群群"
        )
        assert error == ""
        assert record is not None and record.id == 12

    @pytest.mark.asyncio
    async def test_name_partial_match_when_unique(self, monkeypatch):
        _patch_records(
            monkeypatch,
            [_record(12, "运维告警群", is_default=True), _record(13, "每日数据日报")],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_name="告警"
        )
        assert error == ""
        assert record is not None and record.id == 12

    @pytest.mark.asyncio
    async def test_ambiguous_name_is_rejected_with_candidates(self, monkeypatch):
        """有歧义必须拒绝：猜错就是发到别的群，代价比不发送大得多。"""
        _patch_records(
            monkeypatch,
            [_record(15, "项目群A"), _record(17, "项目群B")],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_name="项目群"
        )
        assert record is None
        assert "id=15" in error and "id=17" in error
        assert "群 ID" in error

    @pytest.mark.asyncio
    async def test_unknown_name_is_ignored(self, monkeypatch):
        _patch_records(monkeypatch, [_record(12, "运维告警群", is_default=True)])

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_name="财务群"
        )
        assert record is None
        assert "没有名称匹配" in error
        assert "id=12" in error

    @pytest.mark.asyncio
    async def test_disabled_group_is_not_matched_by_name(self, monkeypatch):
        """按名称找群时只看启用的群：名字对上了但群已停用，仍然不该发。"""
        _patch_records(
            monkeypatch,
            [_record(12, "日常群", is_default=True), _record(16, "财务群", enabled=False)],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_name="财务群"
        )
        assert record is None
        assert "没有名称匹配" in error

    @pytest.mark.asyncio
    async def test_id_wins_over_name(self, monkeypatch):
        _patch_records(
            monkeypatch,
            [_record(12, "运维告警群"), _record(13, "日报群")],
        )

        record, error = await NotificationService.resolve_channel(
            None, 1, "dingtalk", group_id=13, group_name="运维告警群"
        )
        assert error == ""
        assert record is not None and record.id == 13

    @pytest.mark.asyncio
    async def test_no_target_uses_default_group(self, monkeypatch):
        records = [
            _record(12, "日报群"),
            _record(13, "运维告警群", is_default=True),
        ]
        _patch_records(monkeypatch, records)

        async def fake_default(db, user_id, channel_type):  # noqa: ANN001
            return records[1]

        monkeypatch.setattr(NotificationService, "get_config_by_type_raw", fake_default)

        record, error = await NotificationService.resolve_channel(None, 1, "dingtalk")
        assert error == ""
        assert record is not None and record.id == 13


class TestListChannelsShape:
    """列表返回的字段名必须与请求体一致。

    这里踩过坑：响应里键名写成 ``enabled``，而前端/请求体用 ``is_enabled``，
    前端读 ``item.is_enabled`` 恒为 undefined → ``Boolean(undefined)`` 为 false
    → 整列显示「已停用」→ 用户点一次保存就把 is_enabled:false 写回库里，
    等于「点保存 = 停用全部群」，而且没有任何报错。
    """

    @pytest.mark.asyncio
    async def test_items_expose_is_enabled_not_enabled(self, monkeypatch):
        _patch_records(monkeypatch, [_record(12, "运维告警群", enabled=True)])

        out = await NotificationService.list_channels(
            None, 1, "dingtalk", include_credentials=True
        )
        item = out["dingtalk"][0]
        assert item["is_enabled"] is True
        assert "enabled" not in item, "键名必须与请求体一致，否则前端会把「读不到」当成「已停用」"

    @pytest.mark.asyncio
    async def test_read_then_write_back_does_not_flip_state(self, monkeypatch):
        """破坏性最强的那条路径：读出配置 → 原样提交回去 → 状态必须不变。"""
        records = [_record(12, "运维告警群", enabled=True, is_default=True)]
        _patch_records(monkeypatch, records)

        listed = await NotificationService.list_channels(
            None, 1, "dingtalk", include_credentials=True
        )
        item = listed["dingtalk"][0]
        # 前端表单字段与响应字段一一对应，原样提交
        payload = [
            {
                "id": item["id"],
                "name": item["name"],
                "is_enabled": item["is_enabled"],
                "is_default": item["is_default"],
                "webhook_url": item["webhook_url"],
                "secret": item["secret"],
            }
        ]

        class _FakeDB:
            async def commit(self):  # noqa: ANN001 - 只用来满足 save_channels 的调用
                return None

            def add(self, obj):  # noqa: ANN001
                return None

            async def delete(self, obj):  # noqa: ANN001
                return None

        await NotificationService.save_channels(_FakeDB(), 1, "dingtalk", payload)
        saved = json.loads(records[0].config_json)
        assert saved["is_enabled"] is True, "读了再存一轮不能把启用状态翻转成停用"


class TestSaveChannelsValidation:
    """入参校验必须在碰数据库之前完成，否则坏输入会先打一次库再报错。"""

    @pytest.mark.asyncio
    async def test_email_is_not_multi_group(self):
        with pytest.raises(ValueError, match="不支持配置多个群"):
            await NotificationService.save_channels(None, 1, "email", [{"name": "x"}])

    @pytest.mark.asyncio
    async def test_channels_must_be_list(self):
        with pytest.raises(ValueError, match="必须是列表"):
            await NotificationService.save_channels(None, 1, "dingtalk", "oops")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_limit_per_channel(self):
        too_many = [{"name": f"群{i}"} for i in range(MAX_CHANNELS_PER_TYPE + 1)]
        with pytest.raises(ValueError, match="最多配置"):
            await NotificationService.save_channels(None, 1, "dingtalk", too_many)

    @pytest.mark.asyncio
    async def test_name_required(self):
        with pytest.raises(ValueError, match="缺少名称"):
            await NotificationService.save_channels(None, 1, "dingtalk", [{"name": "   "}])

    @pytest.mark.asyncio
    async def test_name_length_limit(self):
        with pytest.raises(ValueError, match="超过 64 个字符"):
            await NotificationService.save_channels(
                None, 1, "dingtalk", [{"name": "群" * 65}]
            )

    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self):
        """重名会让「按名称发送」变得不可判，必须在保存时就挡住。"""
        with pytest.raises(ValueError, match="重复"):
            await NotificationService.save_channels(
                None, 1, "dingtalk", [{"name": "运维群"}, {"name": "运维群"}]
            )

    def test_multi_group_channels_are_the_three_bots(self):
        assert set(MULTI_GROUP_CHANNELS) == {"dingtalk", "feishu", "wechat_work"}


class TestDispatchNotification:
    @pytest.mark.asyncio
    async def test_missing_webhook_is_reported_with_group(self, monkeypatch):
        """群配了但 Webhook 空着时，回执要指明是哪个群，否则用户无从下手。"""
        records = [_record(12, "运维告警群", webhook_url="", is_default=True)]
        _patch_records(monkeypatch, records)

        ok, message, target = await NotificationService.dispatch_notification(
            None, 1, "dingtalk", title="t", content="c"
        )
        assert ok is False
        assert "运维告警群" in message and "Webhook" in message
        assert target is not None and target.id == 12

    @pytest.mark.asyncio
    async def test_unresolved_target_returns_reason_and_no_target(self, monkeypatch):
        _patch_records(monkeypatch, [_record(12, "运维告警群", is_default=True)])

        ok, message, target = await NotificationService.dispatch_notification(
            None, 1, "dingtalk", title="t", content="c", channel_id=99
        )
        assert ok is False and target is None
        assert "不存在" in message


class _MemoryDB:
    """只满足 save_channels / save_user_config 的最小 db 替身。"""

    async def commit(self):  # noqa: ANN201
        return None

    def add(self, obj):  # noqa: ANN001
        return None

    async def delete(self, obj):  # noqa: ANN001
        return None

    async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("不应走到真实查询")

    async def flush(self):  # noqa: ANN201
        return None


class TestDefaultGroupUniqueness:
    """「一个渠道只有一个默认群」，且默认群必须是**已启用**的那一个。

    review 指出的两个缺口：
    1. save_channels 按数组顺序取第一个 is_default，可能选中已停用的群 ——
       保存后「不指定群」仍然发不出去，界面上却看不出问题；
    2. 前端兜底顶默认时不清其它 is_default，会一次提交两个默认。
    """

    @pytest.mark.asyncio
    async def test_enabled_default_wins_over_disabled_one(self, monkeypatch):
        records = [
            _record(1, "已停用群", enabled=False, is_default=True),
            _record(2, "启用群", enabled=True, is_default=False),
        ]
        _patch_records(monkeypatch, records)
        payload = [
            {"id": 1, "name": "已停用群", "is_enabled": False, "is_default": True, "webhook_url": "https://a"},
            {"id": 2, "name": "启用群", "is_enabled": True, "is_default": False, "webhook_url": "https://b"},
        ]

        await NotificationService.save_channels(_MemoryDB(), 1, "dingtalk", payload)

        # is_default 是表列（不在 config_json 里）
        assert records[0].is_default is False, "停用的群不能占默认位"
        assert records[1].is_default is True

    @pytest.mark.asyncio
    async def test_only_one_default_survives(self, monkeypatch):
        """前端可能一次提交多个 is_default，落库后必须只剩一个。"""
        records = [
            _record(1, "群A", enabled=True, is_default=True),
            _record(2, "群B", enabled=True, is_default=True),
        ]
        _patch_records(monkeypatch, records)
        payload = [
            {"id": 1, "name": "群A", "is_enabled": True, "is_default": True, "webhook_url": "https://a"},
            {"id": 2, "name": "群B", "is_enabled": True, "is_default": True, "webhook_url": "https://b"},
        ]

        await NotificationService.save_channels(_MemoryDB(), 1, "dingtalk", payload)

        assert sum(1 for r in records if r.is_default) == 1, "同渠道只能有一个默认群"
