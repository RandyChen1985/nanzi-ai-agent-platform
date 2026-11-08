# 发版文档写作规范（Release Notes Spec）

> **适用范围**：`docs/release/<version>/` 下的三件套交付 —— `release_log.md`、`release_log_wechat.html`、`release-cover-info.md`。
>
> **来源**：v1.0.21 与 v1.0.22 两轮实际交付，以及 v1.0.22 期间三轮用户反馈（合并章节 → Key Features 去技术化 → Bug Fixes 去技术化）沉淀下来的规则。下次写 1.0.23 时按本文件执行，不要从零试错。
>
> **本文件与具体版本无关**：目前放在 `1.0.22/` 只是顺手；后续版本继续用时，建议挪到 `docs/release/` 根目录（或复制一份），别每版都翻上一版的目录。

---

## 1. 交付物与边界

| 项 | 内容 |
|---|---|
| 目录 | `docs/release/<version>/`（如 `1.0.22/`），一版一目录 |
| 三件套 | `release_log.md`（正式手册）、`release_log_wechat.html`（公众号排版）、`release-cover-info.md`（封面与生图文案） |
| 额外登记 | `tests/CHECKLIST.md` 末尾追加一节（4 列表格：项 / 主要文件 / 说明 / 验证） |
| 不做 | 不生成封面图、不执行 `./dev.sh`、不 `git commit`、不改运行时代码 |
| 命名 | 版本号统一写成 `v1.0.22.0`（三段 + 补零），目录名写 `1.0.22` |

---

## 2. 第一步：把统计口径钉死

**先取数，再写字。** 所有数字必须来自命令实测，不许估算。

```bash
BASE=<上一个版本的 tag>          # 如 1.0.21
git log -1 --format='%H %ad %s' $BASE          # 确认 tag 指向的发布 commit
git rev-list --reverse $BASE..HEAD | head -1   # 区间首个提交（起点，不含 tag 本身）
git rev-list --count $BASE..HEAD               # 总提交（含 merge）
git rev-list --count --no-merges $BASE..HEAD   # 非 Merge 提交
git rev-list --count --merges $BASE..HEAD      # merge 数
git diff --shortstat $BASE..HEAD               # 文件 / 增删行
git diff --name-only $BASE..HEAD | wc -l       # 文件数（复核）
git log --first-parent --merges --format='%s' $BASE..HEAD | grep -oE '#[0-9]+' | sort -n | tr '\n' ' '
git diff --name-status --diff-filter=A $BASE..HEAD -- db-prod db-prod-pg   # 新增迁移
```

**写作口径规则**：

- 起点写成「`<首个提交>`（`<上个 tag>` 之后的首个提交，含）」，终点写成「`<HEAD>`（含）」。
- 迁移组数 = MySQL 文件数 = PG 文件数；逐个打开 SQL 注释，确认是否有**预留编号**（v1.0.21 就有 `V195/V196` 预留，写漏会被问到）。
- 测试数字只取**最后一次全量记录**，并在 Metrics 里标明来源 commit；跑不动全量就如实写「按最后一次全量登记：xxx」，不要拼凑。
- 依赖变化用 `git diff $BASE..HEAD -- pyproject.toml requirements.txt` 确认；**为空就明确写「本版无依赖升级」**，别默认有。

---

## 3. `release_log.md` 骨架（照抄这个顺序）

```text
# 🎉 NanZi AI Agent Platform v1.0.22.0 Release Notes
**GitHub Repository**: 链接
（导语一段，业务视角讲清「这个版本解决什么」）
（变更范围 + 提交/文件/增删行/迁移组数）

> ⚠️ **升级注意**：6~8 条，**必须分成三组**（见 §3.1），不许平铺成 8 条等权列表

---

## 🚀 Key Features            ← 6 节，每节 5 条
### 1. 🔐 板块名：人话标题（English Name）

## 🐛 Bug Fixes               ← 4 类固定分组
### 调度、定时任务与 Cron
### 会话、时间线与交互
### 平台、权限与登录
### 工具、沙箱与嵌入

## 🗄️ 数据库迁移清单 (Database Migrations)   ← 表格 + 执行命令（md 与 html 都必须有）
## 📊 Metrics & Release Statistics   ← 代码块
## 👥 Contributors & Acknowledgements
```

