# send_email 支持附件设计

**日期：** 2026-10-22
**需求（用户原话）：** 「现在这个工具不支持发附件，需要扩展一下吧」

---

## 1. 目标

让智能体在发邮件时能把文件作为附件发出：模型在对话里拿到文件路径（用户本轮上传的附件，或自己在工作区生成的文件），调用 `send_email(to_email, subject, content, attachments=[...])` 即可随邮件发出。

## 2. 范围

**做：**
- `send_email` 工具新增 `attachments` 参数
- 底层 `EmailDeliveryService.build_message` / `send_mail` 支持附件（默认空，行为不变）
- 附件路径校验：授权目录 + 扩展名白名单 + 数量/大小上限
- 数据库迁移：更新 `sys_api_tools` 里 `send_email` 的 description（能力说明与实际一致）

**明确不做（已与用户逐项确认）：**
- 只做 `send_email` 工具路径；定时通知、审核结果、密码重置、测试邮件**不带附件**
- 不做正文内联图片（`cid:`）
- 不做附件压缩、分卷、自动重命名
- 不加任何配置项（不新增开关，不新增前端字段）
- 零前端改动：`EmailConfigModal` 只是 SMTP 说明文案，不涉及附件
- 不落库发送记录、不做附件审计表

## 3. 现状核实（证据，非推测）

| 事实 | 证据 |
|---|---|
| 邮件只构造 `MIMEText(plain)` 或 `MIMEMultipart("alternative")`，无附件分支 | `app/services/email_delivery_service.py:514-519` |
| 附件路径授权已有统一入口，Office 工具已用 | `app/services/ai/tools/document_paths.py:88-161`；`word_document_tool.py:34` |
| 模型能拿到附件绝对路径 | 前端注入「用户本轮已上传文件附件：…，其安全托管后的服务器绝对路径是：…」（`frontend/src/composables/chat/useChatAttachments.ts:133`） |
| 工具上下文里有授权附件清单 | `app/core/context.py:54` `authorized_attachment_paths`；`assemble_step.py:140` 注入；`agent_service.py:1000` 采集 |
| `render_markdown` 的既定契约必须保住 | `tests/test_mail_markdown_render.py:138-145` 断言 alternative 两部分顺序；`:222-225` 断言工具发信调用点只有一处且开启渲染 |
| 平台已有 20MB 单文件上限口径 | `document_paths.py:11` `MAX_DOCUMENT_BYTES = 20 * 1024 * 1024` |
| 工具 description 走迁移更新是既有惯例 | `db-prod/V92-update_notification_tool_descriptions.sql`；`db-prod-pg/V5-register_example_search_tool.sql:115` |
| 迁移版本号现状 | MySQL 最新 `V181`；PostgreSQL 最新 `V82` |

## 4. 设计

### 4.1 数据流

```
send_email._arun（事件循环内）
  1. 现有：读邮件渠道开关 → 分层解析 SMTP → 确定收件人
  2. 新增：resolve_email_attachments(paths, context) → list[Path]（任一失败整批抛错）
  3. asyncio.to_thread(send_mail, settings, to, subject, content,
                       render_markdown=True, attachments=resolved)
                                                             │
build_message（同步纯函数）                                    │
  - 无附件：维持现状（text/plain 或 multipart/alternative）
  - 有附件：multipart/mixed ──┬── 正文部分（plain 或 alternative，语义不变）
                              └── N 个附件部分（base64 + RFC 2231 文件名）
```

职责边界：**document_paths 只回答「能不能发」**，**email_delivery_service 只回答「邮件长什么样」**，**工具只回答「用户要什么、怎么报错」**。校验在进线程池之前完成，不会发到一半才发现路径不对。

### 4.2 document_paths.py（新增）

```python
EMAIL_ATTACHMENT_EXTENSIONS = {
    ".docx", ".xlsx", ".pptx", ".pdf", ".csv", ".txt", ".md", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip",
}
MAX_EMAIL_ATTACHMENTS = 5
MAX_EMAIL_ATTACHMENT_TOTAL_BYTES = 20 * 1024 * 1024

class EmailAttachmentError(DocumentPathError): ...

async def resolve_email_attachments(
    paths: Iterable[str], *,
    allowed_attachment_paths: Iterable[str],
    user_id, conversation_id, user_name=None,
) -> list[Path]:
    """逐项 resolve_document_input_path，再校验数量/合计大小；空白项跳过，重复项去重保序。"""
```

单文件 20MB、扩展名、授权目录全部复用 `resolve_document_input_path`，只在其上叠三条批量规则。

### 4.3 email_delivery_service.py（修改）

- `build_message(*, ..., attachments: Sequence[Path] = ())`；`send_mail(..., attachments: Sequence[Path] = ())` 透传
- 新增 `_build_attachment_part(path) -> MIMEBase`：`mimetypes.guess_type` 决定 type（未知退 `application/octet-stream`）、`encoders.encode_base64`、`add_header("Content-Disposition", "attachment", filename=("utf-8", "", name))`；文件名先剥掉 `\x00-\x1f` 与 `\x7f` 控制字符（标准库不会因此多造头，但带换行的文件名在客户端会显示错乱）
- 渲染失败的降级逻辑不变：`render_markdown=True` 但渲染抛错时正文退回单部分 `text/plain`，附件照挂
- **附件只影响最外层容器**：alternative 的两部分与顺序原样保留在 mixed 内

### 4.4 notification_tools.py（修改）

