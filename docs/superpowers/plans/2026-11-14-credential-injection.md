# 结构化凭据统一交付实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**历史整理目标（已完成）：** 将 e79c882a（含）以来的结构化凭据、MCP 结果配置、候选发现、任务复用和脚本 env 能力整理为一份规格、一份计划、每数据库一份迁移、一个交互原型及一个最终提交。本轮目标与进度见下方增补。

**Architecture:** 既有调用链为模型 schema 投影 → 当前身份候选或固定引用 → 原生等待/恢复 → 服务端独立出站注入 → MCP JSONPath 投影，脚本固定 env 经私有 Docker/K8s 执行器。此次在保留最终业务实现的基础上收敛交付材料和提交历史，历史实现证据与本次验证分别记录。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2.x、AgentScope 2.x、Vue 3/TypeScript、pytest、MySQL/PostgreSQL、静态自包含 HTML。

**Spec:** [结构化凭据与工具注入统一规范](../specs/2026-11-14-credential-injection.md)。

## 本轮增补：固定模式即时选择凭据

用户反馈切换为“固定凭据”后没有可选列表。代码依据：`McpCredentialProfileForm.vue` 将选择器显示及候选加载限定为已保存的 `current.selection_mode === 'fixed'`，选择器还位于表单底部；现有候选 API 只接受已经保存且有效的 profile，无法按未保存的类型、版本查询。基线为 `f917f49d`。

- [x] 后端：为现有候选 API 增加成对的草稿类型/版本参数，复用共享、场景及目标校验；保持普通调用候选接口的权限语义。
- [x] 保存：现有 profile 保存支持可选 `fixed_secret_id`，映射与当前用户固定绑定同事务提交，保存失败一起回滚；旧客户端省略字段时兼容原行为。
- [x] 前端：切到固定模式立即在模式选择下方展示候选；支持本人/系统共享标签、已有绑定回填、切换类型或版本重新查询、空列表、错误重试和迟到响应保护；“保存设置”统一保存，移除额外“保存绑定”。
- [x] 文档与原型：更新现有 spec、plan 和 HTML，不新增重复文档、生产组件或数据库迁移。
- [x] 验证：实际 Vue 表单行为与系统 PostgreSQL 隔离回归覆盖草稿候选、共享资格、保存原子性、版本冲突与兼容旧请求；保持固定模式不向模型暴露选择字段。不使用 SQLite，不操作真实凭据或业务表，不启停服务。

本轮验证（均实际执行）：

- 系统 PostgreSQL 隔离回归 **71 passed**：新增 `test_mcp_fixed_profile_selection.py` 19 项，及共享授权、创建开关、候选查询 52 项。验证首次配置、草稿、不可用旧绑定、实际注入、事务回滚（含绑定和审计）、修订冲突与旧客户端兼容。
- 新增真实 Vue 表单行为 **11 passed**：先复现切换固定模式找不到选择器，再验证修复；覆盖立即展示/首次配置、共享与旧绑定回填、切换类型版本、迟到响应、空列表/错误重试、一次保存、失败保留草稿和不可用绑定显式解绑。
- 原有相关回归 **55 passed**：`test_mcp_structured_profile.py`、`test_mcp_tool_tester_result_contract.py`、`test_mcp_group_tabs_contract.py`、`test_mcp_result_fields_save.py`。MCP JSONPath 原样返回及模型参数隔离保持不变。
- 全项目 `vue-tsc --noEmit -p frontend/tsconfig.app.json` 仍有 **155** 条既有错误，本次修改文件及其他候选接口调用组件无类型错误；不能宣称全项目检查通过。
- 未运行真实 MCP/浏览器联调或 MySQL 回归；未启动、停止或部署服务，未操作业务库或真实凭据。原型只演示候选元数据和保存反馈，不发送请求或保存真实凭据。

原型已重建索引（64 项，索引文件无差异），离线检查通过：固定选择框位于映射之前、不自动选首项、本人/系统共享候选、加载中/空/错误重试、统一保存反馈及切回推导模式；既有 3 种模型模式、7 种 JSONPath 结果预设、禁止新建及已有凭据恢复检查通过。未执行浏览器视觉或真实服务联调。

## 历史增补：系统共享凭据统一授权

用户确认取消额外的逐工具凭据授权要求，统一使用凭据中心的共享授权。基线为 `0a7606d4`。代码证据：`center.list_visible_secrets()` 仅检查 `SecretGrant`，而 `mcp_profiles.list_candidates()` 与 `tool_relations._dynamic_tools()` 又要求 `McpCredentialUseGrant`，导致中心可见但模型、测试台和实际调用不可用。二次授权 UI 仅管理员可见，不符合普通用户使用已共享凭据的预期。原“双重授权”规则由本次确认替代。

- [x] MCP：移除额外逐工具凭据门禁，统一候选、固定绑定、实际注入、原生/定时恢复及关联工具；保留共享范围/期限、工具访问、类型版本、状态、场景及域名检查。
- [x] 字段绑定：移除系统结构化字段只能由管理员全局绑定的限制，复用既有共享校验；个人字段绑定只对绑定者生效，执行时再次校验共享权限。
- [x] 旧授权入口：移除界面二次授权管理，说明共享来源并标识系统候选。保留历史表和查询/清理 API，新写入旧 grants API 明确返回 410，不把旧逐工具授权自动转成全局共享。
- [x] 文档与原型：同步现有 spec、plan、HTML、验收指南和测试清单，不增生产组件或迁移。
- [x] 验证：系统 PostgreSQL 隔离库先复现后修复，覆盖用户/部门/全体共享、名称关键词到完整账号、无授权/撤权/到期、错误类型/目标、fixed/infer/原生和定时引用、字段注入及动态关联。只运行相关安全隔离回归，不使用 SQLite，不触碰业务表、真实凭据或服务。

