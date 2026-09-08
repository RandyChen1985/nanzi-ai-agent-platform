import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.schemas.browser import BrowserToolResult
from app.services.ai.browser.browser_runtime import browser_runtime
from app.services.ai.browser.browser_worker import BrowserEnvironmentError, BrowserWaitTimeout
from app.services.ai.tools.tool_compat import tool


def _context_or_error():
    context = get_current_agent_context()
    if context is None or context.user_id is None:
        raise RuntimeError("浏览器工具需要登录用户上下文")
    return context


def _session_id(context) -> str:
    session_id = getattr(context, "browser_session_id", None)
    if not session_id:
        raise RuntimeError("当前对话尚未绑定右侧浏览器会话")
    return str(session_id)


async def _owned_session(context):
    from app.services.ai.browser.browser_session_service import BrowserSessionService

    session_id = _session_id(context)
    async with AsyncSessionLocal() as db:
        return await BrowserSessionService(db).get_owned_session(
            user_id=int(context.user_id), session_id=session_id
        )


async def _persist_browser_result(context: Any, result: Any) -> None:
    """把页面动作后的 URL 和标题写回会话，保证恢复时不会回到旧页面。"""
    if not getattr(result, "url", None) and not getattr(result, "title", None):
        return
    session_id = _session_id(context)
    async with AsyncSessionLocal() as db:
        from app.services.ai.browser.browser_session_service import BrowserSessionService

        service = BrowserSessionService(db)
        await service.update_state(
            user_id=int(context.user_id),
            session_id=session_id,
            url=getattr(result, "url", None),
            title=getattr(result, "title", None),
        )
        await db.commit()


async def _browser_result_json(context: Any, result: Any) -> str:
    await _persist_browser_result(context, result)
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


@tool
async def browser_open(url: str = "https://www.baidu.com/", profile_id: Optional[str] = None) -> str:
    """打开或恢复当前用户的服务端浏览器，会话登录状态可跨对话复用。"""
    context = _context_or_error()
    try:
        session = await browser_runtime.open_for_user(
            user_id=int(context.user_id),
            conversation_id=getattr(context, "conversation_id", None),
            url=url,
            profile_id=profile_id,
        )
        context.browser_session_id = session.id
        snapshot = await browser_runtime.snapshot(session.id)
        payload = snapshot.model_dump(mode="json")
        payload["approval_mode"] = session.approval_mode
        return json.dumps(payload, ensure_ascii=False)
    except BrowserEnvironmentError as exc:
        return json.dumps({
            "error": "BROWSER_ENVIRONMENT_MISSING",
            "message": str(exc),
            "instruction": "服务端浏览器环境（Playwright/Chromium）尚未安装。请礼貌告知用户或管理员在服务器终端执行：playwright install chromium 即可启用网页自动化能力。",
        }, ensure_ascii=False)


@tool
async def browser_snapshot() -> str:
    """读取当前服务端浏览器页面的语义快照，返回 target_ref、滚动元数据和页面文本；目标未出现时先滚动，不要伪造引用或无状态重复读取。"""
    context = _context_or_error()
    session = await _owned_session(context)
    snapshot = await browser_runtime.snapshot(session.id)
    return json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)


@tool
async def browser_scroll(
    direction: Literal["up", "down", "top", "bottom"] = "down",
    amount: int = 640,
) -> str:
    """滚动当前服务端浏览器页面，并返回包含截图、页面文本和最新 target_ref 的新快照。"""
    context = _context_or_error()
    session = await _owned_session(context)
    snapshot = await browser_runtime.scroll(
        session.id,
        direction=direction,
        amount=amount,
    )
    return json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)


