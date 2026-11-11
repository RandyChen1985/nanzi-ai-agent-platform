"""``API_SERVICE_ENV`` 的「生产模式」语义与取值归一化。

背景（真实事故形态）：判断处原先写的是严格相等 ``API_SERVICE_ENV != "production"``，
而仓库里的生产模板（K8s configmap / install.sh / 生产 compose）给的是 ``prod``——
两者不相等，于是「生产部署一直处于调试模式」：

* 500/503 响应的 ``detail`` 直接回显 ``str(exc)``（SQL 片段、路径、内部报错原文）；
* ``/api/test/echo``（回显请求头的调试端点）一直挂着。

因此这里钉住三件事：

1. 读取侧归一化：``strip`` + 小写，``prod`` / ``prd`` 与 ``production`` 等效，
   但字段本身保留原值，日志与诊断仍能按原样排查；
2. 业务代码只认 ``settings.is_production``，不再散落字符串比较（写错一个大小写就
   静默退回调试模式，而且没有任何报错）;
3. 生产模板的取值必须是 ``production``，不能是 ``prod``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _settings(value: str) -> Settings:
    return Settings(API_SERVICE_ENV=value)


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["production", "Production", "PRODUCTION", " prod ", "prd"])
def test_production_aliases_enable_prod_mode(value):
    """``prod`` / ``prd`` / 大小写 / 前后空格的写法都必须算生产，否则就是静默失效。"""
    assert _settings(value).is_production is True


@pytest.mark.parametrize("value", ["dev", "development", "test", "staging", "", "production2"])
def test_non_production_values_keep_debug_mode(value):
    assert _settings(value).is_production is False


def test_raw_value_is_preserved_for_diagnostics():
    """归一化只发生在读取侧：字段保留原值，排查时看到的仍是运维填的那个字符串。"""
    assert _settings(" prod ").API_SERVICE_ENV == " prod "


# --------------------------------------------------------------------------- #
# 业务代码不直接比较字符串
# --------------------------------------------------------------------------- #
def test_main_uses_normalized_flag_everywhere():
    """四处判断（503 DB / 503 缺表 / 500 未捕获 / echo 端点）都要走 is_production。"""
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")

    assert 'API_SERVICE_ENV != "production"' not in source
    assert 'API_SERVICE_ENV == "production"' not in source
    assert source.count("settings.is_production") == 4
    # echo 调试端点只在非生产挂载
    assert "if not settings.is_production:" in source


# --------------------------------------------------------------------------- #
# 生产模板取值
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rel",
    ["k8s_deploy/configmap.yaml", "k8s_deploy/install.sh", "docker/docker-compose.ai-agent.yml"],
)
def test_production_templates_use_full_word(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert 'API_SERVICE_ENV: "production"' in text or "API_SERVICE_ENV:-production" in text, rel
    assert 'API_SERVICE_ENV: "prod"' not in text, f"{rel} 仍在用 prod"
    assert "API_SERVICE_ENV=${API_SERVICE_ENV:-prod}" not in text, f"{rel} 仍在用 prod"


def test_env_templates_explain_the_switch():
    """模板是运维唯一会看的地方：必须写明 production 的含义与「别写 prod」。"""
    for rel in ("env.example", "docker/env.example"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/api/test/echo" in text, rel
        assert "production" in text, rel
