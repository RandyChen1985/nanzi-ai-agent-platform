# Jira Integration Spec

## 1. Goal (目标)
集成 Atlassian Jira，赋予 AI Agent 协同办公能力，实现“对话即工单”和“自然语言查任务”。

## 2. Requirements (需求)
1.  **Search**: 支持通过自然语言查询 Jira 工单（自动转换为 JQL）。
2.  **Create**: 支持通过对话创建新的工单（自动提取 Project, Summary, Type, Assignee）。
3.  **Authentication**: 支持 Basic Auth (User+Password) 兼容老版本 Jira Server。
4.  **Metadata Awareness**: 解决 JQL 字段幻觉问题，通过 Prompt 引导 AI 使用正确的字段名和项目 Key。

## 3. Tool Design (工具设计)

### 3.1 `jira_search`
- **Description**: Search Jira issues using JQL (Jira Query Language).
- **Parameters**:
    - `jql` (string, required): The raw JQL query string.
- **Output**: List of simplified issue objects (Key, Summary, Status, Assignee, Created, Link).

### 3.2 `jira_create_issue`
- **Description**: Create a new Jira issue (Task, Bug, or Story).
- **Parameters**:
    - `project_key` (string, required): The project key (e.g., OPS, NANZI). NOT the project name.
    - `summary` (string, required): Brief title of the issue.
    - `description` (string, required): Detailed description.
    - `issue_type` (string, default="Task"): Task, Bug, Story.
    - `assignee` (string, optional): Username of the assignee (e.g., 'zhangsan').
- **Output**: Created issue details + URL.

### 3.3 `jira_get_metadata` (Optional/Future)
- **Description**: Retrieve available projects and issue types to help construct valid requests.
- **Parameters**: `query` (string, optional) - Filter projects by name.

## 4. Prompt Engineering Strategy (提示词策略)

### 4.1 System Prompt Injection (元数据注入)
为了提高 JQL 生成准确率，在注册 Jira 工具的 Agent（如 `collaboration-agent`）的 System Prompt 中，必须包含以下指南：

```markdown
### Jira Usage Guidelines
1. **Fields**: Use standard Jira fields: `project`, `issuetype`, `status`, `assignee`, `reporter`, `created`.
   - `assignee`: Use username, NOT display name.
   - `status`: Typically 'Open', 'In Progress', 'Done', 'Reopened'.
2. **Project Keys**: Always use the KEY, not the name.
   - Example: Use `project = OPS` not `project = "Operations"`.
   - Common Keys: OPS (运维), NANZI (南孜平台), DATA (大数据).
3. **JQL Syntax**:
   - String values must be quoted: `summary ~ "error"`
   - Dates: `created >= -7d` (last 7 days).
```

### 4.2 Few-Shot Examples (样本提示)
在工具描述或 Prompt 中提供转换示例：

**User**: "查一下张三名下还有哪些没做完的 Bug"
**AI Thought**: User wants `assignee = zhangsan` AND `issuetype = Bug` AND `status != Done`.
**Tool Call**: `jira_search(jql='assignee = "zhangsan" AND issuetype = "Bug" AND status != "Done" ORDER BY created DESC')`

**User**: "给 OPS 项目提个单，服务器磁盘满了"
**Tool Call**: `jira_create_issue(project_key="OPS", summary="服务器磁盘空间告警", description="检测到服务器磁盘使用率超过 90%...", issue_type="Bug")`

## 5. Implementation Plan (实施计划)
1.  **Dependencies**: Add `atlassian-python-api`.
2.  **Config**: Add `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD` to `.env`.
3.  **Code**: Implement `JiraTools` class wrapping `atlassian.Jira`.
4.  **Integration**: Register tools in `ToolRegistry`.
5.  **Validation**: Test connectivity with `jirarobot`.

## 6. Error Handling (错误处理)
- **JQL Errors**: If Jira returns 400 (Bad Request), return the error message ("Field 'xxx' does not exist") to the AI.
- **Self-Correction**: AI should be instructed to read the error message and retry with corrected fields (e.g., retry `owner` -> `assignee`).