**板块划分原则**：

- 6 节左右最舒服；超过 8 节说明该合并。
- **同一件事不要拆两章**。v1.0.22 最初把「ChatBI 数据集范围硬约束」单列一章，与「数据集权限四层治理」讲的是同一条链，被要求合并 —— 现在归入权限章内，用加粗小标题分隔。
- 每节顺序建议：**是什么 → 默认行为是什么 → 边界/例外 → 用户能看到什么**。

---

### 3.1 升级注意怎么写（最容易写糟的一段）

读者是**运维与实施**，不是开发者。分成三组，每组都要写清「不做的后果」：

| 组 | 放什么 | 判断标准 |
|---|---|---|
| 🚨 必须先做（顺序不能错） | 迁移、配置、必须重建的项 | 不做会启动失败或数据错乱 |
| ⚠️ 升级后行为会变 | 语义收紧、入口合并、权限变严 | 用户会感知到「跟以前不一样」 |
| 🔧 视环境处理 | 只影响部分部署形态、可延后 | 不做也不报错，只是仍旧行为 |

每条按 **做什么 → 不做什么后果 → 怎么执行** 写：

- ✅「新代码会读这些新增的列与配置，迁移没跑完会直接报错。执行 `bash db-prod/apply-sql.sh`（MySQL）或 `bash db-prod-pg/apply-sql.sh`（PostgreSQL）」
- ❌「本版本新增 7 组数据库迁移，涵盖知识库收藏、专家团字段…升级时必须按序执行迁移脚本」——只有罗列，没说后果，也没说怎么执行
- 顺序依赖必须点明并解释：`V205` 必须先于 `V206`，且写明「跳过会把菜单权限当成全员授权——提权级错误」
- 行为变更要**主动安抚**，避免被当成故障：「这是设计使然，不是故障」「不需要手工迁数据」「不重建也不报错，只是仍按旧限制跑」
- 内部字段名换成人话：`Memory / MemorySwap / NanoCPUs / PidsLimit` → 「内存、内存交换、CPU 核数、进程数」
- 反面提醒要直白：「清空输入框不等于关掉保护」「如果当初为了跑对而手工挪了一天，现在要挪回来」

正例见 `release_log.md` 的升级注意段（8 条 / 3 组）。

### 3.2 迁移清单（md 与 html 两份都要有）

这是 v1.0.22 补上的缺口：html 一直有迁移表，md 里却只在升级注意与 Metrics 各提一句，读者拿 md 去部署时看不到要跑哪些 SQL。v1.0.21 也有同样问题（历史遗漏），本版起两份都要有。

两边信息必须一致，只是呈现形式不同：

| | `release_log.md` | `release_log_wechat.html` |
|---|---|---|
| 呈现 | 标准 markdown 表格 | 真表格 `<table>` + `<colgroup>`，外层 `overflow-x:auto` |
| 列 | 版本号 / 变更内容 / 迁移文件 | 同左 |
| 迁移文件 | 一个单元格内用 `<br>` 分 MySQL 与 PG 两行 | 分两行写 |
| 附加 | 执行命令代码块 + 是否含预留编号 | 表格下方一行「升级提示」 |

内容规则：

- 版本号写成 `V205` / `V105`：**MySQL 在前、PG 在后**，中间 ` / `。
- 变更内容一句话说清「加了什么 / 影响什么」，不写 SQL 细节。
- 文件名给完整：`V205-dataset-table-scope-permissions.sql`，两套都要列。
- 有顺序依赖的组，在变更内容里加粗标注（**必须先于 `V206` 执行**）。
- 显式写「本版无预留编号」或列出预留编号 —— v1.0.21 曾预留 `V195/V196`，不写会被当成漏项。

## 4. 语言规范（最容易返工的一条）

### 4.1 写什么

- 用户能感知的**现象**：「流式输出时突然不跟底」「部门同事看得到按钮却选不中任何文档」
- 影响面：「1000 人的角色保存从 39.4 秒降到 0.28 秒」
- 现在的行为：「现在只认真实的滚动动作」
- 必要数字：耗时、次数、条数、比例

