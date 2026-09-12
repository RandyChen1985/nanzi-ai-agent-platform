from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"


def test_embed_chat_configures_sandbox_workspace_state_and_endpoints():
    source = EMBED.read_text(encoding="utf-8")
    # docker 端点语义保留（base 端点按 docker|k8s 路由）
    assert "/api/v1/sandbox/docker/workspace" in source
    assert "effectiveSandboxPolicy" in source
    assert "isSandboxWorkspacePolicy" in source
    assert "conversation_id" in source
    assert "sandboxWorkspaceStatus" in source
    # DockerWorkspaceBanner 已彻底移除，不再通过横条提示用户
    assert "DockerWorkspaceBanner" not in source


def test_embed_chat_sandbox_progress_toast_covers_prep_and_bash_ids():
    """沙箱拉起进度 toast 需同时覆盖 prep 占位 id 与 Bash 触发的独立 id，
    否则 Bash 动态拉起（workspace:sandbox:<tool_call_id>）会丢失 toast。"""
    source = EMBED.read_text(encoding="utf-8")
    # 统一判定 helper：两类 id 都命中
    assert "isSandboxPrewarmLogId" in source
    assert 'logId === "workspace:sandbox"' in source
    assert 'logId.startsWith("workspace:sandbox:")' in source
    # toast 分支使用该 helper（而非只精确匹配 prep id）
    assert "isSandboxPrewarmLogId(logId)" in source
    # 三态 toast 文案保留
    assert "正在拉起沙箱运行环境…" in source
    assert "沙箱环境已就绪，正在执行命令…" in source
    assert "沙箱环境启动失败" in source



def test_chat_input_context_modal_renders_docker_workspace_status_and_actions():
    chat_input_source = CHAT_INPUT.read_text(encoding="utf-8")
    assert "sandboxWorkspaceStatus" in chat_input_source
    assert "sandboxWorkspaceInstanceId" in chat_input_source
    assert "sandboxBackend" in chat_input_source
    assert "isDockerSandboxPolicy" in chat_input_source
    assert "isSandboxBackendPolicy" in chat_input_source
    assert "start-sandbox-workspace" in chat_input_source
    assert "refresh-sandbox-workspace" in chat_input_source
    assert "容器已运行" in chat_input_source
    assert "容器未启动" in chat_input_source
    assert "启动容器" in chat_input_source
    assert "启动沙箱 Pod" in chat_input_source
    assert "重试启动" in chat_input_source
    assert "Pod 已运行" in chat_input_source
    assert "Pod 未启动" in chat_input_source

    # 展开详情面板时静默触发沙箱状态刷新
    assert "if (isSandboxBackendPolicy.value) {" in chat_input_source
    assert "emit('refresh-sandbox-workspace', false);" in chat_input_source

    assert "sandboxWorkspaceStartedAt" in chat_input_source
    assert "sandboxWorkspaceUptimeSeconds" in chat_input_source


def test_docker_terminal_modal_component_contract():
    terminal_modal = ROOT / "frontend/src/components/chat/DockerTerminalModal.vue"
    assert terminal_modal.exists()
    source = terminal_modal.read_text(encoding="utf-8")
    assert "/api/v1/sandbox/docker/workspace/exec" in source
    assert "Docker 容器终端" in source
    assert "root@nanzi-sandbox" in source
    assert "常用命令:" in source


def test_docker_terminal_modal_renders_structured_welcome_card_each_open():
    terminal_modal = ROOT / "frontend/src/components/chat/DockerTerminalModal.vue"
    source = terminal_modal.read_text(encoding="utf-8")

    assert 'type WelcomeRecordKind = "command" | "welcome"' in source
    assert "const createWelcomeRecord = ()" in source
    assert 'kind: "welcome"' in source
    assert "records.value = [createWelcomeRecord()]" in source
    assert "immediate: true" in source
    assert "v-if=\"rec.kind === 'welcome'\"" in source
    assert "文件与同步" in source
    assert "/workspace/skills" in source
    assert "/workspace/public/docs" in source
    assert "同步到宿主机用户工作区" in source
    assert "停止 / 空闲回收" in source
    assert "重启 / 销毁" in source
    assert "能力越大，责任越大" in source
    assert "text-emerald-" in source
    assert "text-sky-" in source
    assert "text-violet-" in source
    assert "text-amber-" in source
    assert "text-rose-" in source


def test_docker_terminal_modal_keeps_command_execution_contract():
    terminal_modal = ROOT / "frontend/src/components/chat/DockerTerminalModal.vue"
    source = terminal_modal.read_text(encoding="utf-8")

    assert "/api/v1/sandbox/docker/workspace/exec" in source
    assert "runCommand" in source
    assert "clearTerminal" in source
    assert "commandHistory" in source
    assert "QUICK_COMMANDS" in source


def test_docker_terminal_memory_shortcut_uses_proc_meminfo_without_free_dependency():
    terminal_modal = ROOT / "frontend/src/components/chat/DockerTerminalModal.vue"
    source = terminal_modal.read_text(encoding="utf-8")

    assert '{ label: "内存概览 (/proc/meminfo)", cmd: "cat /proc/meminfo" }' in source
    assert 'cmd: "free -m"' not in source


def test_docker_terminal_opens_maximized_by_default():
    terminal_modal = ROOT / "frontend/src/components/chat/DockerTerminalModal.vue"
    source = terminal_modal.read_text(encoding="utf-8")

    assert "const isMaximized = ref(true);" in source
    assert "isMaximized ? 'h-full max-h-full max-w-full rounded-none' : 'max-w-4xl h-[85vh] max-h-[760px]'" in source



def test_chat_input_context_modal_generalizes_sandbox_workspace_controls():
    chat_input_source = CHAT_INPUT.read_text(encoding="utf-8")
    # 更名后的通用命名
    assert "sandboxWorkspaceStatus" in chat_input_source
    assert "sandboxWorkspaceInstanceId" in chat_input_source
    assert "sandboxWorkspaceStartedAt" in chat_input_source
    assert "sandboxWorkspaceUptimeSeconds" in chat_input_source
    assert "sandboxBackend" in chat_input_source
    # 术语由 backend 计算（docker 语义保留）
    assert "启动容器" in chat_input_source
    assert "启动沙箱 Pod" in chat_input_source
    assert "stop-sandbox-workspace" in chat_input_source
    assert "start-sandbox-workspace" in chat_input_source
    assert "refresh-sandbox-workspace" in chat_input_source
    assert "restart-sandbox-workspace" in chat_input_source
    # k8s 菜单不出现终端项：终端只在 docker 渲染
    assert "open-docker-terminal" in chat_input_source
    assert "进入终端" in chat_input_source


def test_embed_chat_routes_k8s_workspace_endpoints_and_stop_confirm():
    source = EMBED.read_text(encoding="utf-8")
    # k8s / docker workspace 端点按 backend 路由到各自 base
    assert "/api/v1/sandbox/k8s/workspace" in source
    assert "/api/v1/sandbox/docker/workspace" in source
    # 4 个动作（status/ensure/stop/restart）均通过 base 拼接调用
    assert source.count("sandboxWorkspaceBaseEndpoint.value}/") >= 4
    # k8s 停止前二次确认
    assert "showSandboxStopConfirm" in source
    assert "<ConfirmModal" in source
    assert "销毁沙箱 Pod" in source
    assert "sandboxWorkspaceStatus" in source
    assert "sandboxBackend" in source