- `EmailInput.attachments: List[str]`（schema 是干净的 `array of string`，不产生 `anyOf`——部分模型网关对 `anyOf` 支持不稳）；`_arun` 的入参仍是 `Union[List[str], str]`，因为工具调用不经过 pydantic 校验，模型偶尔把数组写成单个字符串/换行串，归一化后再校验
- description 补：填服务器绝对路径；只能用上下文注入的附件路径或工作区文件；不要编造路径；上限 5 个 / 单个 20MB / 合计 20MB
- 成功文案追加附件数：`Successfully sent email to a@b.com (2 attachments)`
- 校验失败 → `Error: ...` 且整封不发，文案点名文件与原因

### 4.5 迁移

- `db-prod/V182-update_email_tool_description_attachments.sql`
- `db-prod-pg/V83-update_email_tool_description_attachments.sql`

只更新 `sys_api_tools.description`，不新增表、不改结构。

## 5. 错误语义与安全

| 场景 | 行为 |
|---|---|
| 附件是空串 / 空数组 / 全是空白项 | 视为无附件，正常发 |
| 同一文件重复 | 去重保序，不报错 |
| 路径越权（`/etc/passwd`、别人的 uploads） | 整封不发，报「文件不在当前会话允许访问的目录中」 |
| 扩展名不在白名单（`.env`/`.sh`/`.exe`） | 整封不发，列出支持的扩展名 |
| 单文件 >20MB / 合计 >20MB / >5 个 | 整封不发，报实际值与上限 |
| 校验通过但读取时文件已删/变短 | 返回 SMTP 层错误，不抛 500 |
| 中文/特殊字符文件名 | RFC 2231 编码，不出现乱码 |

安全不做的事：不跟随符号链接出授权目录（`resolve()` 已保证）、不把文件内容写日志、不把附件读进 LLM 上下文、错误信息不回显文件内容。

## 6. 测试

新增 `tests/test_email_attachments.py`（`no_infrastructure`），沿用 `test_mail_markdown_render.py` 的 `message_from_string` 断言方式：

1. `build_message` 无附件 → 结构与现在一致（回归）
2. 有附件 + `render_markdown=True` → 根 `multipart/mixed`，首部分仍 `alternative` 且顺序不变
3. 有附件 + `render_markdown=False` → 根 mixed，首部分 `text/plain`
4. 附件 base64 解码 == 源文件字节；`Content-Disposition` 的 `filename*` 解出正确中文名
5. 未知扩展名 → `application/octet-stream`
6. `resolve_email_attachments`：白名单外 / 越权 / >5 个 / 单文件超限 / 合计超限 / 空白项 / 重复项
7. 工具层：校验失败时 `send_mail` 未被调用；成功路径参数透传正确、返回文案含附件数量
8. 回归：`test_mail_markdown_render.py`、`test_user_email_and_mail_service.py` 全绿（其他发信路径不传 attachments，行为不变）

## 7. 风险与决策

- **合计 20MB 是原始字节**，base64 后报文约 26.7MB，可能超过部分邮箱 25MB 单封上限。**默认决策：合计上限保持 20MB，SMTP 拒收时把真实错误返回给用户**（用户已批准设计整体，未单独选择该值；若要收紧到 15MB 只改一个常量）。备选：收到 15MB，或按 SMTP 服务商能力做成配置项——两者都留待后续需求触发。
- **TOCTOU**：校验通过后文件可能被替换/删除。读取时复核大小，失败走错误返回值，不抛 500。
- **不把附件内容读入模型上下文**：附件是「已存在于服务器上的文件」，模型只操作路径。

## 8. 未引入的复杂度（YAGNI）

内联图片、定时通知附件、附件压缩、前端配置、审计表——均无当前需求。

## 9. 评审后的修正（2026-10-22）

首轮实现完成后做了一次独立只读评审（无 Critical），据其结论加固了九处：

1. **ASCII 文件名不再强制 RFC 2231**：`filename="x.pdf"` 是普通参数，非 ASCII 才用
   `(charset, language, value)`。老 Outlook / 企业网关 / 只读 `filename` 的客户端
   会把 `filename*=utf-8''x.pdf` 显示成百分号串。
2. **附件失败改用专用异常** `AttachmentPreparationError`，与「可重试」的
   `SmtpDeliveryError` 分开；报文构造阶段的任何异常都返回 `(False, err)` 而不冒泡，
   预期内的超限只打 warning、不打全栈。
3. **合计上限下沉到 `build_message`**：`send_mail` / `build_message` 是公开入口，
   工具层的 20MB 合计拦不住直接调用。
4. **有附件时放大 socket 超时**（≥60s）：20MB 原文 → 约 26.7MB base64，10 秒发不完。
5. **先读盘、按实际字节复核**，失败文案不含服务器绝对路径（不向终端用户暴露容器目录）。
6. **数量上限在碰磁盘前粗判**：100 个路径不会先做 100 次「解析工作区根目录 + stat」。
7. **补 `Date` / `Message-ID`**：带附件的邮件更容易被判垃圾。
8. **文件名控制字符净化** + 已知类型覆盖表（`.md` → `text/markdown`，slim 镜像无
   `/etc/mime.types` 时不会退成 octet-stream）。
9. **`_context_user_name` 四份副本收敛**为 `document_paths.resolve_context_user_name`
   （word / excel / notification / resource_catalog）。

未采纳的评审建议：把合计上限收到 15–18MB。用户已定 20MB，SMTP 拒收时透传真实错误；
要改只需动 `MAX_EMAIL_ATTACHMENT_TOTAL_BYTES` 与 `MAX_ATTACHMENT_TOTAL_BYTES` 两个常量
（有测试钉住二者一致）。
