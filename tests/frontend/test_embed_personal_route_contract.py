"""Contract: /embed/personal 第三方嵌入入口（= /dashboard/personal，但不含顶部面包屑与侧边栏）。

背景：平台已有 /embed/chat 供第三方 iframe 嵌入对话。个人中心同理需要一个可嵌入入口：
/embed/personal 必须与 /dashboard/personal 渲染同一个 PersonalCenter 组件，
唯一区别是不渲染 Dashboard 的顶部 header（含面包屑导航）与左侧主导航。

另：本页 meta.public，路由守卫不拦，因此必须自带凭据门禁——未登录时直接提示
「未登录 / 非法闯入」，而不是渲染成「一个还没设密码的普通用户」的空壳。
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]

ROUTER = "frontend/src/router/index.ts"
EMBED_PERSONAL = "frontend/src/views/EmbedPersonal.vue"

# 模板里的 HTML 注释会提到 header / 面包屑 / 路由名等字样，
# 断言必须只看真实标记，否则「注释里说没有面包屑」会被当成渲染了面包屑。
_TEMPLATE_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _template_block(path: str) -> str:
    """取 <template> 段并剥离 HTML 注释：只保留真实标记。"""
    source = _source(path)
    start = source.index("<template>")
    end = source.index("</template>", start)
    return _TEMPLATE_COMMENT.sub("", source[start:end])


def test_embed_personal_route_registered_under_embed_layout():
    router = _source(ROUTER)
    assert "name: 'EmbedPersonal'" in router
    assert "import('../views/EmbedPersonal.vue')" in router
    assert "path: 'personal'" in router

    # 必须挂在 /embed 布局下（与 EmbedChat 同一父路由），而不是 /dashboard 下
    embed_block = router[router.index("path: '/embed'"):router.index("path: '/dashboard'")]
    assert "name: 'EmbedChat'" in embed_block
    assert "name: 'EmbedPersonal'" in embed_block

    # 鉴权口径与 /embed/chat 一致：第三方 iframe 里没有门户 localStorage 快照，
    # 若不加 public 会被路由守卫弹到登录页。
    personal_block = embed_block[embed_block.index("name: 'EmbedPersonal'"):]
    assert "public: true" in personal_block[: personal_block.index("}")]


def test_embed_personal_reuses_personal_center_component():
    view = _source(EMBED_PERSONAL)
    # 复用主站组件而不是复制实现：PersonalCenter 的更新必须同步到嵌入页
    assert 'from "./PersonalCenter.vue"' in view
    assert "<PersonalCenter />" in view
    # 不得把 Dashboard 布局拉进来（否则顶部面包屑与侧边栏会跟着进来）
    assert "import Dashboard" not in view
    assert "Dashboard.vue" not in view


def test_embed_personal_has_no_breadcrumb_header_or_sidebar():
    """嵌入页不得渲染 Dashboard 的主题框架：顶部 header（面包屑）与左侧边栏。"""
    template = _template_block(EMBED_PERSONAL)
    assert "Breadcrumb" not in template
    assert "<header" not in template
    assert "<aside" not in template
    # 模板里只应有内容容器 + PersonalCenter，没有第二个顶层块
    assert template.count("<PersonalCenter />") == 1


def test_embed_personal_content_spacing_matches_dashboard_personal():
    """视觉口径对齐 Dashboard 的 main：bg-gray-100 + px-3 sm:px-4 + 同一套滚动条。"""
    view = _source(EMBED_PERSONAL)
    dashboard = _source("frontend/src/views/Dashboard.vue")
    # Dashboard 对 PersonalCenter 路由的间距就是 px-3 sm:px-4，无上下内边距
    assert "if (route.name === \"PersonalCenter\") return \"px-3 sm:px-4\";" in dashboard
    assert "bg-gray-100" in view
    assert "px-3 sm:px-4" in view
    assert "custom-scrollbar" in view


# --- 凭据门禁：未登录必须被挡住，且不得诬指网络故障为未登录 ---


def test_embed_personal_gates_render_on_credential_check():
    """PersonalCenter 只能在凭据校验通过后挂载，绝不带着空数据渲染。"""
    view = _source(EMBED_PERSONAL)
    template = _template_block(EMBED_PERSONAL)

    # 用与主站同一个接口判定，不引入新鉴权口径
    assert 'axios.get("/api/portal/auth/me")' in view
    # 挂载前必须校验
    assert "onMounted(verifyCredential)" in view

    # granted 分支必须包住 <PersonalCenter />：二者顺序与分支归属都要成立
    granted_pos = template.index("gateState === 'granted'")
    personal_pos = template.index("<PersonalCenter />")
    assert granted_pos < personal_pos
    # 未通过校验时没有任何 PersonalCenter 渲染路径
    assert template.count("<PersonalCenter />") == 1


def test_embed_personal_unauthorized_shows_clear_refusal_notice():
    """未登录（401/403）必须给出明确的拒绝提示，而不是空壳内容。"""
    view = _source(EMBED_PERSONAL)
    template = _template_block(EMBED_PERSONAL)

    assert "gateState === 'unauthorized'" in template
    assert "未授权访问" in template
    assert "未登录" in template
    # 拒绝态必须可自动播报（无障碍告警语义）
    assert 'role="alert"' in template
    # 主文案要告诉用户「我能做什么」，而不是只报错
    assert "请先登录平台账号，再重新打开本页面" in template
    assert "我已登录，重新核验" in template


def test_embed_personal_keeps_integration_hint_out_of_primary_copy():
    """内部路由 /embed/chat 是给集成方排障的线索，只能出现在灰色小字里。

    对第三方系统的终端用户来说 /embed/chat 毫无意义，不该占据主视觉；
    但它对集成方排障有价值，因此保留为次要说明。
    """
    template = _template_block(EMBED_PERSONAL)

    assert "/embed/chat" in template
    # 技术线索所在段落必须是弱化样式（text-gray-400 + text-[11px]），且位于按钮之后
    hint_pos = template.index("/embed/chat")
    hint_block = template[template.rindex("<p", 0, hint_pos):hint_pos]
    assert "text-gray-400" in hint_block
    assert "text-[11px]" in hint_block
    assert "我已登录，重新核验" in template[:hint_pos]


def test_embed_personal_does_not_treat_network_failure_as_intrusion():
    """非认证类失败（断网 / 服务重启 / 5xx）不得判成「未登录」。"""
    view = _source(EMBED_PERSONAL)
    template = _template_block(EMBED_PERSONAL)

    assert "isAuthFailure" in view
    # 只有 401 / 403 才算认证类失败
    assert "status === 401 || status === 403" in view
    # 必须存在独立的 error 态，而不是把一切失败都归到 unauthorized
    assert '"error"' in view
    assert "暂时无法确认访问权限" in template
    # 两个态的文案必须可区分，避免故障时误伤用户
    assert "未授权访问" in template and "暂时不可用" in template


def test_embed_personal_rejects_200_without_valid_identity():
    """200 但身份结构不对，同样拒绝渲染，避免空壳页面。"""
    view = _source(EMBED_PERSONAL)
    assert 'response.data?.status === "success" ? "granted" : "unauthorized"' in view
