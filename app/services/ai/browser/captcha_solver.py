"""基于多模态大模型（Vision LLM）的浏览器自动化验证码自解算服务。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.schemas.browser import BrowserSnapshot

logger = logging.getLogger(__name__)

CAPTCHA_SOLVER_PROMPT = """你是一个专业的网页验证码识别专家。请仔细观察这张浏览器页面截图，识别当前页面中出现的安全验证码（如滑块拼图验证码、文字/图标点选验证码）。

请严格按照以下 JSON 格式返回识别结果，不要输出任何多余的解释文字或 Markdown 外部包裹：

1. 如果是滑块拼图验证码（Slider Captcha）：
{
  "type": "slider",
  "slider_x": <滑块起始按钮中心X像素坐标，若无法确定可填 null>,
  "slider_y": <滑块起始按钮中心Y像素坐标，若无法确定可填 null>,
  "target_x": <滑块缺口目标中心X像素坐标>,
  "target_y": <滑块缺口目标中心Y像素坐标>,
  "distance_px": <滑块需向右拖动的水平像素距离，正整数>
}

2. 如果是文字点选或图标按顺序点选验证码（Click Sequence Captcha）：
{
  "type": "click_sequence",
  "points": [
    {"x": <第1个点击目标中心X坐标>, "y": <第1个点击目标中心Y坐标>},
    {"x": <第2个点击目标中心X坐标>, "y": <第2个点击目标中心Y坐标>}
  ]
}

3. 如果页面上只有“点击按钮进行验证 / 点击开始验证 / 按住滑块”这类**触发控件**，真正的验证挑战（滑块或点选图片）还没有展开：
{
  "type": "trigger",
  "x": <需要点击的触发按钮中心X像素坐标>,
  "y": <需要点击的触发按钮中心Y像素坐标>,
  "reason": "<按钮上的文字，例如 点击按钮进行验证>"
}

