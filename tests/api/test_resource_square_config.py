"""资源广场数据集目录开关。

默认开启：数据集目录是这次新增能力的核心，默认关闭等于功能上线了却看不到。
但仍留一个开关——某些团队希望数据集只走管理员手工授权，不接受自助申请。
"""

import pytest

from app.services.config_service import (
    RESOURCE_SQUARE_SHOW_DATASETS_KEY,
    validate_config_update,
)

pytestmark = pytest.mark.no_infrastructure


def test_config_key_value():
    assert RESOURCE_SQUARE_SHOW_DATASETS_KEY == "resource_square_show_datasets"


@pytest.mark.parametrize("value", ["true", "false", "1", "0", "yes", "no", "on", "off"])
def test_boolean_literals_are_accepted(value):
    validate_config_update(RESOURCE_SQUARE_SHOW_DATASETS_KEY, value)


@pytest.mark.parametrize("value", ["maybe", "", "2"])
def test_invalid_values_are_rejected(value):
    with pytest.raises(ValueError):
        validate_config_update(RESOURCE_SQUARE_SHOW_DATASETS_KEY, value)
