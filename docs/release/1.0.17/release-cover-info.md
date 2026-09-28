# NanZi AI Agent Platform v1.0.17.0 Cover Info

## 封面主标题

NanZi AI Agent Platform v1.0.17.0

## 封面副标题

AgentScope 2.0.9 精确锁定 · 执行时间线全链路可观测 · 向量索引维度守卫 · Doris 与维度下钻元数据 · 智能体一键复制 · 系统配置与诊断页重构

## 🎯 120 字核心引流文案（带钩子）

### 版本一：痛点反转版（推荐·公众号导读/朋友圈）
> 模型明明返回了 20 条记录，AI 却只看到 7 条，还反复念叨「要去 sessions 读完整结果」？镜像重建一次就悄悄换了 AI 框架版本，线上出故障根本复现不了？NanZi v1.0.17.0 把 AgentScope 精确锁到 2.0.9，并把工具结果上限从 4000 字符统一放宽到 64Ki；执行时间线现在能看到 Bash 命令原文、模型写的意图摘要与调用元信息！

### 版本二：极客硬核版（技术社区/群发）
> NanZi v1.0.17.0 发布：完成 AI 运行时的版本可控化改造——`agentscope` 由 `>=2.0.6` 锁定为 `==2.0.9`，结束「镜像重建一次就悄悄换了版本、线上故障无法复现」的历史。可观测性上，执行时间线补齐 `tool_args` 命令原文与 `tool_summary` 意图摘要，工具结果双闸门统一到 64Ki。数据侧交付 Doris 适配器、`dimension_role`/`hierarchy_group` 维度下钻元数据与 `partition_fields`/`index_fields`，并补齐遗漏的 PG 迁移（否则主库部署即 `UndefinedColumn`）。

### 版本三：数据与企业协同版（企业集成与管理）
> 企业级智能体平台再加固：NanZi v1.0.17.0 让 AI 不再「靠猜」做下钻——字段自带维度角色（time/geo/category/identifier）与层级组序号，ChatBI 能识别可分组字段与下钻链；物理表的分区字段与索引字段进入元数据，生成的 SQL 可裁剪、可命中索引。平台侧支持一键复制智能体（含最新已发布版本，副本直接可用）、物理标识符重名预检与失焦/输入双时机校验、智能体专属头像三层继承（消息 → 智能体 → 全局形象 → 内置默认）；用户管理支持批量启用/禁用（防暴破式地「禁用即踢下线」，并自动跳过操作人自己），角色分配用户重构为标准双列穿梭框；知识库管理页、召回测试页与运营分析看板完成数据正确性、失败态与并发竞态的集中治理，运营指标接口收归管理员专属。

## 视觉关键词

- AI 运行时精确版本锁定与可复现构建（Pinned Runtime & Reproducible Build）
- 大模型请求读取超时可配置与上下文图片上限固化（Configurable LLM Read Timeout & Pinned Image Budget）
- 执行时间线全链路可观测：命令原文、意图摘要与调用元信息（Timeline Observability & Tool-Call Metadata）
- 工具结果双闸门统一 64Ki（Unified 64Ki Tool Result Budget）
- 系统诊断页上下结构重构与 Redis 浏览业务分组（Diagnostics Redesign & Redis Business Grouping）
- 向量索引维度守卫与保留式重建（Vector Index Dim Guard & Preserve-on-Drop）
- 通用任务日志通道与重构进度抽屉（Task Log Channel & Rebuild Progress Drawer）
- Doris 数据源适配与字段维度下钻元数据（Doris Adapter & Dimension Hierarchy Metadata）
- 智能体一键复制、重名预检与三层继承专属头像（Agent Duplication, Name Precheck & Avatar Inheritance）
- 全站表单控件边框/内边距兜底与统一底色（Form Control Baseline & Unified Field Surface）

## 推荐用途

- GitHub Release 头图
- 微信公众号文章封面/首图
- 技术社区与官网版本发布说明配图

## 生成图路径

`docs/release/1.0.17/release-cover-v1.0.17.0.png`

## 最终生图提示词

```text
Use case: ads-marketing
Asset type: 16:9 release-note cover image, final project asset
Primary request: Create a state-of-the-art cinematic landscape cover image for "NanZi AI Agent Platform v1.0.17.0" release notes.
Canvas: 16:9 landscape, 2048x1152 composition.
Scene/backdrop: A high-tech futuristic enterprise AI observability and version-control bridge fused with a data-governance cockpit. In the center, a glowing obsidian runtime core sealed inside a hexagonal "version pin" clamp: a single luminous version tag locked onto a floating dependency manifest plate, with a ghostly duplicate plate drifting away and dissolving into particles (symbolizing the end of silently drifting framework versions and unreproducible builds). Radiating outward are multi-layered luminous digital ribbons: one stream showing a live execution timeline with segmented step cards, from which a glowing terminal command line and a short intent-summary label extend; one stream showing a datastore index being re-sealed with a caliber/dimension gauge and a preserved-document lock icon; one stream showing a cannibalized data table whose columns carry small colored dimension-role badges and a descending drill-down staircase; and a floating agent avatar medallion trio layered one behind another (message → agent → global identity) beside a duplicated agent card splitting into two.
Subject: Futuristic cloud-native agent platform visual: luminous sapphire blue and cyan energy core, floating container/sandbox node geometry, revocable runtime pin capsules, live telemetry ribbon graphs, and sleek glassmorphism data displays.
Style/medium: Premium digital enterprise tech product launch art, clean 3D render mixed with modern editorial minimalism, hyper-detailed glassmorphism, crisp lighting, cinematic depth of field. Suitable for high-end GitHub release banner and WeChat feature cover.
Composition/framing: Balanced wide-angle composition with a clear heroic centerpiece, generous negative space on the upper left for release title typography overlay, clean layout without chaotic clutter.
Lighting/mood: Inspiring, deterministic and reproducible engineering rigor, real-time visibility, data-integrity confidence; luminous cyan glow, cobalt blue, emerald mint accents, and precise instrumentation amber highlights against an obsidian backdrop.
Color palette: Deep space obsidian background, vibrant royal blue (#2563eb), electric cyan (#06b6d4), Kubernetes indigo (#4f46e5), fresh mint emerald (#10b981), and instrumentation amber (#f59e0b).
Text (verbatim): Include only this large readable title if possible: "v1.0.17.0". Do not include any other text.
Constraints: No external company logos, no watermark, no tiny unreadable text, no low-quality artifacts, no humans, no cartoon mascots. Keep the visual crisp, balanced, and instantly readable as a premium thumbnail.
```
