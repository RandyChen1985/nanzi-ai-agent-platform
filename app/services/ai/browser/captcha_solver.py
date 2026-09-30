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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

3. 如果是短信验证码、二维码扫码、人脸识别或无法自动识别的复杂验证码：
{
  "type": "unsupported",
  "reason": "验证码类型无法自动处理或未检测到清晰验证码"
}
"""


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
        """尝试使用 Vision LLM 自动解算当前页面的验证码。

        Returns:
            bool: 是否成功解算并通过验证码（页面状态转为 ready）
        """
        try:
            image_b64 = await self._get_screenshot_base64(session_id, snapshot)
            if not image_b64:
                logger.info("[CaptchaSolver] 无法获取会话 %s 的截图，放弃自动解算", session_id)
                return False

            vision_model = model_name or await self._resolve_vision_model()
            if not vision_model:
                logger.info("[CaptchaSolver] 未配置支持多模态（Vision）的大模型，放弃自动解算")
                return False

            parsed_data = await self._query_vision_model(image_b64, vision_model)
            if not parsed_data:
                logger.info("[CaptchaSolver] Vision 模型未能返回有效解算数据")
                return False

            action_success = await self._execute_captcha_action(session_id, snapshot, parsed_data)
            if not action_success:
                logger.info("[CaptchaSolver] 执行验证码模拟动作失败")
                return False

            # 等待前端与后端网络响应
            await asyncio.sleep(1.5)

            # 重新获取快照检查状态
            new_snapshot = await self.worker.snapshot(session_id)
            if new_snapshot.page_state != "captcha":
                logger.info("[CaptchaSolver] 验证码自动解算成功！页面已恢复正常状态")
                return True
            else:
                logger.info("[CaptchaSolver] 执行动作后页面仍处于验证码状态")
                return False

        except Exception as exc:
            logger.warning("[CaptchaSolver] 自动解算验证码发生异常: %s", exc, exc_info=True)
            return False

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

    async def _execute_captcha_action(
        self, session_id: str, snapshot: BrowserSnapshot, result: Dict[str, Any]
    ) -> bool:
        """根据 Vision 模型给出的坐标驱动 Playwright 执行动作。"""
        action_type = result.get("type")
        handle = self.worker._handle(session_id)
        page = handle.page

        if action_type == "slider":
            distance_px = result.get("distance_px")
            target_x = result.get("target_x")
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
            return True

        elif action_type == "click_sequence":
            points = result.get("points") or []
            if not isinstance(points, list) or not points:
                return False

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
