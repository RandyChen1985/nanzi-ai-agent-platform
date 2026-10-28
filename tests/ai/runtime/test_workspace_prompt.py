import pytest
from types import SimpleNamespace

from app.services.ai.runtime.agentscope.workspace import (
    append_session_workspace_sandbox_to_system_prompt,
    collect_workspace_file_tool_names,
    normalize_workspace_tool_names,
)

pytestmark = pytest.mark.no_infrastructure


def test_normalize_workspace_tool_names_aliases():
    assert normalize_workspace_tool_names({"read_file", "exec_command"}) == {"Read", "Bash"}


def test_collect_workspace_file_tool_names_ignores_sql_tools():
    tools = [
        SimpleNamespace(name="get_dataset_schema"),
        SimpleNamespace(name="Read"),
    ]
    assert collect_workspace_file_tool_names(tools) == {"Read"}


def test_workspace_prompt_mentions_common_container_commands():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Bash"],
    )

    assert "容器常见基础命令" in prompt
    assert "`netstat`" in prompt
    assert "`ping`" in prompt
    assert "`uv`" in prompt
    assert "`npm`" in prompt
    # 旧文案把 wget/jq/unzip/dig/nc 说成“通常预装”，实测镜像里都没有；现在只允许
    # 把它们列进“未必存在”，不能再作为既成事实。
    assert "未必存在" in prompt
    assert "`command -v <cmd>`" in prompt
    assert "不要凭记忆断言未安装" in prompt


def test_workspace_prompt_sandbox_teaches_uv_install_to_workspace():
    """沙箱块必须写清 pip 装不上、uv --target /workspace 才持久。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read"],
        logical_workspace_root="/workspace",
        logical_session_workdir="/workspace/sessions/conv-1",
        logical_docs_dir="/workspace/docs",
    )

    assert "Python 依赖安装（沙箱）" in prompt
    assert "uv pip install --target /workspace/.pylibs" in prompt
    assert "PYTHONPATH=/workspace/.pylibs" in prompt
    assert "import 不到" in prompt
    assert "容器可写层是临时的" in prompt
    assert "只有 `/workspace`（本人工作区）会保留" in prompt
    # 沙箱块不能出现 local 专属的“禁止安装”口径，否则模型会拒绝用户的合理安装请求
    assert "Python 依赖安装（local 策略）" not in prompt


def test_workspace_prompt_local_allows_global_install():
    """local 策略：Bash 跑在后端，必须禁止装依赖，且不能教它往 /workspace 装。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read"],
    )

    assert "Python 依赖安装（local 策略）" in prompt
    # 产品决定：local 不限制安装，直接装进后端环境、全局生效（只提醒平台级影响）
    assert "对宿主机全局生效" in prompt
    assert "pip install <包名>" in prompt
    assert "不要随手用 `--upgrade`" in prompt
    # 但 local 不能给出沙箱专属的 --target 口径
    assert "uv pip install --target" not in prompt


def test_workspace_prompt_distinguishes_tool_paths_from_user_delivery_paths():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Read", "Write"],
    )

    assert "工具调用路径" in prompt
    assert "最终展示给用户" in prompt


def test_workspace_prompt_enforces_host_file_tools_and_docs_rules():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/tmp/workspaces/u1/sessions/conv-1",
        docs_dir="/tmp/workspaces/u1/docs",
        file_tool_names=["Read", "Write", "Bash", "Grep"],
    )

    assert "文件读写与搜索工具优先原则" in prompt
    assert "文件读写与搜索一律走宿主侧文件工具（Read/Write/Edit/Glob/Grep），不要用 Bash 访问文件" in prompt
    # local 策略（未传 logical root）下 Bash 就跑在后端，公共文档并非「沙箱不可见」；
    # 提示词只要求走文件工具读取，不能宣称沙箱专属的可见性结论。
    assert "平台公共文档（如 `data/docs/` 手册、FAQ.md）为只读" in prompt
    assert "沙箱 Bash 不可见" not in prompt
    assert "Bash 文件操作边界" in prompt
    assert "只有在触发运行环境操作" in prompt


def test_workspace_prompt_sandbox_keeps_public_docs_invisible_to_bash():
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Read", "Write", "Bash", "Grep"],
        logical_workspace_root="/workspace",
        logical_session_workdir="/workspace/sessions/conv-1",
        logical_docs_dir="/workspace/docs",
    )

    assert "平台公共文档（如 `data/docs/` 手册、FAQ.md）在沙箱内仅宿主侧可读，沙箱 Bash 不可见" in prompt
    assert "沙箱内 Bash 文件操作边界" in prompt


