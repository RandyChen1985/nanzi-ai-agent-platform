"""generate_image 内置工具：调 OpenAI 兼容图像模型出图，登记成平台产物。

这里刻意只走 ``POST {API 地址}/chat/completions``（非流式），不走
``/images/generations``：实测目标网关的 ``/images/generations`` 在模型名映射上不自洽
——传配置里的 id 报 ``Model mismatch``，传上游自报的名字报 ``No available channel``，
两条都是死路；而 ``/chat/completions`` 同样接受 ``size`` 参数（``宽x高``）、同样返回
base64 图片，并且在 ``content[0].image_url.url`` 上给出 ``data:image/png;base64,...``。

几个必须记住的实测坑（都已在本模块里处理）：

* ``size`` 只认 ``1664x928`` 这种 ``宽x高``；写成星号 ``1328*1328`` 网关**不报错**，
  直接静默回退成默认尺寸——用户拿到构图不对的图却查不到任何错误。所以尺寸必须在
  平台侧按白名单校验，并把可用值回给模型。
* 流式请求只回 usage 与 ``[DONE]``，没有图片，所以 ``stream`` 恒为 ``false``。
* 40MB 级的 base64 图片不落盘就进不了产物体系，因此这里先解到临时文件再发布。
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.context import get_current_agent_context, get_execution_option
from app.services.ai.tools.generated_file_service import (
    publish,
    resolve_delegation_parent_scope,
)
from app.services.ai.tools.tool_compat import tool
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

SWITCH_KEY = "image_generation_enabled"

#: 单次最多张数的硬上限。配置项自身也限制了范围，这里是最后一道，避免写入侧被绕过。
MAX_IMAGES_HARD_LIMIT = 4

#: 改图时输入图（用户上传的原图 / 上一轮生成的结果）的体积上限。
#: 依赖里没有 Pillow，做不了重编码压缩，只能设一个保守上限并在超限时给出人话提示：
#: base64 后体积约为原始 1.33 倍，10MB 的图会变成 13MB 请求体，实测 6MB 的图能正常往返。
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024

#: 「系统设置 → 测试改图」用的内置样图：256×256，米白背景 + 居中朱红圆（1 KB 的 PNG）。
#:
#: 为什么不读用户文件：这是管理员在配置页点的按钮，没有会话、没有工作区身份，
#: 也不该要求他先去上传一张图。构图选「背景 + 一个明确主体」，是因为换背景最容易
#: 肉眼验收——圆还在、背景变了就是改图真的生效了。
TEST_EDIT_SAMPLE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAAECklEQVR42u3dMXIiMRRFUTZEQhE6"
    "9NpZDBlVXoCdG1Sy1Ujd/52qG8944B2pO/Gcvh53KbaTj0AASABIAEgASABIAEgASABIAEgASABI"
    "AEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgA"
    "SABIAEgASABIAEgASABIAEgASABIAAgAn8LKPi5nHwIAZce9VT5MALIWzwMAEaO/fV5hAKDs7n/2"
    "PR4JABxm+pssfsSDLwiA2bufMPp/YPCVAfDe6S/cfb8EXx8AG09/V7vvlOCrBKD+9DEAwPQxAMD0"
    "MQBgfP0Fpt9m4EsHIGL6GADQu/7C028wAMD0g9bvKgDgnjx9V0E6AOtnIBeA9TOQC8D0OxkAYP0M"
    "AOCxx+MQANbPAADWzwAAnvu9DwBg/QwAYP0MAGD9DABg/QwAYP0MAGD9DAAAAAAAWD8DAFg/AwDM"
    "B2CgMw0A4Ph3CQBg/QwA4OHHgxAAjn+XAACOf5cAAI5/lwAAjn+XAACOf5cAAI5/lwAAjn+XAACO"
    "f5cAAI5/lwAAjn+XAAAAAACA9TMAAAAAAOD116swAI5/lwAAAAAAgOcfT0EAOP5dAgAAAAAAAACQ"
    "CcALgNcAAKzfJQCAeQEAgAAAQABkAPAG7D0YAOt3CQBgWAAAIAAAEAAACAAABAAAAgAAAQCAAABA"
    "AAAgAAoAsH4G3AAMWD8AAAAAgAAAQAAAIAAAEAAACAAABAAAAgAAAQCAAPB7geT3AvnNcHL8AyAA"
    "ABAAAAgA/0OMvAH7P8Lk+AdAAAAgALwGKPAF4EgAXAKOfwAAAAAABjz/ZAJwCTj+AQAAgGAADFg/"
    "AAAAAAADXn8zAbgEHP8AuAQc/8EAXAKOfwBcAo7/YAAuAcc/AC4Bx38wAJeA4x8Al4DjPxiAS8Dx"
    "D4BLwPEfDIAB608H4EHIww8ALgHHfzAABqw/HQAD1g8AAAAEA2DA+tMBMGD96QAYsP50AAxYfzoA"
    "Bqw/HQAD1g/AmYHB9QPAgPUDwID1A+B9wHM/AAxYPwAehzz2AMCA9QPAgPUDcHwDaQx+//MzZ5AL"
    "IPkqMH0AQq8CBz8AoQxMH4A/G6jB4Om/y9cNQH0Gpg9AKAPTByCUgekDMIPB3iS8+iF9fQC8l8Fa"
    "CY2fylcGwGwJczC0fwBfEADrGWzrofPv8qUAsF8JDSGDf4KvAIAiGIweAB4sHoAwJD4EACQAJAAk"
    "ACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkACQAJAAkAASAT0EASABIAEgASABI"
    "AEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIAEgASABIy/oGdWyCUZ9GNI0AAAAA"
    "SUVORK5CYII="
)

#: 平台产物下载链接里的 artifact_id 与 token，用于把模型手里的 markdown 链接还原成本地文件。
_ARTIFACT_URL_RE = re.compile(r"/generated-files/([0-9a-f]{32})\?[^\s)]*?token=([^&\s)]+)")

#: 原生 OpenAI 图像接口：``/images/generations``，size/n 是正式参数。
API_FORMAT_OPENAI_IMAGES = "openai_images"
#: 聊天接口：图片作为消息内容返回，一次一张、尺寸格式不严。
API_FORMAT_OPENAI_CHAT = "openai_chat"
API_FORMATS = (API_FORMAT_OPENAI_IMAGES, API_FORMAT_OPENAI_CHAT)

DEFAULT_CONFIG: dict[str, str] = {
    SWITCH_KEY: "0",
    "image_api_format": API_FORMAT_OPENAI_IMAGES,
    "image_api_base_url": "",
    "image_api_key": "",
    "image_model": "",
    "image_default_size": "1024x1024",
    "image_allowed_sizes": "1024x1024,1328x1328,1664x928,928x1664,1328x1008",
    "image_timeout_seconds": "120",
    "image_max_images_per_call": "1",
    "image_prompt_prefix": "",
}

#: 配置项 → 面向用户的字段名。缺配置时要指名道姓，别让用户对着「生成失败」猜。
_FIELD_LABELS = {
    "image_api_base_url": "图像模型 API 地址",
    "image_api_key": "图像模型 API 密钥",
    "image_model": "图像模型 id",
}

_DEFAULT_TIMEOUT_SECONDS = 120.0
_MIN_TIMEOUT_SECONDS = 10.0

_SIZE_RE = re.compile(r"^\s*(\d{2,5})\s*[xX*×]\s*(\d{2,5})\s*$")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


#: 网关会按上游返回 PNG/JPEG/WEBP，不带 mime 时只能自己嗅探——
#: 猜成 PNG 而实际是 JPEG 会让浏览器按错误类型解码，产物预览直接白块。
_MIME_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def sniff_image_mime(data: bytes, *, strict: bool = False) -> str:
    """按文件头判断图片格式。

    ``strict=False``（默认）认不出时按 PNG 兜底——网关返回的字节流极少数情况会缺
    文件头，兜底能让出图流程继续走，比直接失败好。

    ``strict=True`` 认不出时返回空串。**读用户给的文件必须用这一档**：兜底成 PNG
    等于把任意文件（比如一个 exe 或一份 pdf）当成图片发给模型，既浪费一次计费调用，
    又把不该外发的内容送了出去。
    """
    for signature, mime in _MIME_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "" if strict else "image/png"


def normalize_api_format(raw: Any) -> str:
    """把配置值收敛成两种已知格式之一。

    认不出来就回落 ``openai_images``：它是默认值，也是唯一能让尺寸/张数
    真正生效的协议；回落成聊天格式会静默丢掉尺寸参数，更难察觉。
    """
    value = str(raw or "").strip().lower()
    return value if value in API_FORMATS else API_FORMAT_OPENAI_IMAGES


def _as_bool_flag(raw: Any) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _as_float(raw: Any, default: float, *, minimum: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def normalize_size(value: str | None) -> str | None:
    """把用户/模型给的尺寸归一成网关认识的 ``宽x高``。

    星号、大写 X、全角 ×、空格都在这里抹平——网关对非 ``宽x高`` 的写法不报错而是
    静默回退，一旦放过就等于让用户拿到一张尺寸不对的图却查不到原因。
    """
    if value is None:
        return None
    match = _SIZE_RE.match(str(value))
    if not match:
        return None
    return f"{int(match.group(1))}x{int(match.group(2))}"


def parse_allowed_sizes(raw: Any) -> list[str]:
    """解析白名单；顺带把历史可能写进去的星号格式纠正过来。"""
    sizes: list[str] = []
    for chunk in str(raw or "").split(","):
        normalized = normalize_size(chunk)
        if normalized and normalized not in sizes:
            sizes.append(normalized)
    return sizes


def _decode_api_key(raw: Any) -> str:
    """读取密钥。

    直接复用模型注册表那套解密口径（``decrypt_model_api_key``）：密文与历史明文
    两种形态都能读。自己再写一遍等价逻辑，等于埋一份会和加密策略分叉的实现。
    """
    from app.utils.model_credentials import (
        ModelCredentialError,
        decrypt_model_api_key,
    )

    try:
        return decrypt_model_api_key(str(raw or "").strip()) or ""
    except ModelCredentialError as exc:
        logger.warning("[generate_image] 图像模型密钥解密失败: %s", exc)
        raise ValueError(
            "图像模型密钥无法解密（可能 ENCRYPTION_KEY 变更过），请到「系统设置 → 图像生成」重新录入"
        ) from exc


async def load_image_generation_config() -> dict[str, Any]:
    """读取并规范化图像生成配置。

    registry 判断「要不要把这个工具下发给模型」也用它，所以这里是唯一的读入口。
    """
    raw = await ConfigService.get_many(DEFAULT_CONFIG)
    timeout_raw = raw.get("image_timeout_seconds")
    max_images = _as_int(
        raw.get("image_max_images_per_call"),
        1,
        minimum=1,
        maximum=MAX_IMAGES_HARD_LIMIT,
    )
    allowed = parse_allowed_sizes(raw.get("image_allowed_sizes"))
    default_size = normalize_size(raw.get("image_default_size")) or (
        allowed[0] if allowed else "1024x1024"
    )
    if allowed and default_size not in allowed:
        default_size = allowed[0]
    return {
        "enabled": _as_bool_flag(raw.get(SWITCH_KEY)),
        "api_format": normalize_api_format(raw.get("image_api_format")),
        "api_base_url": str(raw.get("image_api_base_url") or "").strip().rstrip("/"),
        "api_key": str(raw.get("image_api_key") or "").strip(),
        "model": str(raw.get("image_model") or "").strip(),
        "default_size": default_size,
        "allowed_sizes": allowed,
        "timeout": _as_float(
            timeout_raw, _DEFAULT_TIMEOUT_SECONDS, minimum=_MIN_TIMEOUT_SECONDS
        ),
        "max_images_per_call": max_images,
        "prompt_prefix": str(raw.get("image_prompt_prefix") or "").strip(),
    }


async def is_image_generation_available() -> bool:
    """系统总开关是否打开。工具暴露与前端入口都用这一个判断。"""
    config = await load_image_generation_config()
    return bool(config["enabled"])


def _chat_completions_url(base_url: str) -> str:
    """拼出 chat/completions 地址。

    允许管理员只填到 ``/v1``；如果已经贴了完整路径也照样能用，避免二次拼接出
    ``/chat/completions/chat/completions``。
    """
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """从 PNG 头部读宽高。

    前端按宽高算占位比例，缺了会跳版；这里自己解析 20 来个字节，不为了两个整数
    去依赖图像库。
    """
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None, None
    try:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    except (ValueError, IndexError):
        return None, None
    if width <= 0 or height <= 0:
        return None, None
    return width, height


def _parse_data_url(url: str) -> tuple[str, bytes] | None:
    """拆 ``data:image/png;base64,...``，返回 (mime, 字节)。"""
    text = str(url or "").strip()
    if not text.startswith("data:"):
        return None
    header, _, payload = text.partition(",")
    if not payload:
        return None
    mime = "image/png"
    if header.startswith("data:") and ";" in header:
        candidate = header[len("data:"):].split(";", 1)[0].strip()
        if candidate:
            mime = candidate
    try:
        return mime, base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None


def _extract_images(payload: dict[str, Any]) -> list[tuple[str, bytes]]:
    """从响应里挖出所有图片。

    正常形态是 ``content`` 为列表、每项 ``{"type": "image_url", "image_url": {"url": ...}}``；
    也兼容 ``content`` 直接是字符串（模型拒答时会给一句自然语言说明）。
    """
    images: list[tuple[str, bytes]] = []
    choices = payload.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            image_url = part.get("image_url")
            url = ""
            if isinstance(image_url, dict):
                url = str(image_url.get("url") or "")
            elif isinstance(image_url, str):
                url = image_url
            parsed = _parse_data_url(url)
            if parsed:
                images.append(parsed)
    return images


def _response_text(payload: dict[str, Any]) -> str:
    """取模型返回的自然语言部分，用于解释「为什么没给图」。"""
    choices = payload.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    if part["text"].strip():
                        return part["text"].strip()
    return ""


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class _TransientError(Exception):
    """网络层可重试错误（连接失败、超时），与「服务端明确拒绝」区分开。"""


async def _request_once(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code >= 400:
        # 原文回给模型：它看到「401 密钥无效」才能直接告诉用户去改配置，
        # 比工具内部吞掉、只报一句「生成失败」有用得多。
        raise PermissionError(
            f"图像模型接口返回 {response.status_code}：{response.text[:600]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"图像模型接口返回的不是合法 JSON：{response.text[:300]}") from exc


async def _post_generate(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """发请求，**只在请求肯定没送达上游时**重试一次。

    出图不可重放：一张图计一次费，重发就是再买一张。所以要分清楚两类失败——

    * 连接没建立起来（ConnectError / ConnectTimeout / PoolTimeout）：请求根本没到上游，
      不可能已经出图计费，重试是安全的；
    * 读超时（ReadTimeout）：请求早就送达了，上游多半已经在出图甚至已出完，这时重发
      等于用户花了钱买到一张被丢弃的图。写超时同理——请求可能已经发完。

    原来的实现把 ``httpx.TimeoutException`` 整体当作可重试，读超时也在里面，
    正是「多付一张钱」的来源。
    """
    try:
        return await _request_once(url=url, headers=headers, body=body, timeout=timeout)
    except _RETRYABLE_ERRORS as exc:
        logger.warning("[generate_image] 连接未建立（%s），重试一次", _error_text(exc))
        try:
            return await _request_once(
                url=url, headers=headers, body=body, timeout=timeout
            )
        except _RETRYABLE_ERRORS as retry_exc:
            raise _TransientError(
                f"连接图像模型服务失败（已重试一次）：{_error_text(retry_exc)}"
            ) from retry_exc
    except httpx.TimeoutException as exc:
        # 读/写超时：请求已经送到上游，重发会重复计费，只报错不重试。
        raise _TransientError(
            f"等待图像模型响应超时：{_error_text(exc)}。"
            "图片生成通常需要数秒到数十秒，可调大「单次超时」后重试；"
            "这里不再自动重发，以免重复生成计费。"
        ) from exc


#: 只包含「请求确定没送达上游」的错误：连接失败/连接超时/连接池等待超时。
#: 读超时与写超时**不在此列**——那时请求已经发出，重发会重复出图计费。
#: 单张图片下载上限。实测原生图像格式 1664x928 约 5.9 MB，留足余量但防止被超大响应打爆内存。
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024

_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


def _images_generations_url(base_url: str) -> str:
    """拼出原生图像接口地址，容错规则与 ``_chat_completions_url`` 一致。"""
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    return f"{base}/images/generations"


def _extract_image_api_payload(payload: dict[str, Any]) -> list[tuple[str, bytes, str]]:
    """解析 ``/images/generations`` 的响应，返回 ``[(mime, 字节, 远程 url)]``。

    正常渠道回 ``data[].b64_json``；也有渠道只给 ``data[].url``，这时把地址带出去
    由调用方再下载——不在这里下载是为了让本函数保持同步、便于单测。
    """
    found: list[tuple[str, bytes, str]] = []
    items = payload.get("data")
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_b64 = item.get("b64_json")
        if isinstance(raw_b64, str) and raw_b64.strip():
            try:
                data = base64.b64decode(raw_b64)
            except (ValueError, TypeError):
                continue
            if data:
                found.append((sniff_image_mime(data), data, ""))
            continue
        remote = str(item.get("url") or "").strip()
        if remote.startswith(("http://", "https://")):
            found.append(("", b"", remote))
    return found


async def _download_image_bytes(url: str, timeout: float) -> tuple[str, bytes] | None:
    """把渠道给的图片地址下回来。

    这个地址来自上游响应体，属于**不完全可信**的输入，所以要按不可信处理：

    * 只接受 http/https —— 否则 ``file://`` 之类会被当成「渠道给的图片」读进来；
    * 不跟随重定向 —— 否则一个 302 就能把请求引向任意地址（含内网）；
    * ``trust_env=False`` —— 与本模块其它请求一致，不读环境代理；
    * 限制大小并流式读取 —— 否则一个超大响应就能把 worker 内存占满。
    """
    if not url.startswith(("http://", "https://")):
        return None
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return None
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > _MAX_DOWNLOAD_BYTES:
                logger.warning(
                    "[generate_image] 图片响应过大（%s 字节），已放弃下载", declared
                )
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    logger.warning(
                        "[generate_image] 下载超过 %s 字节上限，已中止", _MAX_DOWNLOAD_BYTES
                    )
                    return None
                chunks.append(chunk)
    raw = b"".join(chunks)
    return (sniff_image_mime(raw), raw) if raw else None


async def request_image_once(
    *,
    config: dict[str, Any],
    prompt: str,
    size: str,
    api_key: str,
    source_images: list[tuple[str, bytes]] | None = None,
) -> dict[str, Any]:
    """发一次生成请求，返回 ``{mime, data, model, elapsed_ms}``。

    只负责「调模型 + 取回图片字节」，**不落盘、不发布产物**——「系统设置 → 测试生成」
    要用它做一次真实调用（看完就丢弃），工具侧再用 ``_publish_image`` 把字节登记成产物。
    两条路径共用这一段，才不会出现「测通了但真实调用不通」的错位。

    按 ``config['api_format']`` 分两条协议：原生图像接口（size/n 是正式参数）与
    聊天接口（图片作为消息内容返回）。除了拼请求体和取图位置，其余（超时、
    重试、错误文案）完全共用。

    ``source_images`` 传入 ``[(mime, bytes), ...]`` 时走图生图：把每张图以
    ``image_url`` 按顺序塞进同一条 user 消息，让模型按 ``prompt`` 修改。

    **多图是网关能力，但要收着用**：实测 1～2 张可靠（跨图取色、按指代区分「第一张/
    第二张」都准），3 张开始走样，4 张网关照常 200 却会漏内容且顺序错乱，5 张直接
    500（``At most 4 images are supported by this model``）。所以上限由调用方
    （``edit_image``）卡在 2 张，这里只负责拼装。

    **带原图时协议强制为聊天接口**，不看 ``config['api_format']``。
    ``image_api_format`` 这个配置的语义是「生图用哪个端点」，改图没有这个选择权：
    ``/images/generations`` 不吃图输入（五种传参实测全败）。早先的写法是「格式不对
    就报错让管理员去切」，但默认格式恰恰就是原生图像接口，等于把改图按钮做成了一个
    默认永远点不亮的开关。生图和改图共用同一个地址、密钥、模型，只是路径不同，
    没必要让前者替后者做决定。

    实测：聊天接口的 ``size`` 是**真参数**。把「输出 512x512」写进提示词无效，
    但 body 里带 ``size=512x512`` 会真的返回 512×512，所以尺寸必须结构化传。
    """
    started = time.monotonic()
    timeout = float(config["timeout"])
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    use_chat = (
        bool(source_images)
        or config.get("api_format") == API_FORMAT_OPENAI_CHAT
    )

    if use_chat:
        content: Any = prompt
        if source_images:
            # 顺序即语义：调用方约定「第一张是主图，其余是参考图」，
            # 拼装时保持这个顺序，模型才分得清要改哪张。
            content = [{"type": "text", "text": prompt}]
            for image_mime, image_bytes in source_images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime};base64,"
                            + base64.b64encode(image_bytes).decode("ascii")
                        },
                    }
                )
        body: dict[str, Any] = {
            "model": config["model"],
            "messages": [{"role": "user", "content": content}],
            # 流式只回 usage 与 [DONE]，拿不到图片，必须非流式。
            "stream": False,
        }
        if size:
            body["size"] = size
        url = _chat_completions_url(config["api_base_url"])
    else:
        body = {
            "model": config["model"],
            "prompt": prompt,
            # 一次请求只要一张；张数由工具层循环，避免一次调用悄悄产出多张计费图。
            "n": 1,
            "size": size,
        }
        url = _images_generations_url(config["api_base_url"])

    payload = await _post_generate(url=url, headers=headers, body=body, timeout=timeout)

    mime = ""
    data = b""
    if use_chat:
        extracted = _extract_images(payload)
        if extracted:
            mime, data = extracted[0]
    else:
        for candidate_mime, candidate_data, remote in _extract_image_api_payload(payload):
            if remote:
                downloaded = await _download_image_bytes(remote, timeout)
                if downloaded:
                    mime, data = downloaded
                    break
                continue
            mime, data = candidate_mime, candidate_data
            break

    if not data:
        reply = _response_text(payload)
        raise ValueError(
            "图像模型没有返回图片"
            + (f"，它的回复是：{reply[:400]}" if reply else "，也没有给出说明")
        )
    return {
        "mime": mime or "image/png",
        "data": data,
        "model": str(payload.get("model") or "").strip(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


async def _publish_image(
    mime: str,
    data: bytes,
    *,
    context: Any,
    index: int,
    requested_size: str,
) -> dict[str, Any]:
    """落盘并登记成产物，返回给模型/前端看的图片描述。"""
    extension = ".png"
    if "jpeg" in mime or "jpg" in mime:
        extension = ".jpg"
    elif "webp" in mime:
        extension = ".webp"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"generated-image-{stamp}-{index + 1}-{uuid.uuid4().hex[:6]}{extension}"

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as handle:
            handle.write(data)
            tmp_path = Path(handle.name)

        parent_conversation_id, parent_trace_id, _run_id = resolve_delegation_parent_scope(
            context
        )
        artifact = await publish(
            tmp_path,
            filename,
            owner_user_id=context.user_id,
            user_name=(getattr(context, "user_dimensions", None) or {}).get("user_name"),
            conversation_id=getattr(context, "conversation_id", None),
            parent_conversation_id=parent_conversation_id,
            parent_trace_id=parent_trace_id,
            artifact_type="image",
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 清理失败不影响出图结果
                logger.warning("[generate_image] 临时文件清理失败: %s", tmp_path)

    width, height = _png_dimensions(data)
    parsed_ok = bool(width and height)
    if not parsed_ok:
        # 前端按宽高算占位比例，缺了会跳版。网关若回的是 jpg/webp，PNG 头解析不出来，
        # 就退回请求尺寸——它大概率等于真实尺寸，总比让前端拿到 null 强。
        fallback = _SIZE_RE.match(requested_size or "")
        if fallback:
            width = int(fallback.group(1))
            height = int(fallback.group(2))
    payload: dict[str, Any] = {
        "filename": artifact.filename,
        "mime_type": artifact.mime_type or mime,
        "size": artifact.size or len(data),
        "width": width,
        "height": height,
        "download_url": artifact.download_url,
    }
    if not parsed_ok:
        payload["size_estimated"] = True
    # requested_size 为空表示「没指定尺寸、跟随原图」，这时不存在「要的和给的不一致」。
    if requested_size and width and height and f"{width}x{height}" != requested_size:
        # 网关对不支持的尺寸不会报错，只会静默按默认尺寸出图。记下真实尺寸，
        # 让模型能告诉用户「你要的是 1664x928，实际给的是这张」。
        payload["actual_size"] = f"{width}x{height}"
    return payload


@tool
async def generate_image(
    prompt: str,
    size: str | None = None,
    n: int | None = None,
) -> dict[str, Any]:
    """Generate an image from a text prompt and return ready-to-use download URLs.

    Use this when the user asks you to create/draw/produce a picture, poster, cover,
    illustration, icon or any other visual asset. Do not describe an image as if you
    had produced it — call this tool instead.

    ``prompt`` should be a self-contained visual description in the user's language,
    including subject, composition, colour and style. If the user only says something
    vague like "a poster for the annual meeting", expand it into a concrete scene
    description before calling.

    ``size`` is optional; when omitted the platform default is used. Pick it from the
    allowed list documented in the tool description (they are written as
    ``宽x高``, e.g. ``1664x928``). Never invent a size — an unsupported value is
    rejected by this tool with the list of allowed values.

    ``n`` is how many images to produce in this one call (capped by the platform
    limit shown in the tool description). Pass it only when the user explicitly asks
    for several options of the same picture (e.g. "给我两张挑一张" / "出 2 张不同构图");
    every image is billed separately, so leave it at 1 otherwise.
    """
    started = time.monotonic()

    config = await load_image_generation_config()
    if not config["enabled"]:
        return {
            "ok": False,
            "error": (
                "图像生成功能未开启：请联系管理员在「系统设置 → 图像生成」里打开开关，"
                "并确认当前智能体的工具清单中已勾选 generate_image。"
            ),
        }

    missing = [
        label
        for key, label in _FIELD_LABELS.items()
        if not str(_value_of(config, key) or "").strip()
    ]
    if missing:
        return {
            "ok": False,
            "error": (
                "图像生成配置不完整，缺少：" + "、".join(missing)
                + "。请管理员到「系统设置 → 图像生成」补齐后再试。"
            ),
        }

    try:
        api_key = _decode_api_key(config["api_key"])
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    text = str(prompt or "").strip()
    if not text:
        return {"ok": False, "error": "缺少 prompt：请给出想要生成的画面描述。"}

    allowed_sizes = config["allowed_sizes"]
    target_size = config["default_size"]

    # 用户在输入框「图像参数」里点选的尺寸优先级最高：它是 execution_options 里的结构化参数，
    # 点过就代表明确意图，模型自己挑的值一律让位（否则「勾了 512 却出 1024」就成了玄学）。
    # 不在白名单时忽略并回落默认，而不是报错：用户看不到工具的返回值，报错只会变成一次
    # 莫名其妙的生成失败，且模型多半会拿同一个错值重试。
    preferred_size = normalize_size(get_execution_option("image_size"))
    if preferred_size and (not allowed_sizes or preferred_size in allowed_sizes):
        target_size = preferred_size
        logger.info("[image_generation] 采用用户指定尺寸 %s", preferred_size)
    else:
        if preferred_size:
            logger.warning(
                "[image_generation] 用户指定尺寸 %s 不在白名单 %s，回落默认尺寸 %s",
                preferred_size,
                allowed_sizes,
                target_size,
            )
        requested_size = size.strip() if isinstance(size, str) and size.strip() else ""
        if requested_size:
            normalized = normalize_size(requested_size)
            if normalized is None:
                return {
                    "ok": False,
                    "error": (
                        f"尺寸「{requested_size}」格式不对，必须写成 宽x高（例如 1664x928）。"
                        f"可用尺寸：{'、'.join(allowed_sizes) or '（未配置）'}"
                    ),
                }
            if allowed_sizes and normalized not in allowed_sizes:
                # 把可用值直接告诉模型，否则它只会原样重试同一个错误尺寸。
                return {
                    "ok": False,
                    "error": (
                        f"尺寸「{normalized}」不在允许范围内。可用尺寸："
                        f"{'、'.join(allowed_sizes)}。请从中挑一个最接近用户需求的。"
                    ),
                }
            target_size = normalized

    max_images = int(config["max_images_per_call"])
    requested_n = 1
    if n is not None:
        try:
            requested_n = int(n)
        except (TypeError, ValueError):
            requested_n = 1
    requested_n = max(1, requested_n)
    clamped = requested_n > max_images
    count = min(requested_n, max_images)

    full_prompt = text
    if config["prompt_prefix"]:
        full_prompt = f"{config['prompt_prefix']} {text}"

    # 注意：get_current_agent_context() 是同步的（读 contextvar），不要 await。
    context = get_current_agent_context()
    if context is None or getattr(context, "user_id", None) is None:
        return {"ok": False, "error": "当前会话缺少用户身份，无法保存生成的图片。"}

    images: list[dict[str, Any]] = []
    upstream_model = ""

    def _fail(message: str) -> dict[str, Any]:
        """失败收口：已经出来的图不能因为后面一张失败就一起丢掉。"""
        if not images:
            return {"ok": False, "error": message}
        return {
            "ok": True,
            "partial": True,
            "model": upstream_model or config["model"],
            "size": target_size,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "images": images,
            "warning": message,
            "hint": (
                "只成功了一部分：如实告诉用户成品有几张、为什么剩下的没出来，"
                "不要当成全部成功。仍然要用 markdown 图片语法引用已有的 download_url。"
            ),
        }

    for index in range(count):
        try:
            generated = await request_image_once(
                config=config,
                prompt=full_prompt,
                size=target_size,
                api_key=api_key,
            )
        except (_TransientError, PermissionError, ValueError) as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - 兜底，避免工具抛栈打断整轮
            logger.exception("[generate_image] 调用图像模型失败")
            return _fail(f"调用图像模型失败：{_error_text(exc)}")

        if not upstream_model:
            upstream_model = str(generated["model"] or "").strip()

        try:
            images.append(
                await _publish_image(
                    generated["mime"],
                    generated["data"],
                    context=context,
                    index=index,
                    requested_size=target_size,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[generate_image] 产物登记失败")
            return _fail(f"图片已生成但保存失败：{_error_text(exc)}")

    result: dict[str, Any] = {
        "ok": True,
        "model": upstream_model or config["model"],
        "size": target_size,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "images": images,
        "hint": (
            "图片已保存为平台产物。请在最终回答里用 markdown 图片语法引用 download_url "
            "（形如 ![描述](download_url)），不要改写或猜测这个地址。"
        ),
    }
    mismatched = [img["actual_size"] for img in images if img.get("actual_size")]
    if mismatched:
        result["size_mismatch"] = True
        result["note"] = (
            f"你请求的尺寸是 {target_size}，但服务端实际返回的是 {'、'.join(mismatched)}。"
            "如实告诉用户实际尺寸，不要声称按原尺寸出图。"
        )
    if clamped:
        result["requested_n_clamped"] = True
        clamp_note = (
            f"单次最多生成 {max_images} 张，已按上限处理（你请求了 {requested_n} 张）。"
        )
        result["note"] = f"{result['note']} {clamp_note}" if result.get("note") else clamp_note
    return result


def _path_within(path: Path, root: Path) -> bool:
    """``path`` 是否位于 ``root`` 之内（两侧都已 resolve，防 ``..`` 穿越）。"""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _pick_target_size(
    config: dict[str, Any],
    requested: str | None,
    *,
    follow_source_when_unset: bool = False,
) -> tuple[str, str | None]:
    """决定本次出图尺寸，返回 ``(尺寸, 错误文案)``。

    ``follow_source_when_unset`` 只给改图用：返回空串表示**请求体里不带 size**，
    让模型按原图比例出图。这一点很关键——原图是 928×1664 的竖版，若这里回落到
    默认的 1024x1024，用户拿到的就是一张被压成方形的图。

    与 ``generate_image`` 里的判定顺序保持一致：
    用户在输入框「图像参数」点选的尺寸优先级最高（结构化参数代表明确意图），
    其次是模型自己传的值，最后回落默认尺寸。不在白名单时**报错并列出可用值**，
    因为网关对不支持的尺寸不报错、只会静默按默认出图。
    """
    allowed_sizes = config["allowed_sizes"]
    target = config["default_size"]

    preferred = normalize_size(get_execution_option("image_size"))
    if preferred and (not allowed_sizes or preferred in allowed_sizes):
        return preferred, None
    if preferred:
        logger.warning(
            "[image_edit] 用户指定尺寸 %s 不在白名单 %s，回落默认尺寸 %s",
            preferred,
            allowed_sizes,
            target,
        )

    raw = requested.strip() if isinstance(requested, str) and requested.strip() else ""
    if not raw:
        return ("", None) if follow_source_when_unset else (target, None)
    normalized = normalize_size(raw)
    if normalized is None:
        return target, (
            f"尺寸「{raw}」格式不对，必须写成 宽x高（例如 1664x928）。"
            f"可用尺寸：{'、'.join(allowed_sizes) or '（未配置）'}"
        )
    if allowed_sizes and normalized not in allowed_sizes:
        return target, (
            f"尺寸「{normalized}」不在允许范围内。可用尺寸：{'、'.join(allowed_sizes)}。"
            "请从中挑一个最接近用户需求的。"
        )
    return normalized, None


def _looks_like_multiple(value: str) -> bool:
    """粗判「把好几张图塞进了一个只收一张的参数」。

    模型经常写成 ``a.png,b.png`` 或换行分隔，直接当成一个路径去读只会得到
    「找不到文件」，用户还得猜是哪里错了。只查分隔符，不查空格——路径本身
    可能带空格。
    """
    return any(ch in value for ch in ("\n", "\r", ",", "，", "、", ";"))


async def _read_source_image(image: str, *, context: Any) -> tuple[str, bytes]:
    """把图片引用解析成 ``(mime, bytes)``，并做归属、存在性与体积校验。

    两种来源都要支持：

    * 用户上传的附件——消息上下文里给的是工作区绝对路径；
    * 上一轮生成的成品——模型手里只有带 token 的 ``download_url``，
      所以要能从链接反查出产物、再回到本地文件。

    **归属校验不能省**：这些路径最终来自用户输入，直接 ``open()`` 等于开了一个
    读服务器任意文件的口子。范围收紧到「当前用户自己的工作区」。
    """
    raw = str(image or "").strip()
    if not raw:
        raise ValueError(
            "缺少 image：请给出一张要修改的图片——用户上传的附件路径，"
            "或之前生成图片的 download_url（要连 token 一起传）。"
        )

    from app.services.ai.runtime.agentscope.workspace import (
        resolve_workspace_root,
        resolve_workspace_user_key,
    )

    try:
        root = Path(await resolve_workspace_root(ensure_exists=False)).resolve()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法定位用户工作区：{_error_text(exc)}") from exc

    try:
        user_key = resolve_workspace_user_key(
            user_id=getattr(context, "user_id", None),
            user_name=getattr(context, "user_name", None),
        )
    except Exception as exc:  # noqa: BLE001
        # 不能退化成「整个工作区根」：那样其他用户的 uploads/generated 全都可读，
        # 等于把越权读取做成了兜底路径。算不出身份就拒绝，让用户重开会话。
        raise ValueError(
            "无法确认当前用户的工作区，出于安全考虑不能读取这张图片。请重新进入会话后再试。"
        ) from exc
    if not user_key:
        raise ValueError("无法确认当前用户的工作区，出于安全考虑不能读取这张图片。")
    scope = (root / user_key).resolve()

    match = _ARTIFACT_URL_RE.search(raw)
    if match:
        from app.services.ai.tools.generated_file_service import (
            resolve_workspace_artifact,
        )

        artifact = await resolve_workspace_artifact(match.group(1), match.group(2))
        if artifact is None:
            raise ValueError(
                "这张图已失效或不属于当前用户，没法再改它。"
                "请让用户重新上传原图，或重新生成一张。"
            )
        path = Path(artifact.path).resolve()
        if not _path_within(path, root) or not path.is_file():
            raise ValueError("这张图的文件已经不在原位置了，请让用户重新上传或重新生成。")
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = scope / candidate
        path = candidate.resolve()
        if not _path_within(path, scope):
            raise ValueError(
                "只能修改当前用户自己工作区里的图片（uploads/ 或 generated/）。"
                "如果用户想改的是别处的图片，请让他先上传。"
            )
        if not path.is_file():
            raise ValueError(f"找不到这张图：{path.name}，请让用户重新上传。")

    data = path.read_bytes()
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError(
            f"这张图太大（{len(data) / 1048576:.1f} MB），改图上限 "
            f"{MAX_SOURCE_IMAGE_BYTES // 1048576} MB（依赖里没有图像编解码库，"
            "平台无法自动压缩）。请让用户换一张更小的图，或先压缩后重新上传。"
        )
    mime = sniff_image_mime(data, strict=True)
    if not mime:
        raise ValueError("这个文件不是可识别的图片格式，改图只支持 PNG / JPG / WEBP。")
    return mime, data


@tool
async def edit_image(
    image: str,
    instruction: str,
    reference: str | None = None,
    size: str | None = None,
) -> dict[str, Any]:
    """Modify an existing picture according to an instruction, keeping everything else intact.

    Use this when the user wants to change something **inside** a picture they
    already have——把背景换掉、改画风、加减物件、把草图改成成品，而不是从零画一张新的。
    If the user wants a brand-new picture and only borrows the style or subject of
    an existing one, use generate_image instead.

    ``image`` is the picture to modify: the workspace path of an image the user
    uploaded, or the ``download_url`` of an image produced earlier in this
    conversation (pass the whole URL, including its ``token`` parameter).

    ``instruction`` is what to change, in the user's own language. Say explicitly
    what must stay the same when it matters, e.g.
    "把背景换成浅蓝色，杯子和拉花保持不动"。只说「改得好看点」这类要求没有可执行的结果。

    ``reference`` is an optional second picture that only informs **how** the result
    should look——配色、风格、光影、构图、材质。它本身不会被修改，也不会原样出现在
    结果里。典型用法：把「这张杯子的照片」改成「参考这张插画的配色」。
    只在用户确实给了两张图时才填；用户只给一张就别编一个出来。

    一次最多两张（主图 + 参考图）。这是刻意的限制，不是网关限制：网关虽接受 4 张，
    但实测 3 张开始走样、4 张会静默漏掉内容，2 张才是可靠区间。

    ``size`` is optional——省略时按原图比例出图，这对「只改内容」的诉求通常最合适。
    需要换输出尺寸时再从工具描述列出的尺寸里挑一个。
    """
    started = time.monotonic()

    config = await load_image_generation_config()
    if not config["enabled"]:
        return {
            "ok": False,
            "error": (
                "图像生成功能未开启：请联系管理员在「系统设置 → 图像生成」里打开开关，"
                "并确认当前智能体的工具清单中已勾选 edit_image。"
            ),
        }

    missing = [
        label
        for key, label in _FIELD_LABELS.items()
        if not str(_value_of(config, key) or "").strip()
    ]
    if missing:
        return {
            "ok": False,
            "error": (
                "图像生成配置不完整，缺少：" + "、".join(missing)
                + "。请管理员到「系统设置 → 图像生成」补齐后再试。"
            ),
        }

    try:
        api_key = _decode_api_key(config["api_key"])
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    text = str(instruction or "").strip()
    if not text:
        return {
            "ok": False,
            "error": "缺少 instruction：请说明要把这张图改成什么样。",
        }

    # get_current_agent_context() 是同步的（读 contextvar），不要 await。
    context = get_current_agent_context()
    if context is None or getattr(context, "user_id", None) is None:
        return {"ok": False, "error": "当前会话缺少用户身份，无法保存修改后的图片。"}

    try:
        source_mime, source_bytes = await _read_source_image(image, context=context)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    # 参考图是可选的第二张。它的错误要指名道姓，否则用户看到「找不到图片」
    # 会以为是主图丢了，转头去重新上传已经好好的那张。
    sources: list[tuple[str, bytes]] = [(source_mime, source_bytes)]
    reference_text = str(reference or "").strip()
    if reference_text:
        if _looks_like_multiple(reference_text):
            return {
                "ok": False,
                "error": (
                    "参考图只能给一张。把要改的那张作为 image 传入，"
                    "参考的那张作为 reference 传入，一共两张。"
                ),
            }
        try:
            reference_mime, reference_bytes = await _read_source_image(
                reference_text, context=context
            )
        except ValueError as exc:
            return {"ok": False, "error": f"参考图有问题：{exc}"}
        sources.append((reference_mime, reference_bytes))

    target_size, size_error = _pick_target_size(
        config, size, follow_source_when_unset=True
    )
    if size_error:
        return {"ok": False, "error": size_error}

    # 刻意不加 config['prompt_prefix']：那个前缀是给「从零画」用的风格引导，
    # 叠到改图的指令上会跟「保持主体不变」打架——用户要的是改一处，
    # 不是让整张图被重新描述一遍。改图的指令就是 instruction 本身。
    try:
        generated = await request_image_once(
            config=config,
            prompt=text,
            size=target_size,
            api_key=api_key,
            source_images=sources,
        )
    except (_TransientError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 兜底，避免工具抛栈打断整轮
        logger.exception("[edit_image] 调用图像模型失败")
        return {"ok": False, "error": f"调用图像模型失败：{_error_text(exc)}"}

    try:
        published = await _publish_image(
            generated["mime"],
            generated["data"],
            context=context,
            index=0,
            requested_size=target_size,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[edit_image] 产物登记失败")
        return {"ok": False, "error": f"图片已生成但保存失败：{_error_text(exc)}"}

    actual = published.get("actual_size") or (
        f"{published['width']}x{published['height']}"
        if published.get("width") and published.get("height")
        else ""
    )
    result: dict[str, Any] = {
        "ok": True,
        "model": str(generated["model"] or "").strip() or config["model"],
        # 空 target_size 表示没强制尺寸、按原图比例出图，如实说明而不是留一个空串。
        "size": target_size or actual or "跟随原图",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "source": {"mime_type": source_mime, "bytes": len(source_bytes)},
        "reference": (
            {"mime_type": sources[1][0], "bytes": len(sources[1][1])}
            if len(sources) > 1
            else None
        ),
        "image": published,
        "note": (
            "这是基于原图修改的结果，原图没有被覆盖。"
            "用 markdown 图片语法引用 download_url，并说明改了什么、什么没变。"
            "若这张还要接着改，把它的 download_url 作为下一次 image 参数传进来。"
        ),
    }
    if published.get("actual_size"):
        result["size_mismatch"] = (
            f"你请求的尺寸是 {target_size}，但服务端实际返回的是 {published['actual_size']}。"
            "如实告诉用户实际尺寸。"
        )
    return result


def _value_of(config: dict[str, Any], key: str) -> Any:
    """把配置键映射回规范化后的字段名。"""
    return {
        "image_api_base_url": config.get("api_base_url"),
        "image_api_key": config.get("api_key"),
        "image_model": config.get("model"),
    }.get(key, "")


async def preview_image_generation(
    *,
    prompt: str,
    size: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    api_format: str | None = None,
) -> dict[str, Any]:
    """「系统设置 → 图像生成 → 测试生成」用的一次性调用：**不落盘、不发布产物**。

    配置字段留空就用库里已保存的值，填了就用传进来的值——管理员改完地址/模型还没保存
    也能先试通。密钥尤其要这样处理：设置页手里是脱敏串（``abc****wxyz``），拿它去调
    只会 401，只能回落到库里的真实值。

    成功时把图片编码成 data URL 直接回给设置页预览；响应体是几 MB 级别的 base64，
    这是一次性验证场景，可以接受（换成产物链接就意味着要落盘，「不落库」就不成立了）。
    """
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("请先填写要生成的画面描述")
    if len(text) > 2000:
        raise ValueError("画面描述过长（上限 2000 字），请精简后重试")

    config = await load_image_generation_config()
    if not config["enabled"]:
        raise ValueError(
            "图像生成总开关未开启，请先打开开关（测试同样走真实调用，需要开关打开）"
        )

    effective_base = str(base_url or "").strip().rstrip("/") or config["api_base_url"]
    if not effective_base:
        raise ValueError("请先填写 API 地址")
    effective_model = str(model or "").strip() or config["model"]
    if not effective_model:
        raise ValueError("请先填写或选择图像模型")

    # 格式也必须能覆盖：管理员刚把协议从聊天改成原生、还没保存就点「测试生成」，
    # 若这里读库里的旧值，测出来的结果是上一个协议的，等于假装测过了。
    raw_format = str(api_format or "").strip()
    effective_format = raw_format if raw_format in API_FORMATS else config["api_format"]

    raw_key = str(api_key or "").strip()
    if raw_key and "****" in raw_key:
        raw_key = ""  # 脱敏串不是密钥
    effective_key = _decode_api_key(raw_key or config["api_key"])
    if not effective_key:
        raise ValueError("请先填写 API 密钥")

    allowed = config["allowed_sizes"]
    target_size = config["default_size"]
    requested = str(size or "").strip()
    if requested:
        normalized = normalize_size(requested)
        if normalized is None:
            raise ValueError(
                f"尺寸「{requested}」格式不对，必须写成 宽x高（例如 1664x928）"
            )
        if allowed and normalized not in allowed:
            raise ValueError(
                f"尺寸「{normalized}」不在允许范围内，可用：{'、'.join(allowed)}"
            )
        target_size = normalized

    full_prompt = f"{config['prompt_prefix']} {text}" if config["prompt_prefix"] else text
    generated = await request_image_once(
        config={
            **config,
            "api_base_url": effective_base,
            "model": effective_model,
            "api_format": effective_format,
        },
        prompt=full_prompt,
        size=target_size,
        api_key=effective_key,
    )

    mime = generated["mime"]
    data = generated["data"]
    width, height = _png_dimensions(data)
    return {
        "image": f"data:{mime};base64,{base64.b64encode(data).decode()}",
        "mime_type": mime,
        "width": width,
        "height": height,
        "size": target_size,
        "model": generated["model"] or effective_model,
        # 把实际生效的协议回给设置页：两种协议的出图体积/画质差很多，
        # 排查「为什么这次出来的图不一样」时这是第一个该看的字段。
        "api_format": effective_format,
        "bytes": len(data),
        "elapsed_ms": generated["elapsed_ms"],
    }


async def preview_image_edit(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    api_format: str | None = None,
) -> dict[str, Any]:
    """「系统设置 → 图像生成 → 测试改图」用的一次性调用：**不落盘、不发布产物**。

    存在的理由：配置一个「能文生图但不能改图」的模型（例如只有 ``openai`` 端点的
    ``kolors``）是完全合法的，「测试生成」也会通过，但改图会全 500。这一项专门回答
    「这个模型到底能不能吃图输入」，答案是花一次调用换来的，比事后看用户报错便宜。

    用内置样图，指令固定为换背景——改动肉眼可验。错误按「是不是改图能力问题」分成
    两类回给设置页：能力问题要引导去换模型，其余按普通连通性错误处理。
    """
    config = await load_image_generation_config()
    if not config["enabled"]:
        raise ValueError(
            "图像生成总开关未开启，请先打开开关（测试同样走真实调用，需要开关打开）"
        )

    effective_base = str(base_url or "").strip().rstrip("/") or config["api_base_url"]
    if not effective_base:
        raise ValueError("请先填写 API 地址")
    effective_model = str(model or "").strip() or config["model"]
    if not effective_model:
        raise ValueError("请先填写或选择图像模型")

    # 与 edit_image 一致：改图固定走聊天接口。入参留着只为兼容调用方，
    # 传什么都不影响——格式配置管生图，管不到改图。
    effective_format = API_FORMAT_OPENAI_CHAT

    raw_key = str(api_key or "").strip()
    if raw_key and "****" in raw_key:
        raw_key = ""  # 脱敏串不是密钥
    effective_key = _decode_api_key(raw_key or config["api_key"])
    if not effective_key:
        raise ValueError("请先填写 API 密钥")

    sample = base64.b64decode(TEST_EDIT_SAMPLE_PNG_B64)
    # 尺寸跟随样图：不传 size 时模型按原图比例出图，正是改图的常规用法。
    target_size = config["default_size"]
    instruction = "把背景换成浅蓝色，主体保持不变"

    try:
        generated = await request_image_once(
            config={
                **config,
                "api_base_url": effective_base,
                "model": effective_model,
                "api_format": effective_format,
            },
            prompt=instruction,
            size=target_size,
            api_key=effective_key,
            source_images=[("image/png", sample)],
        )
    except (_TransientError, PermissionError) as exc:
        raise ValueError(f"连接图像服务失败：{exc}") from exc
    except ValueError as exc:
        message = str(exc)
        lowered = message.lower()
        if any(
            token in lowered
            for token in ("no image", "没有返回图片", "do_request_failed", "upstream error")
        ):
            raise ValueError(
                f"这个模型看起来不支持改图（{_error_text(exc)}）。"
                "改图需要能接收图片输入的多模态图像模型，请换一个模型再试。"
            ) from exc
        raise

    mime = generated["mime"]
    data = generated["data"]
    width, height = _png_dimensions(data)
    return {
        "image": f"data:{mime};base64,{base64.b64encode(data).decode()}",
        "mime_type": mime,
        "width": width,
        "height": height,
        "size": target_size,
        "model": generated["model"] or effective_model,
        "api_format": effective_format,
        "bytes": len(data),
        "elapsed_ms": generated["elapsed_ms"],
        "instruction": instruction,
        "supports_edit": True,
    }


async def list_image_models(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """拉取 ``GET {API 地址}/models``，供系统设置里的「从列表选择」用。

    由后端代发而不是前端直连：密钥只应留在后端，且浏览器直连第三方网关会被跨域拦下。
    允许传入尚未保存的地址/密钥，这样管理员可以先验证再保存。
    """
    config = await load_image_generation_config()
    target_base = str(base_url or "").strip().rstrip("/") or config["api_base_url"]
    if not target_base:
        raise ValueError("请先填写 API 地址")

    target_key = str(api_key or "").strip()
    if not target_key:
        target_key = _decode_api_key(config["api_key"])

    url = f"{target_base}/models"
    headers: dict[str, str] = {}
    if target_key:
        headers["Authorization"] = f"Bearer {target_key}"

    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(url, headers=headers)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise ValueError(f"连接失败：{_error_text(exc)}") from exc

    if response.status_code >= 400:
        raise PermissionError(
            f"接口返回 {response.status_code}：{response.text[:400]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("接口返回的不是合法 JSON") from exc

    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("接口返回里没有 data 数组")

    models: list[str] = []
    for item in items:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id and model_id not in models:
            models.append(model_id)
    models.sort()
    return {"models": models, "endpoint": url, "count": len(models)}
