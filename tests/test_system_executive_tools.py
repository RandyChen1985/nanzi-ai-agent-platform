import os
import pytest
import asyncio
from app.services.ai.tools.system_executive_tools import (
    validate_safe_path, read_file, write_file,
    exec_command, manage_process, list_process, search_text
)
from app.services.ai.tools.advanced_auxiliary_tools import (
    sqlite_scratchpad, directory_tree_navigator, code_syntax_linter
)

pytestmark = pytest.mark.no_infrastructure

def test_safe_path_validation():
    # 验证合法路径正常返回
    valid_path = os.path.abspath("data/uploads/test.txt")
    assert validate_safe_path(valid_path) == valid_path

    # 验证路径穿越攻击被安全拦截
    with pytest.raises(ValueError, match="安全拦截：路径越界"):
        validate_safe_path("data/uploads/../../../etc/passwd")

def test_read_write_file():
    test_file = "data/uploads/test_junit.txt"
    test_content = "Hello NanZi Agent Executive Tools!\nLine 2\nLine 3"
    
    # 写入测试
    res_write = write_file.invoke({"path": test_file, "content": test_content})
    assert "物理写入成功" in res_write

    # 分页读取测试
    res_read = read_file.invoke({"path": test_file, "offset": 0, "limit": 10})
    assert "[分页读取成功" in res_read
    assert "Hello Yuns" in res_read

    # Tail 读取测试
    res_tail = read_file.invoke({"path": test_file, "tail": True, "limit": 20})
    assert "[Tail 读取成功" in res_tail
    
    # 清理
    if os.path.exists(test_file):
        os.remove(test_file)

@pytest.mark.asyncio
async def test_exec_command():
    # 测试常规命令
    res = await exec_command.ainvoke({"command": "echo 'NanZi'"})
    assert "NanZi" in res
    assert "ExitCode=0" in res

    # 测试超时拦截
    res_timeout = await exec_command.ainvoke({"command": "sleep 40"})
    assert "已强制终止命令进程" in res_timeout

    # 测试高危命令拦截
    res_forbidden = await exec_command.ainvoke({"command": "rm -rf /"})
    assert "安全拦截" in res_forbidden
    assert "禁止删除根目录" in res_forbidden

    res_forbidden2 = await exec_command.ainvoke({"command": "rm /"})
    assert "安全拦截" in res_forbidden2

    res_forbidden3 = await exec_command.ainvoke({"command": "rm -rf /*"})
    assert "安全拦截" in res_forbidden3
    assert "/*" in res_forbidden3

    res_forbidden4 = await exec_command.ainvoke({"command": "shutdown -h now"})
    assert "安全拦截" in res_forbidden4

    # kill 类命令默认放开，只保护 PID 1 / 少数关键进程强杀
    res_ok_kill = await exec_command.ainvoke({"command": "kill -0 12345"})
    assert "ExitCode=" in res_ok_kill

    res_ok_killall = await exec_command.ainvoke({"command": "killall -0 python"})
    assert "ExitCode=" in res_ok_killall

    res_forbidden5 = await exec_command.ainvoke({"command": "kill -9 1"})
    assert "安全拦截" in res_forbidden5

    res_forbidden5b = await exec_command.ainvoke({"command": "kill 1"})
    assert "安全拦截" in res_forbidden5b

    res_forbidden6 = await exec_command.ainvoke({"command": ":(){ :|:& };:"})
    assert "安全拦截" in res_forbidden6

def test_manage_process_and_list_process():
    # 测试列出进程
    res_list = manage_process.invoke({"action": "list"})
    assert "PID" in res_list
    assert "进程名" in res_list

    res_list_direct = list_process.invoke({})
    assert "PID" in res_list_direct
    assert "进程名" in res_list_direct

    # 测试核心主进程误杀拦截
    current_pid = os.getpid()
    res_kill = manage_process.invoke({"action": "kill", "pid": current_pid})
    assert "受到强制保护，禁止终止" in res_kill

def test_search_text_finds_matches_with_context_and_limits():
    test_dir = "data/uploads/search_text_test"
    os.makedirs(test_dir, exist_ok=True)
    first_file = os.path.join(test_dir, "app.log")
    second_file = os.path.join(test_dir, "notes.txt")
    with open(first_file, "w", encoding="utf-8") as f:
        f.write("line before\nERROR database timeout\nline after\n")
    with open(second_file, "w", encoding="utf-8") as f:
        f.write("nothing here\nerror lower case\n")

    try:
        res = search_text.invoke({
            "pattern": "ERROR",
            "path": test_dir,
            "file_glob": "*.log",
            "case_sensitive": False,
            "context_lines": 1,
            "max_results": 5,
        })

        assert "搜索完成" in res
        assert "app.log:2" in res
        assert "ERROR database timeout" in res
        assert "line before" in res
        assert "notes.txt" not in res

        no_match = search_text.invoke({
            "pattern": "ERROR",
            "path": test_dir,
            "case_sensitive": True,
            "file_glob": "*.txt",
        })
        assert "未找到匹配内容" in no_match
    finally:
        for filename in (first_file, second_file):
            if os.path.exists(filename):
                os.remove(filename)
        if os.path.exists(test_dir):
            os.rmdir(test_dir)

