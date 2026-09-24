# K8s 沙箱预置镜像构建脚本 pip 源支持设计

## 背景

`k8s_deploy/build-k8s-sandbox-image.sh` 构建 K8s 沙箱「网关预置镜像」时，会在
镜像内执行两次 `uv pip install`（`BASE_REQS` 基础依赖 + `agentscope --no-deps`）。
脚本生成的 Dockerfile 未指定任何索引地址，实际走官方 `https://pypi.org/simple`。

国内构建机上直连 PyPI 经常出现下载缓慢或超时；失败表现为 `docker build` 阶段
拉包超时，或镜像内依赖装不齐、沙箱网关后续报 `HTTP 500: No module named 'xxx'`。
现有 `--proxy` 只作用于「安装 uv」那一步 curl，覆盖不到 pip 装包。

平台其他脚本（`dev.sh`、`DEVELOPMENT.md`、`HOW_TO_INSTALL.md` 等）已统一使用
清华 PyPI 镜像，并通过 `PYPI_INDEX_URL` 环境变量覆盖。本次让 K8s 沙箱构建脚本
与之对齐。

## 目标与边界

目标：为 `k8s_deploy/build-k8s-sandbox-image.sh` 增加 pip 源支持。

1. 新增 `--pip-index <URL>` 参数，作用于镜像内两处 `uv pip install`。
2. 默认值取环境变量 `PYPI_INDEX_URL`，未设置时使用清华镜像
   `https://pypi.tuna.tsinghua.edu.cn/simple`，与 `dev.sh:23` 约定一致。
3. 传入 `--pip-index https://pypi.org/simple` 可还原官方源。
4. `--dry-run` 预览、交互确认摘要、构建启动日志都展示实际生效的 pip 源。
5. 对 `PIP_INDEX` 做输入校验，拒绝会把内容注入 Dockerfile 的非法值。
6. 当源为 `http://`（内网镜像常见）时，自动追加 uv 的
   `--allow-insecure-host <host>`，使内网 http 源开箱可用；https 源行为不变。

明确不做（保持本次范围最小）：

- 不修改 Docker 沙箱相关代码（`sandbox/docker/*`、
  `docker_template_patch.py`、`docker_prebuild.py`、`docker_workspace.py`）。
  Docker 沙箱镜像 tag 是「Dockerfile 文本 + COPY 文件」的内容哈希，由
  `prepare_build_context` 计算，预构建与运行时必须逐字节一致；只改脚本侧会造成
  tag 漂移，属于独立的后续议题。
- 不向镜像写入 `ENV PIP_INDEX_URL` 或 `/etc/pip.conf`。沙箱 Pod 内 Agent 自行
  `pip install` 走哪个源属于运行时容器环境，不在本次范围。
- 不修改「安装 uv」的 `curl astral.sh` 步骤，该步骤仍由 `--proxy` 覆盖。
- 不修改 `docker/Dockerfile`（平台自身镜像）。

## 方案

改动集中在 `k8s_deploy/build-k8s-sandbox-image.sh` 一个文件，加上文档与测试。

### 参数与优先级

```
--pip-index <URL>   >   PYPI_INDEX_URL 环境变量   >   内置默认（清华镜像）
```

参数解析沿用脚本现有的 `case` 风格（空格分隔取值，不支持 `--flag=value`）。

### 输入校验

`PIP_INDEX` 会被展开进 Dockerfile 文本，因此必须在构建前 fail-fast 校验：

1. 非空。
2. 以 `http://` 或 `https://` 开头。
3. 不含空白字符、单双引号、反引号、`$`、`;`、`\`、`&`、`|`、`<`、`>`、
   `(`、`)`、`{`、`}`。

校验失败时打印明确错误并以非零码退出，不生成构建上下文。

校验位置：参数解析之后，且跳过 `--list` 与 `--sync-template` 两条不构建的
早退路径，使交互摘要与 `--dry-run` 也能提前暴露非法值。

### Dockerfile 注入

脚本已用 `<<EOF`（未加引号）的 heredoc 生成 Dockerfile，shell 变量会展开。
在原有两处安装命令上追加索引参数：

- `uv pip install --python $GATEWAY_VENV/bin/python --default-index <源>` + `BASE_REQS`
- `uv pip install --python $GATEWAY_VENV/bin/python --no-deps --default-index <源> "$AP"`

`--no-deps` 那条同样需要索引：虽然不解析依赖，但仍需从索引下载 agentscope 包本体。

使用 uv 的 `--default-index` 而非已废弃的 `--index-url`（脚本安装的是 latest uv，
参数可用；仓库文档也已统一使用该写法）。

当源为 `http://` 时，追加 `--allow-insecure-host <host>`（uv 的参数名；pip 是
`--trusted-host`，此处不涉及 pip）。https 源不追加任何额外参数。

### 展示

- `--help` 增加 `--pip-index` 条目。
- 脚本头部用法注释增加示例。
- 无参数交互摘要增加「Pip 源」一行。
- 构建开始日志打印实际使用的源。
- `--dry-run` 通过打印生成的 Dockerfile 自然展示（无需额外代码）。

### 文档

- `k8s_deploy/README.md`：构建脚本参数说明处补充 `--pip-index`；前置条件处
  补充「默认使用清华镜像，可用 `--pip-index https://pypi.org/simple` 还原官方源」。
- `sandbox/k8s/README.md`：常用命令示例处补充一条 `--pip-index` 用法。
- `tests/CHECKLIST.md`：登记本次测试覆盖。

## 测试

沿用仓库既有契约测试风格，扩展 `tests/test_k8s_deploy_docs_contract.py`。

1. **静态契约**：脚本含内置默认清华源、读取 `PYPI_INDEX_URL`、解析
   `--pip-index`、Dockerfile 渲染使用 `--default-index`、usage 含该参数。
2. **端到端（`--dry-run`，无需 Docker）**：以 subprocess 执行
   `--dry-run --pip-index <自定义源>`，断言输出的 Dockerfile 中出现该源。
3. **环境变量覆盖**：不传 `--pip-index` 而设置 `PYPI_INDEX_URL`，断言生效。
4. **默认值回归**：不传参数且清空 `PYPI_INDEX_URL`，断言出现清华源。
5. **反例**：`--pip-index` 传含 `&&` 的非法值，断言非零退出、输出含错误提示、
   且未生成任何 Dockerfile 内容。
6. **http 自动放行**：传 `http://` 源，断言 Dockerfile 同时出现
   `--default-index` 与该 host 的 `--allow-insecure-host`。

`--dry-run` 路径在执行 `check_docker_environment_k8s` 时直接返回，不依赖本机
Docker daemon；构建上下文写在 `mktemp -d` 临时目录，不污染仓库。

## 风险

- **默认行为变更**：默认源从官方 PyPI 变为清华镜像。海外用户与 CI 若需官方源，
  须显式传 `--pip-index https://pypi.org/simple`。已在 README 与 `--help` 中写明。
- `PIP_INDEX` 展开进 Dockerfile 属于命令拼接，已用白名单字符校验兜住注入风险。
- 本次不改动任何镜像 tag 计算逻辑，Docker 沙箱预构建与运行时的一致性不受影响。
