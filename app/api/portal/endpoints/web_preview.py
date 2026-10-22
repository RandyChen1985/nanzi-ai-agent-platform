"""网页预览内嵌实测接口（Web Preview Embed Check）。

前端打开预览面板时调用本接口，由**服务端**读取目标站点的真实响应头，回答
「浏览器会不会拦截内嵌展示」，前端据此决定是否展示受限提示卡片。

修的是这个真实问题：旧实现只按硬编码域名名单猜，``www.baidu.com`` 属于
「常见防嵌套站点」名单，于是先弹「该站点通常禁止内嵌预览」，而百度首页实际
并不下发任何防嵌套头，用户点「仍尝试在内嵌框架中加载」就正常打开了。

约定：

- 鉴权沿用门户登录态（``require_api_key``，与其它 ``/api/portal`` 端点一致）。
- 地址校验**先于**任何出站请求；内网/环回/非 http(s) 一律 400，不触发探测。
- 本接口不返回目标站点正文，只返回响应头判定结论，不构成内容代理。
- 探测不出结论时 ``embeddable`` 为 ``null``（未知），由前端降级为经验预判。
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ai.browser.browser_policy import (
    BrowserUrlBlocked,
    validate_public_navigation,
)
from app.services.web_preview_embed_service import probe_embed_policy

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_URL_LENGTH = 2048


class EmbedCheckRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=1,
        max_length=MAX_URL_LENGTH,
        description="待预览的网页地址（仅支持 http/https）",
    )


class EmbedCheckResponse(BaseModel):
    url: str = Field(..., description="请求实测的地址")
    final_url: str = Field("", description="跟随重定向后实际取到响应头的地址")
    embeddable: Optional[bool] = Field(
        None,
        description="true=实测可嵌；false=实测会被浏览器拦截；null=未取得结论（不可达等）",
    )
    reason: str = Field("", description="结论的可读说明")
    signals: Dict[str, str] = Field(
        default_factory=dict, description="参与判定的响应头片段（如 X-Frame-Options）"
    )


@router.post(
    "/embed-check",
    response_model=EmbedCheckResponse,
    summary="实测网页是否允许被内嵌预览",
)
async def check_embed(payload: EmbedCheckRequest) -> EmbedCheckResponse:
    target = (payload.url or "").strip()
    if not target:
        # 与全局 RequestValidationError 处理保持一致：入参不合法一律 400。
        raise HTTPException(status_code=400, detail="网页地址不能为空")

    # 先做 SSRF / 协议校验，再考虑出站；内网地址不得触发任何请求。
    try:
        validate_public_navigation(target)
    except BrowserUrlBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await probe_embed_policy(target)

    return EmbedCheckResponse(
        url=target,
        final_url=result.final_url,
        embeddable=result.embeddable,
        reason=result.reason,
        signals=dict(result.signals),
    )
