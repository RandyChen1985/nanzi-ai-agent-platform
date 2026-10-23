import os
import os.path
import logging
import sqlite3
import pandas as pd
import json
import uuid
import ast
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from typing import Optional, Dict, List, Any, Tuple
from app.core.context import get_current_agent_context
from app.services.ai.tools.tool_compat import tool

logger = logging.getLogger(__name__)

_LEGACY_SANDBOX_DIR = os.path.join("data", "sandbox")


def _resolve_sqlite_scratchpad_dir() -> str:
    """优先使用当前用户私有 sandbox；无 Agent 上下文时回退旧公共目录（单测/脚本）。"""
    from app.core.context import get_current_agent_context
    from app.utils.fs_access import get_user_sandbox_dir

    ctx = get_current_agent_context()
    if ctx and ctx.user_id is not None:
        user_info = {
            "user_id": ctx.user_id,
            "role": "admin" if ctx.is_admin else "user",
        }
        sandbox_dir = get_user_sandbox_dir(user_info)
        if sandbox_dir:
            return sandbox_dir
    return _LEGACY_SANDBOX_DIR


@tool
def sqlite_scratchpad(sql: str, session_id: str, import_data: str = None) -> str:
    """
    会话隔离的轻量 SQLite 数据分析临时沙箱，用于执行数据清洗、多维 SQL 分析等，主库无污染。
    
    Args:
        sql: 要在 SQLite 中执行的 SQL 语句。
        session_id: 隔离会话的标识符 (如 123)。
        import_data: 可选的 JSON 序列化数据（字典格式）。键为临时表名，值为字典列表（数据行）。
    """
    db_dir = _resolve_sqlite_scratchpad_dir()
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, f"sess_{session_id}.db")
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        
        # 1. 检查并导入临时数据
        if import_data:
            try:
                data_dict = json.loads(import_data)
                if isinstance(data_dict, dict):
                    for table_name, rows in data_dict.items():
                        if isinstance(rows, list) and len(rows) > 0:
                            df = pd.DataFrame(rows)
                            df.to_sql(table_name, conn, if_exists="replace", index=False)
            except Exception as import_err:
                return f"导入临时数据失败: {str(import_err)}"
                
        # 2. 执行 SQL 语句
        sql_stripped = sql.strip().upper()
        if sql_stripped.startswith("SELECT"):
            df_res = pd.read_sql_query(sql, conn)
            if df_res.empty:
                return "执行成功，查询结果为空。"
            try:
                return df_res.to_markdown(index=False)
            except ImportError:
                return df_res.to_string(index=False)
        else:
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
            return f"SQL 语句执行成功。受影响行数: {cursor.rowcount}"
            
    except Exception as e:
        return f"SQLite 沙箱执行异常: {str(e)}"
    finally:
        if conn:
            conn.close()