账号语义保持精确匹配：截图中的 `adb` 是名称片段，先用 `keyword="adb"` 发现候选，再以返回的完整账号 `mcp.mail.user-secret` 填写 `credential_account`。不将账号选择改成模糊匹配。

### 本轮共享授权验证记录

- 现有双授权 PostgreSQL 用例在修改前通过，确认旧设计确实会排除仅有基础共享的候选。
- 新增共享字段回归在 API/脚本/MCP 认证三类均因旧权限分支失败；修改后 **3 passed**，覆盖已共享可绑定及解析、其他用户不能复用个人绑定、错误目标/场景、部门变化、授权过期与撤销后拒绝，审计不含合成字段值。
- 新增 MCP 共享回归先观察到 **7 failed / 9 passed**，动态关联的新期望也失败。完成后将新共享用例、创建开关、配置、完整生命周期及关联工具六文件合跑，系统 PostgreSQL **69 passed**；覆盖个人 MCP 属主与他人隔离、同账号歧义、共享撤销、测试选择器和模型候选到服务端注入。
- 将既有 `test_mcp_credential_candidates.py` 从 SQLite/同步适配器迁至现有 PostgreSQL 隔离 fixture，**21 passed**。保留分页游标、上限、元数据隔离和故障分类；集合资格断言不依赖 SQLite 与 PostgreSQL 不同的地区排序。上述数据库回归合计 **90 passed**，不含重复执行计数。
- 相关纯回归 **154 passed**（模型边界、JSONPath、MCP profile、脚本字段与私有执行/输出检查）；前端现有相关回归 **233 passed**。4 个改动 SFC 脚本/模板编译、3 个真实下拉模板 SSR 标签检查通过。
- 原型 3 种模型模式、7 种结果预设、关闭新建/已有选择离线检查通过；索引已重建且无差异。修改 Python AST、`git diff --check` 通过，独立交叉审查未发现阻塞问题。
- 本轮未重跑全项目检查；下方历史记录中的基线类型错误和无关前端契约失败未修复。不宣称 MySQL、浏览器视觉、真实 MCP/容器和部署环境已验证。
- 实现及验证完成后，按用户后续指令创建本地提交，不推送；旧 grants 新写入停用是本轮授权规则统一带来的接口变化，接口给出明确迁移指引，历史数据不删除。

## 历史增补：结构化凭据开关仅控制新建

用户已确认：`structured_credentials_enabled=false` 只禁止新建结构化凭据实例；已有凭据继续允许使用、轮换、编辑资料及重新绑定，类型定义仍可管理。类型字段、版本、Schema 与预览保持可见。单值凭据、变量不受影响；实例权限、启停、过期、密钥校验、脚本输出检查及 JSONPath 原样返回规则不变。

沿用当前 `structured-credentials` 工作区和已有规格、计划、原型，不新建重复文档或组件，不操作真实凭据、业务表、服务或远端。本轮基线为 `4cd34834`，不包含提交或推送操作。关闭后的请求级创建校验仍由服务端执行；不引入跨请求全局停机锁。

用户补充要求不使用 SQLite：本轮数据库回归复用系统的 MySQL/PostgreSQL 与现有 `tests/temporary_database.py` 隔离机制。每用例创建唯一的 `nanzi_test_*` 库并核对当前库，结束时仅清理本次临时库；不引入新数据库组件或自制会话适配器，未配置或无连接权限时明确记录未验证。

- [x] 后端：先增加关闭后创建拒绝、已有解密/轮换/绑定/调用继续、原生选择可用但禁止补录创建的回归；分离创建开关与有效密钥环检查；补充可创建状态/原因，保留类型自身状态。
- [x] 前端：类型按有效创建状态显示和筛选；禁选项明确标注原因；个人/管理员创建与聊天/定时补录行为一致，保留已有候选选择。
- [x] 文档与原型：替换旧的全局停止语义，在现有原型联动演示关闭后的类型展示、新建禁用和已有操作可用，同步系统配置说明与验收指南。
- [x] 验证：隔离测试覆盖配置关闭/恢复、类型自身停用、旧页面提交、fixed/infer、API/脚本绑定、聊天/定时任务恢复；前端编译、原型离线检查及最终差异审查。

### 本轮实现与验证

