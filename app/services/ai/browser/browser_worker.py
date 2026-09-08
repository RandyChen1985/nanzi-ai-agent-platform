from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.schemas.browser import BrowserElement, BrowserSnapshot, BrowserTab, BrowserToolResult
from app.services.ai.browser.browser_policy import (
    BrowserActionClass,
    BrowserUrlBlocked,
    classify_browser_action,
    decide_browser_action,
    redact_browser_arguments,
    redact_browser_cookies,
    validate_browser_navigation,
    validate_browser_request,
    validate_browser_script,
)


class BrowserTargetStale(RuntimeError):
    """页面已经变化，调用方必须先重新获取快照。"""

    def __init__(self, message: str, *, recoverable: bool = False):
        super().__init__(message)
        # 仅当目标语义可安全按最新 DOM 重新定位时才允许 stale 恢复自动重试。
        # 目标解析类失败（快照里的 ref / 推断 index 已失配）不可自动恢复：用同名
        # ref 去最新 DOM 上重定位会把动作静默落到一个语义完全不同的元素上。
        self.recoverable = recoverable


class BrowserWaitTimeout(TimeoutError):
    """浏览器页面在限定时间内没有达到等待条件。"""


class BrowserActionConfirmationRequired(PermissionError):
    """当前 BrowserSession 需要用户确认后才能执行该动作。"""


class BrowserEnvironmentError(RuntimeError):
    """服务端浏览器运行环境或 Chromium 内核未就绪。"""

    def __init__(
        self,
        message: str = (
            "服务端浏览器运行环境未就绪。\n"
            "请在服务器终端依次执行以下命令完成安装：\n"
            "1. pip install playwright\n"
            "2. playwright install chromium\n"
            "（若 Linux 环境缺少系统依赖，可补充执行：playwright install-deps chromium）"
        ),
        *,
        missing_component: str = "playwright",
    ):
        super().__init__(message)
        self.missing_component = missing_component


def _is_browser_env_missing(exc: BaseException) -> tuple[bool, str]:
    """检测异常是否由 Playwright 库或 Chromium 浏览器内核缺失引发。"""
    if isinstance(exc, ModuleNotFoundError) and "playwright" in str(exc).lower():
        return True, (
            "服务端未安装 Playwright 依赖库。\n"
            "请在服务器终端执行：\n"
            "pip install playwright\n"
            "playwright install chromium"
        )
    msg = str(exc).lower()
    if (
        "executable doesn't exist" in msg
        or "please run \"playwright install\"" in msg
        or "playwright install" in msg
        or "browsertype.launch" in msg
        or "looks like playwright was just installed or updated" in msg
    ):
        return True, (
            "服务端尚未下载 Chromium 浏览器内核。\n"
            "请在服务器终端执行：\n"
            "playwright install chromium"
        )
    if "missing dependencies" in msg or "install-deps" in msg:
        return True, (
            "服务端操作系统缺少运行 Chromium 浏览器所需的底层系统库。\n"
            "请在服务器终端执行：\n"
            "playwright install-deps chromium"
        )
    return False, ""


@dataclass(frozen=True)
class BrowserPageInfo:
    url: str
    title: str
    focused_input: bool = False


DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

CHROMIUM_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
]

MANUAL_CLICK_LOAD_TIMEOUT_MS = 1500
SNAPSHOT_NODE_SELECTOR = "body *"
SNAPSHOT_MAX_ELEMENTS = 120
# 抓取帧内交互候选元素；CSS 选择器默认穿透 open shadow DOM，因此对 Shadow DOM 元素同样生效。
# 需以 "% SNAPSHOT_MAX_ELEMENTS" 作为参数格式化，故此处保留 %d 占位符。
SNAPSHOT_JS = r"""
(nodes) => {
  const interactiveRoles = new Set([
    'button', 'link', 'tab', 'option', 'menuitem', 'combobox',
    'checkbox', 'radio', 'switch', 'listbox', 'textbox', 'searchbox', 'spinbutton'
  ]);
  const nativeRoles = {
    button: 'button',
    a: 'link',
    select: 'combobox',
    textarea: 'textbox',
    input: (node) => {
      const t = (node.type || 'text').toLowerCase();
      if (t === 'search') return 'searchbox';
      if (t === 'button' || t === 'submit' || t === 'reset' || t === 'image') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'hidden') return '';
      return 'textbox';
    }
  };
  const isVisible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  };
  const isContentEditable = (node) => {
    try {
      return Boolean(node.isContentEditable || node.getAttribute('contenteditable') === 'true' || node.getAttribute('contenteditable') === '');
    } catch (_) {
      return false;
    }
  };
  const cleanText = (value) => String(value || '')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\s*\n\s*/g, '\n')
    .trim()
    .slice(0, 240);

  const resolveElementLabel = (node) => {
    // 1. 显式 aria-label
    const ariaLabel = node.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return cleanText(ariaLabel);

    // 2. aria-labelledby
    const labelledBy = node.getAttribute('aria-labelledby');
    if (labelledBy) {
      const labelEls = labelledBy.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean);
      const combined = labelEls.map(el => el.innerText || el.textContent || '').join(' ').trim();
      if (combined) return cleanText(combined);
    }

    // 3. 原生表单 labels (HTMLInputElement.labels)
    if (node.labels && node.labels.length > 0) {
      const combined = Array.from(node.labels).map(l => l.innerText || l.textContent || '').join(' ').trim();
      if (combined) return cleanText(combined);
    }

    // 4. label[for="id"]
    if (node.id) {
      try {
        const matchedLabel = document.querySelector(`label[for="${CSS.escape(node.id)}"]`);
        if (matchedLabel && (matchedLabel.innerText || matchedLabel.textContent)) {
          return cleanText(matchedLabel.innerText || matchedLabel.textContent);
        }
      } catch (_) {}
    }

    // 5. title / placeholder
    const title = node.getAttribute('title');
    if (title && title.trim()) return cleanText(title);
    const placeholder = node.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return cleanText(placeholder);

    // 6. innerText / textContent（对于 button、a、contenteditable div）
    const inner = (node.innerText || node.textContent || '').trim();
    if (inner) return cleanText(inner);

    // 7. value（如果非敏感且有值）
    if (node.value && String(node.value).trim()) return cleanText(node.value);

    // 8. 兜底提取 name / id / data-placeholder 等属性
    const nameAttr = node.getAttribute('name') || node.getAttribute('data-placeholder') || node.getAttribute('data-testid') || '';
    if (nameAttr && nameAttr.trim()) return cleanText(nameAttr);

    return '';
  };

  const candidates = [];
  for (let nodeIndex = 0; nodeIndex < nodes.length && candidates.length < %d; nodeIndex += 1) {
    const node = nodes[nodeIndex];
    const tagName = node.tagName.toLowerCase();
    const roleAttribute = (node.getAttribute('role') || '').trim().toLowerCase();
    let nativeRole = nativeRoles[tagName];
    let resolvedNativeRole = typeof nativeRole === 'function' ? nativeRole(node) : nativeRole;
    if (!resolvedNativeRole && isContentEditable(node)) {
      resolvedNativeRole = 'textbox';
    }
    const inferredInteractive = !roleAttribute && !resolvedNativeRole && (
      node.hasAttribute('onclick')
      || (node.hasAttribute('tabindex') && Number(node.tabIndex) >= 0)
      || node.hasAttribute('aria-haspopup')
      || window.getComputedStyle(node).cursor === 'pointer'
    );
    const role = roleAttribute || resolvedNativeRole || (inferredInteractive ? 'button' : '');
    const candidate = Boolean(resolvedNativeRole)
      || (roleAttribute && interactiveRoles.has(roleAttribute))
      || inferredInteractive;
    if (!candidate || !isVisible(node)) continue;
    const sensitive = tagName === 'input'
      && (node.type === 'password' || ['current-password', 'new-password', 'password'].includes(node.getAttribute('autocomplete')));
    const rawName = resolveElementLabel(node);
    if (tagName === 'div' || tagName === 'section' || tagName === 'article') {
      if (!resolvedNativeRole && !roleAttribute && rawName.length > 150) continue;
    }
    const rect = node.getBoundingClientRect();
    candidates.push({
      role,
      tag: tagName,
      _role_source: roleAttribute ? 'explicit' : resolvedNativeRole ? 'native' : 'inferred',
      _node_index: nodeIndex,
      _in_shadow: (node.getRootNode && node.getRootNode() !== document),
      sensitive,
      name: rawName,
      value: sensitive ? '' : cleanText(node.value || ''),
      disabled: Boolean(node.disabled) || node.getAttribute('aria-disabled') === 'true',
      bbox: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    });
  }
  return candidates;
}
"""
SNAPSHOT_PAGE_TEXT_LIMIT = 6000
SNAPSHOT_VISIBLE_TEXT_LIMIT = 12000
SNAPSHOT_SETTLE_DELAY_MS = 150
DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 1800  # 30 分钟无操作自动清理空闲 Chromium 实例
# 动作失败时的自动恢复次数：stale/超时错误先刷新快照再重试，避免一次性失败直接中断流程。
ACTION_RETRY_COUNT = 2
# 动作完成后用于「拖后生效校验」的稳定等待下限（毫秒），不足则等满该值再取后置快照。
POST_ACTION_SETTLE_MS = 300

