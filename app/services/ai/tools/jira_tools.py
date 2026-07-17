from typing import Optional, Type, List, Dict, Any
from pydantic import BaseModel, Field
from app.services.ai.tools.tool_compat import BaseTool
from atlassian import Jira
import os
import json
import logging
import urllib.parse
from dotenv import load_dotenv

# Explicitly load .env from project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path)

logger = logging.getLogger(__name__)

# --- Configuration & Client ---

def get_jira_client() -> Optional[Jira]:
    """Factory to create an authenticated Jira client."""
    url = os.getenv("JIRA_URL")
    username = os.getenv("JIRA_USERNAME")
    password = os.getenv("JIRA_PASSWORD")
    
    if not url or not username or not password:
        logger.warning("Jira configuration missing. JIRA_URL, JIRA_USERNAME, JIRA_PASSWORD are required.")
        return None

    try:
        return Jira(
            url=url,
            username=username,
            password=password,
            verify_ssl=False
        )
    except Exception as e:
        logger.error(f"Failed to initialize Jira client: {e}")
        return None

# --- Input Schemas ---

class JiraSearchInput(BaseModel):
    jql: str = Field(description="The JQL query string. E.g., 'project = OPS AND status = Open'")

class JiraCreateIssueInput(BaseModel):
    project_key: str = Field(description="Project Key (e.g., 'OPS', 'NANZI').")
    summary: str = Field(description="Brief title of the issue.")
    description: str = Field(description="Detailed description of the task or bug.")
    issue_type: str = Field(default="Task", description="Issue Type: 'Task', 'Bug', 'Story'. Default is 'Task'.")

