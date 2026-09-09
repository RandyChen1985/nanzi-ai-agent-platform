# Kubernetes 沙箱输入框浮标：Pod 状态 / 运行时长 / 启停控制设计

## 背景与目标

当 `sandbox_policy = "k8s"` 时，聊天输入区旁的“Sandbox 策略”浮标目前只显示一个 `k8s` 徽标，没有任何沙箱运行信息；而 Docker 策略下同一浮标会展示容器运行状态、运行时长（每秒滚动）、容器 ID，并提供「启动容器 / 刷新 / 进入终端 / 重启容器 / 停止关机」等操作（由 `EmbedChat.vue` 轮询与 `ChatInput.vue` 渲染）。

目标：让 k8s 策略下该浮标获得与 Docker 对等的体验——展示 **Pod 运行状态与运行时长**，提供 **手动启动 / 刷新 / 重启 / 停止** 能力；Docker 浮标既有交互与文案保持不变。

已与用户确认的范围约束：

- 本期**不提供 k8s “进入终端”**（Pod exec 终端需要额外 WebSocket exec 链路，留作后续独立需求）。
- **k8s 的“停止关机”必须弹二次确认**，因为 Pod 重建较慢，且若为动态独立卷并开启 `sandbox_k8s_delete_pvc_on_close`，停止会连带删除 PVC（工作区数据丢失）。
- 手动“启动”仅预热/复用工作区（不执行任何用户命令），与 Docker `ensure` 语义一致。

## 非目标

- 不实现 k8s Pod exec 交互终端。
- 不改动沙箱 RBAC、Pod/PVC 的创建删除策略、空闲回收时长（k8s 与 docker 同为 30 分钟，`K8S_WORKSPACE_IDLE_SECONDS = 1800`）。
- 不改变 Docker 浮标的 UI/文案与现有端点契约。
- 不做跨进程/跨副本的沙箱状态同步（与 docker 现状一致的进程内缓存局限）。
- 不在本需求中为 k8s 增加镜像预构建（prebuild）能力。

## 现状关键事实

- 浮标载体：`frontend/src/components/embed/ChatInput.vue`（仅 `EmbedChat.vue` 与 `AgentDebug.vue` 使用）；`EmbedChat.vue` 是唯一绑定 docker 状态 props 与启停事件的父页面。主聊天页 `Chat.vue` 不使用 `ChatInput`，无此浮标。
- Docker 状态机字段：`dockerWorkspaceStatus: idle|starting|stopping|running|error`，配套 `containerId / startedAt / uptimeSeconds / error`，通过 `ChatInput` 的 `docker-*` props 传入。
- Docker 端点：`/api/v1/sandbox/docker/workspace/{status,ensure,stop,restart,exec}`，runtime 实现在 `app/services/ai/runtime/agentscope/workspace.py`（`ensure_docker_workspace`、`docker_workspace_status`、`stop_docker_workspace`、`restart_docker_workspace`、`docker_workspace_runtime_metadata` 等）。
- k8s workspace 生命周期（`workspace.py`）：
  - `_acquire_k8s_workspace(root, user_key, skill_paths)`，缓存键 `os.path.abspath(root)::user_key::k8s`，**按用户缓存**，与会话首次发消息时 `get_local_workspace → _acquire_k8s_workspace` 使用同一键 → 手动预热可被后续会话直接复用。
  - `_release_k8s_workspace`（引用计数归零才 close）、`reap_idle_k8s_workspaces` / reaper（空闲 1800s 清理）、`_evict_k8s_workspace_cache_entry`（清理用户级 k8s workspace 及其 `_workspace_cache` 会话引用）。
  - `_policy_k8s_workspace` 会在 workspace 上设置 `_platform_sandbox_policy = _platform_execution_backend = "k8s"`；Pod 名为 `self._pod_name`（形如 `as-ws-{user_key}`）；workspace 暴露 `workspace_id`、`is_alive`。
  - Pod 级真实状态（phase / startTime）需经 `kubernetes_asyncio` 读取 `read_namespaced_pod`。
- 错误归一：k8s 已有 `K8sSandboxUnavailableError`（`reason_code` + `user_message`），error_response / agent_service 已识别并直接展示 `user_message`（上一轮修复已完成）。

## 后端设计

### 1. workspace.py 新增 k8s runtime 函数族（与 docker 族并列）

均接受 `user_id / user_name / user_info / conversation_id` 参数，以 `_resolve_sandbox_user_key` 得到 `user_key`；会话缺失或策略非 k8s 时抛 `K8sSandboxUnavailableError`（reason_code：`k8s_policy_not_effective` / `k8s_identity_required` / 其它 `k8s_*`，与 docker 族语义对称）。

