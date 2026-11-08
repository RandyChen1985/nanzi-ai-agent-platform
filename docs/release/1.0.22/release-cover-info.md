# NanZi AI Agent Platform v1.0.22.0 Cover Info

## 封面主标题

NanZi AI Agent Platform v1.0.22.0

## 封面副标题

数据集权限四层治理 · ChatBI 范围硬约束 · 专家团多智能体编排 · 用户模式双界面 · 知识库工作台重构 · 安全审计整改 24 项

## 🎯 120 字核心引流文案（带钩子）

### 版本一：痛点反转版（推荐·公众号导读/朋友圈）
> 配了「只能看这几张表」的权限，用户却还是查到了别的数据集？含敏感字段的口径被顺手带进大模型？团主配齐了 4 位专家，问一句它就回「你自己去办」？NanZi v1.0.22.0 正式发布：数据集权限管到表、字段、行与有效期四层；ChatBI 的数据集范围从「只打日志」变成执行期硬约束；专家团把多专家编排做成开箱能力；一线同事还能切到只有一屏的用户模式！

### 版本二：极客硬核版（技术社区/群发）
> NanZi v1.0.22.0 重磅发布：数据集权限升级为四层治理，`dataset_scope_service` 作为唯一判定源，字段级「所有来源取交集」、执行期按 SQL 位置分类剔列与拒绝，`SHOW TABLES` 这类绕过四道门禁的写法一并封死；ChatBI 补齐范围解析、委派链路继承与指标块 SQL 泄露，越权写法在直查 / CTE / 子查询 / UNION / JOIN 六种形态全部拒绝；角色权限保存的 Redis `SCAN` 从 2N 压到 2，1000 用户 39.4 秒 → 0.28 秒！

### 版本三：企业安全与协同版（企业集成与管理）
> 企业级数据治理与多智能体协同的双里程碑：NanZi v1.0.22.0 让「谁能看哪些表的哪些列、哪些行、到什么时候」第一次可以用界面配置并全局审计——四层权限、权限总览、风险排序、权限模拟；系统级安全审计确认的 24 项越权、代码执行与静默失效全部修复并改为 fail-closed；专家团把「一位团主 + 一支专家队伍」变成可配置、可解释、深度受限的编排单元；用户模式让一线使用者只面对一屏菜单，管理员还能按角色禁用某个界面模式。

## 视觉关键词

- 四层权限栅格与数据资产防线（Four-tier Permission Lattice & Data Asset Perimeter）
- 表 / 字段 / 行 / 有效期的立体权限方阵（Table × Column × Row × Expiry Permission Matrix）
- 专家团编队与委派深度边界（Expert Team Constellation & Delegation Depth Boundary）
- 双界面镜像：简化舱与专业舱（Dual-mode Interface Mirror: Focus Cabin & Pro Console）
- 安全审计的盾形扫描与 fail-closed 闸门（Security Audit Shield & Fail-closed Gate）
- 性能脉冲：缓存批量清理与向量增量同步（Cache Bulk-flush & Vector Incremental Pulse）

## 推荐用途

- GitHub Release 头图
- 微信公众号文章封面 / 焦点首图
- 技术社区（掘金、知乎、V2EX、开源中国）发版文章配图
- 官方发布宣传横幅

## 生成图路径

`docs/release/1.0.22/release-cover-v1.0.22.0.png`

## 最终生图提示词

```text
Use case: ads-marketing
Asset type: 16:9 release-note cover image, final project asset
Primary request: Create an ultra-modern, cinematic enterprise tech cover image for "NanZi AI Agent Platform v1.0.22.0" release notes.
Canvas: 16:9 landscape, 2048x1152 resolution.
Scene/backdrop: A futuristic data-governance command deck built from obsidian glass and frosted panels, floating above a deep slate navy void with slow rivers of cyan data. In the focal center: a four-layer translucent permission lattice — horizontal planes representing dataset, table, column and row — with thin light beams projecting down through all four layers onto a single glowing data core, symbolizing four-tier permission enforcement. To the left: a constellation of small expert avatars arranged in a tight formation around one brighter lead node, connected by short directed arcs of violet light, with a visible depth ring marking a single permitted hop, representing the expert team and delegation depth boundary. To the right: a mirrored pair of interface shells — one minimal focus cabin with a single narrow rail, one dense professional console with layered panels — joined by a soft emerald gradient, representing the dual UI modes. In the foreground: a faceted shield sweeping a thin scan line across the lattice, with a small set of red nodes being sealed behind an amber gate, representing the security audit and fail-closed gates.
Subject: Enterprise data permission governance and multi-agent orchestration platform: four-tier permission lattice, expert team constellation, dual interface mirror, security scan shield.
Style/medium: State-of-the-art 3D digital tech render, ultra-crisp studio lighting, cinematic depth of field, balanced volumetric illumination, clean minimal enterprise UI accents, subtle glassmorphism.
Composition/framing: Balanced wide-angle landscape composition with an inspiring heroic center, generous breathing room on the top-left for typography overlay, strictly clean and uncluttered.
Lighting/mood: Inspiring, deterministic, enterprise-grade engineering rigor; sapphire blue (#2563eb), electric cyan (#06b6d4), modern slate navy (#0f172a / #1e293b), emerald mint (#10b981) for active protection and passing checks, restrained violet (#8b5cf6) for expert team arcs, subtle warm amber accents for sealed and rejected paths.
Color palette: Deep Slate Navy obsidian background, electric cyan glow, vivid royal blue, emerald verification green, restrained violet accent.
Text (verbatim): Include only this crisp modern badge if text is rendered: "v1.0.22.0". Do not generate any messy or garbled text.
Constraints: No messy watermarks, no cartoon characters, no distorted hands/faces, no external trademark logos, ultra-high resolution and crystal-clear visual hierarchy.
```