### 4.2 不写什么

| 禁止 | 例子 |
|---|---|
| 类名 / 方法名 / 函数名 | `resolve_mounted_dataset_scope_ids`、`_invalidate_user_caches_bulk` |
| 组件名 / 文件名 | `RowPolicyConfigModal.vue`、`cron_compat.py` |
| 字段名 / 枚举值 | `blocked_ui_modes`、`scope.columns`、`subject_type='all'` |
| 测试细节 | 「新增 33 项后端单测」「tests/frontend 467 passed」 |
| 实现机制 | 「用 sqlglot AST 提 exp.Column」 |

### 4.3 必须保留的例外

- **迁移版本号**：`V205` 必须先于 `V206` —— 运维照着执行，改成「某个版本」就没法操作。
- **目录 / 路径**：`data/knowledge_images`。
- **commit hash**：Metrics 与变更范围里的缩写 hash。
- **界面上真实存在的名字**：角色码 `default`、开关名「子智能体调用」。

### 4.4 一句话判断标准

> 用户或运维读到这句，能不能知道「**这跟我有什么关系 / 我该做什么**」？答不上来就重写。

### 4.5 改写对照（照这个手感写）

| ❌ 技术写法 | ✅ 用户写法 |
|---|---|
| `cron_compat` 只在调度边界翻译第 5 段，统一输出 APScheduler 别名 | 「工作日」的任务周六也跑了：排期是标准周一到周五，底层调度器给星期编号的方式却少一天 |
| `_compat_content_blocks` 把 `image_url` 块转换时 `mime_type` 硬编码 `image/png` | 图片一律被当成 png：上游是 jpeg / webp，转换环节写死了 png，严格模型直接判「图片无效」 |
| `_invalidate_user_caches_bulk`，`SCAN` 次数从 `2N` 压到 2 | 1000 人的角色保存从 39.4 秒降到 0.28 秒，耗时基本不再随人数增长 |
| 新增 `_filter_metrics_chunk_by_table_scope`，用 sqlglot 提物理表逐条剔除 | 指标口径里藏着的完整 SQL 不再透给无权的人 |

---

## 5. Key Features 细则

- **每节固定 5 条**，单条 1~2 句，单条尽量 ≤ 90 字。
- **零行内代码**（正文里不出现反引号包裹的标识符）。
- 标题用人话，英文副标题按 1.0.21 惯例保留：
  - ✅「知识库：千库万文档也能顺手 (Knowledge Workspace)」
  - ❌「知识库工作台：大数据量分页、编辑权限范围与图片转 Markdown」
- 加粗小标题承担「分类」职责，正文承担「说清效果」职责，不要在正文里复述小标题。
- 一条要点只讲一件事；三件事写三条，不要写成 200 字的长句。

---

## 6. Bug Fixes 细则

- **4 类固定分组**（见骨架），某类为 0 条就删掉该类标题。
- 每条统一模板：**现象（用户会怎么描述）→ 原因（一句）→ 现在的行为**。
- 每条 1~2 句，平均约 100 字符；不写修复函数名、不写测试文件名。
- 允许保留通用技术名词：Docker、MCP、JSON、png、Cron、SQL —— 这些是用户本来就在用的词。
- 一条内含多个子问题的（如「知识库链路 10 处」）用顿号并列短句，不要展开成小作文。

---

## 7. `release_log_wechat.html` 规范

### 7.1 硬性约束

- **纯内联 style**，不出现 `<style>` / `<script>` / 外链资源 —— 微信编辑器只吃内联。
- 标签必须平衡；可见文本里不能有裸 `<` `>`（写 `&lt;` / `&gt;`）。
- **不能出现 markdown 反引号**：`` `foo` `` 在微信里不会渲染，必须换成 `<code style="...">foo</code>`。
- 表格外层必须 `overflow-x:auto` + `-webkit-overflow-scrolling:touch`，并给 `<colgroup>` 定宽。

### 7.2 区块顺序（与 1.0.21/1.0.22 保持一致）