class JiraGetProjectsTool(BaseTool):
    name: str = "jira_get_projects"
    description: str = (
        "Retrieve a list of available Jira projects. "
        "Use this tool when you are unsure which project to search in or to discover project keys."
    )

    def _run(self) -> str:
        jira = get_jira_client()
        if not jira:
            return "Error: Jira client not configured."
        try:
            projects = jira.projects()
            if not projects:
                return "No projects found."
            
            lines = ["Available Jira Projects:"]
            for p in projects:
                lines.append(f"- Key: {p.get('key')} | Name: {p.get('name')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to fetch projects: {str(e)}"

# --- Tool Implementations ---

class JiraSearchTool(BaseTool):
    name: str = "jira_search"
    description: str = (
        "Use this tool to search for Jira issues using JQL. "
        "Provides full history and comments, which is essential for OPS investigation."
    )
    args_schema: Type[BaseModel] = JiraSearchInput

    def _sanitize_text(self, text: str) -> str:
        if not text:
            return ""
        
        # 1. Critical: Break the specific tags that trigger frontend/backend interceptors
        # We use fullwidth brackets '＜' '＞' or simple brackets '[' ']' to defuse them
        text = text.replace("<function_calls>", "[function_calls]")
        text = text.replace("</function_calls>", "[/function_calls]")
        
        # 2. Defuse generic XML/HTML tags that might confuse the LLM
        # Replace <tag> with [tag] or just escape them if they look like system instructions
        # Simple heuristic: if it looks like a tag, replace brackets
        # But we want to keep readability. Let's just handle the most dangerous ones or
        # rely on the fact that LLM is usually smart enough if we explicitly escape known confusion points.
        
        # For this specific "fragile" issue, let's be safer with brackets
        text = text.replace("{{", "{ {").replace("}}", "} }") # Break Jinja/Template markers
        
        return text

    def _run(self, jql: str) -> str:
        jira = get_jira_client()
        if not jira:
            return "Error: Jira client not configured."

        try:
            # User needs data, so we keep limit=10 but don't truncate aggressively
            results = jira.jql(
                jql, 
                limit=10, 
                fields=["summary", "status", "assignee", "created", "updated", "reporter", "description", "comment", "customfield_15102"]
            )
            
            if results is None:
                return "Jira API Error: Request returned no data. Please check JQL syntax or Jira connectivity."
            
            issues = results.get("issues", [])
            total = results.get("total", 0)
            
            if not issues:
                return "[SUCCESS] 未找到匹配的 Jira 工单。"

            header = f"[SUCCESS] 找到 {total} 条工单。正在展示最新的 {len(issues)} 条：\n\n"
            header += "CRITICAL: When referring to these Jira issues, ALWAYS append its reference as [ID:n] at the end of the relevant sentence.\n\n"
            
            simplified_list_for_llm = []
            citations_for_frontend = []
            
            for i, issue in enumerate(issues):
                ref_id = str(i + 1)
                key = issue.get("key")
                fields = issue.get("fields", {})
                
                status = fields.get("status", {}).get("name", "Unknown")
                assignee = fields.get("assignee", {}).get("displayName", "Unassigned")
                summary = self._sanitize_text(fields.get("summary", ""))
                created_date = fields.get("created", "")[:10]
                
                l2_owner_raw = fields.get("customfield_15102")
                l2_owner = "N/A"
                if l2_owner_raw:
                    if isinstance(l2_owner_raw, dict):
                        l2_owner = l2_owner_raw.get("displayName") or l2_owner_raw.get("name") or str(l2_owner_raw)
                    else:
                        l2_owner = str(l2_owner_raw)

                desc_raw = fields.get("description") or ""
                # Relaxed truncation: 5000 chars should cover most OPS logs while staying within safe limits
                if len(desc_raw) > 5000:
                    desc_preview = desc_raw[:5000] + "\n...(内容过长已截断)"
                else:
                    desc_preview = desc_raw
                
                desc_preview = self._sanitize_text(desc_preview)
                
                comments_list = fields.get("comment", {}).get("comments", [])
                formatted_comments = []
                for c in comments_list:
                    c_author = c.get("author", {}).get("displayName", "Unknown")
                    c_created = c.get("created", "")[:16].replace("T", " ")
                    c_body = c.get("body", "").strip()
                    c_body_clean = self._sanitize_text(c_body)
                    formatted_comments.append(f"    - [{c_created}] {c_author}: {c_body_clean}")
                
                all_comments_str = "\n".join(formatted_comments) if formatted_comments else "无评论内容。"

                link = f"{jira.url}/browse/{key}"
                
                # 1. Format for LLM Context
                simplified_list_for_llm.append(
                    f"--- [ID:{ref_id}] Jira: {key} ---\n"
                    f"Summary: {summary}\n"
                    f"Status: {status} | Assignee: {assignee}\n"
                    f"Description: {desc_preview[:1000]}...\n" # LLM gets a shorter preview to save tokens
                    f"Comments: {all_comments_str[:500]}...\n"
                )
                
                # 2. Format for Frontend Citations
                full_detail_text = (
                    f"**【{key}】 {summary}**\n\n"
                    f"- **当前状态**: `{status}`\n"
                    f"- **负责人**: {assignee}\n"
                    f"- **创建日期**: {created_date}\n\n"
                    f"**问题描述**:\n{desc_preview}\n\n"
                    f"**历史备注**:\n{all_comments_str}\n\n"
                    f"🔗 [在 Jira 中打开]({link})"
                )
                
                citations_for_frontend.append({
                    "id": ref_id,
                    "doc_name": f"Jira: {key}",
                    "content": full_detail_text,
                    "url": link,
                    "chunk_id": key, # Use the unique Jira Key as chunk_id to avoid frontend deduplication
                    "similarity": 1.0 # Tool results are always considered highly relevant
                })

            # Return dual-track JSON
            result = {
                "content": header + "\n".join(simplified_list_for_llm),
                "citations": citations_for_frontend
            }
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            error_msg = str(e)
            # Check for JQL syntax errors specifically to prompt LLM self-correction
            if "The value" in error_msg or "does not exist" in error_msg or "is invalid" in error_msg:
                return f"Jira JQL Syntax Error: {error_msg}. Please check field names and values, and try correcting the JQL."
            
            # Handle cases where Jira returns HTML (e.g. 403/502) and JSON decoder fails
            if "403" in error_msg or "Expecting value" in error_msg:
                return (
                    "Jira API Error (403 Forbidden): 权限拒绝或会话锁定。\n"
                    "原因可能是：\n"
                    "1. 服务不可用,联系管理员查看。\n"
                    "2. 触发了 Jira 的安全策略，需在浏览器手动登录一次 jira.yovole.tech 以解除限制。\n"
                    "3. 该账号无权执行当前 JQL 查询。"
                )
            return f"Jira JQL Error: {error_msg}"

class JiraCreateIssueTool(BaseTool):
    name: str = "jira_create_issue"
    description: str = (
        "Generate a draft Jira issue link for OPS or CS projects. "
        "The issue will NOT be created automatically. You must click the link to review and create in your browser."
    )
    args_schema: Type[BaseModel] = JiraCreateIssueInput

    def _run(self, project_key: str, summary: str, description: str, issue_type: str = "Task") -> str:
        # 1. Hardcoded Mapping for Yovole Jira
        mapping = {
            "OPS": {"pid": "10503", "issuetype": "10700"},
            "CS": {"pid": "10806", "issuetype": "11105"}
        }
        
        project_key_upper = project_key.upper()
        if project_key_upper not in mapping:
            return f"❌ 暂不支持项目类型 '{project_key}'。目前仅支持: OPS, CS。"

        config_data = mapping[project_key_upper]
        
        try:
            # 2. Construct the URL
            # Get base URL from environment, fallback to production URL
            base_url = (os.getenv("JIRA_URL") or "https://jira.yovole.tech").rstrip('/')
            
            params = {
                "pid": config_data["pid"],
                "issuetype": config_data["issuetype"]
            }
            
            # Use CreateIssue.jspa which supports pre-filling from query params
            query_string = urllib.parse.urlencode(params)
            draft_url = f"{base_url}/secure/CreateIssue.jspa?{query_string}"
            
            return (
                f"✅ 已为您准备好 **{project_key_upper}** 工单创建链接。\n\n"
                f"请点击下方链接，在浏览器中完成提交：\n\n"
                f"🔗 [**立即前往 Jira 创建工单**]({draft_url})\n\n"
                f"---\n"
                f"**建议填写内容：**\n"
                f"- **项目**: {project_key_upper}\n"
                f"- **标题**: {summary}\n"
                f"- **描述**: {description}\n\n"
                f"> 温馨提示：为了安全起见，内容未通过 URL 传递。请根据上方建议内容在 Jira 页面填写。"
            )

        except Exception as e:
            logger.error(f"Jira link generation failed: {e}")
            return f"Failed to generate Jira draft link. Error: {str(e)}"
