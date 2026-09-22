from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def browser_detect_source() -> str:
    return (ROOT / "frontend/src/utils/browserDetect.ts").read_text(encoding="utf-8")


def modal_source() -> str:
    return (ROOT / "frontend/src/components/login/BrowserUpgradeModal.vue").read_text(encoding="utf-8")


def login_source() -> str:
    return (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")


def test_browser_detect_utility_contract():
    source = browser_detect_source()

    # 包含核心接口定义与导出
    assert "export interface BrowserDetectResult" in source
    assert "isLowVersion: boolean" in source
    assert "isSupported: boolean" in source

    # 包含推荐 Chrome / Edge 官方下载地址
    assert "https://www.google.cn/chrome/" in source
    assert "https://www.microsoft.com/edge" in source

    # 包含主流浏览器最低基线
    assert "MIN_RECOMMENDED_VERSIONS" in source
    assert "Chrome: 90" in source
    assert "Edge: 90" in source
    assert "Firefox: 90" in source
    assert "Safari: 15" in source

    # 包含 IE、Edge、Chrome、Firefox、Safari 正则解析与 Trident 识别
    assert "Trident" in source
    assert "MSIE" in source
    assert "Internet Explorer" in source

    # 包含现代能力探测与会话状态记忆机制
    assert "probeModernFeatures" in source
    assert "nanzi_dismiss_browser_upgrade" in source
    assert "isBrowserUpgradeDismissed" in source
    assert "dismissBrowserUpgrade" in source


def test_browser_upgrade_modal_contract():
    source = modal_source()

    # 无障碍与语义化属性
    assert 'role="dialog"' in source
    assert 'aria-modal="true"' in source
    assert 'aria-labelledby="browser-upgrade-title"' in source

    # 包含引导文案与 Chrome 强烈推荐
    assert "建议升级您的浏览器" in source
    assert "Google Chrome" in source
    assert "Microsoft Edge" in source
    assert "前往下载" in source

    # 安全外链打开
    assert "noopener,noreferrer" in source

    # 具备非强制阻断的“继续访问”与关闭交互
    assert "我已知晓，继续访问" in source
    assert "handleDismiss" in source
    assert "isIE" in source


def test_login_view_browser_detect_integration_contract():
    source = login_source()

    # 确认在登录页引入并挂载了检测与弹窗
    assert "import BrowserUpgradeModal from '../components/login/BrowserUpgradeModal.vue'" in source
    assert "import { detectBrowser, isBrowserUpgradeDismissed, dismissBrowserUpgrade }" in source
    assert "checkBrowserVersion()" in source
    assert "<BrowserUpgradeModal" in source
    assert ":show=\"showBrowserUpgradeModal\"" in source
    assert "@dismiss=\"handleDismissBrowserUpgrade\"" in source
    assert "@close=\"handleCloseBrowserUpgrade\"" in source
