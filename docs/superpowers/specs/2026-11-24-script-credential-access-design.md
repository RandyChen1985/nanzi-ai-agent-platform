# 脚本读取凭据（Script Credential Access）设计稿

- 状态：P1 已实现（含两轮 review 返工），待实机验收（沙箱路径与 Docker 网络需在有 Docker 的环境复核）（`tests/core` 667 passed、`tests/frontend` 5536 passed、`vue-tsc -b` exit 0、343 SFC / 0 problem）
- 日期：2026-11-24
- 前置：凭据中心 P1（`app/services/secrets/`）已上线
- 相关：[`2026-11-09-user-credential-center-design.md`](./2026-11-09-user-credential-center-design.md)

## 1. 要解决的问题

skill 里的 python 脚本要调第三方 API，key 想放凭据中心——现在**没有任何通道**：

| 现有机制 | 为什么用不上 |
|---|---|
| `script_env` 注入 | 只服务平台注册的脚本工具（`sys_script_tools`），skill 里的 `.py` 由模型用 Bash 工具跑，不在其中 |
| `${secret:key.field}` 引用 | 只在 `api_tool` / `mcp` 的出站请求头里渲染，不进 bash 命令 |
| `sys_secret_bindings` | 槽位要 `consumer_type=script_tool` + `consumer_id=<工具 id>`，skill 没有这个身份 |

结果：脚本里 `os.environ["API_KEY"]` 必然 `KeyError`。

**目标**：任意 bash 执行的脚本（local 与沙箱），都能按需取到自己有权使用的凭据值。

## 2. 与既有不变式的关系（必读）

凭据中心的第一条不变式是「显式绑定、默认零授权」。本设计**有意打破它的一半**：

| 凭据来源 | 现状 | 本设计 |
|---|---|---|
| 用户自建（`owner_id = 自己`） | 需显式绑定到消费者 | **默认可用于脚本** |
| 管理员创建、授权给用户 | 需显式绑定 | 仍需显式开启 |

理由：自有凭据的所有权在用户自己，让用户为自己的 key 再做一次授权只是摩擦；授予的凭据所有权在他人，必须显式。

**保留的防线**：两层脚本开关（平台 `secret_policy_allow_script_env` + 用户偏好 `sys_secret_user_prefs`）、单条凭据的场景勾选（`allowed_scenarios` 含 `script_env`）、票据 TTL、每票限流、逐次审计、输出脱敏。

**必须承认的威胁模型变化**：值会离开平台进程进入脚本。脚本可以 `print`、可以发到任意外网。这与「值只在出站那一刻由平台补」本质不同，**技术上拦不住**，只能靠事后审计 + 随时撤销。这是本设计付出的代价，也是「按凭据逐条勾选」必须保留的原因。

## 3. 授权模型（复用现有 `script_env` 场景，不新增列）

### 3.1 现状：这条通道的"授权"早就存在了

凭据中心的场景清单里已经有 `script_env`，界面上是「脚本环境变量」勾选项（`SecretCenter.vue` 的 `SCENES`，`wired: true`）。它现在的含义是"这条凭据可以进脚本子进程"，只是**消费方只有平台脚本工具**（`sys_script_tools`）——skill 里的 `.py` 走的是 Bash 工具，落不到这个消费方上。

所以本次不是新开一条授权通道，而是**把 `script_env` 的覆盖面从"平台脚本工具"扩到"任意 bash 执行"**：

| 消费方 | 现状 | 本次 |
|---|---|---|
| 平台脚本工具 | env 注入（已接通） | 不变 |
| skill / 任意 bash | 无通道 | **helper 取值（新增）** |

两条通道共用同一个 `allowed_scenarios` 勾选，用户只需要理解一件事：「这条凭据允不允许脚本用」。

### 3.2 判定

`policy.evaluate_secret` 里现有分支已经够用，不改判定结构：

```
1. status=active            → 否则 denied_disabled
2. is_live(now)             → 否则 expired
3. allowed_scenarios 含 script_env → 否则 denied_by_scenario
4. 两层脚本开关都开          → 否则 denied_by_script_policy
```

