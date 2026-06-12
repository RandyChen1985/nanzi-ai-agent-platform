---
name: dev-skills
description: 用户专属开发技能，包含中文沟通规范、代码提交流程及自动化构建部署指令。
---

# Dev Skills

此技能定义了用户偏好的开发流程和规范。请在所有任务中严格遵守以下规则。

## 1. 沟通与文档 (Communication & Documentation)

- **核心原则**：始终使用 **中文 (Chinese)** 与用户交流，无论是在对话中还是在生成的文档中。
- **OpenSpec 与需求文档**：
  - 创建或更新 OpenSpec 需求文件（如 `proposal.md`, `design.md` 等）时，必须使用 **中文**。
  - `task.md` 和 `implementation_plan.md` 等工件也必须使用中文编写。

## 2. 计划与原理解释 (Planning & Rationale)

在进行任何功能开发或 Bug 修复 **之前**，必须先确定计划。

- **分级计划机制 (Tiered Planning)**：
  - **微小改动 (Trivial)**：如拼写修复、样式微调。可以在对话中直接解释 Why/How，获得确认后执行。
  - **常规任务 (Normal/Feature)**：涉及逻辑变动。必须创建 `implementation_plan.md`。
  - **主动询问原则**：在开始执行前，Agent 必须询问用户：“此任务是否需要创建正式的实现计划文件？”。
- **创建/更新 Implementation Plan (若确定需要)**：
  - 使用 `implementation_plan.md` 详细列出修改计划。
  - **核心内容必须包含**：
    1.  **为什么这么做 (Why)**：解释修改的动机、根本原因 (Root Cause) 或设计思路。
    2.  **准备怎么搞 (How)**：详细的修改步骤、涉及的文件以及预期的效果。
    3.  **为什么这么修改 (Rationale)**：针对具体的代码变动，解释选择此实现方式的原因。
- **确认先行**：在开始写代码之前，必须先让用户阅读并确认计划。

## 3. 代码提交规范 (Git Workflow)

- **严禁自动推送**：每次修改完成并提交代码时，除非用户下达了明确的 “Push” 或 “推送” 指令，否则 Agent **只允许在本地执行 `git commit` 提交，绝对禁止自动执行 `git push` 同步到远程**。
- **提交确认**：本地进行 `git commit` 前，也必须征得用户明确确认，严禁自动执行。

## 4. 开发环境与启停服务 (Development & Startup)

- **禁止主动执行启动脚本**：代码修改完成后，Agent **不得自行或自动运行** `./dev.sh` 等编译/启动脚本。
- **由用户主导重启**：所有的服务启停、重载与日志观察均由用户手动完成（以方便排查问题）。当修改完成后，仅需通知用户代码已就绪，等待用户自行测试。

## 5. 代码推送与 PR 流程 (Push & PR Workflow)

当用户要求“提交代码并发布”、“Push”或“创建 PR”时，遵循以下流程：

1.  **检查与切换分支 (Branching)**：
    - 根据任务内容（Feature/Fix）生成新分支名（例如 `feat/xxx`），并使用 `git checkout -b <new_branch>` 切换。
2.  **推送到远程 (Pushing)**：
    - 执行 `git push -u origin <new_branch>`。
3.  **创建 Pull Request (Automated PR)**：
    - 自动生成 PR 标题和详细描述，并尝试使用 `gh pr create` 创建。
4.  **分支清理 (Cleanup)**：
    - 在 PR 合并或发布流程结束后，Agent 必须主动询问或提醒用户清理已完成的功能分支。

## 6. 数据库与提示词变更规范 (Database & Prompt Changes)

- **数据库变更 (Database Changes)**：
  - **执行边界 (Strict Boundary)**：凡涉及数据库 Schema 或数据变更，Agent **仅负责**在 `db-prod/` 目录下创建 SQL 脚本。**严禁**自动执行 SQL 语句或通过 Python 脚本直接修改数据库。
  - **命名规范**：使用 `V` 开头 + 自增序号 + 描述（例如 `V29-add_new_table.sql`，必须检查目录下的当前最大序号并 +1）。
- **提示词变更 (Prompt Changes)**：
  - 凡涉及系统提示词 (Prompts) 的新增或更新，必须在 `architech/prompts/` 下操作并进行版本控制。
  - Agent 仅负责创建/更新文件，不负责自动同步到系统内部。