STEALTH_INIT_SCRIPT = """
(() => {
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true,
        });
    } catch (_) {}

    try {
        if (!window.chrome) {
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {},
            };
        }
    } catch (_) {}

    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en-US', 'en'],
            configurable: true,
        });
    } catch (_) {}

    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
            configurable: true,
        });
    } catch (_) {}

    try {
        const originalQuery = window.navigator?.permissions?.query;
        if (typeof originalQuery === 'function') {
            window.navigator.permissions.query = (parameters) => (
                parameters && parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        }
    } catch (_) {}

    // —— 硬件与平台指纹兜底，与 macOS Chrome 默认值保持一致 ——
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8,
            configurable: true,
        });
    } catch (_) {}

    try {
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8,
            configurable: true,
        });
    } catch (_) {}

    try {
        Object.defineProperty(navigator, 'platform', {
            get: () => 'MacIntel',
            configurable: true,
        });
    } catch (_) {}

    try {
        Object.defineProperty(navigator, 'maxTouchPoints', {
            get: () => 0,
            configurable: true,
        });
    } catch (_) {}

    // 视口/屏幕尺寸统一，避免 headless 暴露异常窗口尺寸。
    try {
        if (window.screen) {
            const fixScreen = (descriptor) => Object.defineProperties(window.screen, {
                width: { get: () => descriptor.width, configurable: true },
                height: { get: () => descriptor.height, configurable: true },
                availWidth: { get: () => descriptor.width, configurable: true },
                availHeight: { get: () => descriptor.height, configurable: true },
            });
            fixScreen({ width: 1280, height: 800 });
        }
        Object.defineProperties(window, {
            outerWidth: { get: () => 1280, configurable: true },
            outerHeight: { get: () => 800, configurable: true },
            innerWidth: { get: () => 1280, configurable: true },
            innerHeight: { get: () => 800, configurable: true },
        });
    } catch (_) {}

    // 统一 WebGL 渲染信息，掩盖 headless 默认的 SwiftShader/Google 渲染器信号。
    const spoofWebGl = (gl) => {
        if (!gl) return;
        const vendor = gl.getParameter(gl.VENDOR);
        const renderer = gl.getParameter(gl.RENDERER);
        if (vendor && String(vendor).toLowerCase().indexOf('google') !== -1) {
            try {
                gl.getParameter = ((original) => (name) => {
                    if (name === gl.VENDOR) return 'Intel Inc.';
                    if (name === gl.RENDERER) return 'Intel(R) UHD Graphics 630';
                    return original(name);
                })(gl.getParameter);
            } catch (_) {}
        }
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        if (ext) {
            try {
                gl.getParameter = ((original) => (name) => {
                    if (name === ext.UNMASKED_VENDOR_WEBGL) return 'Intel Inc.';
                    if (name === ext.UNMASKED_RENDERER_WEBGL) return 'Intel(R) UHD Graphics 630';
                    return original(name);
                })(gl.getParameter);
            } catch (_) {}
        }
    };
    try {
        spoofWebGl(document.createElement('canvas').getContext('webgl'));
    } catch (_) {}
    try {
        const canvases = document.querySelectorAll('canvas');
        for (let i = 0; i < canvases.length && i < 8; i += 1) {
            const gl = canvases[i].getContext('webgl') || canvases[i].getContext('experimental-webgl');
            if (gl) spoofWebGl(gl);
        }
    } catch (_) {}

    // —— C1：补充常见自动化检测面的指纹加固，进一步逼近 macOS Chrome 的默认外观 ——

    // 1) 完善 window.chrome.runtime / app 结构：许多反爬脚本只检测属性存在与结构，
    //    而不只是字段真假。补上典型 Chrome 扩展运行时对象的形状。
    try {
        const noop = function() {};
        if (window.chrome) {
            window.chrome.csi = window.chrome.csi || noop;
            window.chrome.loadTimes = window.chrome.loadTimes || noop;
            if (window.chrome.runtime) {
                if (!('id' in window.chrome.runtime)) window.chrome.runtime.id = undefined;
                window.chrome.runtime.getManifest = window.chrome.runtime.getManifest || (() => ({ manifest_version: 3 }));
                window.chrome.runtime.connect = window.chrome.runtime.connect || noop;
                window.chrome.runtime.sendMessage = window.chrome.runtime.sendMessage || noop;
            }
            if (window.chrome.app && !window.chrome.app.isInstalled) {
                window.chrome.app.isInstalled = false;
            }
        }
    } catch (_) {}

    // 2) permissions.query 覆盖到常见权限名：通知、剪贴板、地理位置、摄像头、
    //    麦克风、后台同步等，统一给「未请求」状态（'prompt'/'granted'），避免检测脚本
    //    通过观察自动化环境里权限 API 报错或返回异值来识别。
    try {
        const originalQuery = window.navigator && window.navigator.permissions && window.navigator.permissions.query;
        if (typeof originalQuery === 'function' && window.navigator.permissions) {
            const realQuery = originalQuery;
            window.navigator.permissions.query = (parameters) => {
                const name = parameters && parameters.name;
                const coordinators = ['geolocation', 'camera', 'microphone', 'midi', 'background-sync'];
                if (name === 'notifications') {
                    const state = (window.Notification && window.Notification.permission) || 'default';
                    return Promise.resolve({ state: state === 'denied' ? 'denied' : 'prompt' });
                }
                if (coordinators.indexOf(name) !== -1) {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                if (name === 'clipboard-read' || name === 'clipboard-write') {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                return realQuery(parameters);
            };
        }
    } catch (_) {}

    // 3) 清理少数自动化注入会留下的标记属性：若页面环境带有这些探针属性，视为代理
    //    伪造，直接移除/改值，避免被当成可点破的证据链。
    try {
        const markers = [
            'cdc_adoQpoasnfa76pfcZLmcfl_Array',
            'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
            'cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
            '__injected',
            '__selenium_evaluate',
        ];
        markers.forEach((marker) => {
            if (marker in window) {
                try { delete window[marker]; } catch (_) {}
            }
        });
    } catch (_) {}
})();
"""


@dataclass
class _BrowserHandle:
    context: Any
    page: Any
    browser: Any = None
    tab_ids: dict[int, str] = field(default_factory=dict)
    page_status: dict[int, str] = field(default_factory=dict)
    page_last_status: dict[int, int] = field(default_factory=dict)
    next_tab_number: int = 1
    last_active_at: float = field(default_factory=asyncio.get_event_loop().time if False else lambda: 0.0)
    network_logs: list[dict[str, Any]] = field(default_factory=list)
    dialog_listener: Any = None

    def touch(self) -> None:
        try:
            self.last_active_at = asyncio.get_running_loop().time()
        except RuntimeError:
            pass


def _default_playwright_factory():
    from playwright.async_api import async_playwright

    return async_playwright()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_navigation_context_error(exc: BaseException) -> bool:
    message = str(exc)
    return "Execution context was destroyed" in message and "navigation" in message


def _is_navigation_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or exc.__class__.__name__ == "TimeoutError"


