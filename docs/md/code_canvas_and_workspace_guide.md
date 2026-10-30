# 代码画布与工作区执行指南

代码画布用于在当前会话中查看、运行和解释短小的 Python 或 Shell 代码。它是用户可控的工作区能力，不等同于安装任意系统工具，也不会绕过 Agent、MCP、Skill 或 RBAC 权限。

## 适用场景

- 在 ChatCanvas 中临时验证数据处理、文件整理或计算逻辑；
- 将运行输出继续交给当前对话分析；
- 打开工作区中的 `.py`、`.sh`、`.bash` 文件，确认内容后执行。

当前支持的语言为 `python`、`sh`、`bash`；`python3` 和 `shell` 作为兼容别名。Node.js、JavaScript、TypeScript、SQL 不是代码画布的执行语言。

## 在界面中使用

1. 在对话中打开代码画布，或从工作区预览中打开脚本。
2. 编辑代码并点击运行。画布会在成功打开后固定到当前会话，用户也可以手动取消固定。
3. 运行期间可查看 stdout、stderr 和状态；需要提前结束时点击停止。
4. 运行结束后，可将结果发送回对话，让智能体继续解释或生成下一步操作。

保存文件和运行文件是两个独立动作：只保存不会执行，运行时应以当前画布内容或明确选中的工作区文件为准。

### 执行位置与沙箱策略

画布运行与对话内 Bash 工具同源读取系统配置 `sandbox_policy`：

| 策略 | 执行位置 | 会话工作目录 |
| --- | --- | --- |
| `local` | 平台后端进程 | 后端真实路径 `<workspace_root>/<user_key>/sessions/<conversation_id>` |
| `docker` | 当前会话的 Docker 沙箱容器 | `/workspace/sessions/<conversation_id>` |
| `k8s` | 当前会话的 K8s 沙箱 Pod | `/workspace/sessions/<conversation_id>` |
| `e2b` / `ssh` | 暂不支持 | 没有「宿主工作区 → 沙箱路径」映射，运行会被明确拒绝 |

沙箱策略下，脚本先写入宿主会话目录（容器/Pod 内是同一份文件），再在沙箱内以固定解释器执行；输出仍按 SSE 实时回传。画布输出区会标注实际执行后端（Docker 沙箱 / K8s 沙箱 / 本机）。运行前请确认该会话的沙箱已启动（至少发送过一条消息），否则会提示先启动沙箱，不会静默回退到后端本机执行。

沙箱中的代码引用工作区文件时，请使用会话工作目录内的相对路径（例如 `python ./your_script.py`），或按上表的沙箱逻辑路径引用；把宿主路径或另一个策略下的路径写死在脚本里会找不到文件。

## V1 API

所有接口都需要 API Key。流式执行：

```http
POST /api/v1/chat/code-executions/stream
Content-Type: application/json
Authorization: Bearer <api-key>
```

请求体：

```json
{
  "language": "python",
  "code": "print('hello')",
  "conversation_id": "conversation-id"
}
```

响应为 SSE。事件的 `data` 是 JSON，常见事件包括执行状态、stdout、stderr 和完成结果；状态可能为 `succeeded`、`failed`、`stopped`、`timed_out` 或 `blocked`。

停止执行：

```http
POST /api/v1/chat/code-executions/{execution_id}/stop
Content-Type: application/json
Authorization: Bearer <api-key>
```

```json
{
  "conversation_id": "conversation-id"
}
```

接口详情和通用流式约定见 [API 集成指南](api_integration_guide.md)。

## 执行边界

- 每个用户/会话使用隔离的私有工作区，不能通过路径穿越访问其他工作区；
- 只调用平台允许的固定解释器，不提供 Node、包管理器或任意命令安装入口；
- 默认单次执行超时为 60 秒，输出上限为 100 KB，超限或超时会结束本次执行；
- 停止操作必须匹配当前用户和会话，不能停止其他用户的执行；
- 工作区文件执行仍需通过路径安全校验，悬空符号链接和解析后越界路径会被拒绝；
- 代码画布不改变数据库、MCP Server、Skill 发布或 Agent 工具白名单的权限边界。

生产环境如需调整超时、输出上限或工作区策略，应以当前服务配置和安全评审为准，不要在前端绕过限制。