```text
<!-- 标题区 -->      深蓝渐变，左上角 NANZI · RELEASE NOTES + 版本 + 副标题一行
<!-- 项目声明 -->    个人开源 / MIT / 「南孜」由来（固定文案，逐版沿用）
<!-- 仓库链接 -->    左侧蓝条 + 浅蓝底
<!-- 导语 -->        一段业务描述 + 变更范围
<!-- 升级须知 -->    ⚠️ 琥珀色卡片，三组分段标题 + 条目，共 6~8 条（与 md 同结构）
<!-- 核心特性 -->    6 张卡：编号 · 人话标题 / 一句引言 / 5 条要点 / 截图占位
<!-- Bug Fixes -->   4 张卡：分类标题 + 每条一行（· 标题：正文）
<!-- 迁移表 -->      横向滑动表格（版本号 / 变更内容 / 迁移文件）
<!-- 升级指南 -->    三种方式：源码 / Docker / 沙箱与自检，深色终端代码块
<!-- 统计卡 -->      版本号、commit 范围、提交数、文件数、迁移、测试
<!-- 结语 -->
```

### 7.3 视觉规范

| 用途 | 值 |
|---|---|
| 主色（标题 / 强调） | `#0284c7`、`#0f172a` |
| 卡片左边框轮换 | `#0284c7` → `#8b5cf6` → `#10b981` → `#f59e0b` → `#f43f5e` → `#06b6d4` |
| 正文 | `#334155` / `#475569`，行高 1.8 |
| 代码块背景 | `#0f172a`，命令 `#38bdf8`，注释 `#94a3b8` |
| 升级卡片 | 底 `#fffbeb`、边框 `#f59e0b`、文字 `#78350f` |
| 截图占位 | 白色虚线框 + 📷 + 「【示例截图占位】：…」（每卡一个，写清要截哪几张图） |

### 7.4 每版必须逐屏核对

浏览器打开 `file://` 截图看：标题区、升级须知、第 1 张卡、中间卡、迁移表、升级指南、统计卡、结语。

---

## 8. `release-cover-info.md` 规范

固定 7 段，缺一不可：

1. **封面主标题**：`NanZi AI Agent Platform v1.0.22.0`
2. **封面副标题**：5~6 个卖点用 `·` 连接（可包含小节之外的主题，这里是卖点不是目录）
3. **三版 120 字文案**：痛点反转版（公众号导读/朋友圈）/ 极客硬核版（技术社区）/ 企业安全与协同版（企业集成），每版必须以用户痛点开头
4. **视觉关键词**：6 组，中英对照
5. **推荐用途**：GitHub Release 头图 / 公众号封面 / 技术社区配图 / 宣传横幅
6. **生成图路径**：`docs/release/<version>/release-cover-v<version>.png`
7. **最终生图提示词**：用 `text` 代码块包裹，含 canvas 尺寸、场景、主体、风格、构图、灯光、色板、文字要求、禁止项

---

## 9. 交付前自检（可执行）

### 9.1 结构校验脚本