- `PayloadConfig.cipher()` 仅校验密钥环，新建仍由 `create_payload()` 检查开关。轮换、字段绑定、MCP profile 和原生恢复不再使用创建开关拒绝已有凭据；类型接口和原生卡共用 `creation_availability()` 返回 `can_create` 与 `creation_disabled_reason`。
- 独立审查发现并补齐 `tool_relations._dynamic_tools()` 的旧全局门禁，关闭新建不再隐藏动态适用工具。保留密钥、工具权限、实例状态及归属检查；清除不可达的“总开关关闭导致调用失败”错误映射。未新增生产组件或依赖，未改表结构或迁移文件。
- 新增后端 13 项回归。开发初期的 SQLite 夹具已依用户指令移除，最终复用现有 MySQL/PostgreSQL fixture；系统实际为 PostgreSQL。PG 红灯先观察到 10 项旧语义失败，动态关联再观察到 2 项失败，修复后最终 **48 passed**（新建开关、生命周期、配置保存、关联工具四文件，27.00 秒）。随机临时库由现有 helper 验证当前库并清理，不写业务表、不读取真实凭据。
- 相关无数据库回归 **289 passed**，覆盖模型边界、JSONPath 原样投影、脚本输出/执行策略、MCP 参数与结果保存、错误提示、任务恢复锁序和上下文清理；未执行使用 SQLite 的旧用例。仅有既有 `crypt` 弃用 warning。
- 新增前端行为用例 **9 passed**（先 6 failed/3 passed）；其中两项编译个人/管理员的实际下拉模板，其余挂载实际 Vue SFC，覆盖禁用展示/筛选、版本/预览、已有选择和后端拒绝反馈。未新增组件。5 个改动 Vue/TS 文件及真实依赖的局部类型检查通过。
- 全纯前端 **4994 passed、1 skipped、7 failed**。4 项因独立运行缺少环境配置失败，使用隔离配置复核 **4 passed**；另 3 项未改动的字符串契约仍失败：`test_embed_chat_keeps_lock_truth_driving_recovery_while_composer_uses_busy`、`test_card_emits_scope_and_revoke_events`、`test_chat_pages_send_scope_and_revoke_grants`。全项目类型检查仍有 **155** 条错误（37 个文件，未涉及本轮修改文件），本轮没有扩大范围修改这些问题；不宣称全项目检查通过。
- 原型关闭/恢复、已有选择及类型管理的函数离线检查通过；原有 3 种模型模式与 7 种结果预设回归通过。现有索引重建为 64 项；文档相对链接有效。独立审查发现的运行关联遗漏与原型发布提示均已修正并复核。
- 未执行 MySQL、浏览器视觉、真实 MCP/容器或部署联调；无服务启停、Git 提交或远端推送。系统数据库仅用于既有隔离测试机制，不使用业务凭据。

数据库检查使用已有 Python 环境和仓库外执行器 `/tmp/nanzi-system-db-pytest.py`，仅在内存中读取现有连接配置填充 `TEST_DATABASE_URL`，关闭根 conftest 的业务库/Redis初始化；测试命令对应：

```bash
python -m pytest --noconftest -q \
  tests/secrets/test_structured_creation_gate.py \
  tests/secrets/test_structured_credential_settings.py \
  tests/secrets/test_structured_secret_lifecycle.py \
  tests/api/portal/test_secret_tool_relations.py
```

运行前按 `tests/README.md` 配置 MySQL/PostgreSQL 的 `TEST_DATABASE_URL`；测试账号需具备隔离库权限。不要直接运行会连接业务表的旧全量数据库用例。

## 后续交付：连续迁移编号与两次提交合并

依据用户明确授权，将 `ed6aae401402f8cbc7c78fd4ac04a44821c756ff` 与 `2c9902124d6693db231a4ec6ab8f5cd6f68e3e83` 合并为一个提交，父提交保持 `2920e9ad8a809e06430cc8fdf238d305ff8b5112`。下文“不提交”记录仅指此前实现阶段；此次允许本地压缩，不推送远端。

- [x] 核对两提交连续且工作区初始干净；基线迁移末尾为 MySQL V217、PostgreSQL V117。
- [x] 将本分支合并文件 V220/V120 改为 V218/V118，只改文件名和首行版本注释，SQL 执行语句保持不变；历史主分支迁移编号不动。
- [x] 同步缺表提示、规格、计划、发布指南及测试引用。本文现行脚本路径同步至新编号，之前阶段的测试数字保留原验证范围。
- [x] 保持导入器通用规则：旧开发断点高于新编号时须显式指定新脚本，补充选择清单测试，禁止测试执行 SQL。
- [x] 迁移契约及主分支硬超时迁移回归 **39 passed**；两份 SQL 除首行版本注释外逐字节不变，历史迁移及导入器未修改；Python AST 与 **13** 个相对文档链接检查通过。

压缩验收要求：将上述两提交与编号调整一并形成一个提交；提交树应与压缩前已验证的暂存树完全一致，唯一父提交为 `2920e9ad`，其后恰有一个提交，工作区干净。Git 最终结果以提交完成后的命令核对为准。本次未执行真实数据库、服务启停或远端推送。

## 本轮增补：密钥生成帮助与运行链复核

以下增补记录 rebase 后 `structured-credentials`（基线 `ed6aae40`）的实现阶段，沿用本文件与现有规格、原型。Task 1–5 及其测试、Git 压缩记录属于此前整理；本阶段验证后已按用户后续指令提交为 `2c990212`。未执行真实密钥生成、业务数据库操作或服务启停。

**实施范围：** 在系统配置密钥环输入旁增加可展开的帮助，提供可复制的本地 Python 命令、首次配置与轮换占位 JSON；同步既有规格、发布指南和原型。修复本轮审查确认的候选全量缓冲、过期待恢复记录阻塞新版任务，以及调度恢复缺少 `timedelta` 导入的问题。保留两个小写系统参数、主库明文存储、旧读密钥自动保留和 MCP JSONPath 原样返回规则。

