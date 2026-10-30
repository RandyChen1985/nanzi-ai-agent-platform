"""SSO 登录方式（loginType）的端到端契约。

背景：portal-web 的 `check/login` 把 `loginType`（`local` / `ad`）改成必传项，不传会被
它的参数校验拒成 `code=500`；而平台旧代码只看 `data`，于是把「配置问题」报成
「用户名或密码错误」，现场完全无从判断。

这条链上有五个环节必须同时在位，缺任何一个都会静默退回旧行为（不传 → 一律参数错误）：

1. 登录页把用户选的方式放进请求体；
2. schema 收得住 `login_type` 和 `loginType` 两种写法；
3. service 层「请求优先、env 兜底」，且非法值当场拦下；
4. 端点把 schema 里的值透传下去；
5. public config 把 env 默认值告诉前端，否则选择器初值和后台配置对不上。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LOGIN_VUE = REPO_ROOT / "frontend/src/views/Login.vue"
AUTH_ENDPOINT = REPO_ROOT / "app/api/portal/endpoints/auth.py"
AUTH_SERVICE = REPO_ROOT / "app/services/auth_service.py"


def _read(path: Path) -> str:
    assert path.exists(), f"{path} 不见了：契约测试定位不到源文件"
    return path.read_text(encoding="utf-8")


def test_config_key_is_named_default_type():
    """配置项叫 DEFAULT_TYPE 而不是 TYPE：它只是默认值，页面上的选择可以覆盖它。

    曾经叫 SSO_LOGIN_TYPE，容易被读成「只允许这一种」——实际配 ad 时用户仍能选 local，
    配 local 时用户也仍能选 ad，线上表现和名字给人的预期相反。
    """
    config = _read(REPO_ROOT / "app/core/config.py")

    assert "SSO_LOGIN_DEFAULT_TYPE: str" in config
    assert "SSO_LOGIN_TYPE: str" not in config


def test_login_page_sends_login_type():
    source = _read(LOGIN_VUE)

    assert "login_type: ssoLoginType.value" in source
    # 两种方式都要给用户选，不能靠猜
    assert "SSO_LOGIN_TYPE_OPTIONS" in source
    assert "{ value: 'ad', label: '域账号' }" in source
    assert "{ value: 'local', label: '本地密码' }" in source


def test_login_page_takes_default_from_public_config():
    source = _read(LOGIN_VUE)

    # 默认值必须来自后台配置，否则 env 配成 local 时页面仍高亮「域账号」
    assert "sso_login_type" in source
    assert "configuredSsoLoginType === 'ad' || configuredSsoLoginType === 'local'" in source


def test_schema_accepts_both_key_styles():
    source = _read(AUTH_ENDPOINT)

    assert "login_type: Optional[str] = Field(" in source
    assert 'alias="loginType"' in source
    assert "populate_by_name=True" in source


def test_endpoint_passes_login_type_down():
    source = _read(AUTH_ENDPOINT)

    assert "login_type=request.login_type" in source


def test_service_prefers_request_over_env():
    source = _read(AUTH_SERVICE)

    assert "effective_login_type = requested_login_type or configured_login_type" in source
    assert "'loginType': effective_login_type" in source


def test_service_rejects_unknown_login_type_before_requesting():
    source = _read(AUTH_SERVICE)

    # 只允许 local / ad；非法值不发请求，否则会拿回一个与口令无关的失败
    assert "if effective_login_type not in SSO_LOGIN_TYPES:" in source


def test_login_page_honours_method_switches():
    """被关掉的方式要在页面上置灰 + 标「禁用」，而不是只靠后端拒绝。"""
    source = _read(LOGIN_VUE)

    assert "sso_login_enabled_types" in source
    assert ':disabled="!isSsoTypeEnabled(opt.value)"' in source
    assert "禁用" in source


def test_all_methods_off_disables_the_whole_form():
    """两种方式都关掉时，输入框和提交按钮必须一起禁用并给出提示。"""
    source = _read(LOGIN_VUE)

    assert "const ssoAvailable = computed(() => ssoEnabledTypes.value.length > 0)" in source
    assert source.count(":disabled=\"activeTab === 'sso' && !ssoAvailable\"") == 2  # 用户名 + 密码
    assert ":disabled=\"loading || (activeTab === 'sso' && !ssoAvailable)\"" in source
    assert "SSO 登录已关闭" in source


def test_public_config_exposes_enabled_types():
    source = _read(AUTH_ENDPOINT)

    assert '"sso_login_enabled_types": sso_capabilities["enabled_types"]' in source
    assert '"sso_login_type": sso_capabilities["default_type"]' in source


def test_endpoint_blocks_when_all_methods_off():
    source = _read(AUTH_ENDPOINT)

    assert 'if not sso_login_capabilities()["enabled_types"]:' in source


def test_login_failure_logging_never_includes_password():
    """日志要够详细好排障，但绝不能出现口令本身。"""
    source = _read(AUTH_SERVICE)
    start = source.index("async def authenticate_sso_user")
    end = source.index("async def get_user_2fa_status", start)
    method = source[start:end]

    assert "SSO 认证开始" in method
    assert "SSO 认证响应" in method
    assert "elapsed=" in method
    # 日志调用里不能把 password 变量当参数传出去
    for line in method.splitlines():
        if "logger." in line:
            assert "password" not in line, f"日志里出现了 password：{line.strip()}"