- `k8s_workspace_runtime_metadata(workspace)`：返回安全元数据 `{status, execution_backend: "k8s", workspace_id, pod_name, started_at, uptime_seconds}`。
- `k8s_workspace_status_runtime(...)`：**只读，绝不触发初始化**。
  1. 用与 `get_local_workspace` 相同的解析（`resolve_workspace_root` + `_resolve_sandbox_user_key`）拼出缓存键，查 `_k8s_workspace_cache`；未命中 → 返回 `{status:"idle", execution_backend:"k8s", running:false}`。
  2. 命中：优先通过 `kubernetes_asyncio` `read_namespaced_pod`（命名空间/镜像读取自配置或 workspace 属性）拿真实 `phase`、`start_time`、容器 ready 状态，映射：
     - `Running` 且容器 ready → `running`；
     - `Pending` / `ContainerCreating` → `starting`；
     - `Succeeded`/`Failed`/`Unknown`/删除中 → 按 `is_alive` 降级为 `stopped`/`error`；
     - Pod 404 → 视为已停止（不自动重建）。
  3. `kubernetes_asyncio` 缺失或无权限等异常 → 捕获并降级：以 `workspace.is_alive` 判断 `running/stopped`，`started_at` 回退到进程内 `_platform_started_at`（无则 `None`），不让端点 500。
  4. `uptime_seconds` 由 `started_at` 计算；无 `started_at` 时为 `None`。
- `ensure_k8s_workspace_runtime(...)`：校验会话、有效策略为 k8s、用户身份 → `_acquire_k8s_workspace(root=resolve_workspace_root(), user_key, skill_paths=discover_platform_skill_paths(user_info, skills_custom=False))`（与 `get_local_workspace` 同键，避免重复建 Pod），返回 `k8s_workspace_runtime_metadata`。初始化失败按现有 `K8sSandboxUnavailableError` 归一（k8s_workspace 已包装 Pod/namespace 错误）。该 acquire 增加的引用计数沿用 docker ensure 语义（预热引用，由 reaper 兜底回收，不泄漏）。
- `stop_k8s_workspace_runtime(...)`：定位该用户 k8s 缓存键 → 调用 `_evict_k8s_workspace_cache_entry`（或等价的“移除并安全 close”，复用 `_close_workspace_safely` 以保护共享 PVC 不被删除），返回停止结果。
- `restart_k8s_workspace_runtime(...)`：stop 语义后再走 ensure 语义重建，返回重建后的 metadata。

### 2. sandbox.py 新增端点

复用现有 `DockerWorkspaceEnsureRequest`（`conversation_id`）与鉴权（`require_api_key`，仅当前登录用户操作自己会话）：

- `GET  /api/v1/sandbox/k8s/workspace/status?conversation_id=...`
- `POST /api/v1/sandbox/k8s/workspace/ensure`  `{conversation_id}`
- `POST /api/v1/sandbox/k8s/workspace/stop`     `{conversation_id}`
- `POST /api/v1/sandbox/k8s/workspace/restart`  `{conversation_id}`

异常统一：`K8sSandboxUnavailableError` → `HTTPException(status_code=503 或 409(策略不符), detail={reason_code, message: user_message})`。

status 响应样例：

```json
{
  "status": "running",
  "running": true,
  "execution_backend": "k8s",
  "workspace_id": "admin__1",
  "pod_name": "as-ws-admin--1",
  "started_at": "2026-09-09T10:00:00Z",
  "uptime_seconds": 1234
}
```

未启动时：`{"status":"idle","running":false,"execution_backend":"k8s",...}`。

## 前端设计

### EmbedChat.vue

- 将 docker 专属的控制逻辑按 `effectiveSandboxPolicy ∈ {docker, k8s}` 泛化：**状态变量、props、事件名统一由 `dockerWorkspace*` 更名泛化为 `sandboxWorkspace*`**（`ChatInput.vue` 同步更名；可选 props，`AgentDebug.vue` 不受影响），另加 `sandboxBackend` 计算属性区分术语与端点路由。
  - API URL 与响应归一按 backend 路由：docker → `container_id`；k8s → `pod_name`（均落到同一 `instanceId` 状态字段）。
  - `refreshSandboxWorkspaceStatus / ensureSandboxWorkspace / stopSandboxWorkspace / restartSandboxWorkspace` 在 docker/k8s 下调用各自端点；`conversation_id`、轮询节奏、切换会话/策略时 watch 行为与 docker 现状一致。
  - k8s 下 running 时显示浮标；idle/error 走与 docker 相同的“启动条/错误条”机制（`DockerWorkspaceBanner` 泛化为 `SandboxWorkspaceBanner` 或按 backend 传参）。
