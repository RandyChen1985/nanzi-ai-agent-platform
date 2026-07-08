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
    只允许导航 data 目录及项目内部的安全路径。
    
    Args:
        path: 检索目标目录路径 (如 app/services)。
        suffix: 可选的后缀过滤 (如 .py)。
        keyword: 可选的文件名关键字模糊检索。
    """
    try:
        abs_path = os.path.abspath(path)
        project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))))
        
        if not abs_path.startswith(project_root) and not abs_path.startswith("/app"):
            return f"安全拦截：禁止越权检索系统级路径 {path}！"
            
        if not os.path.exists(abs_path):
            return f"错误：路径 {path} 不存在。"
            
        if not os.path.isdir(abs_path):
            return f"错误：{path} 不是一个目录。"

        result_lines = []
        for root, dirs, files in os.walk(abs_path):
            depth = root.replace(abs_path, '').count(os.sep)
            if depth > 4:
                continue
                
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


async def web_search_baidu_raw(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    通过模拟浏览器访问百度，在互联网上实时检索关于特定问题、最新事实，返回结构化的网页结果列表。
    """
    import urllib.parse
    from bs4 import BeautifulSoup
    
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.baidu.com/s?wd={encoded_query}"
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector("#content_left", timeout=8000)
            except Exception as select_err:
                logger.warning(f"Timeout waiting for #content_left: {select_err}")
            
            html_content = await page.content()
            await browser.close()
    except Exception as e:
        logger.error(f"[web_search_baidu_raw] Playwright error: {e}")
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    content_left = soup.find(id="content_left")
    if not content_left:
        return []
        
    results = content_left.find_all(class_=lambda x: x and ('result' in x or 'c-container' in x))
    parsed_results = []
    for res in results:
        if len(parsed_results) >= max_results:
            break
            
        title_el = res.find("h3", class_="t")
        if not title_el:
            title_el = res.find(class_=lambda x: x and 'title' in x)
            
        a_tag = title_el.find("a") if title_el else res.find("a")
        if not a_tag:
            continue
            
        title_text = a_tag.get_text().strip()
        link = a_tag.get("href", "").strip()
        
        abstract_el = res.find(class_=lambda x: x and 'abstract' in x)
        if not abstract_el:
            abstract_el = res.find(class_=lambda x: x and 'c-span-last' in x)
        
        abstract_text = abstract_el.get_text().strip() if abstract_el else "无简短摘要描述。"
        abstract_text = abstract_text.replace("\xa0", " ")
        
        if title_text and link:
            parsed_results.append({
                "title": title_text,
                "link": link,
                "abstract": abstract_text
            })

    async def _extract_content_from_baidu_link(baidu_link: str) -> Optional[dict]:
        try:
            import httpx
            from bs4 import BeautifulSoup
            from app.services.ai.tools.system_tools import validate_url
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
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
                    "content": cleaned_text
                }
        except Exception as err:
            logger.warning(f"[web_search_baidu_raw] Failed to extract from {baidu_link}: {err}")
            return None
    # 对 Top-2 的加密跳转链接发起高并发内容提取
    import asyncio
    extraction_tasks = []
    for item in parsed_results[:2]:
        extraction_tasks.append(_extract_content_from_baidu_link(item["link"]))

    extraction_results = []
    if extraction_tasks:
        extraction_results = await asyncio.gather(*extraction_tasks)

    for idx, ext_res in enumerate(extraction_results):
        if ext_res:
            parsed_results[idx]["real_url"] = ext_res["real_url"]
            parsed_results[idx]["extracted_content"] = ext_res["content"]

    return parsed_results


@tool
async def web_search_baidu(query: str, max_results: int = 6) -> str:
    """
    通过模拟浏览器访问百度，在互联网上实时检索关于特定问题、技术报错、实时新闻或云枢智能体相关资讯等最新信息。
    不需要任何商业 API Key，完全自主可控。输入查询词，返回包含标题、核心摘要及百度来源链接的精美 Markdown 结果列表。
    
    Args:
        query: 检索关键词 (例如 '云枢智能体 智能运营')。
        max_results: 返回的最多结果条数，默认 6 条。
    """
    try:
        parsed_results = await web_search_baidu_raw(query, max_results=max_results)
        if not parsed_results:
            return "未能检索到任何相关结果，请尝试简化或更换关键词。"
            
        md_lines = [f"### 🔍 百度搜索结果 (关于: '{query}')\n"]
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
        
    except Exception as e:
        return f"百度网页检索异常失败: {str(e)}"
