# NanZi AI Agent Platform v1.0.23.0 Cover Info

## 封面主标题

NanZi AI Agent Platform v1.0.23.0

## 封面副标题

用户级凭据中心 · 业务分组贯穿五类资产 · 工具级运行时参数 · 子代理向用户提问 · 多实例运维视图 · 移动端可用性整治

## 🎯 120 字核心引流文案（带钩子）

### 版本一：痛点反转版（推荐·公众号导读/朋友圈）
> Token 和 API Key 还明文躺在工具配置里，谁打开配置页谁都能拷走？委派出去的专家缺个参数，只能自己瞎猜或者干脆失败？工具明明设了 10 分钟超时，跑到 3 分钟就被砍？NanZi v1.0.23.0 正式发布：密钥改成「引用一条凭据」、模型永远看不到；子代理缺条件会反过来问你，答完接着上次的进度做；超时按工具自己配的跑，配多少生效多少！

### 版本二：极客硬核版（技术社区/群发）
> NanZi v1.0.23.0 重磅发布：凭据中心上线三态取值——固定凭据 / 规则匹配 / 模型兜底，规则内核是纯函数、不查库不解密，声明过用凭据的参数直接从业模型可见 schema 里剔除，注入值全链路脱敏；工具级超时去掉 min 收口、上层从「天花板」降级为「默认值」、看门狗上限 3600 → 86400 秒语义自洽；子代理提问走中断—恢复而非阻塞等待，会话历史落共享缓存（TTL 与提问记录同口径），resume 才接历史；委派结果上限 8000 → 64Ki 对齐通用工具口径！

### 版本三：企业安全与协同版（企业集成与管理）
> 企业级密钥治理与多智能体协同的双里程碑：NanZi v1.0.23.0 让「谁能用哪条凭据、用在哪、改过什么」第一次有了完整答案——个人与平台凭据分层管理、越权一律 404 而非 403（403 等于承认这条凭据存在）、用量统计与变更记录可审计；业务分组统一贯穿智能体、MCP、技能、数据集与工具，五类资产用同一套页签、徽标与筛选；系统诊断新增实例页，多主 / 无主 / 主节点离线直接告警；通知按渠道分群，定时任务可指定目标群。

## 视觉关键词

- 凭据引用链与零明文注入（Credential Reference Chain & Zero-plaintext Injection）
- 三态取值开关：固定 / 规则 / 模型兜底（Tri-state Resolver: Fixed, Rule, Model Fallback）
- 五色业务分组轨道与统一页签（Five-asset Grouping Rails & Unified Tabs）
- 工具级超时标尺与逐级覆盖（Per-tool Timeout Dial & Cascading Override）
- 子代理反向提问与中断恢复回路（Sub-agent Query Loop & Interrupt-resume Circuit）
- 多实例拓扑与分组告警路由（Multi-instance Topology & Grouped Alert Routing）

## 推荐用途

- GitHub Release 头图
- 微信公众号文章封面 / 焦点首图
- 技术社区（掘金、知乎、V2EX、开源中国）发版文章配图
- 官方发布宣传横幅

## 生成图路径

`docs/release/1.0.23/release-cover-v1.0.23.0.png`

## 最终生图提示词

```text
Use case: ads-marketing
Asset type: 16:9 release-note cover image, final project asset
Primary request: Create an ultra-modern, cinematic enterprise tech cover image for "NanZi AI Agent Platform v1.0.23.0" release notes.
Canvas: 16:9 landscape, 2048x1152 resolution.
Scene/backdrop: A futuristic secure automation control room built from obsidian glass and frosted panels, floating above a deep slate navy void with slow rivers of cyan data. In the focal center: a translucent key-shaped credential vault rendered as a faceted glass monolith, with a single narrow beam of light passing through it into a downstream tool conduit — the key itself never visible outside the vault, symbolizing zero-plaintext credential injection. Around the vault: a small three-position selector ring with three distinct illuminated nodes (a solid lock, a branching rule fork, and a soft model silhouette), representing the tri-state value resolver. To the left: five parallel horizontal rails in five distinct accent hues, each carrying a row of small floating asset tokens that snap into grouped columns, representing business grouping across five asset types. To the right: a cluster of interconnected compute nodes arranged as a small constellation with one node highlighted as primary and thin status arcs between them, one node dimmed and marked with an amber warning ring, representing the multi-instance topology view. In the foreground: a circular timeout dial with a graduated scale and a bright arc sweeping past a previous limit marker, with a small question-mark speech bubble arcing from a sub-agent node back to a human node and a return path resuming, representing per-tool timeout override and the sub-agent query loop.
Subject: Enterprise credential governance and multi-agent coordination platform: glass credential vault, tri-state resolver ring, five-asset grouping rails, multi-instance constellation, timeout dial and sub-agent query loop.
Style/medium: State-of-the-art 3D digital tech render, ultra-crisp studio lighting, cinematic depth of field, balanced volumetric illumination, clean minimal enterprise UI accents, subtle glassmorphism.
Composition/framing: Balanced wide-angle landscape composition with an inspiring heroic center, generous breathing room on the top-left for typography overlay, strictly clean and uncluttered.
Lighting/mood: Inspiring, deterministic, enterprise-grade engineering rigor; sapphire blue (#2563eb), electric cyan (#06b6d4), modern slate navy (#0f172a / #1e293b), emerald mint (#10b981) for verified bindings and passing checks, restrained violet (#8b5cf6) for resolver branching, subtle warm amber accents for warnings and rejected paths.
Color palette: Deep Slate Navy obsidian background, electric cyan glow, vivid royal blue, emerald verification green, restrained violet and amber accents.
Text (verbatim): Include only this crisp modern badge if text is rendered: "v1.0.23.0". Do not generate any messy or garbled text.
Constraints: No messy watermarks, no cartoon characters, no distorted hands/faces, no external trademark logos, ultra-high resolution and crystal-clear visual hierarchy.
```