@tool
def directory_tree_navigator(path: str, suffix: str = None, keyword: str = None) -> str:
    """
    分层或递归检索目标目录下的结构，支持按文件后缀和文件名关键字过滤。
    只允许导航项目根目录或服务容器 /app 内的路径；普通用户在 agent_workspaces 下只能导航自己的工作区。
    该工具只返回目录元数据，不读取文件内容。
    已知目标目录只需要查看树结构时使用；如果不确定可访问范围、权限或 Docker/宿主机路径映射，应先使用 list_accessible_directories。
    
    Args:
        path: 检索目标目录路径 (如 app/services)。
        suffix: 可选的后缀过滤 (如 .py)。
        keyword: 可选的文件名关键字模糊检索。
    """
    try:
        ctx = get_current_agent_context()
        is_admin = bool(getattr(ctx, "is_admin", False))
        user_info = None
        if ctx is not None and getattr(ctx, "user_id", None) is not None:
            dimensions = getattr(ctx, "user_dimensions", None) or {}
            user_name = None
            if isinstance(dimensions, dict):
                raw_name = dimensions.get("user_name") or dimensions.get("username")
                user_name = str(raw_name).strip() if raw_name is not None else None
                user_name = user_name or None
            user_info = {
                "user_id": ctx.user_id,
                "id": ctx.user_id,
                "user_name": user_name,
                "username": user_name,
                "role": "admin" if is_admin else "user",
            }

        from app.services.ai.runtime.agentscope.workspace import default_workspace_root
        from app.utils.fs_access import (
            get_user_private_workspace_root,
            is_other_user_workspace_path,
        )

        workspace_root = os.path.realpath(default_workspace_root())
        private_workspace_root = get_user_private_workspace_root(user_info)
        if private_workspace_root:
            private_workspace_root = os.path.realpath(private_workspace_root)

        def is_hidden_workspace_path(candidate: str) -> bool:
            """避免从上层目录递归时泄露其他用户工作区。"""
            if is_admin:
                return False
            normalized = os.path.realpath(candidate)
            if normalized == workspace_root:
                return False
            if not normalized.startswith(workspace_root + os.sep):
                return False
            if private_workspace_root and (
                normalized == private_workspace_root
                or normalized.startswith(private_workspace_root + os.sep)
            ):
                return False
            return True

        project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))))
        abs_path = os.path.realpath(os.path.abspath(path))
        allowed_roots = [os.path.realpath(project_root), os.path.realpath("/app")]
        if not any(
            os.path.commonpath((abs_path, root)) == root
            for root in allowed_roots
        ):
            return f"安全拦截：禁止导航项目根目录或 /app 之外的路径 {path}！"

        if not is_admin and (
            is_hidden_workspace_path(abs_path)
            or (
                abs_path != workspace_root
                and is_other_user_workspace_path(abs_path, user_info)
            )
        ):
            return "安全拦截：普通用户只能导航公共目录和自己的私有工作区，禁止访问其他用户目录。"
            
        if not os.path.exists(abs_path):
            return f"错误：路径 {path} 不存在。"
            
        if not os.path.isdir(abs_path):
            return f"错误：{path} 不是一个目录。"

        result_lines = []
        for root, dirs, files in os.walk(abs_path, topdown=True):
            depth = root.replace(abs_path, '').count(os.sep)
            if depth > 4:
                dirs[:] = []
                continue

            if not is_admin:
                dirs[:] = [
                    directory
                    for directory in dirs
                    if not is_hidden_workspace_path(os.path.join(root, directory))
                ]
                
            indent = "  " * depth
            rel_dir = os.path.basename(root) or root
            
            filtered_files = []
            for f in files:
                if suffix and not f.endswith(suffix):
                    continue
                if keyword and keyword.lower() not in f.lower():
                    continue
                filtered_files.append(f)
                
            if filtered_files or dirs:
                result_lines.append(f"{indent}📂 {rel_dir}/")
                for f in sorted(filtered_files):
                    f_path = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(f_path)
                        sz_str = f"{round(sz/1024, 1)} KB" if sz > 1024 else f"{sz} Bytes"
                    except:
                        sz_str = "unknown"
                    result_lines.append(f"{indent}  📄 {f} ({sz_str})")
                    
        if not result_lines:
            return "检索完成，目录为空或没有匹配过滤条件的文件。"
            
        return "\n".join(result_lines[:200])
    except Exception as e:
        return f"导航目录树失败: {str(e)}"