```python
# HTML：标签平衡 + 反引号 + 裸尖括号（在 docs/release/<version>/ 下运行）
import re
from html.parser import HTMLParser

VOID = {'br','img','hr','meta','link','input','col'}
class P(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=False); self.stack=[]; self.errs=[]
    def handle_starttag(self, t, a):
        if t not in VOID: self.stack.append(t)
    def handle_endtag(self, t):
        if t in VOID: return
        if not self.stack: self.errs.append(f'多余 </{t}>'); return
        x = self.stack.pop()
        if x != t: self.errs.append(f'<{x}> vs </{t}>')
s = open('release_log_wechat.html', encoding='utf-8').read()
p = P(); p.feed(s); p.close()
print('标签平衡:', not p.errs and not p.stack)
print('反引号:', s.count('`'))          # 必须 0
print('裸 &:', len(re.findall(r'&(?!amp;|gt;|lt;|quot;|#)\w*', s)))  # 必须 0
```

### 9.2 逐项确认

- [ ] `release_log.md` 一级章节 = 5 个（Key Features / Bug Fixes / 数据库迁移清单 / Metrics / Contributors）
- [ ] Key Features 每节 5 条，正文零行内代码
- [ ] Bug Fixes 4 类，每条「现象 → 原因 → 现在」
- [ ] 两边的条数**逐卡对齐**（md 第 N 节 ↔ html 第 N 卡），曾有卡片漏条
- [ ] 迁移清单在 **md 与 html 两份都有**，行数 = 迁移组数，且标注先后依赖（如 `V205` → `V206`）与是否含预留编号
- [ ] 升级注意分三组（必须先做 / 行为会变 / 视环境处理），每条都写了「不做什么后果」与执行方式
- [ ] Metrics 数字与第 2 节取数一致
- [ ] `tests/CHECKLIST.md` 已追加本节

### 9.3 渲染核对

```bash
# 用带查询参数的地址避免 file:// 缓存
open "file:///.../release_log_wechat.html?v=1"
```

逐屏截图确认排版；改完 HTML 记得换 `?v=2`，否则看到的是旧内容（踩过）。

---

## 10. 已踩过的坑（别再踩）

1. **反引号进微信**：markdown 的反引号在公众号里原样显示成 `` `foo` ``，必须换 `<code>`。
2. **裸 `>`**：「深度 > 0」在 HTML 里会被当标签，要写 `&gt;`。
3. **`<section>` 计数差 1**：文件顶部注释里也写了 `<section>`，`grep -c` 会比闭合多 1，属正常。
4. **浏览器缓存**：`file://` 页面改完不刷新会看到旧版，navigate 时加 `?v=N`。
5. **卡片漏条**：重写 md 某节后忘了同步 html 卡片，导致两边条数不一致（v1.0.22 的「检索质量」就漏过一次）。**改任何一节，必须回头数一遍两边条数。**
6. **迁移顺序**：`V205` 必须先于 `V206`（后者撤销前者的误回填），漏写会造成提权级误操作。
7. **不要把数字写死成「N 大板块」**：改稿合并章节后，「7 大板块」这类表述会全部失效，需要全局搜一遍。同理，CHECKLIST 里的描述也要跟着改。
8. **别为了「好读」删掉运维必需信息**：迁移版本号、路径、开关名要留；只去掉内部实现细节。

---

## 11. v1.0.22 的三轮反馈（默认值已改，不必再等反馈）

| 轮次 | 反馈 | 落地规则 |
|---|---|---|
| 1 | 「ChatBI 范围硬约束不用单独搞一章」 | 同一条链路的主题合并成一章，用加粗小标题分隔 |
| 2 | 「Key Features 太细、太技术、长篇大论」 | 每节 5 条、单条 1~2 句、零行内代码、标题说人话 |
| 3 | 「Bug Fixes 也要自然语言，类/方法名别显示」 | 统一「现象 → 原因 → 现在」，平均 100 字/条 |

> **结论：第一版就按这个标准写。** 三轮反馈的核心是同一件事 —— 读者是产品使用者与运维，不是读代码的人。

---

## 12. 下次开工清单（照顺序打勾）

```text
[ ] 1. 确认上一个 tag 与发布 commit
[ ] 2. 跑第 2 节的取数命令，把数字记下来
[ ] 3. 读完整提交正文（git log --no-merges --reverse），按主题归堆
[ ] 4. 逐个打开新增迁移 SQL，读注释确认语义与顺序依赖
[ ] 5. 定板块（6 节左右），同链路主题合并
[ ] 6. 写 release_log.md（Key Features → Bug Fixes → 迁移清单 → 升级注意）
[ ] 7. 回读一遍：出现类名/方法名/字段名就改写
[ ] 8. 写 release_log_wechat.html（按 7.2 区块顺序；逐卡对齐 md 条数、迁移表对齐 md 表格）
[ ] 9. 写 release-cover-info.md（三版文案 + 生图 Prompt）
[ ] 10. 跑 9.1 校验脚本 + 逐屏截图
[ ] 11. 在 tests/CHECKLIST.md 追加一节
[ ] 12. 汇报：改了哪些文件、统计口径、未做的事（未跑 dev.sh、未提交）
```