4. 如果是短信验证码、二维码扫码、人脸识别或无法自动识别的复杂验证码：
{
  "type": "unsupported",
  "reason": "验证码类型无法自动处理或未检测到清晰验证码"
}
"""


CAPTCHA_REASON_NO_VISION_MODEL = "no_vision_model"
CAPTCHA_REASON_NO_SCREENSHOT = "no_screenshot"
CAPTCHA_REASON_UNSUPPORTED_TYPE = "unsupported_type"
CAPTCHA_REASON_RECOGNITION_FAILED = "recognition_failed"
CAPTCHA_REASON_ACTION_FAILED = "action_failed"
CAPTCHA_REASON_STILL_CAPTCHA = "still_captcha"
CAPTCHA_REASON_ERROR = "error"
# 解算过程中用户接管了操作：立刻放弃本次尝试（不是失败，不消耗重试额度）
CAPTCHA_REASON_HUMAN_TAKEOVER = "human_takeover"

# 页面上只有“点击按钮进行验证”这类触发控件、真实挑战尚未展开时的动作类型
CAPTCHA_ACTION_TRIGGER = "trigger"
# 点击触发按钮后等待挑战动画展开的秒数（极验等站点的展开动画会超过 1.5 秒）
CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS = float(
    os.environ.get("BROWSER_CAPTCHA_TRIGGER_EXPAND_SECONDS", "3.0")
)
# 解算截图前的渲染等待：验证码多为异步 iframe，页面刚打开时截图往往只有空白占位容器
CAPTCHA_RENDER_WAIT_MS = int(os.environ.get("BROWSER_CAPTCHA_RENDER_WAIT_MS", "2500"))
CAPTCHA_RENDER_SETTLE_SECONDS = float(
    os.environ.get("BROWSER_CAPTCHA_RENDER_SETTLE_SECONDS", "0.4")
)

# 滑块拖动手柄的常见 DOM 选择器。极验等站点的按钮只有约 40px 宽，视觉模型对它的
# 目测坐标常有 10~40px 偏差，直接拿去 mouse.down() 很可能按在按钮外面（表现：
# "拖了 2 秒页面纹丝不动"）。因此必须优先用 DOM 实测位置，模型坐标仅作兜底。
SLIDER_BUTTON_SELECTORS = (
    ".geetest_slider_button",
    ".geetest_btn",
    ".nc_iconfont.btn_slide",
    ".slider-btn",
    ".verify-move-block",
    "[class*='slider']",
    "[class*='drag']",
)

# “点击按钮进行验证”入口按钮的常见 DOM 选择器：极验的入口按钮同样带 geetest_ 前缀
# （geetest_btn / geetest_holder / geetest_radar_btn），挑战容器则是 geetest_panel 等，
# 两者必须区分。入口按钮同样很小，模型目测偏差可达数十像素，点不准挑战就不会展开。
TRIGGER_BUTTON_SELECTORS = (
    ".geetest_btn",
    ".geetest_radar_btn",
    "#gt_btn",
    ".geetest_holder .geetest_btn",
    "[class*='geetest_btn']",
    "[class*='radar']",
)
TRIGGER_BUTTON_CLICK_TIMEOUT_MS = int(
    os.environ.get("BROWSER_CAPTCHA_TRIGGER_CLICK_TIMEOUT_MS", "2500")
)

# 验证码容器选择器：裁剪到该区域后再送模型。整屏 1280×800 里验证码通常只占
# 约 4% 面积——既拖慢识别（vision token 正比于像素面积），又让缺口只占图宽
# 约 3.5%（模型看不清）。裁剪后像素约降一个数量级、缺口占比提升约 4 倍。
CAPTCHA_CONTAINER_SELECTORS = (
    ".geetest_panel",
    ".geetest_popup",
    ".geetest_window",
    ".geetest_widget",
    ".geetest_box",
    ".geetest_holder",
    ".nc_wrapper",
    ".nc-container",
    "[class*='geetest_panel']",
    "[class*='geetest_popup']",
    "[class*='verify-wrap']",
)
CAPTCHA_CROP_PADDING_PX = int(os.environ.get("BROWSER_CAPTCHA_CROP_PADDING_PX", "28"))
CAPTCHA_CROP_MIN_SIDE_PX = int(os.environ.get("BROWSER_CAPTCHA_CROP_MIN_SIDE_PX", "60"))

# 验证码背景图画布。仅用于“拖动基准对照”实验：极验既可能按“按钮中心↔缺口中心”
# 计算所需位移，也可能按“画布左边缘→缺口”计算，两者在本站相差数十像素。
CAPTCHA_IMAGE_SELECTORS = (
    ".geetest_canvas_bg",
    ".geetest_canvas_img",
    ".geetest_canvas_fullbg",
    ".geetest_bg",
    "[class*='canvas_bg']",
)

# DOM 探测超时。Playwright 的 bounding_box()/click() 对"不存在"的元素会等待自身
# 默认超时（30 秒）——几十个候选选择器逐个等下去会把整个解算拖死（线上实测
# browser_open 卡满 62 秒且没有任何解算日志）。因此必须先用 count() 预检
# （它不等待，立即返回 0/1），再对后续操作传短超时。
CAPTCHA_DOM_PROBE_TIMEOUT_MS = int(
    os.environ.get("BROWSER_CAPTCHA_DOM_PROBE_TIMEOUT_MS", "800")
)

CAPTCHA_REASON_MESSAGES = {
    CAPTCHA_REASON_NO_VISION_MODEL: "未配置支持多模态的默认模型，无法自动识别验证码",
    CAPTCHA_REASON_NO_SCREENSHOT: "无法获取页面截图，无法自动识别验证码",
    CAPTCHA_REASON_UNSUPPORTED_TYPE: "验证码类型不支持自动识别",
    CAPTCHA_REASON_RECOGNITION_FAILED: "未能从页面截图中识别出验证码目标",
    CAPTCHA_REASON_ACTION_FAILED: "验证码目标已识别，但操作执行失败",
    CAPTCHA_REASON_STILL_CAPTCHA: "已执行拖动/点击，页面仍处于验证状态",
    CAPTCHA_REASON_ERROR: "自动解算过程发生异常",
    CAPTCHA_REASON_HUMAN_TAKEOVER: "用户已接管操作，已停止自动解算",
}


@dataclass(frozen=True)
class CaptchaSolveOutcome:
    """一次验证码自动解算尝试的结果，供上层决定是否重试。"""

    solved: bool
    retryable: bool
    reason_code: str = ""
    message: str = ""
    # 过程可视化：解算器"看到了什么、做了什么"，供上层广播给前端在静态截图上绘制
    captcha_kind: Optional[str] = None
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    slider_x: Optional[float] = None
    slider_y: Optional[float] = None
    distance_px: Optional[int] = None
    trajectory: Optional[List[Dict[str, float]]] = None


def _geometry_from(captured: Dict[str, Any]) -> Dict[str, Any]:
    """把进度回调累积的几何信息映射为 CaptchaSolveOutcome 的字段。"""
    return {
        "captcha_kind": captured.get("kind"),
        "target_x": captured.get("target_x"),
        "target_y": captured.get("target_y"),
        "slider_x": captured.get("slider_x"),
        "slider_y": captured.get("slider_y"),
        "distance_px": captured.get("distance_px"),
        "trajectory": captured.get("trajectory"),
    }


def _shift_captcha_coordinates(
    result: Dict[str, Any], clip: Optional[Dict[str, int]]
) -> Dict[str, Any]:
    """把模型基于裁剪图给出的坐标换算回页面坐标。

    裁剪后模型看到的图片以裁剪区左上角为原点，它返回的 x/y 都相对该图；而后续
    鼠标动作使用页面坐标，必须加回裁剪偏移。``distance_px`` 是差值，不受影响。
    """
    if not clip:
        return result
    offset_x = float(clip.get("x") or 0)
    offset_y = float(clip.get("y") or 0)
    if offset_x == 0 and offset_y == 0:
        return result

    shifted = dict(result)
    for key in ("x", "y", "slider_x", "slider_y", "target_x", "target_y"):
        value = shifted.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            shifted[key] = float(value) + (offset_x if key.endswith("x") else offset_y)
    return shifted


def _clean_json_text(text: str) -> str:
    """提取大模型输出中的 JSON 字符串。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
    match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw


class BrowserCaptchaSolver:
    """浏览器验证码自解算器，协调 Vision LLM 识别与拟人化动作驱动。"""

    def __init__(self, worker: Any) -> None:
        self.worker = worker
        # 记住"入口按钮已点击过"的会话：该状态必须跨尝试共享，
        # 否则每次重试都会再点一次"点击按钮进行验证"，3 次额度可能全耗在点按钮上
        self._trigger_clicked: set[str] = set()
        # 本轮识别所用的裁剪区域（按会话）：模型返回的是裁剪图坐标，
        # 需要据此换算回页面坐标，否则后续鼠标动作会整体偏移
        self._captcha_clips: Dict[str, Dict[str, int]] = {}

    def reset_trigger_guard(self, session_id: str) -> None:
        """页面恢复正常或会话被清理时，解除"入口按钮已点击"记忆。"""
        self._trigger_clicked.discard(session_id)

    async def solve_captcha(
        self,
        session_id: str,
        snapshot: BrowserSnapshot,
        *,
        model_name: Optional[str] = None,
    ) -> bool:
        """兼容入口：仅返回是否成功解算并通过验证码。"""
        outcome = await self.solve_captcha_detailed(session_id, snapshot, model_name=model_name)
        return outcome.solved

    async def solve_captcha_detailed(
        self,
        session_id: str,
        snapshot: BrowserSnapshot,
        *,
        model_name: Optional[str] = None,
        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> CaptchaSolveOutcome:
        """执行一次自动解算尝试，返回可判定的结构化结果。

        先解析视觉模型再取截图：未配置多模态模型时不做无用的截图与网络调用，
        便于上层把它与"识别失败"区分开（前者不消耗重试额度）。

        on_progress 用于把"识别到的缺口位置、需要拖动的距离、实际执行轨迹"实时
        抛给上层广播，前端据此在静态截图上绘制 AI 的解算计划与轨迹。
        """
        captured: Dict[str, Any] = {}

        async def _emit(payload: Dict[str, Any]) -> None:
            captured.update(payload)
            if on_progress is not None:
                await on_progress(payload)

        try:
            vision_model = model_name or await self._resolve_vision_model()
            if not vision_model:
                logger.info("[CaptchaSolver] 未配置支持多模态（Vision）的大模型，放弃自动解算")
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=False,
                    reason_code=CAPTCHA_REASON_NO_VISION_MODEL,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_NO_VISION_MODEL],
                )

            trigger_used = session_id in self._trigger_clicked
            while True:
                shot_started = time.monotonic()
                image_b64 = await self._get_screenshot_base64(session_id, snapshot)
                if not image_b64:
                    logger.info("[CaptchaSolver] 无法获取会话 %s 的截图，放弃自动解算", session_id)
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=False,
                        reason_code=CAPTCHA_REASON_NO_SCREENSHOT,
                        message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_NO_SCREENSHOT],
                    )
                # 过程可见性：截图、调用模型、识别失败都要有独立阶段，
                # 否则一旦模型没给出坐标，前端只能显示一句"正在识别"，用户全程黑盒
                await _emit({
                    "phase": "screenshot",
                    "elapsed_ms": int((time.monotonic() - shot_started) * 1000),
                })
                logger.info(
                    "[CaptchaSolver] 已获取截图（base64 %d 字节，耗时 %dms），交给视觉模型 %s",
                    len(image_b64),
                    int((time.monotonic() - shot_started) * 1000),
                    vision_model,
                )

                await _emit({"phase": "analyzing", "model": vision_model})
                model_started = time.monotonic()
                parsed_data = await self._query_vision_model(image_b64, vision_model)
                model_elapsed_ms = int((time.monotonic() - model_started) * 1000)
                if parsed_data:
                    # 图片被裁剪过时模型返回的是裁剪图坐标，先换算回页面坐标
                    parsed_data = _shift_captcha_coordinates(
                        parsed_data, self._captcha_clips.get(session_id)
                    )
                if not parsed_data:
                    logger.info("[CaptchaSolver] Vision 模型未能返回有效解算数据")
                    await _emit({"phase": "unrecognized", "elapsed_ms": model_elapsed_ms})
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=True,
                        reason_code=CAPTCHA_REASON_RECOGNITION_FAILED,
                        message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_RECOGNITION_FAILED],
                    )

                if should_abort is not None and should_abort():
                    # 用户在识别期间接管了操作：立刻收手，绝不执行拖拽/点击去和用户抢滑块
                    logger.info("[CaptchaSolver] 用户已接管操作，放弃本次自动解算动作")
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=False,
                        reason_code=CAPTCHA_REASON_HUMAN_TAKEOVER,
                        message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_HUMAN_TAKEOVER],
                        **_geometry_from(captured),
                    )

                action_type = str(parsed_data.get("type") or "")

                if action_type == CAPTCHA_ACTION_TRIGGER:
                    # “点击按钮进行验证”等触发控件：先点开挑战，再用展开后的新截图重新识别。
                    # 点击只是展开动作，不计入重试额度；入口按钮跨尝试只点一次，
                    # 防止"点按钮 → 仍返回 trigger → 再点按钮"把 3 次额度全部空耗。
                    if trigger_used:
                        logger.info(
                            "[CaptchaSolver] 入口按钮本会话已点击过，模型仍只看到 trigger，"
                            "判定该站点无法自动处理（不再消耗剩余额度）"
                        )
                        await _emit({
                            "phase": "unsupported",
                            "reason": "点击验证入口后仍停留在入口页",
                        })
                        return CaptchaSolveOutcome(
                            solved=False,
                            retryable=False,
                            reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
                            message="点击验证入口后仍停留在入口页，未识别到可操作的验证码内容",
                        )
                    await _emit({
                        "phase": "trigger",
                        "reason": str(parsed_data.get("reason") or ""),
                    })
                    if not await self._execute_captcha_action(
                        session_id, snapshot, parsed_data, on_progress=_emit
                    ):
                        logger.info("[CaptchaSolver] 点击验证触发按钮失败")
                        return CaptchaSolveOutcome(
                            solved=False,
                            retryable=True,
                            reason_code=CAPTCHA_REASON_ACTION_FAILED,
                            message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_ACTION_FAILED],
                        )
                    trigger_used = True
                    self._trigger_clicked.add(session_id)
                    logger.info(
                        "[CaptchaSolver] 已点击验证触发按钮（%s），等待挑战展开后重新识别",
                        parsed_data.get("reason") or "未知按钮",
                    )
                    await asyncio.sleep(CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS)
                    new_snapshot = await self.worker.snapshot(session_id)
                    if new_snapshot.page_state != "captcha":
                        logger.info("[CaptchaSolver] 点击触发按钮后页面已恢复正常状态")
                        self._trigger_clicked.discard(session_id)
                        return CaptchaSolveOutcome(
                            solved=True,
                            retryable=False,
                            message="点击验证触发按钮后页面已恢复正常",
                        )
                    snapshot = new_snapshot
                    continue

                if action_type == "unsupported":
                    detail = str(parsed_data.get("reason") or "").strip()
                    message = CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_UNSUPPORTED_TYPE]
                    if detail:
                        message = f"{message}（{detail}）"
                    await _emit({
                        "phase": "unsupported",
                        "reason": detail,
                        "elapsed_ms": model_elapsed_ms,
                    })
                    logger.info("[CaptchaSolver] 模型判定该验证码类型不可自动处理: %s", detail)
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=False,
                        reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
                        message=message,
                    )

                break

            await _emit({"phase": "acting", "kind": action_type})
            action_success = await self._execute_captcha_action(
                session_id, snapshot, parsed_data, on_progress=_emit
            )
            if not action_success:
                logger.info("[CaptchaSolver] 执行验证码模拟动作失败")
                return CaptchaSolveOutcome(
                    solved=False,
                    retryable=True,
                    reason_code=CAPTCHA_REASON_ACTION_FAILED,
                    message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_ACTION_FAILED],
                    **_geometry_from(captured),
                )

            # 等待前端与后端网络响应
            await _emit({"phase": "verifying"})
            await asyncio.sleep(1.5)

            # 重新获取快照检查状态
            new_snapshot = await self.worker.snapshot(session_id)
            if new_snapshot.page_state != "captcha":
                logger.info("[CaptchaSolver] 验证码自动解算成功！页面已恢复正常状态")
                self._trigger_clicked.discard(session_id)
                await _emit({"phase": "solved", **_geometry_from(captured)})
                return CaptchaSolveOutcome(
                    solved=True,
                    retryable=False,
                    message="验证码自动解算成功",
                    **_geometry_from(captured),
                )

            logger.info("[CaptchaSolver] 执行动作后页面仍处于验证码状态")
            await _emit({"phase": "still_captcha", **_geometry_from(captured)})
            return CaptchaSolveOutcome(
                solved=False,
                retryable=True,
                reason_code=CAPTCHA_REASON_STILL_CAPTCHA,
                message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_STILL_CAPTCHA],
                **_geometry_from(captured),
            )

        except Exception as exc:
            logger.warning("[CaptchaSolver] 自动解算验证码发生异常: %s", exc, exc_info=True)
            return CaptchaSolveOutcome(
                solved=False,
                retryable=True,
                reason_code=CAPTCHA_REASON_ERROR,
                message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_ERROR],
                **_geometry_from(captured),
            )

    async def _resolve_vision_model(self) -> Optional[str]:
        from app.services.ai.multimodal_support import resolve_default_multimodal_model_name

        return await resolve_default_multimodal_model_name()

    async def _wait_for_page_render(self, page: Any) -> None:
        """截图前等页面渲染稳定。

        验证码组件多为异步 iframe + 异步图片解码：页面刚打开或刚点击入口按钮时截图，
        往往只能拿到空白的占位容器（线上表现：模型回答"完成验证区域仅显示空白灰色容器"）。
        等待失败不应影响解算，因此全部兜底忽略。
        """
        load_state = getattr(page, "wait_for_load_state", None)
        if callable(load_state):
            try:
                await load_state("networkidle", timeout=CAPTCHA_RENDER_WAIT_MS)
            except Exception:
                pass
        wait_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_timeout):
            try:
                await wait_timeout(int(CAPTCHA_RENDER_SETTLE_SECONDS * 1000))
            except Exception:
                pass

    async def _locate_captcha_container(self, page: Any) -> Optional[Dict[str, float]]:
        """用 DOM 选择器定位验证码容器（裁剪区域的依据）。"""
        for selector in CAPTCHA_CONTAINER_SELECTORS:
            try:
                locator = page.locator(selector).first
                # 必须先 count()：对不存在的元素直接 bounding_box() 会等满默认 30 秒
                if await locator.count() == 0:
                    continue
                box = await locator.bounding_box(timeout=CAPTCHA_DOM_PROBE_TIMEOUT_MS)
            except Exception:
                continue
            if (
                box
                and box.get("width", 0) >= CAPTCHA_CROP_MIN_SIDE_PX
                and box.get("height", 0) >= CAPTCHA_CROP_MIN_SIDE_PX
            ):
                return box
        return None

    async def _resolve_captcha_clip(self, page: Any) -> Optional[Dict[str, int]]:
        """计算裁剪区域：容器外扩一圈并夹在视口内；定位不到容器时返回 None（回退整屏）。"""
        box = await self._locate_captcha_container(page)
        if not box:
            return None

        viewport = getattr(page, "viewport_size", None) or {}
        viewport_width = float(viewport.get("width") or 0)
        viewport_height = float(viewport.get("height") or 0)

        left = max(float(box.get("x") or 0) - CAPTCHA_CROP_PADDING_PX, 0.0)
        top = max(float(box.get("y") or 0) - CAPTCHA_CROP_PADDING_PX, 0.0)
        right = float(box.get("x") or 0) + float(box.get("width") or 0) + CAPTCHA_CROP_PADDING_PX
        bottom = float(box.get("y") or 0) + float(box.get("height") or 0) + CAPTCHA_CROP_PADDING_PX
        if viewport_width > 0:
            right = min(right, viewport_width)
        if viewport_height > 0:
            bottom = min(bottom, viewport_height)

        width = int(round(right - left))
        height = int(round(bottom - top))
        if width < CAPTCHA_CROP_MIN_SIDE_PX or height < CAPTCHA_CROP_MIN_SIDE_PX:
            return None
        return {"x": int(round(left)), "y": int(round(top)), "width": width, "height": height}

    async def _get_screenshot_base64(
        self, session_id: str, snapshot: BrowserSnapshot
    ) -> Optional[str]:
        """实时截图验证码画面，实时截图不可用时回退到快照缓存。

        必须优先实时截图：快照缓存可能是"验证码组件尚未渲染"那一刻的画面，
        模型只会拿到一张空白图，从而给出"未渲染出验证码"这类无效结论。

        截图优先裁剪到验证码容器（PNG 无损）：整屏图里验证码只占几个百分点，
        裁剪能同时降低识别耗时并提升缺口清晰度；定位不到容器时回退整屏 JPEG。
        """
        try:
            handle = self.worker._handle(session_id)
            page = handle.page
            await self._wait_for_page_render(page)

            clip = await self._resolve_captcha_clip(page)
            if clip is not None:
                cropped = await page.screenshot(clip=clip, type="png")
                if cropped:
                    self._captcha_clips[session_id] = clip
                    logger.info(
                        "[CaptchaSolver] 已裁剪验证码区域 (%d, %d) %dx%d 后交给视觉模型",
                        clip["x"],
                        clip["y"],
                        clip["width"],
                        clip["height"],
                    )
                    return base64.b64encode(cropped).decode("utf-8")

            self._captcha_clips.pop(session_id, None)
            screenshot_bytes = await page.screenshot(type="jpeg", quality=80, full_page=False)
            if screenshot_bytes:
                return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception as exc:
            logger.info("[CaptchaSolver] 实时截图失败，回退快照缓存: %s", exc)

        if snapshot.screenshot_ref and os.path.isfile(snapshot.screenshot_ref):
            try:
                with open(snapshot.screenshot_ref, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                pass
        return None

    async def _query_vision_model(
        self, image_b64: str, vision_model: str
    ) -> Optional[Dict[str, Any]]:
        """调用 Vision LLM 获取验证码坐标结构化结果。"""
        # langchain_core 已随 AgentScope 迁移移除，这里用平台自带的兼容消息类
        from app.services.ai.runtime.agentscope.compat import HumanMessage
        from app.services.ai.config import AgentConfigProvider

        message = HumanMessage(
            content=[
                {"type": "text", "text": CAPTCHA_SOLVER_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
            ]
        )

        llm = await AgentConfigProvider.get_configured_llm(
            streaming=False,
            model_override=vision_model,
            temp_override=0.0,
        )

        response = await llm.ainvoke([message])
        content_text = ""
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                content_text = "".join(str(c) for c in content)
            else:
                content_text = str(content or "")
        else:
            content_text = str(response or "")

        cleaned = _clean_json_text(content_text)
        preview = " ".join(content_text.split())[:400]
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                # 诊断关键：把模型"看到了什么、给出了什么"留在日志里。
                # 否则出现"识别不到"时无法区分是模型没看到图，还是坐标/距离算错。
                logger.info(
                    "[CaptchaSolver] 视觉模型 %s 返回: %s | 原始输出: %s",
                    vision_model,
                    json.dumps(data, ensure_ascii=False)[:400],
                    preview,
                )
                return data
        except Exception as exc:
            logger.warning("[CaptchaSolver] 解析 Vision 模型 JSON 失败: %s, 原始输出: %s", exc, content_text)
        return None

    @staticmethod
    async def _notify_progress(
        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        payload: Dict[str, Any],
    ) -> None:
        """上报解算进度；广播失败绝不影响解算本身。"""
        if on_progress is None:
            return
        try:
            await on_progress(payload)
        except Exception as exc:
            logger.warning("[CaptchaSolver] 上报解算进度失败: %s", exc)

    async def _execute_captcha_action(
        self,
        session_id: str,
        snapshot: BrowserSnapshot,
        result: Dict[str, Any],
        *,
        on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> bool:
        """根据 Vision 模型给出的坐标驱动 Playwright 执行动作。"""
        action_type = result.get("type")
        handle = self.worker._handle(session_id)
        page = handle.page

        if action_type == CAPTCHA_ACTION_TRIGGER:
            x = result.get("x")
            y = result.get("y")
            if x is None and y is None:
                logger.warning("[CaptchaSolver] 验证触发按钮坐标缺失: %s", result)
            return await self._click_trigger_button(
                page,
                float(x) if x is not None else None,
                float(y) if y is not None else None,
            )

        if action_type == "slider":
            distance_px = result.get("distance_px")
            target_x = result.get("target_x")
            target_y = result.get("target_y")
            slider_x = result.get("slider_x")
            slider_y = result.get("slider_y")

            # 若未直接给出 distance_px 但给出了 target_x 和 slider_x
            if distance_px is None and target_x is not None and slider_x is not None:
                distance_px = int(round(target_x - slider_x))

            if not distance_px or distance_px <= 0:
                logger.warning("[CaptchaSolver] 滑块计算距离无效: %s", distance_px)
                return False

            # 寻找滑块起点：鼠标必须按在按钮上（模型给的 slider_x 是拼图块，不能当落点）
            sx, sy = await self._find_slider_start(handle.page, slider_x, slider_y)
            # 拖动距离一律采用模型自报值：模型同时给出拼图块与缺口的位置，两者同基准相减，
            # 是唯一自洽的来源。DOM 侧根本读不到拼图块的真实位置——`.geetest_canvas_slice`
            # 的 bounding box 覆盖整幅画布（实测其 x 恰好等于 画布 x + 130，即画布宽的一半），
            # 拿它当拼图块会把 165px 算成 77px；按钮同样不行（与拼图块起点不同，会多算约 27px）。
            logger.info(
                "[CaptchaSolver] 拖动距离采用模型自报值 %spx（拼图块目测 %s → 缺口 %s）",
                distance_px,
                slider_x,
                target_x,
            )

            geometry: Dict[str, Any] = {
                "kind": "slider",
                "target_x": float(target_x) if target_x is not None else None,
                "target_y": float(target_y) if target_y is not None else None,
                "slider_x": float(sx),
                "slider_y": float(sy),
                "distance_px": int(distance_px),
            }
            await self._notify_progress(on_progress, {"phase": "recognized", **geometry})

            # 生成拟人化平滑拖拽轨迹
            trajectory = self.worker._slider_trajectory(sx, sy, int(distance_px))
            logger.info(
                "[CaptchaSolver] 滑块识别结果: 起点 (%s, %s) 缺口目标 (%s, %s) 需拖动 %spx，轨迹 %d 点",
                sx,
                sy,
                target_x,
                target_y,
                distance_px,
                len(trajectory),
            )
            # 基准对照实验：极验既可能按“按钮中心↔缺口中心”算位移，也可能按“画布左边缘→缺口”
            # 算。两种候选在本站相差数十像素，先把两个值都打出来，一次复现即可判定。
            image_box = await self._locate_captcha_image(page)
            if image_box is not None:
                logger.info(
                    "[CaptchaSolver] 拖动参考: 画布 x=%s（宽 %s）/ 按钮 x=%s / 缺口 x=%s / 距离 %spx",
                    image_box["x"],
                    image_box["width"],
                    sx,
                    target_x,
                    distance_px,
                )

            await page.mouse.move(sx, sy)
            await asyncio.sleep(random.uniform(0.05, 0.1))
            await page.mouse.down()
            for x, y, delay in trajectory:
                await page.mouse.move(x, y)
                await asyncio.sleep(delay)
            await asyncio.sleep(random.uniform(0.08, 0.15))
            await page.mouse.up()
            # 拖动结果实测：重读按钮位置，区分“被弹回起点 / 停在终点 / 停在半路”。
            # 这是判断“距离算错”还是“行为特征被风控”的关键证据。
            await asyncio.sleep(random.uniform(0.25, 0.35))
            after = await self._locate_slider_by_dom(page)
            if after is None:
                logger.info("[CaptchaSolver] 拖动结果实测: DOM 已找不到滑块按钮（可能被极验移除）")
            else:
                moved = float(after[0]) - float(sx)
                if moved <= 2:
                    verdict = "按钮被弹回起点（极验拒绝了本次拖动）"
                elif abs(moved - float(distance_px)) <= 3:
                    verdict = "按钮停在终点（距离被接受，失败另有原因）"
                else:
                    verdict = "按钮停在半路（实际位移与预期不符）"
                logger.info(
                    "[CaptchaSolver] 拖动结果实测: 按钮 (%s, %s) 位移 %.1fpx / 预期 %spx → %s",
                    after[0],
                    after[1],
                    moved,
                    distance_px,
                    verdict,
                )
            await self._notify_progress(
                on_progress,
                {
                    "phase": "dragged",
                    **geometry,
                    "trajectory": [
                        {"x": float(point[0]), "y": float(point[1])} for point in trajectory
                    ],
                },
            )
            return True

        elif action_type == "click_sequence":
            points = result.get("points") or []
            if not isinstance(points, list) or not points:
                return False

            click_points = [
                {"x": float(pt["x"]), "y": float(pt["y"])}
                for pt in points
                if isinstance(pt, dict) and pt.get("x") is not None and pt.get("y") is not None
            ]
            if click_points:
                logger.info(
                    "[CaptchaSolver] 点选识别结果: %d 个目标 %s",
                    len(click_points),
                    click_points,
                )
                await self._notify_progress(
                    on_progress,
                    {"phase": "recognized", "kind": "click_sequence", "points": click_points},
                )

            for pt in points:
                if not isinstance(pt, dict):
                    continue
                x = pt.get("x")
                y = pt.get("y")
                if x is None or y is None:
                    continue
                await self.worker._human_smooth_mouse_move(page, float(x), float(y), steps=random.randint(6, 12))
                await asyncio.sleep(random.uniform(0.08, 0.18))
                await page.mouse.click(float(x), float(y))
                await asyncio.sleep(random.uniform(0.2, 0.4))
            if click_points:
                await self._notify_progress(
                    on_progress,
                    {"phase": "dragged", "kind": "click_sequence", "points": click_points},
                )
            return True

        elif action_type == "unsupported":
            logger.info("[CaptchaSolver] 模型判定该验证码类型为 unsupported: %s", result.get("reason"))
            return False

        return False

    async def _click_trigger_button(
        self,
        page: Any,
        hint_x: Optional[float],
        hint_y: Optional[float],
    ) -> bool:
        """点击“点击按钮进行验证”入口按钮：DOM 实测优先，模型坐标兜底。

        极验的入口按钮不大，视觉模型对它的目测坐标偏差可达数十像素（线上实测同一个
        按钮两次分别给出 (553,353) 与 (597,363)）。点不准，挑战就永远不会展开，
        于是一轮又一轮地停在入口页空转。``locator.click()`` 由浏览器自己定位元素
        中心，比“按坐标盲点”可靠得多。
        """
        for selector in TRIGGER_BUTTON_SELECTORS:
            try:
                locator = page.locator(selector).first
                # 必须先 count()：否则不存在的元素会让 click() 等满自身默认超时
                if await locator.count() == 0:
                    continue
                await locator.click(timeout=TRIGGER_BUTTON_CLICK_TIMEOUT_MS)
                logger.info("[CaptchaSolver] 已通过 DOM 选择器 %s 点击验证入口按钮", selector)
                return True
            except Exception:
                continue

        if hint_x is None or hint_y is None:
            logger.warning("[CaptchaSolver] 验证入口按钮既无法通过 DOM 定位，也没有模型坐标")
            return False

        logger.info(
            "[CaptchaSolver] DOM 未能定位验证入口按钮，回退模型坐标 (%s, %s)", hint_x, hint_y
        )
        await self.worker._human_smooth_mouse_move(
            page, float(hint_x), float(hint_y), steps=random.randint(6, 12)
        )
        await asyncio.sleep(random.uniform(0.1, 0.2))
        await page.mouse.click(float(hint_x), float(hint_y))
        return True

    async def _locate_captcha_image(self, page: Any) -> Optional[Dict[str, float]]:
        """定位验证码背景画布（仅用于拖动基准对照实验）。"""
        for selector in CAPTCHA_IMAGE_SELECTORS:
            try:
                locator = page.locator(selector).first
                # 必须先 count()：对不存在的元素直接 bounding_box() 会等满默认 30 秒
                if await locator.count() == 0:
                    continue
                box = await locator.bounding_box(timeout=CAPTCHA_DOM_PROBE_TIMEOUT_MS)
            except Exception:
                continue
            if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                return box
        return None

    async def _locate_element_center(
        self, page: Any, selectors: Tuple[str, ...]
    ) -> Optional[Tuple[float, float]]:
        """按选择器依次尝试，返回首个命中元素的中心坐标（像素级）。"""
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                # 必须先 count()：对不存在的元素直接 bounding_box() 会等满默认 30 秒
                if await locator.count() == 0:
                    continue
                box = await locator.bounding_box(timeout=CAPTCHA_DOM_PROBE_TIMEOUT_MS)
                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                    return (
                        box["x"] + box["width"] / 2.0,
                        box["y"] + box["height"] / 2.0,
                    )
            except Exception:
                continue
        return None

    async def _locate_slider_by_dom(self, page: Any) -> Optional[Tuple[float, float]]:
        """用 DOM 选择器精确定位滑块拖动手柄的中心坐标（鼠标必须按在这里）。"""
        return await self._locate_element_center(page, SLIDER_BUTTON_SELECTORS)

    async def _find_slider_start(
        self, page: Any, hint_x: Optional[float], hint_y: Optional[float]
    ) -> Tuple[float, float]:
        """寻找滑块起始拖动手柄的坐标：DOM 实测优先，模型目测兜底。"""
        dom_start = await self._locate_slider_by_dom(page)
        if dom_start is not None:
            logger.info(
                "[CaptchaSolver] 滑块按钮由 DOM 精确定位: 中心 (%.1f, %.1f)（模型目测 (%s, %s)）",
                dom_start[0],
                dom_start[1],
                hint_x,
                hint_y,
            )
            return dom_start

        if hint_x is not None and hint_y is not None and hint_x > 0 and hint_y > 0:
            logger.info(
                "[CaptchaSolver] DOM 未能定位滑块按钮，回退模型坐标 (%s, %s)", hint_x, hint_y
            )
            return float(hint_x), float(hint_y)

        # 默认回退位置（中心偏下）
        viewport = getattr(page, "viewport_size", None) or {"width": 1280, "height": 800}
        return viewport.get("width", 1280) * 0.4, viewport.get("height", 800) * 0.5
