# workspace-archive-extract Specification

## Purpose

让用户在工作空间里直接解压 zip / tar / tar.gz / tgz，而不必下载到本地、解开、再逐个上传。
解压目标必须由用户显式选择（目录树），默认落在压缩包同名子文件夹里。

## Requirements

### Requirement: 菜单入口

工作区行操作菜单（⋮ 与右键共用 `buildWorkspaceActionEntries`）**MUST** 对可解压的压缩包提供「解压到…」项。

#### Scenario: 普通压缩包

- 目标为非目录、后缀属于 `.zip` / `.tar` / `.tar.gz` / `.tgz` 时，菜单出现 `extract`，文案「解压到…」；
- 条目不因预览不可用而消失（压缩包通常不可预览）。

#### Scenario: 不支持的压缩格式

`.rar` / `.7z` **MUST** 显示但置灰，`disabledHint` 说明「暂不支持 rar/7z，请先转成 zip」。

#### Scenario: 回收站与目录

- 回收站条目 **MUST NOT** 提供解压；
- 目录条目 **MUST NOT** 提供解压。

### Requirement: 目标目录选择

点击「解压到…」**MUST** 弹出目录树，默认定位到压缩包所在目录，并预填「压缩包名去扩展名」作为子文件夹名；用户可下钻改选目标目录、可修改或清空子文件夹名。

#### Scenario: 默认子文件夹

- `glob-test.zip` 在 `docs/` 下，默认目标为 `docs/glob-test/`；
- 子文件夹名为空时，直接解压到所选目录。

#### Scenario: 同名冲突

目标子文件夹已存在时 **MUST** 自动追加 `_1`、`_2`（不覆盖、不合并）；直接解压到所选目录时，已存在的成员文件跳过并计入 `skipped`。

### Requirement: 安全与配额

解压 **MUST** 在服务端完成，且满足：

- 源路径只需可读（`assert_path_allowed`），目标路径必须可写（`_resolve_writable_entry_path`）——公共只读目录里的压缩包允许解压到用户自己的目录；
- 成员路径规范化后必须落在最终解压目录内，拒绝 `..`、绝对路径（zip slip）；
- 跳过符号链接、硬链接、设备文件等非普通成员，并记录在 `skipped`；
- 解压后总字节数 ≤ `WORKSPACE_EXTRACT_MAX_MB`（默认 1024）、条目数 ≤ `WORKSPACE_EXTRACT_MAX_ENTRIES`（默认 5000），超限即中止。

#### Scenario: 路径穿越

压缩包含 `../evil.txt` 成员时，该成员被拒绝；不得在解压目录之外产生任何文件。

#### Scenario: 压缩炸弹

解压后总量超过上限时，返回 4xx，且本次已写出的文件被回滚，不留下半成品。

### Requirement: 格式支持

- `.zip`：`zipfile`；文件名优先 UTF-8 flag，缺失时按 cp437 → gbk 回退解码；
- `.tar` / `.tar.gz` / `.tgz`：`tarfile`，`r:*` 自动识别；
- 其他格式返回 400 并带可读原因。

#### Scenario: tar.gz

`.tar.gz` 包能正常解出目录结构与文件内容。

### Requirement: 接口与反馈

`POST /api/v1/chat/fs/extract` 接收 `{archive_path, target_dir, subdir?}`，返回 `{path, file_count, total_bytes, skipped}`。

#### Scenario: 前端反馈

解压成功后刷新当前目录并 toast「已解压 N 个文件到 xxx」；失败时 toast 后端 `detail`。
