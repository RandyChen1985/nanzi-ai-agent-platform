"""producer 硬超时看门狗：配置解析与取值校验。

看门狗与工具超时是**两个维度**：它管的是「整轮」（从用户发消息到收尾），不针对单个
工具或单次模型调用。所以 `agent_max_toolcall_timeout` 配得比它大并不会生效——工具还
没到自己的超时，整轮就先被取消并释放会话锁。这个约束由迁移 SQL 的 description 说明，
这里锁定解析与校验的行为。
"""
import math

import pytest

from app.services.ai.runtime.producer_watchdog import (
    DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS,
    MAX_PRODUCER_HARD_TIMEOUT_SECONDS,
    MIN_PRODUCER_HARD_TIMEOUT_SECONDS,
    PRODUCER_HARD_TIMEOUT_KEY,
    parse_producer_hard_timeout,
    validate_producer_hard_timeout,
)

pytestmark = pytest.mark.no_infrastructure


def test_key_and_default_match_the_read_site():
    assert PRODUCER_HARD_TIMEOUT_KEY == "agent_chat_producer_hard_timeout_seconds"
    assert DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS == 600.0
    assert MIN_PRODUCER_HARD_TIMEOUT_SECONDS == 60.0
    assert MAX_PRODUCER_HARD_TIMEOUT_SECONDS == 3600.0


@pytest.mark.parametrize("raw", ["900", " 900 ", 900, 900.0, "60", "3600"])
def test_valid_values_are_parsed(raw):
    assert parse_producer_hard_timeout(raw) == pytest.approx(
        float(str(raw).strip())
    )


@pytest.mark.parametrize("raw", ["0", 0, 0.0, "-1", -600.0])
def test_zero_and_negative_disable_the_watchdog(raw):
    """0/负值归一为 0（关闭）：非 0 的负数没有别的含义，不该被当成小超时。"""
    assert parse_producer_hard_timeout(raw) == 0.0


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", True, False, float("nan"), float("inf"), float("-inf"),
     "59.9", "3600.1", 1e9],
)
def test_garbage_and_out_of_range_fall_back_to_default(raw):
    assert parse_producer_hard_timeout(raw) == DEFAULT_PRODUCER_HARD_TIMEOUT_SECONDS


def test_parse_accepts_a_custom_default():
    assert parse_producer_hard_timeout("bad", default=123.0) == 123.0


@pytest.mark.parametrize("raw", ["0", 0, "60", "600", "3600", 900.0])
def test_validate_accepts_zero_and_the_range(raw):
    validate_producer_hard_timeout(raw)  # 不抛即通过


@pytest.mark.parametrize("raw", ["59.9", "3600.1", "-1", "abc", "", None, True, float("nan")])
def test_validate_rejects_out_of_range_and_garbage(raw):
    with pytest.raises(ValueError):
        validate_producer_hard_timeout(raw)


def test_validate_message_states_the_allowed_shapes():
    """报错必须同时给出「0 = 关闭」与区间，否则运维不知道该填什么。"""
    with pytest.raises(ValueError) as excinfo:
        validate_producer_hard_timeout("10")
    message = str(excinfo.value)
    assert PRODUCER_HARD_TIMEOUT_KEY in message
    assert "0" in message
    assert "60" in message and "3600" in message


def test_parse_is_consistent_with_validate_on_the_range():
    """解析与校验不能各说各话：校验放行的值，解析不得悄悄回退默认。"""
    for raw in ("60", "600", "3600"):
        validate_producer_hard_timeout(raw)
        assert parse_producer_hard_timeout(raw) == float(raw)


def test_no_math_domain_leaks_into_parsed_values():
    for raw in ("0", "600", "3600", "bad"):
        value = parse_producer_hard_timeout(raw)
        assert math.isfinite(value)


def test_config_service_routes_the_watchdog_key_to_its_validator():
    """管理端保存路径必须真的走到看门狗校验，否则区间限制形同虚设。

    反证：`validate_config_update` 对未登记的键是放行的，所以这条测试在接线断掉时
    会因「不抛错」而失败——这正是它要防的回归。
    """
    from app.services.config_service import validate_config_update

    validate_config_update(PRODUCER_HARD_TIMEOUT_KEY, "600")
    validate_config_update(PRODUCER_HARD_TIMEOUT_KEY, "0")
    with pytest.raises(ValueError):
        validate_config_update(PRODUCER_HARD_TIMEOUT_KEY, "10")
