"""登录页「API Key」Tab 不得先出现再消失。

## 现象

打开登录页时右侧面板会短暂出现三个 Tab（SSO 登录 / 本地账号 / API Key），紧接着 API Key
那一个消失，只剩两个。

## 根因

`hideLoginApiKey` 的取值来自公开配置 `hide_login_apikey`，只能等
`GET /api/portal/auth/config/public` 返回才知道。首版把它的初值写成 `false`（显示），
于是首帧按「不隐藏」渲染出 API Key Tab，配置回来后再置 `true` 把它删掉——一次
「闪一下然后隐藏」。

## 修复约定

1. 初值必须是**隐藏**：隐藏环境（`hide_login_apikey = true`）从头到尾不渲染这个 Tab；
   允许 API Key 的环境则是 Tab 稍后出现，不是闪退。
2. 配置确实没拿到（请求失败 / 响应不是 `success`）时必须回退为显示——不能因为一次
   接口抖动就把这条登录通道永久藏掉。
3. 回退判定只能看「配置是否真的拿到」，不能用 `catch` 兜底：`fetchPublicConfig` 里
   时区动态导入等后续步骤失败也会落到 `catch`，那会把已读到的 `true` 覆盖成 `false`，
   该藏的 Tab 又放出来。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LOGIN = ROOT / "frontend/src/views/Login.vue"

pytestmark = pytest.mark.no_infrastructure


def _normalized() -> str:
    # 归一化空白：契约约束行为，不应因重新格式化/换行而假失败
    return re.sub(r"\s+", " ", LOGIN.read_text(encoding="utf-8"))


def test_api_key_tab_defaults_to_hidden():
    """初值必须隐藏，否则首帧一定多渲染一个 Tab。"""
    source = _normalized()

    assert "const hideLoginApiKey = ref(true)" in source
    assert "const hideLoginApiKey = ref(false)" not in source


def test_tab_strip_filters_by_the_flag():
    """Tab 渲染必须以该标志过滤，而不是无条件渲染。"""
    source = _normalized()

    assert "(t.id !== 'apikey' || !hideLoginApiKey)" in source


def test_missing_public_config_falls_back_to_showing_api_key():
    """拿不到配置时回退为显示：不能因为一次接口抖动把登录通道藏掉。"""
    source = _normalized()

    fetch = source[source.index("const fetchPublicConfig = async") :]
    fetch = fetch[: fetch.index("const showBrowserUpgradeModal")]

    assert "let configLoaded = false" in fetch
    assert "configLoaded = true" in fetch
    assert "if (!configLoaded) hideLoginApiKey.value = false" in fetch


def test_fallback_is_not_wired_to_the_catch_block():
    """回退判定不能用 catch 兜底。

    时区动态导入在 `configLoaded = true` 之后，它失败也会落进 `catch`；若把回退写在
    `catch` 里，配置读到的 `true` 会被覆盖成 `false`，该藏的 Tab 又冒出来。
    """
    source = _normalized()

    fetch = source[source.index("const fetchPublicConfig = async") :]
    fetch = fetch[: fetch.index("const showBrowserUpgradeModal")]

    catch_at = fetch.index("} catch (e) {")
    catch_body = fetch[catch_at : fetch.index("} finally {")]

    assert "hideLoginApiKey.value = false" not in catch_body, "回退必须写在 finally 的 configLoaded 判断里"
    assert "hideLoginApiKey.value = false" in fetch[fetch.index("} finally {") :]