def test_workspace_prompt_local_points_skills_at_backend_path():
    """local：公共技能提示必须是后端真实路径，禁止出现沙箱专属路径。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read", "Glob"],
        public_skills_path="/app/data/skills",
        personal_skills_path="/app/data/agent_workspaces/u1/skills",
    )

    assert "/app/data/skills" in prompt
    assert "`/app/data/agent_workspaces/u1/skills`" in prompt
    assert "Bash 与文件工具同根" in prompt
    # 沙箱专属路径一个都不能出现，否则模型会先 ls 一个不存在的目录再被迫摸索
    assert "/workspace/skills" not in prompt
    assert "/workspace/personal-skills" not in prompt
    assert ".seed" not in prompt
    assert "ls -a /workspace/skills" not in prompt
    # 技能文本统一走 read_skill_instruction；Bash 只用于执行技能脚本
    assert "read_skill_instruction(skill_id)" in prompt
    assert "只有需要执行技能自带脚本" in prompt


def test_workspace_prompt_sandbox_points_skills_at_workspace_copy():
    """Docker/K8s：Bash 用 /workspace/skills 副本，且与文件工具路径分开描述。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read", "Glob"],
        logical_workspace_root="/workspace",
        logical_session_workdir="/workspace/sessions/conv-1",
        logical_docs_dir="/workspace/docs",
        logical_public_docs_dir="/workspace/public/docs",
    )

    assert "/workspace/skills" in prompt
    assert "/workspace/personal-skills" in prompt
    assert ".seed" in prompt
    assert "只有需要执行技能自带脚本时才" in prompt
    assert "read_skill_instruction(skill_id)" in prompt
    # 沙箱 Bash 看不到后端技能根，提示词不得把它当成可访问路径
    assert "/app/data/skills" not in prompt



@pytest.mark.asyncio
async def test_append_workspace_prompt_when_file_tools_and_conversation(monkeypatch):
    async def _root():
        return "/tmp/workspaces"

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )

    tools = [SimpleNamespace(name="Grep")]
    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=tools,
    )
    assert "Base prompt" in result
    assert "[Session Workspace & Path Sandbox]" in result
    assert "/tmp/workspaces/u1/sessions/conv-1" in result or "/tmp/workspaces/u1/sessions/conv_1" in result
    assert "/tmp/workspaces/u1/docs" in result
    assert "/uploads/" in result
    assert "/sandbox/" in result
    assert "Grep" in result


@pytest.mark.asyncio
async def test_append_docker_workspace_prompt_uses_real_user_workspace_paths(monkeypatch):
    async def _root():
        return "/tmp/workspaces"

    async def _config(key, default=None):
        return "docker" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        _config,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._resolve_docker_public_docs_source",
        lambda: None,
    )

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Read")],
    )

    assert "/tmp/workspaces/u1/sessions/conv-1" in result
    assert "/tmp/workspaces/u1/docs" in result
    assert "Bash 使用沙箱路径" in result
    assert "Read/Write/Edit/Glob/Grep 使用文件工具路径" in result
    assert "不要将 `/tmp` 等容器专属路径交给 Read" in result
    assert "Bash 的相对路径默认相对会话目录" in result
    assert "文件工具的相对路径默认相对用户工作区根目录" in result
    assert "公共 docs 未挂载" in result


@pytest.mark.asyncio
async def test_append_workspace_prompt_skips_without_conversation_or_file_tools():
    tools = [SimpleNamespace(name="memory_search")]
    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=tools,
    )
    assert result == "Base prompt"

    file_tools = [SimpleNamespace(name="Read")]
    result_no_conv = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id=None,
        tools=file_tools,
    )
    assert result_no_conv == "Base prompt"


@pytest.mark.asyncio
async def test_append_local_workspace_prompt_uses_backend_skill_path(monkeypatch):
    """回归：sandbox_policy=local 时不得注入 /workspace/skills 与 .seed 布局。"""

    async def _root():
        return "/app/data/agent_workspaces"

    async def _config(key, default=None):
        return "local" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config)
    monkeypatch.setattr(
        "app.utils.fs_access.get_platform_skills_root",
        lambda: "/app/data/skills",
    )

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Bash"), SimpleNamespace(name="Read")],
    )

    assert "/app/data/skills" in result
    assert "/workspace/skills" not in result
    assert "/workspace/personal-skills" not in result
    assert ".seed" not in result
    assert "Bash 与文件工具同根" in result


@pytest.mark.asyncio
async def test_append_k8s_workspace_prompt_uses_sandbox_skill_path(monkeypatch):
    async def _root():
        return "/app/data/agent_workspaces"

    async def _config(key, default=None):
        return "k8s" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config)

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Bash"), SimpleNamespace(name="Read")],
    )

    assert "/workspace/skills" in result
    assert "/workspace/personal-skills" in result
    assert ".seed" in result
    # K8s 适配器固定挂载 subPath docs，提示词必须与目录清单一致地说已挂载
    assert "/workspace/public/docs" in result
    assert "公共 docs 未挂载" not in result