新增的只有：`scene=sandbox_script` 时第 3 步按 `script_env` 判定（两者等价），且不检查 `target_host`（脚本自己发请求，平台没有出站可校验）。

### 3.3 "默认可用"怎么落地

现状是 `allowed_scenarios` 默认 `NULL`（fail-closed，全不勾）。本方案**不迁移存量数据**，只改新建路径：

- 新建：凭据创建时默认勾选 `script_env`（自有凭据），授予凭据默认不勾
- 存量：保持原状（未勾 = 不可用），需要用的由用户自己勾一下
- 关闭：用户取消勾选即可，下一次取值就拒

**为什么不用"自有就绕过勾选"的判定特判**：那会让"取消勾选"失去意义——用户明明取消了，判定还得看归属放行。勾选状态必须是唯一真相，否则界面上那个复选框是假的。

**为什么不新增 `script_access` 列**：`allowed_scenarios` 已经在表达"这条凭据能用在哪些场景"，再加一列就是一个凭据有两个授权口径，早晚漂移。界面上也会出现"勾了脚本环境变量却仍不能读"的矛盾状态。

## 4. 票据

**无状态 HMAC，不落库**（避免为 5 分钟生命周期建表 + 清理任务）。

格式：`v1.<b64url(payload)>.<hmac_sha256_hex>`

```json
{"uid":"u123","cid":"conv-abc","sid":"sandbox-xyz","exp":1793000000,"jti":"a1b2c3d4","aud":"script-get"}
```

- 密钥：复用平台 `SECRET_KEY`，`aud` 参与签名，防止票据被挪用到别的接口
- TTL：**300 秒**
- 粒度：每次 bash 执行签一张，同一次执行内的多次 helper 调用共用（脚本里循环取多个 key 不必反复签）
- 撤销：TTL 到点自然失效；需要立刻止血时用「脚本开关」关掉（下一张票不再签发，且开关在 `evaluate_secret` 里每次校验）

## 5. 平台接口

`POST /internal/script-credential/get`（**独立的 `/internal/` 路由**，不走 v1 / portal 任何登录态——沙箱里没有 session，也没法带 API key）

```http
POST /internal/script-credential/get
X-Nanzi-Ticket: v1.<payload>.<sig>
Content-Type: application/json

{"key": "weather.api.key"}
```

成功：

```json
{"code": 0, "data": {"value": "...", "expires_at": null}}
```

拒绝：

```json
{"code": 403, "msg": "这条凭据没有勾选「脚本可用」", "deny": "denied_by_scenario"}
```

副作用：

1. `register_injected(值)` —— 回显兜底对它生效
2. usage log：`scene=sandbox_script`、`consumer_type=bash`、`consumer_id=<conversation_id>`
3. 限流：**每张票据 60 次/分钟**（脚本里 `for` 循环取几百条要能拦住）

**为什么单独开一个路由前缀**：v1 的 `verify_v1_api_access` 依赖 `require_api_key`，白名单只跳过细粒度权限、**仍要求登录态**，沙箱里两样都没有。`/internal/` 自成一个路由，唯一的门就是票据。

**部署侧要配**：`/internal/` 不应暴露到公网，网关层只允许集群内 / 沙箱网段访问。票据是第二道门，不是唯一一道。

## 6. helper 协议

平台写入工作区，两个文件都在 `<会话目录>/.nanzi/`：

| 文件 | 权限 | 内容 |
|---|---|---|
| `nanzi-credential` | 0600，可执行 | python 脚本，不含任何凭据值 |
| `ticket` | 0600 | 票据字符串 |

命令前缀（由平台拼接，`set -a` 不需要——这里只导出环境变量）：

```bash
export PATH="$PWD/.nanzi:$PATH" NANZI_TICKET="$PWD/.nanzi/ticket" NANZI_PLATFORM_URL="http://host.docker.internal:8000" && 
```

用法：

```bash
API_KEY=$(nanzi-credential get weather.api.key)
nanzi-credential list                    # 列出可用 key 名，不含值
```

`list` 是必要的：脚本作者需要知道有哪些 key 可拿，否则只能猜名字。

