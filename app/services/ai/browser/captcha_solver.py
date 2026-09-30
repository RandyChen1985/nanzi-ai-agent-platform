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
# 点击触发按钮后等待挑战动画展开的秒数
CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS = 1.5

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

            trigger_used = False
            while True:
                image_b64 = await self._get_screenshot_base64(session_id, snapshot)
                if not image_b64:
                    logger.info("[CaptchaSolver] 无法获取会话 %s 的截图，放弃自动解算", session_id)
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=False,
                        reason_code=CAPTCHA_REASON_NO_SCREENSHOT,
                        message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_NO_SCREENSHOT],
                    )

                parsed_data = await self._query_vision_model(image_b64, vision_model)
                if not parsed_data:
                    logger.info("[CaptchaSolver] Vision 模型未能返回有效解算数据")
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
                    # 点击只是展开动作，不计入重试额度；每轮最多点一次，防止模型误判导致反复乱点。
                    if trigger_used:
                        logger.info("[CaptchaSolver] 触发按钮已点击过，模型仍返回 trigger，判定为识别失败")
                        return CaptchaSolveOutcome(
                            solved=False,
                            retryable=True,
                            reason_code=CAPTCHA_REASON_RECOGNITION_FAILED,
                            message=CAPTCHA_REASON_MESSAGES[CAPTCHA_REASON_RECOGNITION_FAILED],
                        )
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
                    logger.info(
                        "[CaptchaSolver] 已点击验证触发按钮（%s），等待挑战展开后重新识别",
                        parsed_data.get("reason") or "未知按钮",
                    )
                    await asyncio.sleep(CAPTCHA_TRIGGER_EXPAND_WAIT_SECONDS)
                    new_snapshot = await self.worker.snapshot(session_id)
                    if new_snapshot.page_state != "captcha":
                        logger.info("[CaptchaSolver] 点击触发按钮后页面已恢复正常状态")
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
                    logger.info("[CaptchaSolver] 模型判定该验证码类型不可自动处理: %s", detail)
                    return CaptchaSolveOutcome(
                        solved=False,
                        retryable=False,
                        reason_code=CAPTCHA_REASON_UNSUPPORTED_TYPE,
                        message=message,
                    )

                break

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
            await asyncio.sleep(1.5)

            # 重新获取快照检查状态
            new_snapshot = await self.worker.snapshot(session_id)
            if new_snapshot.page_state != "captcha":
                logger.info("[CaptchaSolver] 验证码自动解算成功！页面已恢复正常状态")
                return CaptchaSolveOutcome(
                    solved=True,
                    retryable=False,
                    message="验证码自动解算成功",
                    **_geometry_from(captured),
                )

            logger.info("[CaptchaSolver] 执行动作后页面仍处于验证码状态")
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

    async def _get_screenshot_base64(
        self, session_id: str, snapshot: BrowserSnapshot
    ) -> Optional[str]:
        """读取快照截图或直接调用 Playwright 截图并转为 Base64。"""
        if snapshot.screenshot_ref and os.path.isfile(snapshot.screenshot_ref):
            try:
                with open(snapshot.screenshot_ref, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                pass

        # 尝试通过 worker page 截图
        try:
            handle = self.worker._handle(session_id)
            page = handle.page
            screenshot_bytes = await page.screenshot(type="jpeg", quality=80, full_page=False)
            return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception:
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
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
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
            if x is None or y is None:
                logger.warning("[CaptchaSolver] 验证触发按钮坐标缺失: %s", result)
                return False
            await self.worker._human_smooth_mouse_move(
                page, float(x), float(y), steps=random.randint(6, 12)
            )
            await asyncio.sleep(random.uniform(0.1, 0.2))
            await page.mouse.click(float(x), float(y))
            return True

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

            # 寻找滑块起点
            sx, sy = await self._find_slider_start(handle.page, slider_x, slider_y)

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
            await page.mouse.move(sx, sy)
            await asyncio.sleep(random.uniform(0.05, 0.1))
            await page.mouse.down()
            for x, y, delay in trajectory:
                await page.mouse.move(x, y)
                await asyncio.sleep(delay)
            await asyncio.sleep(random.uniform(0.08, 0.15))
            await page.mouse.up()
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

    async def _find_slider_start(
        self, page: Any, hint_x: Optional[float], hint_y: Optional[float]
    ) -> Tuple[float, float]:
        """寻找滑块起始拖动手柄的坐标。"""
        if hint_x is not None and hint_y is not None and hint_x > 0 and hint_y > 0:
            return float(hint_x), float(hint_y)

        # 尝试通过常见滑块按钮选择器在页面上定位
        slider_selectors = [
            ".geetest_slider_button",
            ".geetest_btn",
            ".nc_iconfont.btn_slide",
            ".slider-btn",
            ".verify-move-block",
            "[class*='slider']",
            "[class*='drag']",
        ]
        for sel in slider_selectors:
            try:
                locator = page.locator(sel).first
                box = await locator.bounding_box()
                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                    return box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0
            except Exception:
                continue

        # 默认回退位置（中心偏下）
        viewport = getattr(page, "viewport_size", None) or {"width": 1280, "height": 800}
        return viewport.get("width", 1280) * 0.4, viewport.get("height", 800) * 0.5
