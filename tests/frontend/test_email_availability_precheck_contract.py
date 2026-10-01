"""契约：个人中心「修改邮箱」的实时可用性预检。

守的是「边输边查」这件事**必须真的连到后端权威判定**，而不是本地猜：
本地正则只能看出格式，看不出占用。同时守住限流降级——预检是体验优化，
不能因为它失败就把用户挡在保存之外。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")
AUTH_PY = (ROOT / "app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")


def test_input_triggers_precheck_against_backend():
    """输入必须触发后端预检；纯本地正则判断不出「占用」。"""
    assert "onEmailInput" in SRC, "输入框必须绑定输入处理"
    assert "@input=\"onEmailInput\"" in SRC, "缺少 @input 绑定，边输边查不会发生"
    assert "axios.get('/api/portal/auth/me/email-available'" in SRC, (
        "预检必须调用后端权威接口"
    )


def test_precheck_is_debounced_and_discards_stale_responses():
    """必须去抖，且丢弃在途的旧响应（慢响应不得覆盖新结论）。"""
    assert "EMAIL_CHECK_DEBOUNCE_MS" in SRC, "缺少去抖常量"
    assert re.search(r"clearTimeout\(\s*emailCheckTimer\s*\)", SRC), "去抖未清理上一个定时器"
    assert "emailCheckSeq" in SRC and SRC.count("seq !== emailCheckSeq") == 2, (
        "缺少请求序号（成功与异常两条路径都要丢弃过期响应）"
    )
    # 关键：请求必须**经过去抖**才发出。只断言常量/clearTimeout 存在是不够的，
    # 把 setTimeout 去掉、每次按键直接请求，那些要素依然都在。
    idx = SRC.index("const onEmailInput")
    body = SRC[idx : idx + 900]
    assert "setTimeout(" in body, "onEmailInput 必须经 setTimeout 去抖"
    assert re.search(r"setTimeout\(\(\) => \{[^}]*runEmailCheck", body, re.S), (
        "runEmailCheck 必须只在去抖回调里调用，不能在输入处理里直接调用"
    )


def test_precheck_degrades_silently_on_rate_limit():
    """429 必须静默降级，不能阻止保存（预检是优化，不是放行条件）。"""
    idx = SRC.index("const runEmailCheck")
    body = SRC[idx : idx + 1400]
    assert "429" in body, "必须区分 429 与其他错误"
    assert "emailCheckDisabled.value = true" in body, "429 后应停止本次会话的预检"
    assert "emailCheckState.value = 'error'" in body, "网络异常必须静默处理"


def test_save_is_blocked_only_by_definitive_negative():
    """只有确定的否定结论（占用/格式非法）才拦保存；checking/error 一律放行。"""
    assert "emailCheckBlocksSave" in SRC
    idx = SRC.index("const emailCheckBlocksSave = computed")
    body = SRC[idx : idx + 300]
    assert "'taken'" in body and "'invalid'" in body, "只应包含确定的否定结论"
    assert "'checking'" not in body and "'error'" not in body, (
        "checking/error 不得拦保存，否则一次查不到就把可用的操作锁死"
    )
    assert "emailCheckBlocksSave" in SRC[SRC.index('@click="saveEmail"') - 300 : SRC.index('@click="saveEmail"')], (
        "保存按钮必须按预检结论禁用"
    )


def test_precheck_reset_on_edit_session_boundaries():
    """进入/离开编辑态必须重置，避免上一次的结论串到下一次。"""
    for fn in ("startEditEmail", "cancelEditEmail"):
        idx = SRC.index(f"const {fn} = ")
        assert "resetEmailCheck()" in SRC[idx : idx + 500], f"{fn} 必须重置预检状态"
    assert "emailCheckDisabled.value = false" in SRC[SRC.index("const startEditEmail = ") :][:500], (
        "429 降级不能跨编辑会话一直生效"
    )


def test_email_available_endpoint_stays_authenticated_and_limited():
    """钉住后端前提：必须登录 + 按用户限流，否则预检就成了公开的邮箱枚举接口。"""
    idx = AUTH_PY.index('@router.get("/me/email-available"')
    sig = AUTH_PY[idx : AUTH_PY.index("):", idx)]
    assert "require_api_key" in sig, "预检接口必须要求登录"
    body = AUTH_PY[idx : idx + 2200]
    assert "is_email_check_rate_limited" in body, "必须按用户限流以抑制枚举"


def test_email_available_endpoint_excludes_self():
    """本人当前邮箱必须判为可用，否则打开编辑框就先看到「已被占用」。"""
    idx = AUTH_PY.index('@router.get("/me/email-available"')
    body = AUTH_PY[idx : idx + 2200]
    assert "exclude_user_id=user_id" in body, "必须排除自己"
