# 个人 Skills 体验设计

**日期:** 2026-07-19  
**状态:** 已批准，实施中

## 目标

1. 任意登录用户可在个人中心完整管理「我的技能」（含文件树，对齐技能工作台能力）。
2. 对话 `create_skills` → 个人中心编辑 → 聊天挂载试用形成闭环。
3. 挂载/注入个人技能时能正确预加载 `SKILL.md`。

## 非目标

- 普通用户管理平台技能
- 个人技能按智能体白名单 / 改 `skills_custom` 语义
- 导出 zip、创建技能 tool-nudge（可二期）
- `create_skills` 覆盖确认（可二期）

## 方案

抽/复用「我的技能」完整编辑能力；个人中心新增页签；管理台「我的技能」与个人中心共用同一套能力。聊天侧修复预加载与挂载 `scope`，并为 `create_skills` 成功结果提供「去编辑 / 立即挂载」入口。

## 入口与权限

- 个人中心新增 `?tab=skills`「我的技能」页签；仅需登录，不要求 `menu:skills_management`。
- `/dashboard/skills` 保留：平台技能写操作仍需管理权限；「我的技能」复用同一组件。
- 技能抽屉空态引导对话创建 + 链到 `/dashboard/personal?tab=skills`。

## 编辑能力

组件能力对齐现工作台「我的技能」：列表、新建、启停、删除、zip 导入、详情与文件树、读写上传、预览 SKILL.md。API 仍用 `/api/portal/skills/personal/*`。

实施时允许先以 `SkillsManagement` 的 `personalOnly` 模式挂载到个人中心，再视需要抽出 `PersonalSkillsWorkbench`；产品行为以本设计为准。

## 对话闭环

1. `create_skills(scope=personal)` 成功后返回可解析的 `skill_id` / `scope` / `name`。
2. 前端结果卡：「去编辑」→ `/dashboard/personal?tab=skills&skill_id=...`；「立即挂载」→ 当前会话挂载（含 `scope: personal`）。
3. 覆盖已存在技能首期保持现状。

## 预加载修复

- `load_skill_md_content` 支持个人目录（`skill_md_path` / `scope` + `user_info`），不限于 `SKILLS_DIR`。
- 挂载附件携带 `scope` 与 `skillMeta`。
- `_inject_skills` 优先按 meta 路径读全文。

## 验收

- 无管理权限用户可在个人中心 CRUD + 文件树；不能写平台技能。
- 有管理权限用户在工作台「我的技能」与个人中心行为一致。
- 对话创建后可跳转编辑、可挂载；个人技能注入含完整 SKILL.md。
- 抽屉空态正确；自动化测试覆盖预加载、挂载 scope、个人中心入口契约。