- [x] 在现有规格与发布指南明确 32 个安全随机原始字节、44 字符带末尾 `=` 的 URL-safe Base64 格式，以及活跃编号、编号格式、轮换和留空行为。
- [x] 在现有原型系统配置区增加展开帮助与复制反馈，移除固定全零可用示例；仅复制命令或占位文本，不生成真实密钥。
- [x] 将原型 JSON 校验与当前后端格式要求对齐，明确原型只演示格式及状态，不写入配置或读取旧密钥。
- [x] 在实际 Vue 系统配置页接入同一帮助内容，保持只写输入及既有保存接口行为。
- [x] 将 `list_candidates` 改为按 `(account, id)` 稳定分批读取，仅显式提供 `limit` 时限量，并在权限过滤后计数；未提供 `limit` 时保持完整候选结果。
- [x] 在 `recover_wait` 恢复旧等待前重验任务存在、执行所有者及任务定义摘要；旧任务修改、所有者变化或删除后，旧等待不能阻塞新版任务。维持 `task → wait` 锁序及 claimed/expired 的既有判断优先级。
- [x] 补齐调度恢复路径的 `timedelta` 导入，添加能实际触达对应分支的回归验证。
- [x] 删除任务入口也先锁任务再删除等待；读取已消费 claim 时使用锁定当前读，避免可重复读旧快照误开等待。移除恢复错误分支的重复摘要判断。
- [x] 离线校验原型的格式边界、留空与启用状态、复制成功/失败分支；重新生成原型索引并记录实际验证结果。
- [x] 复核本轮代码、文案与运行链发现，完成相应前端检查，将未实测范围与历史记录分开说明。

### 本轮实现与回归记录

- 配置页通过 `frontend/src/components/system/CredentialKeyringHelp.vue` 展示帮助，复用现有支持 HTTP 环境的剪贴板工具。复制成功、返回 false 和抛出异常均有反馈；不自动填写输入，不执行生成命令。
- `mcp_profiles.list_candidates` 首批按所需数量读取，后续扫描批次逐步扩大至最多 100 条。新增 6 项进程内 SQLite 测试覆盖提前停止、跨批权限过滤、同账号续读、长段不适用候选、无上限和存在性查询。
- `task_credential_wait`、`scheduler_service`、`task_center_service` 新增 12 项 mock 回归，覆盖任务变化/删除/归属变化、claim 当前读、已消费和过期优先级、租约回收及删除锁序、取消与选择错误后的上下文和执行锁释放。相关缺陷均先复现测试失败，再完成修复。
- 最终后端联合回归 **439 passed**：候选与 capability、profile、模型/运行边界、JSONPath/health/结果存储、测试台参数与结果保存、MCP 进度、脚本映射/执行/provider、任务等待、出站、凭据清单、管理员类型/审计、用户属性、聊天修复、三级超时、任务通知组、工具/Agent 错误脱敏和任务 cron 防线。仅有既有 `crypt` 弃用 warning。
- 前端相关回归 **300 passed**：MCP 测试台与结果保存、凭据中心、脚本映射、类型草稿、聊天表面、推理展示、任务通知，以及系统配置保存栏/布局/移动页签/MCP 页签/引导/硬超时参数。
- 后端检查使用 `/tmp/nanzi-rebase-pytest-safe.py` 包装（Python 3.11），禁用 dotenv 和根 conftest 基础设施初始化，连接配置指向不可用回环端口；用例使用 mock/进程内 SQLite。另在仓库外临时检查中验证配置保存的 **6** 个分支：无密钥不能启用、省略字段保留、显式空/空白不清除、轮换保留旧密钥并拒绝同编号改值、无字段不保存。未访问业务数据库或真实凭据。
- 两个变动 Vue SFC 编译通过；帮助组件及剪贴板依赖的局部 `vue-tsc --noEmit` 通过；复制分支和占位 JSON 离线验证通过。变动的 6 个 Python 文件 AST 检查及 `git diff --check` 通过。
- 保持两个配置名称、JSON 格式、明文主库存储、服务端注入、fixed/infer 行为、JSONPath 原样投影和脚本输出检查不变；脚本前置检查与出站前新鲜校验承担不同职责，未为了减少查询而合并。未修改接口或迁移，也无本轮必须实施的兼容性调整建议。
- 未运行浏览器实际点击/窄屏视觉、真实 MySQL/PostgreSQL 锁并发、真实 MCP/容器及部署联调；mock/SQLite 不证明双库并发验收通过。未运行全量前端构建或宣称全项目类型检查通过。未生成真实密钥、启停服务、提交或推送。

### 本轮文档与原型验证记录

- Node `vm.Script` 编译完整内联脚本通过；执行原型实际格式函数的 **30** 个案例，并用同一批确定性合成数据调用后端实际 `parse_payload_keyring`，结果全部一致。覆盖合法多密钥、编号长度边界、URL-safe 字符、缺失/多余填充、非法编号/顶层字段/类型、未包含活跃编号和占位文本。
- 原型实际状态函数的 **18** 个留空/空白/有值与启用/关闭组合通过；已有有效密钥留空保留，无有效密钥且留空启用被拒绝。
- 原型实际复制函数的 **5** 个案例通过：三个复制目标、剪贴板拒绝、剪贴板 API 缺失。复制只影响剪贴板/反馈，不修改密钥输入；失败时聚焦并选择相应文本。首次/轮换的 **2** 个占位 JSON 均不能通过密钥校验。未执行帮助中的生成命令。
- HTML 解析确认 **74** 个静态 ID 无重复、**6** 个导航目标有效；外部资源、网络请求、浏览器持久存储及动态执行/随机生成调用均为 0。规格、计划与发布指南的 **13** 个相对链接目标存在。
- 已执行 `python3 prototypes/build_index.py`：扫描 **64** 个原型，生成索引与当前树一致，无索引差异。`git diff --check` 通过。
- 校验使用仓库外的临时 Node/Python 脚本，仅执行原型函数和纯密钥格式模块，没有加载应用、读取部署密钥、访问业务数据库或启动服务。以上是离线分支检查；未将浏览器实际点击、窄屏视觉或真实配置保存计为通过。实际 Vue 与运行链验证在完成后另记。