@tool
async def web_renderer_and_snapshot(url: str) -> str:
    """
    通过 Playwright 无头模式异步加载渲染外部网页，捕获视口截图保存为本地媒体工件，并提取干净的可读文本返回。
    适用于 Vision 双模态识图与纯文本分析。
    
    Args:
        url: 待渲染与抓取的外部网页合法链接 URL。
    """
    try:
        from app.services.ai.tools.system_tools import validate_url
        validate_url(url)
    except Exception as e:
        return f"安全拦截：URL 校验未通过: {str(e)}"

    media_dir = "data/uploads/media"
    os.makedirs(media_dir, exist_ok=True)
    snapshot_filename = f"web_{uuid.uuid4().hex[:12]}.png"
    snapshot_path = os.path.join(media_dir, snapshot_filename)
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            await page.goto(url, wait_until="networkidle", timeout=25000)
            await page.screenshot(path=snapshot_path, full_page=False)
            
            html_content = await page.content()
            await browser.close()
            
            soup = BeautifulSoup(html_content, "html.parser")
            for script_or_style in soup(["script", "style", "header", "footer", "nav", "iframe"]):
                script_or_style.extract()
                
            text_lines = [line.strip() for line in soup.get_text().splitlines() if line.strip()]
            cleaned_text = "\n".join(text_lines[:150])
            
            return (
                f"### 网页渲染成功！\n"
                f"📸 视觉截图已保存为媒体工件，物理路径：`{snapshot_path}`\n\n"
                f"📝 **网页核心提取文本**：\n"
                f"```text\n"
                f"{cleaned_text}\n"
                f"```"
            )
        except Exception as err:
            return f"网页抓取渲染失败: {str(err)}"

@tool
def code_syntax_linter(code: str, language: str = "python") -> str:
    """
    静态检测输入的 Python 源码语法合规性与排错，提前拦截语法错误和拼写异常。
    
    Args:
        code: 待检测的源码字符串内容。
        language: 编程语言类型。目前仅原生支持 "python"。
    """
    language = language.lower()
    if language != "python":
        return f"提示：当前静态检测仅原生支持 python 语法分析，跳过对 {language} 的静态分析。"
        
    try:
        ast.parse(code)
        return "🎉 静态语法检测通过！未发现 Python 语法错误。"
    except SyntaxError as e:
        return (
            f"❌ 发现 Python 语法错误！\n"
            f"错误描述: {e.msg}\n"
            f"出错位置: 第 {e.lineno} 行，第 {e.offset} 列\n"
            f"错误代码片段:\n```python\n{e.text or ''}```"
        )
    except Exception as general_err:
        return f"分析代码时发生异常: {str(general_err)}"


@tool
async def fetch_static_web_url(url: str) -> str:
    """
    极速、轻量地直接拉取外部静态网页（如新闻、Markdown、技术文档、JSON数据接口等），
    自动剥离网页噪音，提取干净的文本内容返回。
    适用于无需 JS 动态渲染的快速内容抓取，响应极快（通常 <300ms）。
    
    Args:
        url: 待抓取的外部静态网页合法链接 URL。
    """
    try:
        from app.services.ai.tools.system_tools import validate_url
        validate_url(url)
    except Exception as e:
        return f"安全拦截：URL 校验未通过: {str(e)}"
        
    try:
        import httpx
        from bs4 import BeautifulSoup
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            
            # 1. 应对 JSON 数据接口
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                return f"### 抓取成功！(数据格式: JSON)\n\n```json\n{response.text[:8000]}\n```"
            
            # 2. 应对 HTML 网页，使用 BeautifulSoup 脱水提纯
            soup = BeautifulSoup(response.text, "html.parser")
            for script_or_style in soup(["script", "style", "header", "footer", "nav", "iframe"]):
                script_or_style.extract()
                
            text_lines = [line.strip() for line in soup.get_text().splitlines() if line.strip()]
            cleaned_text = "\n".join(text_lines[:150])
            
            return (
                f"### 静态网页拉取成功！(轻量通道)\n\n"
                f"📝 **页面提炼内容**：\n"
                f"```text\n"
                f"{cleaned_text}\n"
                f"```"
            )
    except Exception as err:
        return f"静态抓取失败，建议尝试使用慢通道 'web_renderer_and_snapshot' 工具。错误: {str(err)}"


_BAIDU_SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _baidu_search_url(query: str) -> str:
    import urllib.parse

    return f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"


