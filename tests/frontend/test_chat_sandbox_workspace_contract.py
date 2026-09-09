from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
BANNER = ROOT / "frontend/src/components/chat/DockerWorkspaceBanner.vue"


def test_docker_workspace_banner_has_start_running_and_retry_states():
    source = BANNER.read_text(encoding="utf-8")
    assert "启动我的 Docker 沙箱" in source
    assert "重试启动" in source
    assert "Docker 沙箱容器已运行" in source
    assert "Docker 沙箱容器启动中" in source
    assert "workspaceStatus" in source
    assert "defineEmits" in source
    assert "关闭 Docker 沙箱提示" in source
    assert '(event: "close")' in source
    assert "AUTO_DISMISS_SECONDS = 3" in source
    assert "handleMouseEnter" in source
    assert "handleMouseLeave" in source


def test_embed_chat_places_workspace_action_in_banner_and_calls_ensure_api():
    source = EMBED.read_text(encoding="utf-8")
    assert 'import DockerWorkspaceBanner from "@/components/chat/DockerWorkspaceBanner.vue"' in source
    assert "/api/v1/sandbox/docker/workspace/ensure" in source
    assert "effectiveSandboxPolicy" in source
    assert 'effectiveSandboxPolicy.value === "docker"' in source
    assert "<DockerWorkspaceBanner" in source
    assert "#banner" in source
    assert "conversation_id" in source
    assert "/api/v1/sandbox/docker/workspace/status" in source
    assert '@close="dismissDockerWorkspaceBanner"' in source


def test_embed_chat_does_not_render_workspace_action_for_non_docker_policy():
    source = EMBED.read_text(encoding="utf-8")
    control_pos = source.find("<DockerWorkspaceBanner")
    assert control_pos != -1
    control_block = source[control_pos : control_pos + 700]
    assert "showDockerWorkspaceControl" in control_block
    assert "effectiveSandboxPolicy.value === \"docker\"" in source


CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"


def test_embed_chat_persists_banner_dismiss_and_auto_hides_when_running():
    source = EMBED.read_text(encoding="utf-8")
    assert "nanzi_dismissed_docker_workspace_banner" in source
    assert "readDockerWorkspaceBannerDismissed" in source
    assert "dockerWorkspaceStatusLoaded" in source
    assert 'dockerWorkspaceStatus.value === "running"' in source
    assert 'dockerWorkspaceStatus.value === "error"' in source
    assert 'name="bash-banner-fade"' in source



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


def test_docker_workspace_banner_supports_k8s_backend_copy():
    source = BANNER.read_text(encoding="utf-8")
    assert 'backend?: "docker" | "k8s"' in source
    assert "Kubernetes 沙箱 Pod" in source
    assert "启动我的沙箱 Pod" in source
    assert "关闭沙箱提示" in source


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