## Global Constraints

- Python 3.11、FastAPI、SQLAlchemy 2.x、AgentScope 2.x；前端使用现有 Vue 3/TypeScript 工程。
- 沿用当前凭据存储、归属/共享授权、类型版本、场景、域名、有效期及平台/个人策略。账号提示和绑定引用不授予权限。
- 已接入 MCP 视作用户已知且受信任的工具；本轮不新增远端信任审批，不采用 MCP Elicitation。
- 只生成代码、文档、原型和迁移文件，不直接修改真实数据库；服务启停与部署由用户在控制台执行。
- 原型仅存放在 `prototypes/`，CSS/JS 内联、无外部资源，一项需求一个 HTML，并通过 `python prototypes/build_index.py` 登记。
- 上一轮整理已完成获授权的本地提交压缩；后续本地提交与此次两提交合并均按用户明确授权执行，不推送或创建 PR。旧实现通过归档标签保留。
- 上一轮整理保留了最终业务实现；本轮增加已授权的密钥生成帮助、修正文案及上述运行链问题，不扩大凭据权限；不得重新启用已撤销的本轮原文账号限制或 MCP 结果敏感/echo 过滤。

## Review Focus

- 已执行过旧迁移的数据库：合并迁移不能破坏存量值，需同时审查首次安装和历史部分/完整迁移路径；由任务 2 覆盖。
- token/password 名称、与注入值相同的返回值、空数组与无匹配：MCP JSONPath 按选中节点原样返回；脚本仍有独立输出检查，由任务 4 覆盖。
- fixed 与 infer：固定直接调用，候选工具给出无需查询；infer 仅查当前用户/共享且工具类型匹配的元数据，由任务 3/4 覆盖。
- 旧审计与测试数字：缺口只描述旧终点，不把历史测试通过数当本轮执行结果；由任务 1/4 覆盖。
- 历史压缩与归档：包含 e79c882a 本身，保留其父提交及范围前文档；标签、最终差异和提交数量分别核对，由任务 1/5 覆盖。

## 文件与责任边界

| 文件/目录 | 责任 |
| --- | --- |
| `docs/superpowers/specs/2026-11-14-credential-injection.md` | 唯一现行规格、失效规则与历史来源索引 |
| 本计划 | 整理步骤、已完成实现地图、历史证据与本次实际验证 |
| `db-prod/V218-add-structured-credentials.sql` | MySQL 合并迁移 |
| `db-prod-pg/V118-add-structured-credentials.sql` | PostgreSQL 合并迁移 |
| `app/main.py` | 缺表提示同步为 V218/V118，仅调整文案 |
| `prototypes/2026-11-14-credential-injection.html` | 类型/实例、MCP 模式/映射/结果、脚本和恢复流程演示 |
| `prototypes/index.html` | 由 build_index.py 生成的原型登记 |
| `docs/credential-center-verification.md` | 当前迁移、配置及实际环境验收说明 |
| `tests/CHECKLIST.md` | 最终交付路径和本次测试结果 |

## Task 1: 统一规格、计划与历史来源

**Files:** 创建上表 spec/plan；移除本范围新增的两份旧 spec、两份旧 plan、提交审计及 `specs/archive/2026-11-13-credential-injection-v1/` 的 5 个文件；范围前的 3 份原路径文档保持不动。

**Interfaces:** 输入为归档标签 `archive/credential-injection-20261114` 和统一规格；输出为两个当前文档、原件来源/哈希表及可从标签重建的旧路径清单。

- [x] 核对原范围为 `e79c882a7224ecfe1d0f7692e42982a331943e0f^..8a805eb7af2e01a2864697bf6bfb13f0188453e7`，共 20 提交。
- [x] 建立归档标签并记录原完整 HEAD，历史文件和被压缩提交保持可达。
- [x] 将凭据类型、配置、fixed/infer/ask、JSONPath、原生恢复、任务引用与脚本 env 合入统一规格；将旧需求明确列为失效，保留来源哈希。
- [x] 将先前两份计划的实现边界与历史验证摘要合入本计划，明确其不是本轮重跑结果。
- [x] 删除被整合副本并检查只有本需求的一份新增 spec 和一份新增 plan；旧路径仅在历史恢复清单和检查命令中保留，外部现行入口由发布指南同步。

检查：`git diff --name-status` 应只移除本范围新增文档；`rg -n '2026-11-13-credential-injection|2026-11-14-script-credential-env' docs tests prototypes` 的现行入口不得指向已删除文件。按 Markdown 相对路径核对本次修改文件的链接。

## Task 2: 每数据库一份迁移

**Files:** 新建 `db-prod/V218-add-structured-credentials.sql`、`db-prod-pg/V118-add-structured-credentials.sql`；移除本范围 V216–V219/V116–V119；更新发布指南及清单引用。

**Interfaces:** 读取旧四个迁移各自的表、列、种子与配置语义；输出对应数据库可审查的统一升级 SQL。不得执行真实数据库迁移。

- [x] 对比两套旧迁移，列明类型/版本、结构化 payload、profile、工具授权、任务等待、旧表扩展、系统设置和两级结果策略，避免漏列/种子。
- [x] 按依赖顺序合入 V218/V118，保留已存在结构/配置的兼容处理，不覆盖管理员已经配置的有效值。
- [x] 删除 8 个本范围旧迁移文件，同步发布指南为每数据库一个入口，说明旧版本已执行时的处理。
- [x] 核对首次安装、旧结构部分已存在、旧四个迁移均已应用三个路径；记录静态检查和实际双库验证的边界。