失败：`exit 1` + stderr 一句人话，四种情况分别说清楚：

| 场景 | stderr |
|---|---|
| 票据过期 | `票据已过期，请重新执行命令` |
| 开关没开 | `脚本读取凭据的开关未开启（管理员策略或个人偏好）` |
| 无权读取 | `这条凭据未允许脚本读取` |
| key 不存在 | `没有名为 X 的凭据` |

## 7. 注入时机与清理

接线点在 `LazySandboxBashNativeTool.__call__`（**唯一入口**，沙箱与 local 两条后端都在它内部）：

1. 先判定该用户**有无可用凭据**（查一次可用集合）；没有则**完全不加前缀**，不写任何文件——大多数会话不该为这个功能付 IO
2. 有则签发一张票、写 helper + ticket
3. 命令加前缀执行
4. `finally` 删 ticket；`helper` 保留（不含值，下次重写覆盖）

local 与沙箱的差异只有 cwd 与平台地址，命令前缀逻辑共用。

## 8. 网络可达（硬前提）

项目现在是零配置，容器访问不到平台。必须补：

| 环境 | 平台地址 |
|---|---|
| local 执行 | `http://127.0.0.1:<port>` |
| docker 沙箱 | `http://host.docker.internal:<port>`，容器启动加 `--add-host=host.docker.internal:host-gateway` |
| k8s 沙箱 | `http://<service>.<ns>.svc.cluster.local:<port>` |

配置项：`system_configs.sandbox_platform_base_url`，留空则按运行环境推导。

**这条不通，整套功能不可用**，且失败表现是「helper 连不上」而不是「没权限」，容易误判成权限问题。

## 9. 脱敏

三处必须接上，缺一处就漏：

1. 接口返回前 `register_injected(值)`
2. **bash 工具结果回灌前 `redact_text`** —— 当前 `workspace.py` / `middleware.py` 里完全没有，脚本 `print` 出来的值会原样进模型上下文
3. helper 的 stderr 也过一遍（拒因里可能带 key 名或片段）

### 实现时的关键修正：跨进程

第 2 条原设计想当然了。`register_injected` 是 `ContextVar`，只活在那次
`/internal/script-credential/get` 请求的上下文里；Bash 结果回到 agent 侧时，
那个 context 早就销毁了——**根本扫不到**。

实际做法是「按标识复现」：

1. 接口侧成功后，把**取过哪些 key**（不含值）写进 Redis `script_cred_consumed:<jti>`，TTL 900s
2. Bash 执行完，按 jti 读回这批 key，逐个 `resolve_by_key(record_usage=False)` 重新解密
3. `register_injected` → `redact_text` → `reset_injected`

明文不进任何存储，只在脱敏那一步的内存里过一遍。`record_usage=False` 是因为
这一次不是用户行为，记流水会把统计搞乱。

**这一层只保护模型上下文**：脚本把值写进自己的日志、或直接发到外部，平台拦不住，
也不打算假装能拦（见第 2 节的威胁模型）。

## 10. 前端

不新增开关，只改现有场景清单的措辞——`script_env` 现在同时覆盖两条通道，文案要说清：

```js
{ key: 'script_env', label: '脚本可用', hint: '默认关，需平台 + 个人都开',
  desc: '平台脚本工具以环境变量注入；skill 里的脚本用 nanzi-credential 取值。两条通道都算', wired: true }
```

新建凭据时默认勾上 `script_env`（自有凭据），授予凭据默认不勾。

另外在凭据详情补一块「最近脚本访问」：复用现有 usage 列表筛 `scene IN (script_env, sandbox_script)`。

### 使用指引放在现有「?」弹窗

那个弹窗（`usageHelpOpen`，标题「这些凭据会被用在哪里」）本来就在回答同一个问题，不需要新开 tab——tab 是「我的凭据 / 系统共享」的数据筛选，塞说明进去语义不对。

要改的：