def _shared_browser_search_fallback(query: str, *, failed_channels: str) -> str:
    """生成切换到右侧共享浏览器的明确下一步，保留现有会话与面板事件链路。"""
    search_url = _baidu_search_url(query)
    return (
        f"未能检索到任何相关结果（{failed_channels}）。"
        "请不要继续重复当前搜索；请改用右侧共享浏览器继续处理："
        f"调用 browser_open，参数为 {{\"url\": {json.dumps(search_url, ensure_ascii=False)}}}。"
        "打开后调用 browser_snapshot，根据快照中的 target_ref 使用 browser_fill 和 browser_click；"
        "如果页面出现验证码，请暂停自动操作并交给人工接管。"
    )


def _parse_baidu_serp_html(html_content: str, max_results: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_content, "html.parser")
    content_left = soup.find(id="content_left")
    if not content_left:
        return []

    results = content_left.find_all(
        class_=lambda x: x and ("result" in x or "c-container" in x)
    )
    parsed_results: List[Dict[str, Any]] = []
    for res in results:
        if len(parsed_results) >= max_results:
            break

        title_el = res.find("h3", class_="t")
        if not title_el:
            title_el = res.find(class_=lambda x: x and "title" in x)

        a_tag = title_el.find("a") if title_el else res.find("a")
        if not a_tag:
            continue

        title_text = a_tag.get_text().strip()
        link = a_tag.get("href", "").strip()

        abstract_el = res.find(class_=lambda x: x and "abstract" in x)
        if not abstract_el:
            abstract_el = res.find(class_=lambda x: x and "c-span-last" in x)

        abstract_text = abstract_el.get_text().strip() if abstract_el else "无简短摘要描述。"
        abstract_text = abstract_text.replace("\xa0", " ")

        if title_text and link:
            parsed_results.append(
                {
                    "title": title_text,
                    "link": link,
                    "abstract": abstract_text,
                }
            )
    return parsed_results


async def _extract_content_from_baidu_link(baidu_link: str) -> Optional[dict]:
    try:
        import httpx
        from app.services.ai.tools.system_tools import validate_url

        headers = {"User-Agent": _BAIDU_SEARCH_USER_AGENT}
        async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
            response = await client.get(baidu_link, headers=headers, follow_redirects=True)
            real_url = str(response.url)
            validate_url(real_url)

            soup = BeautifulSoup(response.text, "html.parser")
            for script_or_style in soup(["script", "style", "header", "footer", "nav", "iframe"]):
                script_or_style.extract()

            text_lines = [line.strip() for line in soup.get_text().splitlines() if line.strip()]
            cleaned_text = "\n".join(text_lines)

            if len(cleaned_text) > 600:
                cleaned_text = cleaned_text[:600] + "\n...(余下网页正文已省略)"

            return {
                "real_url": real_url,
                "content": cleaned_text,
            }
    except Exception as err:
        logger.warning("[web_search_baidu] Failed to extract from %s: %s", baidu_link, err)
        return None