检查：原起点父提交到最终树新增的 SQL 每数据库恰好一份；完整比较表/列/种子语义。只有运行专用可销毁双库测试才可声称实际执行通过。

## Task 3: 补齐并登记交互原型

**Files:** `prototypes/2026-11-14-credential-injection.html`、自动生成的 `prototypes/index.html`；在规格、计划和清单中引用。

**Interfaces:** 消费统一规格，使用合成数据展示用户入口、配置与结果。原型不连接真实后端、凭据或外部服务。

- [x] 一个 HTML 内提供类型/实例、MCP 参数模式、JSONPath 结果、脚本 env 和原生等待/恢复的主要流程及事件处理。
- [x] fixed 显示服务端固定注入和直接调用，infer 显示本人/共享类型候选；不展示真实输入值给模型。
- [x] JSONPath 示例覆盖原样保留 token/password 节点及空/非法/无匹配 restricted；说明示例范围，避免把简化演示冒充完整 RFC 求值器。
- [x] 脚本映射同时展示单值和结构化字段，说明其独立回显检查及固定绑定，不引入脚本动态账号选择。
- [x] 确认 title 使用“功能 · 做了什么”，样式和脚本内联且无外部依赖，运行 `python prototypes/build_index.py`。
- [x] 静态检查事件连接、响应式断点及原型总览登记，并执行脚本语法/分支输出校验；浏览器视觉与真实点击受工具策略限制，需人工验收，未计为已通过。

## Task 4: 验证最终行为与文档一致性

**Files:** 当前凭据/MCP/脚本测试、`docs/credential-center-verification.md`、`tests/CHECKLIST.md`、本计划。

**Interfaces:** 消费前三任务最终树；输出明确命令、结果、未运行项及残留基线问题。历史结果只放历史附录。

- [x] 核对输入凭据不可见、fixed 直接调用、infer 授权类型候选、原生恢复和任务引用边界，与原型/文档一致。
- [x] 运行聚焦的 JSONPath/结果保存/候选/脚本行为回归；后端 284 passed、前端 223 passed，实际命令见本次记录，未操作运行库。
- [x] 检查迁移和文档链接，运行 `git diff --check`，检查原型登记及脚本语法；业务逻辑未改，仅同步缺表提示的迁移编号。
- [x] 更新发布指南中过时的脚本全禁用、0600 env 文件和明文输出验收步骤；保留旧历史内容时明确上下文。
- [x] 在清单登记最终 spec、plan、两份迁移和原型；将本次实际命令/结果追加到下方执行记录。

可复用的聚焦命令（实际执行范围及结果以本次记录为准）：

```bash
python -m pytest --noconftest -q --tb=short \
  tests/secrets/test_mcp_jsonpath_semantics.py \
  tests/secrets/test_mcp_health_status_result.py \
  tests/secrets/test_mcp_structured_profile.py \
  tests/secrets/test_mcp_credential_candidates.py \
  tests/secrets/test_credential_model_boundary.py \
  tests/secrets/test_credential_outbound_flow.py \
  tests/secrets/test_script_env_binding.py \
  tests/ai/tools/test_credential_script_execution.py \
  tests/ai/tools/test_credential_script_providers.py
python -m pytest --confcutdir=tests/frontend -q \
  tests/frontend/test_mcp_result_fields_save.py \
  tests/frontend/test_script_env_binding_contract.py
git diff --check
```

## Task 5: 本地历史压缩与交付核对

**Files:** Git 提交历史；不新增业务文件。

**Interfaces:** 消费已审查最终树、归档标签及起点父提交。输出开发分支在起点父提交之上的一个整合提交；不推送共享分支。

- [x] 确认工作区的每项变更都属于授权范围，保留旧 HEAD 标签并核对范围前历史。
- [x] 将起点含自身以来的 20 个提交和本次整理压缩为一个本地提交，提交说明描述最终能力和交付物。
- [x] 核对新提交父节点等于原 e79c882a 的父提交、相对该父节点仅有一个提交、工作区干净。
- [x] 比较归档标签与新树：业务行为保持一致，本次差异限迁移、文档、原型、对应测试登记及 app/main.py 的迁移编号提示文案；没有其他业务源码差异。
- [x] 交付记录包含归档标签、验证和未执行环境测试；最终提交号由 Git 及交付回复提供，不写入提交自身。不推送、部署或运行迁移。

## 已完成实现地图（历史范围）

以下用于理解最终代码位置，属于既有实现，不要求本次整理重新实现。