def test_sqlite_scratchpad():
    from app.core.context import AgentContext, set_agent_context
    from app.utils.fs_access import get_user_sandbox_dir

    session_id = "test_sess_99"
    set_agent_context(AgentContext(
        agent_id="test-agent",
        agent_name="TestAgent",
        user_id=1,
        conversation_id="conv-test",
    ))
    import_data = '{"users": [{"id": 1, "name": "Alice", "role": "admin"}, {"id": 2, "name": "Bob", "role": "user"}]}'
    sql = "SELECT name FROM users WHERE role = 'admin'"
    res = sqlite_scratchpad.invoke({"sql": sql, "session_id": session_id, "import_data": import_data})

    assert "Alice" in res
    assert "Bob" not in res

    sandbox_dir = get_user_sandbox_dir({"user_id": 1, "role": "user"})
    assert sandbox_dir
    db_path = os.path.join(sandbox_dir, f"sess_{session_id}.db")
    if os.path.exists(db_path):
        os.remove(db_path)

def test_code_syntax_linter():
    # 正确代码
    res_ok = code_syntax_linter.invoke({"code": "def hello():\n    print('hi')"})
    assert "静态语法检测通过" in res_ok

    # 语法错误代码
    res_err = code_syntax_linter.invoke({"code": "def hello()\n    print('hi')"})
    assert "发现 Python 语法错误" in res_err
    assert "第 1 行" in res_err

@pytest.mark.asyncio
async def test_fetch_static_web_url_flow():
    from unittest.mock import patch, MagicMock, AsyncMock
    from app.services.ai.tools.advanced_auxiliary_tools import fetch_static_web_url
    
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_response.text = """
    <html>
      <head><style>body { color: red; }</style></head>
      <body>
        <header><h1>Header Navigation</h1></header>
        <div class="content">
          <p>Hello NanZi Light Weight Web Content!</p>
          <script>console.log("noisy script");</script>
        </div>
        <footer>Footer Info</footer>
      </body>
    </html>
    """
    
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    
    with patch("app.services.ai.tools.system_tools.validate_url", return_value=True), \
         patch("httpx.AsyncClient") as mock_class:
        mock_class.return_value.__aenter__.return_value = mock_client
        res = await fetch_static_web_url.ainvoke({"url": "https://example.com/news"})
        
    assert "静态网页拉取成功" in res
    assert "Hello NanZi Light Weight Web Content!" in res
    assert "Header Navigation" not in res
    assert "Footer Info" not in res
    assert "noisy script" not in res

@pytest.mark.asyncio
async def test_fetch_static_web_url_ssrf():
    from app.services.ai.tools.advanced_auxiliary_tools import fetch_static_web_url
    
    res = await fetch_static_web_url.ainvoke({"url": "http://127.0.0.1:8080/admin"})
    assert "安全拦截：URL 校验未通过" in res


def test_create_skills_tool(tmp_path):
    from app.services.ai.tools.system_executive_tools import create_skills
    from unittest.mock import patch, PropertyMock

    # 将 settings.SKILLS_DIR mock 成 pytest 的 tmp_path 隔离真实环境
    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)
        
        skill_id = "test-ai-write"
        name = "AI自动写作技能"
        description = "自动生成文章"
        content = "---\nname: AI自动写作技能\ndescription: 自动生成文章\n---\n\n# 守则\n1. Imperatives"
        
        # 1. 成功创建技能
        res = create_skills.invoke({
            "skill_id": skill_id,
            "name": name,
            "description": description,
            "skill_md_content": content
        })
        assert "创建成功" in res
        
        # 验证物理文件写入
        skill_file = tmp_path / skill_id / "SKILL.md"
        assert skill_file.exists()
        assert skill_file.read_text(encoding="utf-8") == content

        # 2. 测试非法ID拦截
        res_bad_id = create_skills.invoke({
            "skill_id": "../bad-path",
            "name": name,
            "description": description,
            "skill_md_content": content
        })
        assert "非法技能 ID 格式" in res_bad_id