@tool
async def browser_press(
    key: str,
    target_ref: Optional[str] = None,
    snapshot_id: Optional[str] = None,
) -> str:
    """向当前焦点或快照目标发送有限键盘操作，如 Enter、Tab、Escape 或方向键。"""
    context = _context_or_error()
    session = await _owned_session(context)
    if target_ref and not snapshot_id:
        raise ValueError("按目标发送键盘操作需要 snapshot_id")
    result = await browser_runtime.press(
        session.id,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
        key=key,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_wait_for(
    condition: Literal["text", "url", "target", "page_state", "element", "network_idle", "ready"] = "text",
    value: str = "",
    target_ref: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    timeout_ms: int = 10000,
) -> str:
    """等待页面文本、URL、可见目标元素或网络加载完成，并返回最新快照。超时上限支持至 30000ms（最高30秒）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    try:
        snapshot = await browser_runtime.wait_for(
            session.id,
            condition=condition,
            value=value,
            target_ref=target_ref,
            snapshot_id=snapshot_id,
            timeout_ms=timeout_ms,
        )
        return json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
    except (BrowserWaitTimeout, TimeoutError) as exc:
        # 页面等待超时非致命异常：自动截取并返回当前最新快照，避免红色工具报错阻断模型决策
        try:
            current_snapshot = await browser_runtime.snapshot(session.id)
            payload = current_snapshot.model_dump(mode="json")
            payload["wait_status"] = "timeout"
            payload["wait_warning"] = (
                f"页面在设定时间内未达成等待条件（{exc}）。已为您返回当前最新的页面快照，"
                "请检查页面是否正在加载中、是否弹出验证码或已有部分数据可直接读取。"
            )
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return json.dumps({
                "wait_status": "timeout",
                "message": f"页面等待超时（{exc}）。建议调用 browser_snapshot 查看页面当前状态。",
            }, ensure_ascii=False)


@tool
async def browser_select_option(
    target_ref: str,
    snapshot_id: str,
    value: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """选择当前快照中的原生下拉框选项。value 和 label 至少提供一个。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.select_option(
        session.id,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
        value=value,
        label=label,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_read_visible() -> str:
    """读取当前截图视口内的页面文字，适合长列表等非交互内容。"""
    context = _context_or_error()
    session = await _owned_session(context)
    payload = await browser_runtime.read_visible(session.id)
    return json.dumps(payload, ensure_ascii=False)


@tool
async def browser_hover(target_ref: str, snapshot_id: str) -> str:
    """悬停当前快照目标，以展开菜单、日期选择器或提示信息。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.hover(session.id, target_ref=target_ref, snapshot_id=snapshot_id)
    return await _browser_result_json(context, result)


@tool
async def browser_drag(
    source_ref: str,
    target_ref: str,
    snapshot_id: str,
) -> str:
    """将当前快照中的一个目标拖到另一个目标。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.drag(
        session.id,
        source_ref=source_ref,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_slider_drag(
    source_ref: str,
    snapshot_id: str,
    distance_px: int | None = None,
    gap_target_ref: str | None = None,
) -> str:
    """对滑块验证码（如百度安全验证、曲线/缺口匹配、极验滑块等）执行拟人轨迹拖拽。

    内置三阶贝塞尔曲线与物理加速度/减速/回弹防风控算法。
    - ``source_ref``：滑块按钮元素在快照中的语义引用标识（如 'e12'）；
    - ``distance_px``：要向右拖动的目标像素距离（可根据视觉图像测量或估算）；
    - ``gap_target_ref``：或者传入目标缺口/曲线落点元素的引用标识，系统会自动计算两者的像素间距；
    - 两者至少提供其一。执行后会自动返回拖动后的新页面状态。
    """
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.slider_drag(
        session.id,
        source_ref=source_ref,
        snapshot_id=snapshot_id,
        distance_px=distance_px,
        gap_target_ref=gap_target_ref,
    )
    return await _browser_result_json(context, result)


async def _browser_history_action(action: Literal["back", "forward", "reload"]) -> str:
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.navigate_history(session.id, action=action)
    return await _browser_result_json(context, result)


@tool
async def browser_back() -> str:
    """返回当前浏览器历史记录上一页。"""
    return await _browser_history_action("back")


@tool
async def browser_forward() -> str:
    """前进当前浏览器历史记录下一页。"""
    return await _browser_history_action("forward")


@tool
async def browser_reload() -> str:
    """刷新当前浏览器页面并返回页面信息。"""
    return await _browser_history_action("reload")


@tool
async def browser_tabs() -> str:
    """列出当前浏览器会话的标签页，不返回令牌或 Playwright 对象。"""
    context = _context_or_error()
    session = await _owned_session(context)
    tabs = await browser_runtime.list_tabs(session.id)
    return json.dumps([tab.model_dump(mode="json") for tab in tabs], ensure_ascii=False)


@tool
async def browser_switch_tab(tab_id: str) -> str:
    """切换到当前浏览器会话中的指定标签页。"""
    context = _context_or_error()
    session = await _owned_session(context)
    info = await browser_runtime.switch_tab(session.id, tab_id)
    result = BrowserToolResult(
        session_id=session.id,
        action="switch_tab",
        url=info.url,
        title=info.title,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_close_tab(tab_id: str) -> str:
    """关闭当前浏览器会话中的指定标签页，至少保留一个标签页。"""
    context = _context_or_error()
    session = await _owned_session(context)
    info = await browser_runtime.close_tab(session.id, tab_id)
    result = BrowserToolResult(
        session_id=session.id,
        action="close_tab",
        url=info.url,
        title=info.title,
    )
    return await _browser_result_json(context, result)


def _browser_user_info(context: Any) -> dict[str, Any]:
    return {
        "user_id": getattr(context, "user_id", None),
        "id": getattr(context, "user_id", None),
        "user_name": getattr(context, "user_name", None),
        "username": getattr(context, "user_name", None),
    }


def _safe_browser_file_path(context: Any, file_path: str) -> Path:
    from app.services.ai.tools.generated_file_service import generated_files_root
    from app.utils.fs_access import get_user_private_workspace_root
    from app.utils.fs_paths import normalize_fs_path

    normalized = normalize_fs_path(file_path)
    candidate = Path(normalized).resolve() if normalized else None
    user_root = get_user_private_workspace_root(_browser_user_info(context))
    allowed_roots = [Path(user_root).resolve()] if user_root else []
    allowed_roots.append(generated_files_root().resolve())
    if candidate is None or not candidate.is_file() or not any(
        candidate == root or root in candidate.parents for root in allowed_roots
    ):
        raise ValueError("文件不存在或不属于当前用户允许的浏览器文件目录")
    return candidate


@tool
async def browser_upload(target_ref: str, snapshot_id: str, file_path: str) -> str:
    """把当前用户允许目录中的文件上传到快照目标，不返回服务器物理路径。"""
    context = _context_or_error()
    session = await _owned_session(context)
    source = _safe_browser_file_path(context, file_path)
    result = await browser_runtime.upload(
        session.id,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
        file_path=str(source),
    )
    return await _browser_result_json(context, result)


@tool
async def browser_download(target_ref: str, snapshot_id: str) -> str:
    """点击快照中的下载目标，并返回带时效能力链接的下载结果。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.download(
        session.id,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
    )
    await _persist_browser_result(context, result)
    download_path = str(result.data.get("download_path") or "")
    filename = str(result.data.get("filename") or "download")
    if not download_path:
        raise RuntimeError("浏览器下载结果缺少文件")
    from app.services.ai.tools.generated_file_service import publish

    artifact = await publish(
        download_path,
        filename,
        owner_user_id=context.user_id,
        user_name=(context.user_dimensions or {}).get("user_name"),
        conversation_id=context.conversation_id,
        trace_id=context.trace_id,
        artifact_type="browser_download",
    )
    payload = result.model_dump(mode="json")
    payload["data"] = artifact.to_tool_payload()
    return json.dumps(payload, ensure_ascii=False)


@tool
async def browser_click(
    target_ref: str,
    snapshot_id: str,
) -> str:
    """按快照中的语义 target_ref 点击页面元素；执行前由 AgentScope 运行时权限确认。"""
    context = _context_or_error()
    session_id = _session_id(context)
    async with AsyncSessionLocal() as db:
        from app.services.ai.browser.browser_session_service import BrowserSessionService

        session = await BrowserSessionService(db).get_owned_session(
            user_id=int(context.user_id), session_id=session_id
        )
        result = await browser_runtime.click(
            session_id,
            target_ref=target_ref,
            snapshot_id=snapshot_id,
            approval_mode=session.approval_mode,
            # 只有 AgentScope 已通过 check_permissions 才会进入工具调用；
            # confirmed 不暴露给模型，避免模型参数绕过 guarded 模式。
            confirmed=True,
        )
    return await _browser_result_json(context, result)


@tool
async def browser_fill(
    target_ref: str,
    snapshot_id: str,
    value: str,
) -> str:
    """向当前浏览器语义输入框填值；敏感值不会进入工具结果或审计预览。"""
    context = _context_or_error()
    session_id = _session_id(context)
    await _owned_session(context)
    result = await browser_runtime.fill(
        session_id,
        target_ref=target_ref,
        snapshot_id=snapshot_id,
        value=value,
        sensitive=None,
    )
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


@tool
async def browser_export_pdf(
    filename: str | None = None,
    print_background: bool = True,
) -> str:
    """将当前浏览器网页渲染导出为 A4 格式矢量 PDF 文件，并发布为会话可下载附件。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.export_pdf(
        session.id,
        filename=filename,
        print_background=print_background,
    )
    await _persist_browser_result(context, result)
    pdf_path = str(result.data.get("pdf_path") or "")
    pdf_name = str(result.data.get("filename") or "page.pdf")
    if not pdf_path:
        raise RuntimeError("浏览器 PDF 导出失败")
    from app.services.ai.tools.generated_file_service import publish

    artifact = await publish(
        pdf_path,
        pdf_name,
        owner_user_id=context.user_id,
        user_name=(context.user_dimensions or {}).get("user_name"),
        conversation_id=context.conversation_id,
        trace_id=context.trace_id,
        artifact_type="pdf",
    )
    payload = result.model_dump(mode="json")
    payload["data"] = artifact.to_tool_payload()
    return json.dumps(payload, ensure_ascii=False)


@tool
async def browser_extract_table(
    selector: str | None = None,
    max_rows: int = 50,
) -> str:
    """结构化解析提取网页中的 table 表格或网格数据，输出规整的 Markdown 表格与 JSON 列表。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.extract_table(
        session.id,
        selector=selector,
        max_rows=max_rows,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_handle_dialog(
    action: str = "accept",
    prompt_text: str | None = None,
) -> str:
    """预设原生 JavaScript 弹窗（alert/confirm/prompt）的自动应答策略（accept 确认或 dismiss 取消）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.handle_dialog(
        session.id,
        action=action,
        prompt_text=prompt_text,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_execute_js(
    script: str,
) -> str:
    """在当前浏览器页面执行 JavaScript 脚本并获取返回结果（支持复杂页面自动化，带资源上限但不是隔离沙箱）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.execute_js(
        session.id,
        script=script,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_check_auth() -> str:
    """智能探测当前网页的登录与认证状态（检查 Cookie、LocalStorage 与页面注销/登录标志）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.check_auth(session.id)
    return await _browser_result_json(context, result)


@tool
async def browser_get_network_logs(
    filter_url: str | None = None,
    limit: int = 20,
) -> str:
    """获取当前浏览器会话捕获的网络请求元数据（支持 URL 关键词过滤，不返回接口响应正文）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.get_network_logs(
        session.id,
        filter_url=filter_url,
        limit=limit,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_get_cookies(
    urls: list[str] | None = None,
) -> str:
    """获取当前浏览器会话指定 URL 或当前域名的 Cookie 元数据（Cookie 值会脱敏）。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.get_cookies(
        session.id,
        urls=urls,
    )
    return await _browser_result_json(context, result)


@tool
async def browser_set_cookies(
    cookies: list[dict[str, Any]],
) -> str:
    """向当前浏览器会话注入一组 Cookie（支持 name、value、domain、path 等字段），实现免密直登目标系统。"""
    context = _context_or_error()
    session = await _owned_session(context)
    result = await browser_runtime.set_cookies(
        session.id,
        cookies=cookies,
    )
    return await _browser_result_json(context, result)