| 能力 | 主要实现 | 相关测试 |
| --- | --- | --- |
| 类型、版本、payload、密钥配置 | `app/models/credential_type.py`；`app/services/secrets/credential_schema.py`、`structured_payload.py`、`payload_crypto.py`、`payload_config.py`、`type_catalog.py`；类型 API 与 CredentialTypeEditor/Preview | `test_structured_payload.py`、`test_structured_secret_lifecycle.py`、`test_structured_credential_settings.py`、前端 schema draft |
| 模型投影和服务端映射 | `app/services/secrets/mcp_profiles.py`；`app/services/ai/tools/mcp_factory.py`、`mcp_client.py`；McpCredentialProfileForm | `test_mcp_structured_profile.py`、`test_credential_model_boundary.py`、`test_credential_outbound_flow.py` |
| 元数据候选与统计 | `app/services/ai/tools/mcp_credential_candidates.py`、`credential_catalog_tools.py`；`app/services/secrets/tool_relations.py` | `test_mcp_credential_candidates.py`、`test_credential_inventory.py`、`test_credential_catalog.py`、`test_secret_tool_relations.py` |
| 原生表单、恢复、任务复用 | `credential_approval.py`、`task_credential_wait.py`；AgentScope tools/resume/event_stream；scheduler/task_center_service；CredentialApprovalPicker、TaskCredentialWaitModal | `test_task_credential_wait.py`、`test_credential_operational_boundary.py`、恢复快照生命周期 |
| JSONPath 与记录 | `mcp_jsonpath.py`、`mcp_model_result.py`、`mcp_result_storage.py`；McpModelResultFieldsForm、McpToolTester；MCP 参数设置 API | `test_mcp_jsonpath_semantics.py`、`test_mcp_health_status_result.py`、`test_mcp_result_policy_save.py`、`test_mcp_projected_result_storage.py`、前端结果保存 |
| 脚本字段、隔离执行、输出 | `field_binding.py`、`injection.py`、`center.py`；`credential_script_execution.py`、`credential_script_providers.py`、`credential_script_worker.py`、`script_tool_runtime.py`；ScriptToolWizard | `test_script_env_binding.py`、`test_script_credential_picker.py`、`test_credential_script_execution.py`、`test_credential_script_providers.py`、前端 script env |
| 会话挂载与兼容 | `session_mcp_tools.py`、运行时工具清单/别名、McpCascadeMenu、EmbedChat | 会话 MCP、清单一致性、挂载反馈、旧 cN 选择与脚本运行器回归 |

## 历史审计与开发记录

原 2026-11-13 审计只覆盖 e79c882a 至 2126435c 的 14 提交。审计指出上下文账号限制、缺结构化候选发现、类型说明不足、业务 schema 过度收缩、失败混同缺失、文档过期、固定失效恢复不足、任务变更取消等待不足、旧卡补录未验任务、后续调度未复用引用十类差距。846a0886 的历史计划记录这些差距已闭环；后续六提交继续修正脚本、结果保存与模式说明。它们是演进记录，不是当前待修清单。

| 原提交（顺序） | 主要交付 |
| --- | --- |
| e79c882a | 结构化类型/实例、MCP 组映射、原生恢复、输入隔离与双库结构 |
| 8fdfc2ce、69fe2a9e、cb750162 | 类型展示、录入预览、按启用状态创建、自定义类型可视化 |
| d67c8c94、9178e9cf | 测试依赖及专用可销毁 MySQL/PostgreSQL 测试库 |
| d42f58bb、3c92c395 | 字段文案、系统配置开关和密钥环 |
| 99e20dec、2abb24f2、7fd131d7 | 业务结果字段、JSONPath、工具级结果策略和诊断 |
| 38a26a6e、566be7af、2126435c | 会话挂载/关联统计、标准 JSONPath/解包、结构化统计和旧编号 |
| 846a0886 | 上下文候选、注入、失败分类、原生恢复及任务复用 |
| a0e770c9 | 恢复单值/结构化脚本 env、私有执行和输出检查 |
| d9d51bd2、86158361 | 结果与参数来源一起保存、健康状态处理修正 |
| 3f72e890 | 按 JSONPath 原样返回并保存选中结果，取消二次敏感/echo 过滤 |
| 8a805eb7 | 区分固定注入与 infer 候选查询，模型模式说明与动态策略投影 |

旧完整审计/计划及原件均保存在 `archive/credential-injection-20261114`；源路径、完整来源提交、SHA-256 和恢复方式见统一规格附录 B。

### 历史验证摘要（不是本次执行结果）

| 阶段 | 当时记录的结果 | 当时限制 |
| --- | --- | --- |
| 起始结构化交付 | 后端 614 passed；前端 588 passed、1 skipped；隔离 Vite 打包成功 | 双库真实执行、浏览器登录联调及部署未验证；应用类型检查 157 项既有诊断 |
| v2 候选/任务闭环 | 后端联合 195 passed；前端契约 39 passed；337 个 SFC 无模板问题；24 个 Python 文件编译及 diff 检查通过 | 使用合成值、模拟 MCP 和同步进程内 SQLite；未配置专用 TEST_DATABASE_URL，双库及实际事务并发未验证；类型检查前后各 157 项，无新增 |
| 脚本 env 恢复 | 后端定向 192 passed；前端直接相关 231 passed；前端全量 4893 passed、1 skipped、4 failed；337 个 SFC 无模板问题；20 个 Python 文件编译及 diff 检查通过 | 4 项前端失败在 846a0886 快照复现；类型检查前后各 157 项；真实 Docker/K8s、双库未执行；旧 DB 自动清理 fixture 套件只收集未运行 |

脚本阶段四项既有前端失败：chat_surface_refactor 的 permission/external panels；embed_run_recovery 的 quickSendBlocked；session_grant_card 的 emits_scope_and_revoke_events 与 chat_pages_send_scope_and_revoke_grants。v2 扩展 scheduler 测试另遇隔离环境数据库不可用和缺 seed_data fixture，未计入通过数；既有 session_status 上下文估算 12 与 18 的差异也未修改。旧临时目录日志不作为长期交付依赖，完整当时说明可从归档标签读取。

## 本次整理执行记录（2026-11-14）

- 压缩前 HEAD：`8a805eb7af2e01a2864697bf6bfb13f0188453e7`；归档标签：`archive/credential-injection-20261114`。
- 本次工作为用户明确要求的交付收敛与本地提交压缩，不重新部署或操作真实数据库。
- 已完成本次范围、现行需求、历史原件来源和外部旧文档引用检查；统一规格/计划已写入。后续各项按上方复选框记录，最终测试命令与结果由本次执行者追加，不沿用历史通过数。