async def _enrich_baidu_results_with_top_content(
    parsed_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    import asyncio

    if not parsed_results:
        return parsed_results

    extraction_tasks = [
        _extract_content_from_baidu_link(item["link"]) for item in parsed_results[:2]
    ]
    extraction_results = await asyncio.gather(*extraction_tasks)
    for idx, ext_res in enumerate(extraction_results):
        if ext_res:
            parsed_results[idx]["real_url"] = ext_res["real_url"]
            parsed_results[idx]["extracted_content"] = ext_res["content"]
    return parsed_results


def _format_baidu_search_markdown(
    query: str,
    parsed_results: List[Dict[str, Any]],
    *,
    title_prefix: str,
) -> str:
    md_lines = [f"### 🔍 {title_prefix} (关于: '{query}')\n"]
    for idx, item in enumerate(parsed_results, 1):
        display_link = item.get("real_url") or item["link"]
        md_lines.append(f"{idx}. **[{item['title']}]({display_link})**")
        md_lines.append(f"   > 📝 摘要: {item['abstract']}\n")

    extracted_sections = []
    for idx, item in enumerate(parsed_results[:2], 1):
        if item.get("extracted_content"):
            extracted_sections.append(
                f"#### 📄 网页 {idx}: {item['title']}\n"
                f"* **真实源链接**: {item['real_url']}\n"
                f"* **网页提炼正文 (前600字)**:\n"
                f"```text\n"
                f"{item['extracted_content']}\n"
                f"```"
            )

    if extracted_sections:
        md_lines.append("\n---\n")
        md_lines.append("### 📄 自动提取的网页全文提炼 (Top-2 网页深度正文)\n")
        md_lines.extend(extracted_sections)

    return "\n".join(md_lines)


async def web_search_baidu_raw(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    通过模拟浏览器访问百度，在互联网上实时检索关于特定问题、最新事实，返回结构化的网页结果列表。
    """
    search_url = _baidu_search_url(query)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=_BAIDU_SEARCH_USER_AGENT,
            )
            page = await context.new_page()
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector("#content_left", timeout=8000)
            except Exception as select_err:
                logger.warning("Timeout waiting for #content_left: %s", select_err)

            html_content = await page.content()
            await browser.close()
    except Exception as e:
        logger.error("[web_search_baidu_raw] Playwright error: %s", e)
        return []

    parsed_results = _parse_baidu_serp_html(html_content, max_results)
    return await _enrich_baidu_results_with_top_content(parsed_results)


async def web_search_baidu_http_raw(
    query: str,
    max_results: int = 3,
    *,
    deep_fetch: bool = False,
) -> List[Dict[str, Any]]:
    """
    使用 httpx 直接抓取百度结果页 HTML（无 Playwright），返回结构化结果。
    """
    import httpx

    search_url = _baidu_search_url(query)
    headers = {
        "User-Agent": _BAIDU_SEARCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=True) as client:
            response = await client.get(search_url, headers=headers)
            response.raise_for_status()
            html_content = response.text
    except Exception as e:
        logger.error("[web_search_baidu_http_raw] httpx error: %s", e)
        return []

    parsed_results = _parse_baidu_serp_html(html_content, max_results)
    if deep_fetch:
        return await _enrich_baidu_results_with_top_content(parsed_results)
    return parsed_results


@tool
async def web_search_baidu_http(
    query: str,
    max_results: int = 6,
    deep_fetch: bool = False,
) -> str:
    """
    轻量百度联网搜索（httpx 直接抓取结果页，无需启动浏览器）。
    适合优先使用：延迟通常明显低于 Playwright 版 web_search_baidu。
    英文/国际资讯可改用 web_search_bing_http。
    若本工具无结果或疑似被反爬拦截，先改用较慢的 web_search_baidu；仍失败时改用右侧共享浏览器的 browser_open。
    需要阅读某个结果正文时，可对真实 URL 调用 fetch_static_web_url。

    Args:
        query: 检索关键词（例如 '智能体 智能运营'）。
        max_results: 返回的最多结果条数，默认 6 条。
        deep_fetch: 是否并发抓取 Top-2 结果页正文（默认 False，更快）。
    """
    try:
        parsed_results = await web_search_baidu_http_raw(
            query,
            max_results=max_results,
            deep_fetch=deep_fetch,
        )
        if not parsed_results:
            return _shared_browser_search_fallback(
                query,
                failed_channels="百度 HTTP 搜索无结果，web_search_baidu 尚未尝试",
            )
        return _format_baidu_search_markdown(
            query,
            parsed_results,
            title_prefix="百度 HTTP 搜索结果",
        )
    except Exception as e:
        return _shared_browser_search_fallback(
            query,
            failed_channels=f"百度 HTTP 搜索异常：{str(e)}",
        )


@tool
async def web_search_baidu(query: str, max_results: int = 6) -> str:
    """
    通过 Playwright 无头浏览器访问百度做联网检索（较慢，适合 HTTP 轻量通道失败时的兜底）。
    优先尝试更快的 web_search_baidu_http；本工具会渲染结果页并对 Top-2 链接自动抽取正文。
    若仍无结果或遇到验证码，改用右侧共享浏览器的 browser_open。
    不需要商业 API Key。

    Args:
        query: 检索关键词（例如 '智能体 智能运营'）。
        max_results: 返回的最多结果条数，默认 6 条。
    """
    try:
        parsed_results = await web_search_baidu_raw(query, max_results=max_results)
        if not parsed_results:
            return _shared_browser_search_fallback(
                query,
                failed_channels="百度 HTTP 与 Playwright 搜索均无结果",
            )
        return _format_baidu_search_markdown(
            query,
            parsed_results,
            title_prefix="百度搜索结果",
        )
    except Exception as e:
        return _shared_browser_search_fallback(
            query,
            failed_channels=f"百度 HTTP 与 Playwright 搜索异常：{str(e)}",
        )


def _parse_bing_serp_html(html_content: str, max_results: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html_content, "html.parser")
    blocks = soup.select("li.b_algo")
    parsed_results: List[Dict[str, Any]] = []
    for block in blocks:
        if len(parsed_results) >= max_results:
            break
        a_tag = block.select_one("h2 a")
        if not a_tag:
            continue
        title_text = a_tag.get_text().strip()
        link = (a_tag.get("href") or "").strip()
        if not title_text or not link:
            continue
        if link.startswith("/"):
            link = f"https://www.bing.com{link}"

        snippet_el = (
            block.select_one("p.b_algoSlug")
            or block.select_one("p.b_lineclamp2")
            or block.select_one("div.b_caption p")
        )
        abstract_text = (
            snippet_el.get_text().strip().replace("\xa0", " ")
            if snippet_el
            else "无简短摘要描述。"
        )
        parsed_results.append(
            {
                "title": title_text,
                "link": link,
                "abstract": abstract_text,
            }
        )
    return parsed_results


async def web_search_bing_http_raw(
    query: str,
    max_results: int = 3,
    *,
    deep_fetch: bool = False,
) -> List[Dict[str, Any]]:
    """使用 httpx 抓取 Bing 结果页 HTML（无 Playwright）。"""
    import urllib.parse
    import httpx

    search_url = (
        "https://www.bing.com/search?"
        f"q={urllib.parse.quote(query)}&setlang=zh-hans&mkt=zh-CN"
    )
    headers = {
        "User-Agent": _BAIDU_SEARCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=True) as client:
            response = await client.get(search_url, headers=headers)
            response.raise_for_status()
            html_content = response.text
    except Exception as e:
        logger.error("[web_search_bing_http_raw] httpx error: %s", e)
        return []

    parsed_results = _parse_bing_serp_html(html_content, max_results)
    if deep_fetch:
        return await _enrich_baidu_results_with_top_content(parsed_results)
    return parsed_results


@tool
async def web_search_bing_http(
    query: str,
    max_results: int = 6,
    deep_fetch: bool = False,
) -> str:
    """
    轻量 Bing 联网搜索（httpx 直接抓取结果页，无需浏览器）。
    适合英文、国际资讯与技术资料；中文日常检索优先用更快的 web_search_baidu_http。
    无结果时可改试 web_search_baidu_http / web_search_baidu。
    需要阅读某个结果正文时，可对真实 URL 调用 fetch_static_web_url。

    Args:
        query: 检索关键词（例如 'AgentScope ReAct tooling'）。
        max_results: 返回的最多结果条数，默认 6 条。
        deep_fetch: 是否并发抓取 Top-2 结果页正文（默认 False，更快）。
    """
    try:
        parsed_results = await web_search_bing_http_raw(
            query,
            max_results=max_results,
            deep_fetch=deep_fetch,
        )
        if not parsed_results:
            return (
                "未能检索到任何相关结果，请尝试简化或更换关键词；"
                "中文检索可改用 web_search_baidu_http，或较慢的 web_search_baidu。"
            )
        return _format_baidu_search_markdown(
            query,
            parsed_results,
            title_prefix="Bing HTTP 搜索结果",
        )
    except Exception as e:
        return f"Bing HTTP 网页检索异常失败: {str(e)}"
