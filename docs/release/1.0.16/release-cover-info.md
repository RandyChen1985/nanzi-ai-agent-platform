# NanZi AI Agent Platform v1.0.16.0 Cover Info

## 封面主标题

NanZi AI Agent Platform v1.0.16.0

## 封面副标题

门户会话令牌化 · 浏览器零长期密钥 · 会话可吊销与审计脱敏 · 数据集质量治理分 · K8s 沙箱零配置共享工作区

## 🎯 120 字核心引流文案（带钩子）

### 版本一：痛点反转版（推荐·公众号导读/朋友圈）
> 登录后真实 API Key 还躺在浏览器里？禁用员工后旧会话居然还能用一整天？数据集被删了却看不出质量在恶化？NanZi v1.0.16.0 让浏览器彻底告别长期密钥——登录只发可即时吊销的不透明会话令牌，配套审计脱敏与嵌入实例级隔离；同时上线数据集质量治理分（0-100 可解释）与 K8s 沙箱零配置共享用户工作区！

### 版本二：极客硬核版（技术社区/群发）
> NanZi v1.0.16.0 发布：完成建库以来最彻底的身份凭据重构——门户会话令牌化（`auth:api_key` 键空间零分支复用 + 按用户索引成批吊销 + 60s 回源复核）、API Key `nzi_` 前缀、登录响应体不再回传密钥、审计日志键名与值级双重脱敏、嵌入票据「只读校验→原子核销」防误耗；数据侧交付数据集质量治理分体系与元数据漂移 AI 语义治理（动作↔漂移类型白名单、SAVEPOINT 批量隔离、采样 SQL 安全引用）；运维侧实现 K8s 沙箱零配置共享平台数据卷、双库迁移工具链断点续跑与 PG 原生 psql 模式。

### 版本三：数据与企业协同版（企业集成与管理）
> 企业级智能体平台底座再加固：NanZi v1.0.16.0 支持门户会话 24h 滑动续期与一键成批吊销、登录防暴破 5 次/15 分钟锁定、30 天 HSTS 与 CSP 安全响应头、代客签发独立 API 权限码与权限卡片接口路径可视化；同步交付数据集质量分排序与降级提示、全库巡检逐数据集异常隔离、上下文 token 口径对齐 AgentScope，以及一套带交互确认与环境预检的双数据库迁移工具链。

## 视觉关键词

- 门户会话令牌化与浏览器零长期密钥（Opaque Portal Session & Zero Long-Lived Key in Browser）
- 会话可吊销与用户级索引成批注销（Revocable Session & Per-User Revocation Index）
- 审计日志凭据全链路脱敏（Audit Trail Credential Masking）
- 嵌入凭据实例级隔离与票据原子核销（Instance-Scoped Embed Credential & Atomic Ticket Redemption）
- 数据集质量治理分可解释评分（Dataset Quality Score & Explainable Breakdown）
- 元数据漂移 AI 语义治理与人机协同收录（AI Semantic Drift Governance & HITL Column Adoption）
- K8s 沙箱零配置自动共享平台数据卷（Zero-Config Shared Platform PVC for K8s Sandbox）
- 双数据库迁移工具链断点续跑与原生 psql 模式（Migration Toolkit with Resume & Native psql）
- 上下文 token 口径统一与预算契约收敛（Unified Token Accounting & Budget Contract）
- 企业级认证韧性：防暴破锁定、HSTS/CSP 安全响应头（Auth Resilience & Security Headers）

## 推荐用途

- GitHub Release 头图
- 微信公众号文章封面/首图
- 技术社区与官网版本发布说明配图

## 生成图路径

`docs/release/1.0.16/release-cover-v1.0.16.0.png`

## 最终生图提示词

```text
Use case: ads-marketing
Asset type: 16:9 release-note cover image, final project asset
Primary request: Create a state-of-the-art cinematic landscape cover image for "NanZi AI Agent Platform v1.0.16.0" release notes.
Canvas: 16:9 landscape, 2048x1152 composition.
Scene/backdrop: A high-tech futuristic enterprise AI security hardening vault fused with a data-governance control room. In the center, a glowing obsidian keyless authentication core: a translucent glass credential token floating inside a hexagonal containment shield, from which a severed and dissolving long-lived metal key drifts away into particles (symbolizing the browser no longer holding long-lived API keys). Radiating outward are multi-layered luminous digital ribbons: one stream showing a user-indexed revocation graph with nodes extinguishing in synchronized waves; one stream showing a redacted audit log ticker with masked credential glyphs; one stream showing a holographic dataset health dial scoring 0-100 with a working-progress ring and graded dimension bars; and a floating Kubernetes helm tied to a shared luminous storage volume prism (symbolizing zero-config shared platform PVC for the K8s sandbox).
Subject: Futuristic cloud-native agent platform visual: luminous sapphire blue and emerald green energy core, floating Kubernetes helm and Pod node geometry, revocable session token capsules, cryptographic shield protection, and sleek glassmorphism data displays.
Style/medium: Premium digital enterprise tech product launch art, clean 3D render mixed with modern editorial minimalism, hyper-detailed glassmorphism, crisp lighting, cinematic depth of field. Suitable for high-end GitHub release banner and WeChat feature cover.
Composition/framing: Balanced wide-angle composition with a clear heroic centerpiece, generous negative space on the upper left for release title typography overlay, clean layout without chaotic clutter.
Lighting/mood: Inspiring, rock-solid security, instantaneous revocation, enterprise-grade data governance rigor; luminous cyan glow, cobalt blue, emerald mint accents, and protective security gold highlights against an obsidian backdrop.
Color palette: Deep space obsidian background, vibrant royal blue (#2563eb), electric cyan (#06b6d4), Kubernetes indigo (#4f46e5), fresh mint emerald (#10b981), and protective security gold (#f59e0b).
Text (verbatim): Include only this large readable title if possible: "v1.0.16.0". Do not include any other text.
Constraints: No external company logos, no watermark, no tiny unreadable text, no low-quality artifacts, no humans, no cartoon mascots. Keep the visual crisp, balanced, and instantly readable as a premium thumbnail.
```