### 本次已执行回归

后端 **284 passed**（11.78 秒），1 条既有 crypt 弃用 warning；前端相关契约 **223 passed**（2.95 秒）。以下结果由本次执行提供，不引用历史通过数。业务逻辑未修改，`app/main.py` 仅同步缺表提示中的迁移编号；本次不重复应用全量类型检查，此前 157 项基线诊断仍按历史限制呈现。

```bash
python -m pytest --noconftest -q \
  tests/secrets/test_mcp_credential_candidates.py \
  tests/secrets/test_mcp_credential_capability.py \
  tests/secrets/test_mcp_structured_profile.py \
  tests/secrets/test_credential_operational_boundary.py \
  tests/secrets/test_credential_model_boundary.py \
  tests/secrets/test_mcp_jsonpath_semantics.py \
  tests/secrets/test_mcp_health_status_result.py \
  tests/core/test_mcp_projected_result_storage.py \
  tests/api/test_mcp_tool_execute_credential_args.py \
  tests/api/test_mcp_result_policy_save.py \
  tests/ai/tools/test_mcp_factory_progress_stream.py \
  tests/secrets/test_script_env_binding.py \
  tests/ai/tools/test_credential_script_execution.py \
  tests/ai/tools/test_credential_script_providers.py \
  tests/secrets/test_task_credential_wait.py --tb=short
python -m pytest --confcutdir=tests/frontend -q \
  tests/frontend/test_mcp_tool_tester_result_contract.py \
  tests/frontend/test_mcp_result_fields_save.py \
  tests/frontend/test_secret_center_entry_contract.py \
  tests/frontend/test_script_env_binding_contract.py \
  tests/frontend/test_credential_schema_draft.py
```

本次文档整合前另校验原归档四份原件的 SHA-256，与统一规格附录 B 的历史索引全部一致。所有旧副本均可从归档标签重建，未改范围之前的三份原路径文档。原型浏览器检查及最终提交核对在相应任务完成后另记；未执行真实双库迁移、容器部署或服务启停。

### 合并迁移与文档复核

- `tests/test_structured_credentials_migration_contract.py`：本次 **16 passed**（2.08 秒），只解析文件及验证导入器选择，不连接数据库。覆盖六表、五列、九类种子及 schema hash、系统参数保留、幂等列保护、旧迁移已删除时 `--last` 按版本选中合并文件。
- 使用项目真实 SQL 解析器归一比较：MySQL **30** 个语句、PostgreSQL **33** 个语句，与归档中各原四份迁移的有效语句同序相同。两份新增文件分别为 V218/V118，原 V216–V219/V116–V119 已移除；真实首次/部分升级/重复执行仍待专用可销毁双库验证。
- 最终统一规格、计划及发布指南的 **13** 个相对链接已检查，目标均存在，包含新原型。发布指南的手动执行命令和 `--last` 行为已对照两套 apply-sql.sh 源码核对；缺表提示也已同步为 V218/V118。

合并迁移及既有导入器的相关回归合计 **91 passed、11 deselected**（包含上面的 16 项）。排除项依赖当前缺失的原生 `mysql`/`psql` 客户端；测试连接使用 mock，包装脚本使用临时替代执行器或在连接前退出，没有执行真实 SQL：

```bash
python -m pytest --noconftest -q --tb=short \
  tests/test_structured_credentials_migration_contract.py \
  tests/test_producer_hard_timeout_migration_contract.py \
  tests/test_db_prod_apply_sql.py \
  tests/test_db_prod_pg_apply_sql.py \
  tests/test_pg_prod_apply_sql.py \
  -k 'not apply_sql_native_sh'
```

### 原型离线校验

- 已运行 `python prototypes/build_index.py`，索引为 **59** 个原型，新页面已登记。
- HTML 解析检查：**70** 个静态 ID 无重复，6 个导航目标均存在；外部资源、网络请求、浏览器持久存储 API 均为 0。
- Node 语法检查通过；对原型实际 `renderModel` 与 `renderResult` 函数作内存分支校验，3 种模型模式及 7 个结果预设全部符合预期；这不是浏览器实际点击测试。
- 独立静态复核确认：仅缺失情境提供补录，固定免账号选择，JSONPath 选中内容原样返回，脚本/任务保持各自约定。
- 浏览器工具因协议策略拒绝 `file://` 预览，本轮没有绕过；视觉、窄屏排版和浏览器真实点击留待人工打开 HTML 验收。没有启动服务。

### Git 压缩核对

- 整合提交的父节点为 `fd8b2b373273be7dcd7a5f4534457f151e6593d2`，等于原 e79c882a 的父节点；相对它的提交数量为 **1**。
- 压缩前标签仍指向 `8a805eb7af2e01a2864697bf6bfb13f0188453e7`。暂存树与第一次压缩后的树一致，验证提交过程中没有丢失文件；本记录回填到同一个整合提交。
- 对照原范围父节点，新增交付材料恰为 MySQL SQL 1 份、PostgreSQL SQL 1 份、spec 1 份、plan 1 份、原型 1 份及其索引更新。
- 对照归档标签，app/frontend/依赖/环境模板中仅 `app/main.py` 的缺表迁移编号文案变化；凭据与工具运行逻辑未变化。
- 本轮查询的远端 main/dev 分支均为 `2920e9ad`，与本地共享上述父节点但另有 18 个独立提交。本次未合并远端改动、未推送或强推，后续集成须保留这些独立开发成果。