1. 把「脚本工具 · 环境变量」这一条扩成「脚本 · 两条通道」：① 平台脚本工具（`env:` 注入）② skill / 任意 bash（`nanzi-credential get`），附一行用法示例
2. 补两块内容：**前提**（凭据勾「脚本可用」+ 两层开关都开）、**边界**（脚本自己发请求，值交出去之后平台拦不住打印和外发，只留调用记录）
3. 弹窗底部那句「值是加密存的，只在注入那一刻解密，不落日志、不进对话历史」**必须补限定词**——脚本通道不属于"注入"，值确实会交到脚本手上。原句照旧会让人以为脚本也拿不到，与事实相反

## 11. 数据库变更

**无表结构变更，无数据迁移。**

`allowed_scenarios` 是现成字段，`script_env` 是现有场景值，本次只是让它在新的消费方（bash / skill）上生效。存量凭据保持原状，需要脚本读取的由用户自己勾选。

## 12. 测试计划

| 层 | 用例 |
|---|---|
| 票据 | 签发/校验、过期、篡改 payload、`aud` 不匹配、密钥错 |
| 授权 | 勾选后允许、取消勾选后拒绝、授予凭据未勾拒绝（回填不影响授予凭据）、停用、过期 |
| 接口 | 200 取值、四种拒绝的人话、限流 60/min、usage log 落对 |
| helper | local 端到端：写文件 → 执行 → stdout 是值；ticket 删除；`list` 不含值 |
| 脱敏 | `print(key)` 后结果里是掩码、`register_injected` 生效 |
| 回归 | 无可用凭据时不写文件、不加前缀（性能路径） |

沙箱内的网络连通性需真环境验证，标注为集成测试，不放进单元测试。

## 13. 明确不做

- **不做环境变量注入**：`env` 一条命令全暴露，且随子进程继承
- **不做代理 token**：第三方静态 key 没法代理，做成中转等于自建网关，超出范围
- **不做跨用户共享**：凭据归属不变，只是取值通道新增一条
- **不在前端展示凭据值**：helper 是唯一出口，且只对脚本可见

## 14. 分期

- **P1（本次）**：票据 + 接口 + helper + 授权 + local/docker 网络 + 三处脱敏
- **P2**：k8s 网络、限流策略调优、按凭据维度的调用统计页

---

## 待确认的三个细节

1. **限流阈值** 60 次/分钟是否合适（脚本批量取 100 个 key 会被拦）
2. **`list` 是否暴露 key 名**：key 名本身算不算敏感信息
3. **helper 保留策略**：命令结束后删除 helper，还是留着给后续命令用（留着更省事，但工作区里多一个常驻文件）

## 12. 实现后才暴露的四个接线问题（第二轮 review）

设计稿假设「Bash 工具的返回值就是结果」，实现后发现这个假设在 AgentScope 2.x 上不成立。
四个问题都**不报错**，只是功能静默失效——纯函数测试全绿也发现不了：

| 问题 | 真实形态 | 后果 |
|---|---|---|
| 返回值类型 | `await tool(command=...)` 返回**尚未执行**的 `async_generator` | 清理票据放进 `__call__` 的 `finally`，命令还没跑票就没了，本机模式 100% 失败 |
| 输出 chunk 结构 | `ToolChunk(content=[TextBlock])`，正文在 `TextBlock.text` | 只认 `str` 时脱敏永远命中不了，明文静默进模型上下文 |
| 沙箱路径 | 命令看到的是容器内路径 | `NANZI_TICKET_FILE` 传宿主绝对路径，容器里不存在，沙箱 100% 报「票据不存在」 |
| 并行执行 | 同一会话可同时跑多条 Bash | 固定文件名 `ticket` 会互删，慢的那条中途丢票，且消费记录挂错票导致漏脱敏 |

对应改法：脱敏与清理都移进生成器内部、按 `TextBlock.text` 就地掩码、票据路径按**命令视角**拼接
（宿主路径只留给删文件用）、票据文件名带 jti 并用 `NANZI_TICKET_FILE` 传递。

**教训**：这个功能的测试不能只测纯函数。「命令真的被执行了」「输出真的被掩码了」必须用
真的 `LocalWorkspace.Bash` 跑一遍才算验证过，见 `tests/core/test_script_credential_wiring.py`。