def test_workspace_prompt_e2b_uses_e2b_workdir_skills():
    """E2B：Bash 的逻辑根是 /home/user/workspace（AgentScope E2BWorkspace 的 workdir）。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read"],
        logical_workspace_root="/home/user/workspace",
        logical_public_docs_dir=None,
        sandbox_skills_dir="/home/user/workspace/skills",
        sandbox_skill_layout="seed",
        personal_skills_container_path=None,
    )

    assert "/home/user/workspace/skills" in prompt
    assert ".seed" in prompt
    # e2b 没有 personal-skills 挂载，也没有公共 docs 挂载
    assert "/workspace/personal-skills" not in prompt
    assert "/workspace/public/docs" not in prompt
    assert "个人技能源目录以只读方式挂载" not in prompt
    assert "个人技能没有独立挂载点" in prompt
    # 后端技能根在沙箱里不可访问
    assert "/app/data/skills" not in prompt
    assert "公共技能用 `/workspace/skills` 下的副本" not in prompt


def test_workspace_prompt_ssh_uses_flat_skill_layout_without_seed():
    """SSH：平台自研 SshWorkspace 把技能平铺到 <remote_workdir>/skills，没有 .seed。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/app/data/agent_workspaces/u1/sessions/conv-1",
        docs_dir="/app/data/agent_workspaces/u1/docs",
        file_tool_names=["Bash", "Read"],
        logical_workspace_root="/srv/agent",
        logical_public_docs_dir=None,
        sandbox_skills_dir="/srv/agent/skills",
        sandbox_skill_layout="flat",
        personal_skills_container_path=None,
    )

    assert "/srv/agent/skills" in prompt
    assert "平铺副本" in prompt
    assert "/srv/agent/skills/<skill_id>/SKILL.md" in prompt
    assert ".seed/" not in prompt
    assert "/workspace/skills" not in prompt
    assert "/workspace/personal-skills" not in prompt


@pytest.mark.asyncio
async def test_append_e2b_workspace_prompt_uses_e2b_logical_root(monkeypatch):
    async def _root():
        return "/app/data/agent_workspaces"

    async def _config(key, default=None):
        return "e2b" if key == "sandbox_policy" else default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config)
    monkeypatch.setattr(
        "app.utils.fs_access.get_platform_skills_root",
        lambda: "/app/data/skills",
    )

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Bash"), SimpleNamespace(name="Read")],
    )

    assert "/home/user/workspace/skills" in result
    assert "/home/user/workspace" in result
    assert "/app/data/skills" not in result
    assert "/workspace/personal-skills" not in result
    assert "/workspace/public/docs" not in result


@pytest.mark.asyncio
async def test_append_ssh_workspace_prompt_uses_configured_remote_workdir(monkeypatch):
    async def _root():
        return "/app/data/agent_workspaces"

    async def _config(key, default=None):
        if key == "sandbox_policy":
            return "ssh"
        if key == "sandbox_ssh_remote_workdir":
            return "/srv/agent"
        return default

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        _root,
    )
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config)
    monkeypatch.setattr(
        "app.utils.fs_access.get_platform_skills_root",
        lambda: "/app/data/skills",
    )

    result = await append_session_workspace_sandbox_to_system_prompt(
        "Base prompt",
        user_id="u1",
        conversation_id="conv-1",
        tools=[SimpleNamespace(name="Bash"), SimpleNamespace(name="Read")],
    )

    assert "/srv/agent/skills" in result
    assert "平铺副本" in result
    assert "/workspace/skills" not in result
    assert "/workspace/personal-skills" not in result
    assert "/app/data/skills" not in result


def test_workspace_prompt_dependency_paths_follow_logical_workspace_root():
    """e2b 的工作区根是 /home/user/workspace，依赖路径不能写死 /workspace。"""
    from app.services.ai.agent_prompts import AgentServicePrompts

    prompt = AgentServicePrompts.session_workspace_sandbox_block(
        session_workdir="/data/u/sessions/conv-1",
        docs_dir="/data/u/docs",
        file_tool_names=["Bash"],
        logical_workspace_root="/home/user/workspace",
        logical_session_workdir="/home/user/workspace/sessions/conv-1",
        logical_docs_dir="/home/user/workspace/docs",
    )

    assert "uv pip install --target /home/user/workspace/.pylibs" in prompt
    assert "PYTHONPATH=/home/user/workspace/.pylibs" in prompt
    assert "只有 `/home/user/workspace`（本人工作区）会保留" in prompt
    assert "--target /workspace/.pylibs" not in prompt