- 不做周期轮询：沿用 docker 现状（会话/策略切换触发一次 status 查询 + 前端本地每秒滚动时长），避免增加 K8s API 调用频率。

### ChatInput.vue

- 浮标面板可见条件：`isDockerSandboxPolicy` → `(docker | k8s)`。
- 新增 prop `sandboxBackend?: "docker" | "k8s"`（缺省按 `contextUsage.sandbox_policy` 推断），文案与 ID 语义按 backend 切换：
  - 状态点颜色逻辑复用（running=绿 / starting,stopping=琥珀脉冲 / error=红 / idle=灰）；
  - 文案：`容器已运行/容器未启动/容器启动中…/容器关机中…` ↔ `Pod 已运行/Pod 未启动/Pod 创建中…/Pod 终止中…`；
  - ID：`容器 ID: abcd1234…` ↔ `Pod: as-ws-admin--1`（截断展示 + title 全名）；
  - 按钮：`启动容器` ↔ `启动沙箱 Pod`；错误态 `重试启动` 复用；
  - 自动回收行：docker 文案不变，k8s 显示 `空闲 30m 自动回收沙箱`（同 30 分钟）。
- 「操作」下拉：docker 保持 `进入终端 / 重启容器 / 停止关机`；k8s 仅 `重启 Pod / 停止关机`。
- **k8s 停止二次确认**：点击停止先弹确认（文案明确：将销毁沙箱 Pod；若为动态独立卷且开启 `delete_pvc_on_close`，工作区数据随 PVC 删除；Pod 重建需等待镜像拉取与调度），确认后才发 stop 请求。确认弹层沿用项目惯例：`ChatInput.vue` 内局部 `showConfirmModal` + Tailwind overlay（参照 `ChatSettings.vue` / `McpServiceDesk.vue`），不引入全局组件。docker 不弹。
- 恢复“运行中”状态时重置本地 uptime tick（现有 `dockerUptimeTimer` 机制对 k8s 同样生效）。

## 测试

后端（pytest，`no_infrastructure`，mock `kubernetes_asyncio`）：

- status：无缓存 → `idle` 且不触发初始化；Pod `Running` → `running + started_at + uptime_seconds`；Pod `Pending` → `starting`；Pod 404 / SDK 缺失 / 无权限 → 优雅降级不抛 500。
- ensure：策略非 k8s → `K8sSandboxUnavailableError`（`k8s_policy_not_effective`）；会话缺失/无身份 → 对应错误；与 `_acquire_k8s_workspace` 同键复用（第二次调用不重复建 Pod）。
- stop：清理用户级 k8s 缓存 + 会话级 `_workspace_cache` 引用；共享 PVC 不被删除。
- restart：stop 后重建成功返回 running metadata。
- 端点层：4 个端点的路由/鉴权/错误 detail 结构（复用 docker 端点的测试风格）。

前端：

- `vue-tsc --noEmit` 通过；
- `pytest --confcutdir=tests/frontend`（既有前端契约测试如有沙箱相关则同步更新）；
- 回归：docker 既有测试（含 `test_bind_docker_workspace_*`、docker 端点）全绿。

## 风险与边界

- Pod 创建受镜像拉取/调度影响，可能数十秒：starting 态沿用 docker 模式（按钮禁用 + 琥珀脉冲），前端不做长轮询。
- status 依赖集群可读权限；无权限时降级只反映进程内缓存（UI 以缓存视图为准并允许手动“刷新”）。
- 动态独立卷 + `delete_pvc_on_close=true` 时停止会删除 PVC 数据 → 二次确认文案必须覆盖该风险。
- 手动 ensure 与“当前会话正在执行任务”并发：复用同一缓存键 + 进程内锁（`_k8s_workspace_locks`），不新建重复 Pod；执行中任务不受影响。
- 多 worker 进程部署下与 docker 相同存在进程内缓存局限，本期不做跨进程同步（沿用既有架构约束）。

## 上线与验证

实现完成后由用户在控制台执行 `./dev.sh` 后人工验证：

1. k8s 策略下进入会话，浮标显示「Pod 未启动」+「启动沙箱 Pod」；点击启动 → `Pod 创建中…` → `Pod 已运行`，展示 Pod 名与每秒增长运行时长；
2. 「刷新」可用；「操作 → 重启 Pod」可重建；「停止关机」弹二次确认，确认后回到未启动态；
3. 停止后向该会话发消息，平台应自动重建 Pod 且正常执行（验证缓存键复用）；
4. 切回 docker 策略，浮标交互与旧版一致（回归）。