class BrowserWorker:
    """隔离管理远程 Chromium 页面，不向上层暴露 Playwright 对象。"""

    def __init__(
        self,
        *,
        playwright_factory: Callable[[], Any] | None = None,
        url_validator: Callable[[str], str] = validate_browser_navigation,
        request_validator: Callable[[str], str] = validate_browser_request,
        screenshot_dir: str | None = "data/uploads/browser",
    ) -> None:
        self._playwright_factory = playwright_factory or _default_playwright_factory
        self._url_validator = url_validator
        self._request_validator = request_validator
        self._screenshot_dir = screenshot_dir
        self._playwright = None
        self._playwright_context_manager = None
        self._handles: dict[str, _BrowserHandle] = {}
        self._snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        self._cached_chromium_version: str | None = None

    async def _ensure_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        try:
            value = self._playwright_factory()
            value = await _maybe_await(value)
            if hasattr(value, "__aenter__"):
                self._playwright_context_manager = value
                value = await value.__aenter__()
            self._playwright = value
            return value
        except Exception as exc:
            is_env_err, guidance = _is_browser_env_missing(exc)
            if is_env_err:
                raise BrowserEnvironmentError(guidance) from exc
            raise

    async def check_environment(self) -> dict[str, Any]:
        """探测当前运行时的 Playwright 与 Chromium 环境状态、版本及路径。"""
        result = {
            "status": "ready",
            "playwright_installed": False,
            "playwright_version": None,
            "chromium_installed": False,
            "chromium_executable": None,
            "chromium_version": None,
            "error_detail": None,
            "install_guide": None,
        }
        try:
            import importlib.metadata

            result["playwright_installed"] = True
            result["playwright_version"] = importlib.metadata.version("playwright")
        except Exception:
            try:
                import playwright

                result["playwright_installed"] = True
                result["playwright_version"] = getattr(playwright, "__version__", None)
            except ImportError:
                result["status"] = "missing_package"
                result["error_detail"] = "未安装 Python playwright 依赖库"
                result["install_guide"] = "pip install playwright\nplaywright install chromium"
                return result

        try:
            pw = await self._ensure_playwright()
            chromium = getattr(pw, "chromium", None)
            if chromium is not None:
                executable_path = getattr(chromium, "executable_path", None)
                if callable(executable_path):
                    exec_path = executable_path()
                else:
                    exec_path = str(executable_path) if executable_path else None
                result["chromium_executable"] = exec_path
                if exec_path and os.path.exists(exec_path):
                    result["chromium_installed"] = True
                    result["status"] = "ready"
                    # 读取 Chromium 版本号
                    if self._cached_chromium_version:
                        result["chromium_version"] = self._cached_chromium_version
                    else:
                        active_version = None
                        for h in self._handles.values():
                            b = getattr(h, "browser", None)
                            if b and hasattr(b, "version"):
                                active_version = b.version
                                break
                            ctx = getattr(h, "context", None)
                            b2 = getattr(ctx, "browser", None)
                            if b2 and hasattr(b2, "version"):
                                active_version = b2.version
                                break
                        if active_version:
                            result["chromium_version"] = str(active_version)
                            self._cached_chromium_version = str(active_version)
                        else:
                            try:
                                b = await chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
                                result["chromium_version"] = str(b.version)
                                self._cached_chromium_version = str(b.version)
                                await b.close()
                            except Exception:
                                pass
                else:
                    result["chromium_installed"] = False
                    result["status"] = "missing_driver"
                    result["error_detail"] = "尚未下载 Chromium 浏览器内核"
                    result["install_guide"] = "playwright install chromium"
        except Exception as exc:
            is_env_err, guidance = _is_browser_env_missing(exc)
            result["status"] = "missing_driver" if is_env_err else "error"
            result["error_detail"] = str(exc)
            result["install_guide"] = guidance or "playwright install chromium"

        return result

    async def open(self, *, session_id: str, profile_path: str, url: str) -> BrowserPageInfo:
        self._url_validator(url)
        playwright = await self._ensure_playwright()
        os.makedirs(profile_path, mode=0o700, exist_ok=True)
        try:
            os.chmod(profile_path, 0o700)
        except OSError:
            # 某些受限文件系统不支持 chmod，仍由 Worker 的路径隔离继续保护。
            pass

        common_context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": DEFAULT_BROWSER_USER_AGENT,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }

        try:
            chromium = playwright.chromium
            launch_persistent_context = getattr(chromium, "launch_persistent_context", None)
            if launch_persistent_context is not None:
                context = await launch_persistent_context(
                    user_data_dir=profile_path,
                    headless=True,
                    args=CHROMIUM_LAUNCH_ARGS,
                    **common_context_kwargs,
                )
                browser = None
            else:
                browser = await chromium.launch(
                    headless=True,
                    args=CHROMIUM_LAUNCH_ARGS,
                )
                context = await browser.new_context(**common_context_kwargs)
        except Exception as exc:
            is_env_err, guidance = _is_browser_env_missing(exc)
            if is_env_err:
                raise BrowserEnvironmentError(guidance) from exc
            raise

        add_init_script = getattr(context, "add_init_script", None)
        if callable(add_init_script):
            await _maybe_await(add_init_script(STEALTH_INIT_SCRIPT))

        await self._install_request_guard(context)
        page = await context.new_page()
        handle = _BrowserHandle(context=context, page=page, browser=browser)
        on_event = getattr(context, "on", None)
        if callable(on_event):
            try:
                on_event("page", lambda new_page: self._track_new_page(handle, new_page))
            except Exception:
                pass
        self._install_status_tracking(handle, page)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            final_url = str(getattr(page, "url", url) or url)
            self._url_validator(final_url)
        except Exception:
            await _maybe_await(context.close())
            if browser is not None:
                await _maybe_await(browser.close())
            raise

        handle.touch()
        self._handles[session_id] = handle
        self._snapshots.pop(session_id, None)
        return await self._page_info(page)

    def _track_new_page(self, handle: _BrowserHandle, new_page: Any) -> None:
        self._install_status_tracking(handle, new_page)

    async def _page_info(self, page: Any, *, focused_input: bool = False) -> BrowserPageInfo:
        url = str(getattr(page, "url", "") or "")
        title = str(await _maybe_await(page.title()))
        return BrowserPageInfo(url=url, title=title, focused_input=focused_input)

    async def current_page_info(self, session_id: str) -> BrowserPageInfo:
        """读取当前页面信息，供恢复已有会话时避免无意义的重复导航。"""
        return await self._page_info(self._handle(session_id).page)

    def _install_status_tracking(self, handle: _BrowserHandle, page: Any) -> None:
        """为页面挂载响应状态监听，用于快照中的 page_status 错误检测。"""
        key = id(page)
        handle.page_status.setdefault(key, "ready")
        on_event = getattr(page, "on", None)
        if not callable(on_event):
            return
        try:
            async def _record_network_log(response: Any) -> None:
                try:
                    req = getattr(response, "request", None)
                    url = str(getattr(response, "url", "") or "")
                    method = str(getattr(req, "method", "GET") or "GET") if req else "GET"
                    status = int(getattr(response, "status", lambda: 0)() or 0)
                    headers = getattr(response, "headers", {}) or {}
                    content_type = headers.get("content-type", "")
                    resource_type = str(getattr(req, "resource_type", lambda: "")() or "") if req else ""
                    if resource_type in {"fetch", "xhr", "document"} or "json" in content_type or "text" in content_type:
                        log_item = {
                            "url": url,
                            "method": method,
                            "status": status,
                            "resource_type": resource_type,
                            "content_type": content_type,
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                        }
                        handle.network_logs.append(log_item)
                        if len(handle.network_logs) > 100:
                            handle.network_logs.pop(0)
                except Exception:
                    pass

            def _on_response(response: Any) -> None:
                try:
                    status = int(getattr(response, "status", lambda: 0)() or 0)
                except (TypeError, ValueError):
                    status = 0
                handle.page_last_status[key] = status
                if status >= 400:
                    handle.page_status[key] = "error"
                else:
                    handle.page_status[key] = "ready"
                try:
                    asyncio.create_task(_record_network_log(response))
                except Exception:
                    pass

            def _on_request_failed(_request: Any) -> None:
                handle.page_status[key] = "error"

            def _on_page_close() -> None:
                handle.page_status.pop(key, None)
                handle.page_last_status.pop(key, None)

            on_event("response", _on_response)
            on_event("requestfailed", _on_request_failed)
            on_event("close", _on_page_close)
        except Exception:
            # 监听挂载失败不影响快照生成，回退为 ready。
            pass

    def _page_status(self, handle: _BrowserHandle, page: Any) -> str:
        key = id(page)
        status = handle.page_status.get(key, "ready")
        if status == "error" and handle.page_last_status.get(key) == 404:
            return "not_found"
        return status

    def _tab_id(self, handle: _BrowserHandle, page: Any) -> str:
        key = id(page)
        existing = handle.tab_ids.get(key)
        if existing:
            return existing
        tab_id = f"tab-{handle.next_tab_number}"
        handle.next_tab_number += 1
        handle.tab_ids[key] = tab_id
        return tab_id

    def _pages(self, handle: _BrowserHandle) -> list[Any]:
        pages = list(getattr(handle.context, "pages", []) or [])
        if handle.page not in pages:
            pages.append(handle.page)
        for page in pages:
            self._tab_id(handle, page)
        return pages

    async def list_tabs(self, session_id: str) -> list[BrowserTab]:
        handle = self._handle(session_id)
        tabs: list[BrowserTab] = []
        for page in self._pages(handle):
            tabs.append(
                BrowserTab(
                    tab_id=self._tab_id(handle, page),
                    url=str(getattr(page, "url", "") or ""),
                    title=str(await _maybe_await(page.title())),
                    active=page is handle.page,
                )
            )
        return tabs

    async def switch_tab(self, session_id: str, tab_id: str) -> BrowserPageInfo:
        handle = self._handle(session_id)
        page = next(
            (candidate for candidate in self._pages(handle) if self._tab_id(handle, candidate) == tab_id),
            None,
        )
        if page is None:
            raise BrowserTargetStale("浏览器标签页不存在，请先获取标签页列表")
        handle.page = page
        self._snapshots.pop(session_id, None)
        return await self._page_info(page)

    async def close_tab(self, session_id: str, tab_id: str) -> BrowserPageInfo:
        handle = self._handle(session_id)
        pages = self._pages(handle)
        page = next(
            (candidate for candidate in pages if self._tab_id(handle, candidate) == tab_id),
            None,
        )
        if page is None:
            raise BrowserTargetStale("浏览器标签页不存在，请先获取标签页列表")
        if len(pages) <= 1:
            raise BrowserTargetStale("浏览器至少需要保留一个标签页")
        await _maybe_await(page.close())
        handle.tab_ids.pop(id(page), None)
        remaining = [candidate for candidate in pages if candidate is not page]
        if handle.page is page:
            handle.page = remaining[-1]
        self._snapshots.pop(session_id, None)
        return await self._page_info(handle.page)

    async def close_other_tabs(self, session_id: str, tab_id: str) -> BrowserPageInfo:
        handle = self._handle(session_id)
        pages = self._pages(handle)
        target_page = next(
            (candidate for candidate in pages if self._tab_id(handle, candidate) == tab_id),
            None,
        )
        if target_page is None:
            raise BrowserTargetStale("浏览器标签页不存在，请先获取标签页列表")
        for page in pages:
            if page is not target_page:
                try:
                    await _maybe_await(page.close())
                except Exception:
                    pass
                handle.tab_ids.pop(id(page), None)
        handle.page = target_page
        self._snapshots.pop(session_id, None)
        return await self._page_info(target_page)

    async def close_tabs_to_right(self, session_id: str, tab_id: str) -> BrowserPageInfo:
        handle = self._handle(session_id)
        pages = self._pages(handle)
        target_idx = next(
            (idx for idx, candidate in enumerate(pages) if self._tab_id(handle, candidate) == tab_id),
            -1,
        )
        if target_idx < 0:
            raise BrowserTargetStale("浏览器标签页不存在，请先获取标签页列表")
        pages_to_close = pages[target_idx + 1 :]
        for page in pages_to_close:
            try:
                await _maybe_await(page.close())
            except Exception:
                pass
            handle.tab_ids.pop(id(page), None)
        remaining = [p for p in pages if p not in pages_to_close]
        if handle.page in pages_to_close:
            handle.page = remaining[-1] if remaining else pages[target_idx]
        self._snapshots.pop(session_id, None)
        return await self._page_info(handle.page)

    async def close_all_tabs(self, session_id: str, default_url: str = "https://www.baidu.com") -> BrowserPageInfo:
        handle = self._handle(session_id)
        old_pages = self._pages(handle)
        new_page = await _maybe_await(handle.context.new_page())
        handle.page = new_page
        self._tab_id(handle, new_page)
        target_url = str(default_url or "").strip() or "https://www.baidu.com"
        self._url_validator(target_url)
        await _maybe_await(new_page.goto(target_url, wait_until="domcontentloaded", timeout=25000))
        for page in old_pages:
            try:
                await _maybe_await(page.close())
            except Exception:
                pass
            handle.tab_ids.pop(id(page), None)
        self._snapshots.pop(session_id, None)
        return await self._page_info(new_page)

    async def new_tab(self, session_id: str, url: str = "https://www.baidu.com") -> BrowserPageInfo:
        handle = self._handle(session_id)
        page = await _maybe_await(handle.context.new_page())
        handle.page = page
        self._tab_id(handle, page)
        target_url = str(url or "").strip() or "https://www.baidu.com"
        self._url_validator(target_url)
        await _maybe_await(page.goto(target_url, wait_until="domcontentloaded", timeout=25000))
        self._snapshots.pop(session_id, None)
        return await self._page_info(page)

    async def _has_focused_input(self, page: Any, *, x: float, y: float) -> bool:
        evaluate = getattr(page, "evaluate", None)
        if callable(evaluate):
            try:
                if bool(
                    await _maybe_await(
                        evaluate(
                            """
                            ({x, y}) => {
                              const isTextInput = (element) => {
                                const candidate = element?.closest?.('textarea, [contenteditable="true"], [role="textbox"], input');
                                if (!candidate) return false;
                                if (candidate.matches('textarea, [contenteditable="true"], [role="textbox"]')) return true;
                                return !['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image', 'hidden']
                                  .includes((candidate.type || 'text').toLowerCase());
                              };
                              const target = document.elementFromPoint(x, y);
                              const active = document.activeElement;
                              return isTextInput(active) && (isTextInput(target) || target === active || !target);
                            }
                            """,
                            {"x": x, "y": y},
                        )
                    )
                ):
                    return True
            except Exception:
                pass

        # 点击 iframe 内的输入框时，主文档的 activeElement 通常只是 iframe 节点；
        # 对可访问的 frame 再检查一次当前焦点，跨域 frame 则由 Playwright 在 frame
        # 上执行，避免在页面中注入脚本或绕过站点权限。
        for frame in list(getattr(page, "frames", []) or []):
            frame_evaluate = getattr(frame, "evaluate", None)
            if not callable(frame_evaluate):
                continue
            try:
                if bool(
                    await _maybe_await(
                        frame_evaluate(
                            """
                            () => {
                              const active = document.activeElement;
                              if (!active) return false;
                              const candidate = active.closest?.('textarea, [contenteditable="true"], [role="textbox"], input');
                              if (!candidate) return false;
                              if (candidate.matches('textarea, [contenteditable="true"], [role="textbox"]')) return true;
                              return !['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image', 'hidden']
                                .includes((candidate.type || 'text').toLowerCase());
                            }
                            """
                        )
                    )
                ):
                    return True
            except Exception:
                continue
        return False

    async def _detect_captcha(self, page: Any) -> tuple[bool, str | None]:
        evaluate = getattr(page, "evaluate", None)
        if not callable(evaluate):
            return False, None
        try:
            result = await _maybe_await(
                evaluate(
                    """
                    () => {
                      const bodyText = (document.body?.innerText || '').toLowerCase();
                      const textMarkers = [
                        '验证码', '安全验证', '人机验证', '滑块验证',
                        'captcha', 'verify-human'
                      ];
                      const hasTextMarker = textMarkers.some((marker) => bodyText.includes(marker));
                      const hasChallengeNode = Array.from(document.querySelectorAll('[id], [class]')).some((node) => {
                        const value = `${node.id || ''} ${typeof node.className === 'string' ? node.className : ''}`.toLowerCase();
                        return /captcha|geetest|nc_1|slider-verify|verify-slider/.test(value);
                      });
                      const hasChallengeFrame = Array.from(document.querySelectorAll('iframe')).some((frame) =>
                        /captcha|geetest|nc_1|slider-verify|verify-slider/.test((frame.getAttribute('src') || '').toLowerCase())
                      );
                      return {
                        matched: hasTextMarker || hasChallengeNode || hasChallengeFrame,
                        reason: hasTextMarker ? '页面要求人工完成安全验证' : '页面出现验证码控件'
                      };
                    }
                    """
                )
            )
            if not isinstance(result, dict) or not result.get("matched"):
                return False, None
            return True, str(result.get("reason") or "页面要求人工完成安全验证")
        except Exception:
            return False, None

    async def _network_idle_or_timeout(self, page: Any, window: float) -> bool:
        """在 window 秒内等待网络接近空闲，返回是否空闲。

        Playwright 的 wait_for_load_state('networkidle') 会因长轮询/高频请求而长时间不返回，
        因此这里仅在 window 内多次短等待；每次都命中即认为已空闲，避免永久阻塞。
        """
        deadline = asyncio.get_running_loop().time() + window
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            wait_state = getattr(page, "wait_for_load_state", None)
            ready = False
            if callable(wait_state):
                try:
                    await _maybe_await(wait_state("networkidle", timeout=min(250, max(50, int(remaining * 1000)))))
                    ready = True
                except Exception:
                    ready = False
            if ready:
                return True

    def _handle(self, session_id: str) -> _BrowserHandle:
        try:
            handle = self._handles[session_id]
            handle.touch()
            return handle
        except KeyError as exc:
            raise RuntimeError(f"浏览器会话不存在或已关闭：{session_id}") from exc

    async def clean_idle_sessions(self, max_idle_seconds: float = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS) -> list[str]:
        """清理超过指定闲置时间的 Chromium 会话，释放内存与无用进程。"""
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return []
        expired_sessions: list[str] = []
        for session_id, handle in list(self._handles.items()):
            if handle.last_active_at > 0 and (now - handle.last_active_at) >= max_idle_seconds:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            try:
                await self.close(session_id)
            except Exception:
                pass
        return expired_sessions

    async def _install_request_guard(self, context: Any) -> None:
        route_method = getattr(context, "route", None)
        if not callable(route_method):
            return

        async def handle_route(route: Any) -> None:
            request = getattr(route, "request", None)
            request_url = str(getattr(request, "url", "") or "")
            if request_url:
                try:
                    self._request_validator(request_url)
                except Exception:
                    await _maybe_await(route.abort())
                    return
            await _maybe_await(route.continue_())

        await _maybe_await(route_method("**/*", handle_route))

    async def snapshot(self, session_id: str) -> BrowserSnapshot:
        for attempt in range(2):
            try:
                return await self._snapshot_once(session_id)
            except Exception as exc:
                if attempt == 0 and _is_navigation_context_error(exc):
                    await asyncio.sleep(0.05)
                    continue
                raise

    async def _snapshot_once(self, session_id: str) -> BrowserSnapshot:
        handle = self._handle(session_id)
        info = await self._page_info(handle.page)
        captcha_detected, _captcha_reason = await self._detect_captcha(handle.page)
        page_context = await self._snapshot_page_context(handle.page)
        # 主文档元素抓取走原 page.locator("body *") 路径（CSS 选择器默认穿透 open shadow DOM，
        # 保证 _node_index 能通过 nth() 稳定重定位）；返回值按帧分组：main 帧 frame_index=None。
        locator = handle.page.locator(SNAPSHOT_NODE_SELECTOR)
        raw_elements = await locator.evaluate_all(SNAPSHOT_JS % SNAPSHOT_MAX_ELEMENTS)

        # 聚合跨帧候选元素：主帧 (frame_index=None) + 每个 iframe 子帧。
        # 所有 CSS 定位默认穿透 open shadow DOM，故此处对 Shadow DOM 无需特殊处理。
        frame_groups: list[tuple[Any, list[dict[str, Any]]]] = []
        if raw_elements:
            frame_groups.append((None, raw_elements))
        for frame_index, frame in enumerate(list(getattr(handle.page, "frames", []) or [])):
            if frame_index == 0:
                continue  # 主帧已在上方收集
            frame_locator = getattr(frame, "locator", None)
            if not callable(frame_locator):
                continue
            try:
                sub_raw = await _maybe_await(
                    frame_locator(SNAPSHOT_NODE_SELECTOR).evaluate_all(
                        SNAPSHOT_JS % SNAPSHOT_MAX_ELEMENTS
                    )
                )
            except Exception:
                continue  # 子帧可能已销毁或跨域受限，忽略
            if sub_raw:
                frame_groups.append((frame_index, sub_raw))

        snapshot_id = uuid.uuid4().hex
        elements: list[BrowserElement] = []
        target_map: dict[str, dict[str, Any]] = {}
        for group_index, (frame_index, items) in enumerate(frame_groups):
            for inner_index, raw in enumerate(items or [], start=1):
                index = group_index * SNAPSHOT_MAX_ELEMENTS + inner_index
                item = dict(raw or {})
                ref = f"e{index}"
                if frame_index is not None:
                    item["_frame_index"] = frame_index
                sensitive = bool(item.get("sensitive", False))
                name = item.get("name")
                if sensitive and name == item.get("value"):
                    name = None
                element = BrowserElement(
                    ref=ref,
                    tag=item.get("tag"),
                    role=item.get("role"),
                    name=name,
                    value=None if sensitive else item.get("value"),
                    disabled=bool(item.get("disabled", False)),
                    sensitive=sensitive,
                    bbox=item.get("bbox"),
                )
                elements.append(element)
                target_map[ref] = item
        if session_id not in self._snapshots:
            self._snapshots[session_id] = {}
        self._snapshots[session_id][snapshot_id] = target_map
        if len(self._snapshots[session_id]) > 5:
            oldest_key = next(iter(self._snapshots[session_id]))
            self._snapshots[session_id].pop(oldest_key, None)

        screenshot_ref = await self._capture_screenshot(handle.page, session_id, snapshot_id)
        return BrowserSnapshot(
            session_id=session_id,
            snapshot_id=snapshot_id,
            tab_id=self._tab_id(handle, handle.page),
            url=info.url,
            title=info.title,
            screenshot_ref=screenshot_ref,
            elements=elements,
            page_state="captcha" if captcha_detected else "ready",
            page_status=self._page_status(handle, handle.page),
            scroll_x=page_context.get("scroll_x", 0),
            scroll_y=page_context.get("scroll_y", 0),
            can_go_back=bool(page_context.get("can_go_back", False)),
            can_go_forward=bool(page_context.get("can_go_forward", False)),
            viewport_width=page_context.get("viewport_width"),
            viewport_height=page_context.get("viewport_height"),
            document_width=page_context.get("document_width"),
            document_height=page_context.get("document_height"),
            page_text=page_context.get("page_text", ""),
            visible_text=page_context.get("visible_text", ""),
        )

    async def _snapshot_page_context(self, page: Any) -> dict[str, Any]:
        evaluate = getattr(page, "evaluate", None)
        if not callable(evaluate):
            return {}
        try:
            result = await _maybe_await(
                evaluate(
                    r"""
                    () => {
                      const root = document.documentElement;
                      const body = document.body;
                      const cleanText = (value) => String(value || '')
                        .replace(/\u00a0/g, ' ')
                        .replace(/[ \t]+/g, ' ')
                        .replace(/\s*\n\s*/g, '\n')
                        .replace(/\n{3,}/g, '\n\n')
                        .trim();
                      const visibleText = () => {
                        const viewportWidth = window.innerWidth || 0;
                        const viewportHeight = window.innerHeight || 0;
                        const lines = [];
                        for (const node of Array.from(body?.querySelectorAll('*') || [])) {
                          const style = window.getComputedStyle(node);
                          const rect = node.getBoundingClientRect();
                          const hasDirectText = Array.from(node.childNodes || [])
                            .some((child) => child.nodeType === Node.TEXT_NODE && String(child.textContent || '').trim());
                          if (!hasDirectText || style.display === 'none' || style.visibility === 'hidden'
                            || rect.width <= 0 || rect.height <= 0 || rect.right <= 0 || rect.bottom <= 0
                            || rect.left >= viewportWidth || rect.top >= viewportHeight) continue;
                          const text = cleanText(node.textContent || '');
                          if (text) lines.push(text);
                        }
                        return Array.from(new Set(lines)).join('\n').slice(0, %d);
                      };
                      let canGoBack = false;
                      let canGoForward = false;
                      try {
                        if (window.navigation) {
                          canGoBack = Boolean(window.navigation.canGoBack);
                          canGoForward = Boolean(window.navigation.canGoForward);
                        } else if (window.history) {
                          canGoBack = (window.history.length || 0) > 1;
                        }
                      } catch (e) {}
                      return {
                        scroll_x: Math.round(window.scrollX || 0),
                        scroll_y: Math.round(window.scrollY || 0),
                        can_go_back: canGoBack,
                        can_go_forward: canGoForward,
                        viewport_width: Math.round(window.innerWidth || 0),
                        viewport_height: Math.round(window.innerHeight || 0),
                        document_width: Math.max(root?.scrollWidth || 0, body?.scrollWidth || 0),
                        document_height: Math.max(root?.scrollHeight || 0, body?.scrollHeight || 0),
                        page_text: cleanText(body?.innerText || '').slice(0, %d),
                        visible_text: visibleText(),
                      };
                    }
                    """
                    % (SNAPSHOT_VISIBLE_TEXT_LIMIT, SNAPSHOT_PAGE_TEXT_LIMIT)
                )
            )
        except Exception:
            return {}
        if not isinstance(result, dict):
            return {}
        context: dict[str, Any] = {}
        for key in ("scroll_x", "scroll_y"):
            try:
                context[key] = float(result.get(key) or 0)
            except (TypeError, ValueError):
                context[key] = 0
        context["can_go_back"] = bool(result.get("can_go_back", False))
        context["can_go_forward"] = bool(result.get("can_go_forward", False))
        for key in ("viewport_width", "viewport_height", "document_width", "document_height"):
            try:
                value = result.get(key)
                context[key] = int(value) if value is not None else None
            except (TypeError, ValueError):
                context[key] = None
        context["page_text"] = str(result.get("page_text") or "")[:SNAPSHOT_PAGE_TEXT_LIMIT]
        context["visible_text"] = str(result.get("visible_text") or "")[:SNAPSHOT_VISIBLE_TEXT_LIMIT]
        return context

    def has_session(self, session_id: str) -> bool:
        return session_id in self._handles

    async def navigate(self, session_id: str, url: str) -> BrowserPageInfo:
        handle = self._handle(session_id)
        self._url_validator(url)
        previous_url = str(getattr(handle.page, "url", "") or "")
        try:
            await handle.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as exc:
            if not _is_navigation_timeout(exc):
                raise
            final_url = str(getattr(handle.page, "url", "") or "")
            if not final_url or final_url == previous_url:
                raise
            self._url_validator(final_url)
            self._snapshots.pop(session_id, None)
            return await self._page_info(handle.page)
        final_url = str(getattr(handle.page, "url", url) or url)
        self._url_validator(final_url)
        self._snapshots.pop(session_id, None)
        return await self._page_info(handle.page)

    async def scroll(self, session_id: str, *, direction: str, amount: int) -> BrowserSnapshot:
        """滚动当前页面并返回滚动后的完整语义快照。"""
        handle = self._handle(session_id)
        normalized_direction = str(direction or "down").strip().lower()
        if normalized_direction not in {"up", "down", "top", "bottom"}:
            raise ValueError("滚动方向必须是 up、down、top 或 bottom")
        normalized_amount = max(100, min(abs(int(amount or 640)), 2000))

        if normalized_direction == "top":
            await _maybe_await(handle.page.evaluate("() => window.scrollTo(0, 0)"))
        elif normalized_direction == "bottom":
            await _maybe_await(
                handle.page.evaluate(
                    "() => window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))"
                )
            )
        else:
            delta_y = normalized_amount if normalized_direction == "down" else -normalized_amount
            mouse = getattr(handle.page, "mouse", None)
            if mouse and hasattr(mouse, "wheel"):
                await _maybe_await(mouse.wheel(0, delta_y))
            else:
                await _maybe_await(handle.page.evaluate(f"() => window.scrollBy(0, {delta_y})"))

        wait_for_timeout = getattr(handle.page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            await _maybe_await(wait_for_timeout(SNAPSHOT_SETTLE_DELAY_MS))
        else:
            await asyncio.sleep(0)
        self._snapshots.pop(session_id, None)
        return await self.snapshot(session_id)

    async def wait_for(
        self,
        session_id: str,
        *,
        condition: str = "text",
        value: str = "",
        target_ref: str | None = None,
        snapshot: BrowserSnapshot | None = None,
        timeout_ms: int = 10000,
        **kwargs: Any,
    ) -> BrowserSnapshot:
        """等待受限页面条件满足后返回新快照，不执行任意页面脚本。"""
        handle = self._handle(session_id)
        normalized_condition = str(condition or "text").strip().lower()
        if normalized_condition == "ready":
            normalized_condition = "network_idle"
        if normalized_condition not in {"text", "url", "target", "page_state", "element", "network_idle"}:
            raise ValueError("等待条件必须是 text、url、target、page_state、element 或 network_idle")
        timeout = max(100, min(int(timeout_ms or 10000), 30000))
        expected = str(value or "").strip()
        if normalized_condition != "network_idle" and not expected and not target_ref:
            raise ValueError("等待条件值或目标元素引用不能为空")
        deadline = asyncio.get_running_loop().time() + (timeout / 1000)
        while True:
            current_url = str(getattr(handle.page, "url", "") or "")
            if normalized_condition == "url" and expected in current_url:
                return await self.snapshot(session_id)
            if normalized_condition in {"text", "target"} and expected:
                context = await self._snapshot_page_context(handle.page)
                haystack = context.get("visible_text" if normalized_condition == "target" else "page_text", "")
                if expected.casefold() in str(haystack).casefold():
                    return await self.snapshot(session_id)
            if normalized_condition == "page_state":
                captcha, _reason = await self._detect_captcha(handle.page)
                current_state = "captcha" if captcha else "ready"
                if expected.casefold() == current_state:
                    return await self.snapshot(session_id)
            if normalized_condition in {"element", "target"} and target_ref:
                try:
                    if snapshot is not None:
                        target = self._target(session_id, snapshot, target_ref)
                        locator = self._locator_for(handle.page, target)
                    else:
                        locator = handle.page.locator(f"[data-ref='{target_ref}'], #{target_ref}")
                    is_visible = getattr(locator, "is_visible", None)
                    visible = bool(is_visible and await _maybe_await(is_visible()))
                except Exception:
                    visible = False
                if visible:
                    return await self.snapshot(session_id)
            if normalized_condition == "element" and expected:
                try:
                    locator = handle.page.locator(expected)
                    is_visible = getattr(locator, "is_visible", None)
                    visible = bool(is_visible and await _maybe_await(is_visible()))
                except Exception:
                    visible = False
                if visible:
                    return await self.snapshot(session_id)
            remaining_sec = deadline - asyncio.get_running_loop().time()
            if remaining_sec <= 0:
                cond_desc = f"条件[{normalized_condition}], 预期[{expected or target_ref or '就绪'}]"
                raise BrowserWaitTimeout(f"在 {int(round(timeout / 1000))} 秒内页面等待未满足：{cond_desc}")
            # network_idle：等待高频加载/轮询请求逐渐平息，而不是瞬时空闲。
            if normalized_condition == "network_idle":
                idle_ready = await self._network_idle_or_timeout(handle.page, min(2.0, max(0.1, remaining_sec)))
                if idle_ready:
                    return await self.snapshot(session_id)
                remaining_sec = deadline - asyncio.get_running_loop().time()
                if remaining_sec <= 0:
                    raise BrowserWaitTimeout(f"在 {int(round(timeout / 1000))} 秒内网络未完全空闲就绪")
            wait_for_timeout = getattr(handle.page, "wait_for_timeout", None)
            if callable(wait_for_timeout):
                await _maybe_await(wait_for_timeout(min(250, max(1, int(remaining_sec * 1000)))))
            else:
                await asyncio.sleep(min(0.25, remaining_sec))

    async def read_visible(self, session_id: str) -> dict[str, Any]:
        handle = self._handle(session_id)
        page_context = await self._snapshot_page_context(handle.page)
        info = await self._page_info(handle.page)
        return {
            "session_id": session_id,
            "url": info.url,
            "title": info.title,
            "scroll_x": page_context.get("scroll_x", 0),
            "scroll_y": page_context.get("scroll_y", 0),
            "text": page_context.get("visible_text", ""),
        }

    async def press(
        self,
        session_id: str,
        *,
        target_ref: str | None,
        key: str,
        snapshot: BrowserSnapshot | None,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        normalized_key = str(key or "").strip()[:40]
        if not normalized_key:
            raise ValueError("键盘操作缺少 key")
        if target_ref:
            if snapshot is None:
                raise BrowserTargetStale("按目标发送键盘操作需要页面快照")
            target = self._target(session_id, snapshot, target_ref)
            locator = self._locator_for(handle.page, target)
            await self._validate_inferred_target(locator, target)
            await locator.press(normalized_key)
        else:
            await _maybe_await(handle.page.keyboard.press(normalized_key))
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(session_id=session_id, action="press", url=info.url, title=info.title)

    async def select_option(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot: BrowserSnapshot,
        value: str | None,
        label: str | None = None,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        target = self._target(session_id, snapshot, target_ref)
        locator = self._locator_for(handle.page, target)
        await self._validate_inferred_target(locator, target)
        select_option = getattr(locator, "select_option", None)
        if not callable(select_option):
            raise BrowserTargetStale("当前目标不是可选下拉框，请重新获取快照")
        options: dict[str, str] = {}
        if value:
            options["value"] = value
        elif label:
            options["label"] = label
        else:
            raise ValueError("下拉选择需要 value 或 label")
        try:
            await _maybe_await(select_option(**options))
        except Exception as exc:
            option_name = label or value
            try:
                await _maybe_await(locator.click())
                option_locator = handle.page.get_by_role("option", name=option_name, exact=True)
                await _maybe_await(option_locator.click())
            except Exception as fallback_exc:
                raise BrowserTargetStale("下拉目标无法选择该选项，请刷新页面快照") from fallback_exc
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(session_id=session_id, action="select_option", url=info.url, title=info.title)

    async def hover(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot: BrowserSnapshot,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        target = self._target(session_id, snapshot, target_ref)
        locator = self._locator_for(handle.page, target)
        await self._validate_inferred_target(locator, target)
        try:
            bounding_box = getattr(locator, "bounding_box", None)
            box = await _maybe_await(bounding_box()) if callable(bounding_box) else None
            if box and isinstance(box, dict) and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await self._human_smooth_mouse_move(handle.page, target_x, target_y, steps=random.randint(6, 12))
                await asyncio.sleep(random.uniform(0.04, 0.08))
        except Exception:
            pass
        await _maybe_await(locator.hover())
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(session_id=session_id, action="hover", url=info.url, title=info.title)

    async def drag(
        self,
        session_id: str,
        *,
        source_ref: str,
        target_ref: str,
        snapshot: BrowserSnapshot,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        source = self._target(session_id, snapshot, source_ref)
        target = self._target(session_id, snapshot, target_ref)
        source_locator = self._locator_for(handle.page, source)
        target_locator = self._locator_for(handle.page, target)
        await self._validate_inferred_target(source_locator, source)
        await self._validate_inferred_target(target_locator, target)
        await self._ensure_actionable(source_locator, what="拖拽源元素")
        await self._ensure_actionable(target_locator, what="拖拽目标元素")
        await _maybe_await(source_locator.drag_to(target_locator))
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(session_id=session_id, action="drag", url=info.url, title=info.title)

    async def slider_drag(
        self,
        session_id: str,
        *,
        source_ref: str,
        snapshot: BrowserSnapshot,
        distance_px: int | None = None,
        gap_target_ref: str | None = None,
    ) -> BrowserToolResult:
        """拟人轨迹的坐标级滑块拖拽。

        两种触发方式（至少提供其一）：

        * ``distance_px``：直接指定要横向拖动的像素距离；
        * ``gap_target_ref``：指定缺口(落点)元素，方法会用滑块与缺口的
          bounding box 中心差值自动测量所需拖动距离（间距测量）。

        拖拽本身为非对抗性的拟人化：分段移动、可变步长、轻微 y 抖动与末尾
        过冲回弹，仅模拟人手拖动滑块的自然物理特性，不绕过任何反爬逻辑。
        """
        if distance_px is None and gap_target_ref is None:
            raise ValueError("slider_drag 需要提供 distance_px 或 gap_target_ref 之一")

        handle = self._handle(session_id)
        source = self._target(session_id, snapshot, source_ref)
        source_locator = self._locator_for(handle.page, source)
        await self._validate_inferred_target(source_locator, source)
        await self._ensure_actionable(source_locator, what="滑块元素")

        source_box = await _maybe_await(source_locator.bounding_box())
        if not source_box or source_box.get("width", 0) <= 0:
            raise BrowserTargetStale("无法定位滑块元素，请重新获取快照")
        sx = source_box["x"] + source_box["width"] / 2.0
        sy = source_box["y"] + source_box["height"] / 2.0

        measured: int | None = None
        if gap_target_ref is not None:
            gap = self._target(session_id, snapshot, gap_target_ref)
            gap_locator = self._locator_for(handle.page, gap)
            await self._validate_inferred_target(gap_locator, gap)
            await self._ensure_actionable(gap_locator, what="滑块缺口元素")
            gap_box = await _maybe_await(gap_locator.bounding_box())
            if not gap_box:
                raise BrowserTargetStale("无法定位滑块缺口元素，请重新获取快照")
            gx = gap_box["x"] + gap_box["width"] / 2.0
            measured = int(round(gx - sx))

        travel = int(distance_px) if distance_px is not None else measured
        if not travel or travel <= 0:
            raise ValueError("滑块拖动距离无效或非正数")

        points = self._slider_trajectory(sx, sy, travel)
        await _maybe_await(handle.page.mouse.move(sx, sy))
        await _maybe_await(handle.page.mouse.down())
        for x, y, delay in points:
            await _maybe_await(handle.page.mouse.move(x, y))
            await asyncio.sleep(delay)
        await _maybe_await(handle.page.mouse.up())

        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(
            session_id=session_id,
            action="slider_drag",
            url=info.url,
            title=info.title,
            data={
                "distance_px": travel,
                "steps": len(points),
                "measured_gap_px": measured,
            },
        )

    def _slider_trajectory(
        self, start_x: float, start_y: float, travel_px: int
    ) -> list[tuple[float, float, float]]:
        """生成拟人滑块拖拽轨迹点 ``(x, y, 段间延时秒)``。

        * 分段数随距离增长但封顶；
        * 水平位移采用 smoothstep 缓入缓出（起速慢、中段快、末端回落），叠加
          轻微 y 抖动，模拟人手拖动时先慢后快再减速收尾的自然物理特征；
        * 距离较大时末尾加入一次小的过冲回弹，模拟人手惯性后略微回位。
        """
        distance = float(abs(travel_px))
        segments = int(min(6 + distance / 40.0, 22))
        if segments < 1:
            segments = 1
        base_delay = random.uniform(0.012, 0.026)
        points: list[tuple[float, float, float]] = []
        for i in range(segments):
            t = (i + 1) / segments
            # smoothstep（三次 Hermite）：0→0 起、0.5→中、1→终，缓入缓出。
            ease = t * t * (3.0 - 2.0 * t)
            speed = math.sin(math.pi * t) ** 1.5
            x = start_x + travel_px * ease
            jitter = math.sin(i * 1.7 + random.uniform(0, 0.6)) * random.uniform(0.6, 1.6)
            delay = base_delay * random.uniform(0.7, 1.5) / (0.5 + speed)
            points.append((x, start_y + jitter, delay))
        if distance > 40:
            overshoot_target = start_x + travel_px * 1.012
            points.append((overshoot_target, start_y + random.uniform(-1, 1), base_delay * 1.2))
            points.append((start_x + travel_px, start_y, base_delay * 0.8))
        return points

    async def upload(
        self,
        session_id: str,
        *,
        target_ref: str,
        file_path: str,
        snapshot: BrowserSnapshot,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        source = Path(file_path).resolve()
        if not source.is_file():
            raise ValueError("待上传文件不存在")
        if source.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("上传文件大小不能超过 20MB")
        target = self._target(session_id, snapshot, target_ref)
        locator = self._locator_for(handle.page, target)
        await self._validate_inferred_target(locator, target)
        set_input_files = getattr(locator, "set_input_files", None)
        if not callable(set_input_files):
            raise BrowserTargetStale("当前目标不是文件输入控件，请重新获取快照")
        await _maybe_await(set_input_files(str(source)))
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(
            session_id=session_id,
            action="upload",
            url=info.url,
            title=info.title,
            data={"filename": source.name, "size": source.stat().st_size},
        )

    async def download(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot: BrowserSnapshot,
    ) -> BrowserToolResult:
        handle = self._handle(session_id)
        target = self._target(session_id, snapshot, target_ref)
        locator = self._locator_for(handle.page, target)
        await self._validate_inferred_target(locator, target)
        expect_download = getattr(handle.page, "expect_download", None)
        if not callable(expect_download):
            raise RuntimeError("当前浏览器不支持下载捕获")
        async with expect_download(timeout=25000) as download_info:
            await _maybe_await(locator.click())
        download = await _maybe_await(download_info.value)
        path_method = getattr(download, "path", None)
        download_path = await _maybe_await(path_method()) if callable(path_method) else None
        filename = str(getattr(download, "suggested_filename", "download") or "download")
        if not download_path:
            raise RuntimeError("浏览器下载文件不可用")
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(
            session_id=session_id,
            action="download",
            url=info.url,
            title=info.title,
            data={"download_path": str(download_path), "filename": Path(filename).name},
        )

    async def _history_action(self, session_id: str, action: str) -> BrowserToolResult:
        handle = self._handle(session_id)
        method = getattr(handle.page, action, None)
        if not callable(method):
            raise RuntimeError(f"浏览器不支持{action}操作")
        await _maybe_await(method(wait_until="domcontentloaded", timeout=25000))
        info = await self._page_info(handle.page)
        if info.url and not info.url.startswith("about:"):
            self._url_validator(info.url)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(session_id=session_id, action=action, url=info.url, title=info.title)

    async def go_back(self, session_id: str) -> BrowserToolResult:
        return await self._history_action(session_id, "go_back")

    async def go_forward(self, session_id: str) -> BrowserToolResult:
        return await self._history_action(session_id, "go_forward")

    async def reload(self, session_id: str) -> BrowserToolResult:
        return await self._history_action(session_id, "reload")

    async def export_pdf(
        self,
        session_id: str,
        *,
        filename: str | None = None,
        print_background: bool = True,
    ) -> BrowserToolResult:
        """将当前浏览器页面渲染并导出为 A4 格式矢量 PDF 文件。"""
        handle = self._handle(session_id)
        page_pdf = getattr(handle.page, "pdf", None)
        if not callable(page_pdf):
            raise RuntimeError("当前浏览器环境不支持 PDF 导出（需要 Chromium 内核）")
        session_dir = Path("data/generated") / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        pdf_name = str(filename or f"page_{uuid.uuid4().hex[:8]}.pdf").strip()
        if not pdf_name.endswith(".pdf"):
            pdf_name = f"{pdf_name}.pdf"
        target_path = session_dir / pdf_name
        pdf_bytes = await _maybe_await(
            page_pdf(
                format="A4",
                print_background=print_background,
                margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"},
            )
        )
        if not pdf_bytes:
            raise RuntimeError("PDF 生成失败，返回内容为空")
        target_path.write_bytes(pdf_bytes)
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="export_pdf",
            url=info.url,
            title=info.title,
            data={
                "pdf_path": str(target_path.resolve()),
                "filename": pdf_name,
                "size_bytes": len(pdf_bytes),
            },
        )

    async def extract_table(
        self,
        session_id: str,
        *,
        selector: str | None = None,
        max_rows: int = 50,
    ) -> BrowserToolResult:
        """结构化解析网页中的 table 或数据表格元素，输出 JSON 结构与 Markdown。"""
        handle = self._handle(session_id)
        extract_script = """
        ({ selector, maxRows }) => {
            const tableEl = selector ? document.querySelector(selector) : document.querySelector('table, [role="table"], [role="grid"]');
            if (!tableEl) return { found: false, message: "未在当前页面找到匹配的数据表格" };
            
            const headers = [];
            const headerCells = tableEl.querySelectorAll('thead th, thead td, th, [role="columnheader"]');
            if (headerCells.length > 0) {
                headerCells.forEach(th => headers.push(th.innerText.trim().replace(/\\s+/g, ' ')));
            }
            
            const rows = [];
            const bodyRows = tableEl.querySelectorAll('tbody tr, tr, [role="row"]');
            let count = 0;
            bodyRows.forEach(tr => {
                if (count >= maxRows) return;
                const cells = tr.querySelectorAll('td, th, [role="cell"], [role="gridcell"]');
                if (cells.length > 0) {
                    const rowData = Array.from(cells).map(c => c.innerText.trim().replace(/\\s+/g, ' '));
                    // 排除全空的表头重复行
                    if (rowData.some(v => v !== '')) {
                        rows.push(rowData);
                        count++;
                    }
                }
            });
            
            // 自动推导表头
            const finalHeaders = headers.length > 0 ? headers : (rows.length > 0 ? rows[0].map((_, i) => `Col_${i+1}`) : []);
            const dataRows = (headers.length === 0 && rows.length > 0) ? rows.slice(1) : rows;
            
            // 构建 Markdown 表格
            let markdown = '';
            if (finalHeaders.length > 0) {
                markdown += '| ' + finalHeaders.join(' | ') + ' |\\n';
                markdown += '| ' + finalHeaders.map(() => '---').join(' | ') + ' |\\n';
                dataRows.forEach(r => {
                    markdown += '| ' + finalHeaders.map((_, i) => (r[i] || '')).join(' | ') + ' |\\n';
                });
            }
            
            return {
                found: true,
                headers: finalHeaders,
                row_count: dataRows.length,
                rows: dataRows,
                markdown: markdown
            };
        }
        """
        result = await _maybe_await(
            handle.page.evaluate(extract_script, {"selector": selector, "maxRows": max(1, min(max_rows, 200))})
        )
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="extract_table",
            url=info.url,
            title=info.title,
            data=dict(result or {}),
        )

    async def handle_dialog(
        self,
        session_id: str,
        *,
        action: str = "accept",
        prompt_text: str | None = None,
    ) -> BrowserToolResult:
        """为当前会话设置原生 alert/confirm/prompt 对话框的自动应答监听器。"""
        handle = self._handle(session_id)
        action_type = "accept" if str(action).lower() in {"accept", "ok", "yes", "confirm"} else "dismiss"

        old_listener = handle.dialog_listener
        remove_listener = getattr(handle.page, "remove_listener", None)
        if old_listener is not None and callable(remove_listener):
            await _maybe_await(remove_listener("dialog", old_listener))

        async def _dialog_handler(dialog: Any) -> None:
            try:
                if action_type == "accept":
                    await _maybe_await(dialog.accept(prompt_text or ""))
                else:
                    await _maybe_await(dialog.dismiss())
            except Exception:
                pass

        def _dialog_listener(dialog: Any) -> None:
            asyncio.create_task(_dialog_handler(dialog))

        handle.dialog_listener = _dialog_listener
        handle.page.on("dialog", _dialog_listener)
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="handle_dialog",
            url=info.url,
            title=info.title,
            data={"configured_action": action_type, "prompt_text": prompt_text},
        )

    async def execute_js(
        self,
        session_id: str,
        *,
        script: str,
    ) -> BrowserToolResult:
        """在当前页面上下文执行受限 DOM JavaScript 脚本并捕获返回值。"""
        handle = self._handle(session_id)
        raw_script = validate_browser_script(script)
        try:
            eval_result = await asyncio.wait_for(
                _maybe_await(handle.page.evaluate(raw_script)),
                timeout=15,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("页面 JavaScript 执行超时") from exc
        except Exception as e:
            raise RuntimeError(f"页面 JavaScript 执行报错: {str(e)}") from e
        serialized_result = json.dumps(eval_result, ensure_ascii=False, default=str)
        if len(serialized_result) > 20000:
            eval_result = {
                "truncated": True,
                "size_chars": len(serialized_result),
                "preview": serialized_result[:20000],
            }
        info = await self._page_info(handle.page)
        self._snapshots.pop(session_id, None)
        return BrowserToolResult(
            session_id=session_id,
            action="execute_js",
            url=info.url,
            title=info.title,
            data={"result": eval_result},
        )

    async def check_auth(self, session_id: str) -> BrowserToolResult:
        """智能探测当前网页的登录与认证状态（检查 Cookie、LocalStorage 与页面登录标识）。"""
        handle = self._handle(session_id)
        cookies = await _maybe_await(handle.context.cookies())
        auth_detect_script = """
        () => {
            const hasLocalStorageTokens = Object.keys(localStorage).some(k => 
                /token|auth|session|jwt|user|login/i.test(k) && localStorage.getItem(k)
            );
            const hasSessionStorageTokens = Object.keys(sessionStorage).some(k => 
                /token|auth|session|jwt|user|login/i.test(k) && sessionStorage.getItem(k)
            );
            const bodyText = document.body ? document.body.innerText : '';
            const hasLogoutIndicator = /退出登录|退出|注销|个人中心|我的账号|Log out|Sign out/i.test(bodyText);
            const hasLoginIndicator = /立即登录|请先登录|Sign in|Log in|注册/i.test(bodyText) && !hasLogoutIndicator;
            
            return {
                has_local_storage_tokens: hasLocalStorageTokens,
                has_session_storage_tokens: hasSessionStorageTokens,
                has_logout_indicator: hasLogoutIndicator,
                has_login_indicator: hasLoginIndicator,
                page_url: window.location.href,
            };
        }
        """
        detect_res = await _maybe_await(handle.page.evaluate(auth_detect_script))
        has_logout_indicator = bool(detect_res.get("has_logout_indicator"))
        has_login_indicator = bool(detect_res.get("has_login_indicator"))
        has_storage_tokens = bool(
            detect_res.get("has_local_storage_tokens")
            or detect_res.get("has_session_storage_tokens")
        )
        if has_logout_indicator:
            is_logged_in = True
            auth_confidence = "high"
            auth_reason = "页面存在退出登录标识"
        elif has_login_indicator:
            is_logged_in = False
            auth_confidence = "high"
            auth_reason = "页面存在登录提示且未发现退出登录标识"
        elif has_storage_tokens:
            is_logged_in = None
            auth_confidence = "unknown"
            auth_reason = "检测到存储中的认证字段，但缺少明确的页面登录状态标识"
        elif cookies:
            is_logged_in = None
            auth_confidence = "unknown"
            auth_reason = "检测到 Cookie，但 Cookie 本身不足以证明登录有效"
        else:
            is_logged_in = None
            auth_confidence = "unknown"
            auth_reason = "未检测到明确的登录或未登录状态证据"

        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="check_auth",
            url=info.url,
            title=info.title,
            data={
                "is_authenticated": is_logged_in,
                "auth_confidence": auth_confidence,
                "auth_reason": auth_reason,
                "cookie_count": len(cookies),
                "indicators": detect_res,
            },
        )

    async def get_network_logs(
        self,
        session_id: str,
        *,
        filter_url: str | None = None,
        limit: int = 20,
    ) -> BrowserToolResult:
        """获取当前浏览器会话最近捕获的网络请求与 API 接口日志列表。"""
        handle = self._handle(session_id)
        logs = list(handle.network_logs)
        if filter_url:
            filter_lower = filter_url.lower()
            logs = [item for item in logs if filter_lower in item.get("url", "").lower()]
        max_limit = max(1, min(limit, 50))
        selected_logs = logs[-max_limit:]
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="get_network_logs",
            url=info.url,
            title=info.title,
            data={
                "total_captured": len(handle.network_logs),
                "matched_count": len(selected_logs),
                "logs": selected_logs,
            },
        )

    async def get_cookies(
        self,
        session_id: str,
        *,
        urls: list[str] | None = None,
    ) -> BrowserToolResult:
        """获取当前浏览器会话指定 URL 或当前域名的所有 Cookie 列表。"""
        handle = self._handle(session_id)
        if urls:
            cookies = await _maybe_await(handle.context.cookies(urls))
        else:
            cookies = await _maybe_await(handle.context.cookies())
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="get_cookies",
            url=info.url,
            title=info.title,
            data={
                "count": len(cookies),
                "cookies": redact_browser_cookies(cookies),
            },
        )

    async def set_cookies(
        self,
        session_id: str,
        *,
        cookies: list[dict[str, Any]],
    ) -> BrowserToolResult:
        """向当前浏览器上下文注入一组 Cookie，用于免密直登或会话恢复。"""
        handle = self._handle(session_id)
        if not isinstance(cookies, list) or len(cookies) == 0:
            raise ValueError("待注入的 cookies 列表不能为空")
        add_cookies = getattr(handle.context, "add_cookies", None)
        if not callable(add_cookies):
            raise RuntimeError("当前浏览器环境不支持 Cookie 注入")
        await _maybe_await(add_cookies(cookies))
        self._snapshots.pop(session_id, None)
        info = await self._page_info(handle.page)
        return BrowserToolResult(
            session_id=session_id,
            action="set_cookies",
            url=info.url,
            title=info.title,
            data={
                "injected_count": len(cookies),
                "status": "success",
            },
        )

    async def manual_input(self, session_id: str, *, event: str, payload: dict[str, Any]) -> BrowserPageInfo:
        """转发面板的人工接管输入；不接受任意 JS，只转发有限的浏览器输入事件。"""
        handle = self._handle(session_id)
        focused_input = False
        if event == "mouse_click":
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            pages_before = {
                id(page)
                for page in list(getattr(handle.context, "pages", []) or [])
            }
            await _maybe_await(handle.page.mouse.click(x, y))
            pages_after = list(getattr(handle.context, "pages", []) or [])
            new_pages = [page for page in pages_after if id(page) not in pages_before]
            if not new_pages:
                for _ in range(8):
                    await asyncio.sleep(0.05)
                    pages_after = list(getattr(handle.context, "pages", []) or [])
                    new_pages = [page for page in pages_after if id(page) not in pages_before]
                    if new_pages:
                        break
            if new_pages:
                handle.page = new_pages[-1]
                popup_url = str(getattr(handle.page, "url", "") or "")
                if popup_url:
                    self._request_validator(popup_url)
            else:
                wait_for_load_state = getattr(handle.page, "wait_for_load_state", None)
                if callable(wait_for_load_state):
                    try:
                        await _maybe_await(
                            wait_for_load_state(
                                "domcontentloaded",
                                timeout=MANUAL_CLICK_LOAD_TIMEOUT_MS,
                            )
                        )
                    except Exception:
                        # 点击可能触发长时间加载或下载；保留当前页面并继续刷新快照。
                        pass
            focused_input = await self._has_focused_input(handle.page, x=x, y=y)
        elif event == "key":
            key = str(payload.get("key", ""))[:40]
            if not key:
                raise ValueError("键盘事件缺少 key")
            await _maybe_await(handle.page.keyboard.press(key))
        elif event == "text":
            text = str(payload.get("text", ""))[:2000]
            insert_text = getattr(handle.page.keyboard, "insert_text", None)
            if callable(insert_text):
                await _maybe_await(insert_text(text))
            else:
                await _maybe_await(handle.page.keyboard.type(text))
        elif event == "mouse_down":
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            await _maybe_await(handle.page.mouse.move(x, y))
            await _maybe_await(handle.page.mouse.down())
        elif event == "mouse_move":
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            await _maybe_await(handle.page.mouse.move(x, y))
        elif event == "mouse_up":
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            await _maybe_await(handle.page.mouse.move(x, y))
            await _maybe_await(handle.page.mouse.up())
        elif event == "scroll":
            direction = str(payload.get("direction") or "").strip().lower()
            delta_y = float(payload.get("delta_y", 0))
            if direction == "top":
                try:
                    await _maybe_await(handle.page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })"))
                except Exception:
                    pass
            elif direction == "bottom":
                try:
                    await _maybe_await(
                        handle.page.evaluate(
                            "() => window.scrollTo({ top: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), behavior: 'instant' })"
                        )
                    )
                except Exception:
                    pass
            else:
                delta_y = max(-2000.0, min(delta_y, 2000.0))
                # 1. 移动鼠标到视口中心，保证聚焦在主要滚动区域
                viewport = getattr(handle.page, "viewport_size", None) or {"width": 1280, "height": 800}
                mid_x = float(viewport.get("width", 1280)) / 2
                mid_y = float(viewport.get("height", 800)) / 2
                mouse = getattr(handle.page, "mouse", None)
                if mouse:
                    try:
                        if hasattr(mouse, "move"):
                            await _maybe_await(mouse.move(mid_x, mid_y))
                        if hasattr(mouse, "wheel"):
                            await _maybe_await(mouse.wheel(0, delta_y))
                    except Exception:
                        pass
                # 2. window.scrollBy 兜底确保无论页面是否有复杂 wheel 拦截都能产生滚动
                try:
                    await _maybe_await(handle.page.evaluate(f"() => window.scrollBy({{ top: {delta_y}, behavior: 'instant' }})"))
                except Exception:
                    pass

            # 等待短暂渲染稳定时间，确保截图捕获到滚动后的真实画面
            wait_for_timeout = getattr(handle.page, "wait_for_timeout", None)
            if callable(wait_for_timeout):
                try:
                    await _maybe_await(wait_for_timeout(180))
                except Exception:
                    await asyncio.sleep(0.18)
            else:
                await asyncio.sleep(0.18)
        else:
            raise ValueError("不支持的浏览器人工输入事件")
        self._snapshots.pop(session_id, None)
        return await self._page_info(handle.page, focused_input=focused_input)

    async def _capture_screenshot(self, page: Any, session_id: str, snapshot_id: str) -> str | None:
        if not self._screenshot_dir:
            return None
        directory = Path(self._screenshot_dir)
        directory.mkdir(parents=True, exist_ok=True)
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80] or "session"
        path = directory / f"{safe_session_id}_{snapshot_id}.jpeg"
        try:
            await page.screenshot(path=str(path), type="jpeg", quality=75, full_page=False)
        except Exception:
            try:
                await page.screenshot(path=str(path), full_page=False)
            except Exception:
                return None
        return str(path)

    def _target(self, session_id: str, snapshot: BrowserSnapshot, target_ref: str) -> dict[str, Any]:
        handle = self._handle(session_id)
        if snapshot.tab_id and snapshot.tab_id != self._tab_id(handle, handle.page):
            raise BrowserTargetStale("浏览器页面已变化，请先重新获取页面快照")
        snapshot_map = self._snapshots.get(session_id, {}).get(snapshot.snapshot_id)
        if snapshot.session_id != session_id or not snapshot_map or target_ref not in snapshot_map:
            raise BrowserTargetStale("浏览器页面已变化，请先重新获取页面快照")
        return snapshot_map[target_ref]

    def _frame_for(self, page: Any, target: dict[str, Any]) -> Any:
        """根据 target 的 _frame_index 返回目标所在 frame，无法解析时回退主帧 page。"""
        frame_index = target.get("_frame_index")
        if isinstance(frame_index, int) and frame_index >= 1:
            frames = list(getattr(page, "frames", []) or [])
            if frame_index < len(frames):
                frame = frames[frame_index]
                if callable(getattr(frame, "locator", None)):
                    return frame
        return page

    def _locator_for(self, page: Any, target: dict[str, Any]) -> Any:
        container = self._frame_for(page, target)
        if target.get("_role_source") == "inferred":
            node_index = target.get("_node_index")
            if isinstance(node_index, int) and node_index >= 0:
                return container.locator(SNAPSHOT_NODE_SELECTOR).nth(node_index)
        role = str(target.get("role") or "").strip()
        name = str(target.get("name") or "").strip()
        if role and name:
            return container.get_by_role(role, name=name, exact=True)
        if role:
            return container.get_by_role(role)
        raise BrowserTargetStale("目标缺少可复现的语义定位信息，请刷新页面快照")

    async def _validate_inferred_target(self, locator: Any, target: dict[str, Any]) -> None:
        if target.get("_role_source") != "inferred":
            return
        evaluate = getattr(locator, "evaluate", None)
        if not callable(evaluate):
            return
        try:
            current = await _maybe_await(
                evaluate(
                    """
                    (node) => ({
                      name: node.getAttribute('aria-label')
                        || node.getAttribute('title')
                        || node.getAttribute('placeholder')
                        || node.innerText
                        || node.value
                        || ''
                    })
                    """
                )
            )
        except Exception as exc:
            raise BrowserTargetStale("浏览器页面已变化，请先重新获取页面快照") from exc
        expected_name = " ".join(str(target.get("name") or "").split()).casefold()
        current_name = " ".join(str((current or {}).get("name") or "").split()).casefold()
        if expected_name and current_name and expected_name not in current_name and current_name not in expected_name:
            raise BrowserTargetStale("浏览器页面已变化，请先重新获取页面快照")

    async def _ensure_actionable(
        self, locator: Any, *, timeout_ms: int = 5000, what: str = "目标元素"
    ) -> None:
        """动作前置保障：等待定位元素处于可见/可交互状态。

        仅当底层定位对象实现了 ``wait_for`` 时才执行（Playwright 定位器有该
        方法；测试用 fake/独立定位对象可能没有，此时静默跳过等待，仍保留后续
        动作自身的重试兜底），避免页面尚未渲染完成就落点导致点击/输入落到错误
        元素上。等待超时统一抛出 :class:`BrowserWaitTimeout`。
        """
        wait_for = getattr(locator, "wait_for", None)
        if not callable(wait_for):
            return
        try:
            await _maybe_await(wait_for(state="visible", timeout=timeout_ms))
        except (BrowserWaitTimeout, TimeoutError) as exc:
            raise BrowserWaitTimeout(
                f"等待{what}可交互超时，请刷新页面快照后重试"
            ) from exc
        except Exception as exc:  # WaitTimeoutError / TimeoutError 命名空间各异
            if exc.__class__.__name__ in {"TimeoutError", "TimeoutError"}:
                raise BrowserWaitTimeout(
                    f"等待{what}可交互超时，请刷新页面快照后重试"
                ) from exc
            raise

    async def _run_with_stale_recovery(
        self,
        session_id: str,
        action_fn,
        *,
        action_name: str,
        snapshot: BrowserSnapshot,
        retries: int = ACTION_RETRY_COUNT,
    ) -> BrowserToolResult:
        """动作失败恢复统一：stale/超时归一化 + 自动重试刷新快照。

        ``action_fn`` 是一个可重入的协程工厂，签名 ``(snapshot) -> BrowserToolResult``，
        内部必须基于传入的 ``snapshot`` 重新解析目标。首轮使用调用方提供的
        ``snapshot``；当执行抛出 ``BrowserWaitTimeout``、``TimeoutError`` 或 Playwright
        的 stale-element/timeout 错误时，本方法刷新快照、弹出旧目标映射后重新调用
        ``action_fn``，最多重试 ``retries`` 次；仍失败则抛出抽取自底层异常的归一化
        异常，供上层统一处理。目标解析类失败（``BrowserTargetStale`` 默认不可恢复，
        即 ref 已失配、推断 index 已漂移）则不做自动重试，立即原样上抛，由上层引导
        用户重取快照后再指定目标，避免重定位静默落到语义不同的元素上。
        """
        current_snapshot = snapshot
        attempt = 0
        last_error: Exception | None = None
        while attempt <= retries:
            try:
                result = await action_fn(current_snapshot)
                if result is not None:
                    return result
            except Exception as exc:
                if not self._is_recoverable_action_error(exc):
                    raise
                last_error = exc
            attempt += 1
            if attempt > retries:
                break
            # 刷新快照后重试，让目标以最新 DOM 重新解析。
            try:
                current_snapshot = await self.snapshot(session_id)
            except Exception:
                break  # 快照刷新本身就失败，直接放弃重试
        if isinstance(last_error, (BrowserTargetStale, BrowserWaitTimeout)):
            raise last_error
        return self._raise_normalized_action_error(last_error, action_name)

    def _is_recoverable_action_error(self, exc: Exception) -> bool:
        """判断异常是否为可自动恢复的 stale / 超时类错误。

        目标解析类失败（``BrowserTargetStale`` 默认不可恢复）不做自愈重试：快照里的
        ref / 推断 index 已失配时，用同名 ref 去最新 DOM 上重新定位会把动作静默落到
        语义完全不同的元素上，属危险的行为错位，必须让上层引导用户重新获取快照后再
        指定目标。仅当 ``recoverable=True``（真·执行期 transient stale，目标可安全按
        最新 DOM 重定位）或等待超时类错误时才进入自动恢复重试。
        """
        if isinstance(exc, BrowserTargetStale):
            return bool(getattr(exc, "recoverable", False))
        if isinstance(exc, (BrowserWaitTimeout, TimeoutError)):
            return True
        name = exc.__class__.__name__.lower()
        return ("stale" in name or "timeout" in name) and isinstance(exc, Exception)

    def _raise_normalized_action_error(self, error: Exception | None, action_name: str):
        raise BrowserTargetStale(
            f"{action_name} 连续执行失败，请刷新页面快照后重试或调整目标"
        ) from error

    async def _post_action_settle(self, page: Any) -> None:
        """动作后拟人化视线与 DOM 稳定等待（随机等待 120ms ~ 260ms）。"""
        settle_ms = random.randint(120, 260)
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if not callable(wait_for_timeout):
            return await asyncio.sleep(settle_ms / 1000.0)
        await _maybe_await(wait_for_timeout(settle_ms))

    async def _human_smooth_mouse_move(self, page: Any, target_x: float, target_y: float, steps: int = 8) -> None:
        """模拟真实人类鼠标从当前点平滑移动到目标坐标，带有微小随机抖动。"""
        mouse = getattr(page, "mouse", None)
        if not mouse or not hasattr(mouse, "move"):
            return
        try:
            start_x = getattr(page, "_last_mouse_x", None)
            start_y = getattr(page, "_last_mouse_y", None)
            if start_x is None or start_y is None:
                start_x = random.uniform(100, 500)
                start_y = random.uniform(100, 400)
            step_count = max(4, steps)
            for i in range(1, step_count + 1):
                t = i / float(step_count)
                ease_t = 2 * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 2) / 2
                curr_x = start_x + (target_x - start_x) * ease_t + (random.uniform(-1.2, 1.2) if i < step_count else 0)
                curr_y = start_y + (target_y - start_y) * ease_t + (random.uniform(-1.2, 1.2) if i < step_count else 0)
                await _maybe_await(mouse.move(curr_x, curr_y))
                await asyncio.sleep(random.uniform(0.005, 0.015))
            page._last_mouse_x = target_x
            page._last_mouse_y = target_y
        except Exception:
            pass

    async def _human_click_locator(self, locator: Any, page: Any) -> None:
        """模拟真实人类对指定元素先平滑移动鼠标再进行自然按压点击。"""
        try:
            bounding_box = getattr(locator, "bounding_box", None)
            box = await _maybe_await(bounding_box()) if callable(bounding_box) else None
            if box and isinstance(box, dict) and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
                target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                await self._human_smooth_mouse_move(page, target_x, target_y, steps=random.randint(5, 10))
                await asyncio.sleep(random.uniform(0.03, 0.07))
        except Exception:
            pass
        await _maybe_await(locator.click())

    async def _human_type_into_locator(self, locator: Any, value: str, page: Any) -> None:
        """模拟真实人类逐字敲击键盘输入，带拟人按键延迟与段间停顿。"""
        try:
            await self._human_click_locator(locator, page)
            await asyncio.sleep(random.uniform(0.04, 0.09))
            press_seq = getattr(locator, "press_sequentially", None)
            if callable(press_seq):
                if len(value) <= 60:
                    await press_seq(value, delay=random.randint(30, 65))
                    return
                else:
                    chunk_size = 25
                    for idx in range(0, len(value), chunk_size):
                        chunk = value[idx:idx + chunk_size]
                        await press_seq(chunk, delay=random.randint(15, 35))
                        if idx + chunk_size < len(value):
                            await asyncio.sleep(random.uniform(0.06, 0.15))
                    return
        except Exception:
            pass
        await locator.fill(value)

    async def click(
        self,
        session_id: str,
        *,
        target_ref: str,
        snapshot: BrowserSnapshot,
        approval_mode: str = "autopilot",
        confirmed: bool = False,
    ) -> BrowserToolResult:
        target = self._target(session_id, snapshot, target_ref)
        action_class: BrowserActionClass = classify_browser_action(
            role=target.get("role"), name=target.get("name")
        )
        decision = decide_browser_action(approval_mode, action_class)
        if decision.requires_confirmation and not confirmed:
            raise BrowserActionConfirmationRequired(decision.reason)
        # 动作执行失败时按 stale/超时归一化并自动刷新快照重试（A2）。
        return await self._run_with_stale_recovery(
            session_id,
            self._click_exec(session_id, target_ref),
            action_name="点击",
            snapshot=snapshot,
        )

    def _click_exec(self, session_id: str, target_ref: str):
        """点击动作的可重入执行体，供 stale 恢复重试。"""

        async def exec_(snapshot):
            handle = self._handle(session_id)
            target = self._target(session_id, snapshot, target_ref)
            locator = self._locator_for(handle.page, target)
            await self._validate_inferred_target(locator, target)
            await self._ensure_actionable(locator, what="目标元素")
            pages_before = {
                id(page)
                for page in list(getattr(handle.context, "pages", []) or [])
            }
            await self._human_click_locator(locator, handle.page)
            pages_after = list(getattr(handle.context, "pages", []) or [])
            new_pages = [page for page in pages_after if id(page) not in pages_before]
            if not new_pages:
                for _ in range(4):
                    await asyncio.sleep(0.05)
                    pages_after = list(getattr(handle.context, "pages", []) or [])
                    new_pages = [page for page in pages_after if id(page) not in pages_before]
                    if new_pages:
                        break
            if new_pages:
                handle.page = new_pages[-1]
            await self._post_action_settle(handle.page)
            info = await self._page_info(handle.page)
            self._snapshots.pop(session_id, None)
            return BrowserToolResult(
                session_id=session_id,
                action="click",
                url=info.url,
                title=info.title,
            )

        return exec_

    async def fill(
        self,
        session_id: str,
        *,
        target_ref: str,
        value: str,
        snapshot: BrowserSnapshot,
        sensitive: bool | None = None,
    ) -> BrowserToolResult:
        # 动作执行失败时按 stale/超时归一化并自动刷新快照重试（A2）。
        return await self._run_with_stale_recovery(
            session_id,
            self._fill_exec(session_id, target_ref, value, sensitive=sensitive),
            action_name="输入",
            snapshot=snapshot,
        )

    def _fill_exec(self, session_id: str, target_ref: str, value: str, *, sensitive: bool | None):
        """输入动作的可重入执行体，供 stale 恢复重试。"""

        async def exec_(snapshot):
            handle = self._handle(session_id)
            target = self._target(session_id, snapshot, target_ref)
            locator = self._locator_for(handle.page, target)
            await self._validate_inferred_target(locator, target)
            await self._ensure_actionable(locator, what="目标元素")
            await self._human_type_into_locator(locator, value, handle.page)
            await self._post_action_settle(handle.page)
            info = await self._page_info(handle.page)
            # 页面快照推断的敏感性是下限，调用方只能追加标记，不能用 False 覆盖密码字段。
            is_sensitive = bool(target.get("sensitive", False)) or bool(sensitive)
            payload = redact_browser_arguments({"value": value, "sensitive": is_sensitive})
            self._snapshots.pop(session_id, None)
            return BrowserToolResult(
                session_id=session_id,
                action="fill",
                url=info.url,
                title=info.title,
                data=payload,
            )

        return exec_

    async def close(self, session_id: str) -> None:
        handle = self._handles.pop(session_id, None)
        self._snapshots.pop(session_id, None)
        if handle is None:
            return
        await _maybe_await(handle.context.close())
        if handle.browser is not None:
            await _maybe_await(handle.browser.close())

    async def shutdown(self) -> None:
        for session_id in list(self._handles):
            await self.close(session_id)
        if self._playwright_context_manager is not None:
            await self._playwright_context_manager.__aexit__(None, None, None)
        self._playwright = None
        self._playwright_context_manager = None
