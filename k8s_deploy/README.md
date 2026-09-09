# NanZi AI Agent Platform：Kubernetes 部署

## 目录

- [目录边界](#目录边界)
- [目录内容](#目录内容)
- [手动构建 sandbox 镜像，加速 Pod 启动](#手动构建-sandbox-镜像加速-pod-启动)
- [手动导入 NanZi 镜像](#手动导入-nanzi-镜像)
- [给第一次部署的人：先按这 9 步做](#给第一次部署的人先按这-9-步做)
  - [推荐快捷向导：使用 install.sh 一键交互式部署](#推荐快捷向导使用-installsh-一键交互式部署)
  - [手动逐步部署（第 0 ~ 8 步）](#手动逐步部署第-0-步确认你手里有什么)
- [首次登录后的可选能力配置](#首次登录后的可选能力配置)
- [K3s 单机实操(仅测试)：在一台 Linux 测试服务器上运行 NanZi](#k3s-单机实操仅测试在一台-linux-测试服务器上运行-nanzi)
  - [1. 适合什么配置](#1-适合什么配置)
  - [2. 安装 K3s](#2-安装-k3s)
  - [3. 先做一个 K3s 冒烟测试（可选）](#3-先做一个-k3s-冒烟测试可选)
  - [4. K3s 单机导入 NanZi 镜像](#4-k3s-单机导入-nanzi-镜像)
  - [5. 检查 K3s 存储并部署 NanZi](#5-检查-k3s-存储并部署-nanzi)
  - [6. K3s 单机常见问题](#6-k3s-单机常见问题)
  - [7. 从单机扩展到多节点时要注意](#7-从单机扩展到多节点时要注意)
- [云原生安全沙箱配置（Kubernetes 原生 Pod 隔离）](#云原生安全沙箱配置kubernetes-原生-pod-隔离)
  - [1. 核心架构与原理](#1-核心架构与原理)
  - [2. 配置与开启步骤](#2-配置与开启步骤)
- [启动后常用操作](#启动后常用操作)
- [当前支持结论](#当前支持结论)
- [常见问题与注意事项](#常见问题与注意事项)
  - [上线前最小检查清单](#上线前最小检查清单)
- [升级和回滚](#升级和回滚)
- [多副本前置条件](#多副本前置条件)
- [本地静态验证](#本地静态验证)

本目录提供基于普通 Kubernetes YAML 和 Kustomize 的部署基线，不包含 Helm Chart。
默认目标是：使用现有 Docker 镜像、集群外部 MySQL/PostgreSQL 和 Redis Stack，运行一
个单副本应用 Pod；RAGFlow、LLM、SSO 等可选能力按外部依赖准备后再配置启用。

## 目录边界

这是 **NanZi 平台应用层** 的 K8S 部署目录，只负责把 NanZi 应用镜像运行起来并接入
已有基础设施，不负责安装或管理下面的外部依赖：

| 外部依赖/基础设施 | 是否由本目录部署 | 部署责任 |
| --- | --- | --- |
| MySQL / PostgreSQL | 否 | 用户或运维方准备数据库、账号、库表和备份策略 |
| Redis Stack / RediSearch | 否 | 用户或运维方准备 Redis、认证、高可用和持久化策略 |
| RAGFlow | 否 | 用户或运维方准备 RAGFlow 服务及其 API 凭据 |
| LLM 网关/模型服务 | 否 | 用户或运维方准备模型服务及 API Key |
| SSO、Jira 等第三方系统 | 否 | 用户或运维方准备第三方地址、账号和凭据 |
| Oracle Instant Client | 否 | 需要 Thick 模式时由用户制作对应镜像并单独挂载 |
| Ingress Controller、TLS、StorageClass | 否 | 由目标 K8S 集群或平台运维统一提供 |
| 数据库迁移 | 否 | 按 `db-prod/` 或 `db-prod-pg/` 的方案由运维方执行一次 |

本目录只提供这些外部依赖的配置接入位置和检查说明，不会创建 MySQL、PostgreSQL、Redis
或 RAGFlow 的 StatefulSet/Deployment，也不会替用户生成真实凭据。


## 目录内容

以下为本目录各文件的用途与作用。

**部署资源清单（Kustomize 默认应用）**

| 文件 | 用途 / 作用 |
| --- | --- |
| `kustomization.yaml` | 默认资源入口，把所有清单聚合为一次 `kubectl apply -k`；不含真实 Secret 与 Ingress 示例 |
| `namespace.yaml` | 创建 `nanzi-ai-agent` 命名空间（平台 Pod 与 ServiceAccount 所在） |
| `serviceaccount.yaml` | 应用 Pod 的 ServiceAccount（`nanzi-ai-agent-sa`），供平台以受控身份访问 Kubernetes API 并管理沙箱 Pod/PVC |
| `configmap.yaml` | 非敏感配置与环境变量（外部数据库/Redis 地址等）；占位地址需按实际环境修改 |
| `pvc.yaml` | `/app/data` 持久卷（默认 20Gi、ReadWriteOnce），承载平台数据与沙箱共享数据根目录 |
| `deployment.yaml` | 单副本应用 Deployment（容器、环境变量、PVC 挂载、`/health` 探针）；已绑定沙箱 RBAC 的 ServiceAccount |
| `service.yaml` | ClusterIP Service，端口 80 转发到容器 8001，供 Ingress/内部调用 |

**配置与可选模板（默认不直接 apply）**

| 文件 | 用途 / 作用 |
| --- | --- |
| `secret.example.yaml` | Secret 模板（数据库/Redis/API Key 等敏感值）；复制后改名并填真实值，不要直接提交真实凭据 |
| `sandbox-rbac.example.yaml` | `sandbox_policy = k8s` 时沙箱所需的最小 RBAC：让平台 ServiceAccount 能在 `agent-sandboxes` 命名空间创建/管理 Pod、PVC 等 |
| `data-init-job.example.yaml` | 可选的一次性公共文档初始化 Job（把镜像内 `data/docs` 同步到 PVC）；不在默认 Kustomize 资源中 |
| `ingress.example.yaml` | ingress-nginx 可选示例，含 SSE 超时与会话粘性配置 |

**运维与部署脚本工具**

| 文件 | 用途 / 作用 |
| --- | --- |
| `install.sh` | 向导式交互安装/升级/运维工具：集群自检、分步配置生成与幂等 apply；支持 `install` 显式子命令、`upgrade`（滚动升级）、`images`/`import`/`check-sandbox-image`（节点镜像查看/导入/沙箱镜像检查）、`--dry-run` 演练、`-y` 免交互等 |
| `build-k8s-sandbox-image.sh` | 构建 K8s 沙箱“网关预置镜像”（把 AgentScope 网关 venv + gateway 脚本打进镜像），加速沙箱 Pod 冷启动；docker build → save → 自动导入节点 containerd |
| `nanzi-k8s.sh` | K8s / K3s 运维快捷脚本：`status`/`sandboxes`/`restart-pod`/`restart-pod-force`/`restart-k3s`/`restart-all`/`logs`/`events`/`test`；重启类命令带 y/N 二次确认。核心子命令均为纯 `kubectl`，任意标准 K8s 集群可用；脚本会自动探测本机是否运行 K3s，非 K3s 环境会跳过 systemctl 检查并拒绝 `restart-k3s`/`restart-all` |

**辅助资源与文档**

| 文件 | 用途 / 作用 |
| --- | --- |
| `sandbox-image/` | 网关预置镜像构建上下文：内含 `_mcp_gateway_app.py`（AgentScope gateway 脚本模板副本），供 `build-k8s-sandbox-image.sh` 使用，可用其 `--sync-template` 按 agentscope 版本刷新 |
| `upgrade.md` | 镜像更新与滚动发布操作说明（Tag 变化/不变场景、K3s/非 K3s 镜像导入、沙箱网关预置镜像详解） |
| `README.md` | 本目录部署说明（本文档） |
| `images/` | 文档/运维截图素材 |

## 手动构建 sandbox 镜像，加速 Pod 启动

K8s 沙箱（`sandbox_policy = k8s`）的网关环境放在 Pod 内的 `/root/.agentscope`（临时写层），
每次新 Pod 冷启动都要执行 AgentScope bootstrap（apt + uv + venv + 安装 `mcp` 等依赖），
这是沙箱比 Docker 慢、且容易出现“创建中等待/初始化失败”的主要原因。

可以通过**手动构建一次“网关预置镜像”**，把网关 venv 与 gateway 脚本直接打进镜像；
配置 `sandbox_k8s_image` 指向它之后，新 Pod 起来**直接跳过整个 bootstrap**，冷启动从数十秒
降到秒级。

### 一、前置条件

- 一台**能访问 Docker daemon** 的构建机（节点宿主机或开发机均可，**不要在 NanZi 平台 Pod 内执行**，脚本会自检拦截）；
- 构建机可访问 PyPI（拉取 `mcp/uvicorn/fastapi/httpx` 网关基础依赖与 agentscope 工具链依赖 `docstring_parser/jinja2/aiofiles/tree_sitter/tree_sitter_bash/python-frontmatter`，清单见 `build-k8s-sandbox-image.sh` 的 `BASE_REQS`），网络受限请加 `--proxy`；
- 需要访问本仓库 `k8s_deploy/`（内含 `build-k8s-sandbox-image.sh` 与 `sandbox-image/_mcp_gateway_app.py` 模板）。

### 二、构建并导入

```bash
cd k8s_deploy

# 1) （可选）先预览将要执行的 Dockerfile 与命令，不实际构建
./build-k8s-sandbox-image.sh --dry-run

# 2) 正式构建：docker build → save 出 tar → 自动导入节点 containerd（ctr -n k8s.io / k3s ctr）
./build-k8s-sandbox-image.sh --version 1.0.0

# 3) 若脚本未自动导入成功（例如在非节点机器上构建），把 tar 拷到节点后导入：
#    ./install.sh import nanzi-sandbox-k8s_1.0.0.tar
```

> 执行示例（真实输出：单机 containerd 节点，Docker 构建 + 自动导入）：
>
> ```text
> $ ./build-k8s-sandbox-image.sh --version 1.0.0
> ℹ  开始构建 K8s 沙箱网关预置镜像：nanzi-sandbox-k8s:1.0.0（基础镜像 python:3.11-slim）
> [+] Building 84.9s (7/9)                                                                    docker:default
>  => [internal] load metadata for docker.io/library/python:3.11-slim                              0.0s
>  => CACHED [1/5] FROM docker.io/library/python:3.11-slim                                         0.0s
>  => [2/5] RUN apt-get update -qq && apt-get install -y curl ca-certificates ripgrep ...         54.7s
>  => [3/5] RUN curl -LsSf https://astral.sh/uv/install.sh | ... uv ...                            8.3s
>  => [4/5] RUN uv venv /root/.agentscope/.venv && uv pip install ... "mcp<2.0.0" ...             20.7s
>  => naming to docker.io/library/nanzi-sandbox-k8s:1.0.0                                          0.2s
> ✔  镜像构建完成：nanzi-sandbox-k8s:1.0.0
> ℹ  导出镜像为 tar（文件式，非管道）...
> ✔  已导出：nanzi-sandbox-k8s_1.0.0.tar
> ℹ  正在导入节点 containerd（ctr -n k8s.io）...
> unpacking docker.io/library/nanzi-sandbox-k8s:1.0.0 (sha256:...)...done
> ✔  已导入节点容器运行时：nanzi-sandbox-k8s:1.0.0
>
> ✅ 构建完成。将系统配置 sandbox_k8s_image 设为 nanzi-sandbox-k8s:1.0.0，
>    之后新建/重启的沙箱 Pod 将直接使用预置网关环境。
> ```

常用参数：`--base-image python:3.11-slim`、`--image-name nanzi-sandbox-k8s`、
`--version <标签>`、`--proxy http://<代理>`、`--agentscope-version <版本>`（默认跟随平台
venv 的 agentscope 版本，建议保持平台一致）、`--dry-run` 演练、`--no-import`。

### 三、确认节点已导入

```bash
./install.sh check-sandbox-image nanzi-sandbox-k8s:1.0.0     # 或
crictl images | grep nanzi-sandbox-k8s
```

### 四、让沙箱使用该镜像

1. 打开平台「系统配置 → 沙箱 → `sandbox_k8s_image`」，填 `nanzi-sandbox-k8s:1.0.0`
   （页面该项下方有操作提示卡片）；
2. 之后**新建/重启**的沙箱 Pod 将使用预置网关环境，冷启动显著加速（已运行 Pod 需重建才生效）。

### 五、注意事项

- **不构建也能正常使用**：默认 `python:3.11-slim` 由集群直接拉取，AgentScope 会在 Pod 内自动初始化（只是慢）；预置镜像属于“一次配置、长期受益”的加速项。
- **平台升级后建议重建**：当 agentscope 升级导致网关依赖或 gateway 模板变化时，重新执行
  `./build-k8s-sandbox-image.sh --version <新版本>` 并把 `sandbox_k8s_image` 更新到新标签。
- **版本一致性**：在平台代码目录（有 `.venv`）执行会自动 pin 平台 agentscope 版本；若在其它机器执行且未指定版本，脚本会提示“将安装 PyPI 最新 agentscope，可能有协议漂移风险”，请用 `--agentscope-version <平台版本>` 显式指定。
- 详细原理与更多参数见 `upgrade.md` 顶部“可选加速：K8s 沙箱网关预置镜像”章节。

## 手动导入 NanZi 镜像

平台自身镜像（`nanzi-ai-agent`）需要先准备好并导入**节点容器运行时**后，K8S 才能拉起
应用 Pod。下面先讲“镜像从哪来”，再讲“怎么导入节点”。

### 一、准备 NanZi 镜像（二选一）

**方式 A：源码目录用 Docker 构建**

在仓库 `docker/` 目录按节点架构执行构建脚本（产出镜像 `nanzi-ai-agent:<版本>`，并在
`docker/release/` 下自动 `docker save` 生成镜像归档 tar）：

```bash
cd docker
./build_linux_x86.sh 1.2.0        # x86_64 节点（或用 ./build_native.sh 跟随当前架构）
# ./build_linux_arm.sh 1.2.0      # ARM64 节点
```

**方式 B：从 GitHub 下载官方 Release 镜像**

打开 [NanZi Releases](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases)，
在 **Assets** 中下载与节点架构匹配的镜像归档 tar（例如
`nanzi-ai-agent_linux-amd64_<版本>.tar`），文件名以 Release 页面实际显示为准。

### 二、导入节点容器运行时

> ⚠️ **不要用 `docker load` 当导入完成**：`docker load` 只把镜像加载进 Docker daemon，
> K8s 节点的 containerd（K3s 也是 containerd）读不到。镜像必须导入到**节点的 containerd**。

得到镜像归档 tar 后，把它放到目标节点，然后二选一：

**推荐：使用目录工具一键导入（自动识别 K3s / 普通 containerd）**

```bash
cd k8s_deploy
./install.sh import nanzi-ai-agent_linux-amd64_1.2.0.tar   # 或你实际拿到的 tar 名
```

**或手动执行对应命令**

```bash
# 普通 containerd（非 K3s）：
sudo ctr -n k8s.io images import nanzi-ai-agent_linux-amd64_1.2.0.tar
# K3s：
sudo k3s ctr images import nanzi-ai-agent_linux-amd64_1.2.0.tar
```

若 tar 尚未在节点上（例如在开发机用 Docker 构建/下载），把它 `scp`/U 盘拷到节点后再
执行导入。

> 执行示例（真实输出：K3s 节点，先 `docker save` 成 tar 再文件式导入，勿用管道）：
>
> ```text
> root@yunshu-test2:/app/k8s/k8s_deploy# docker save -o nanzi-ai-agent_1.0.14.0.tar nanzi-ai-agent:1.0.14.0
> root@yunshu-test2:/app/k8s/k8s_deploy# k3s ctr images import nanzi-ai-agent_1.0.14.0.tar
> WARN[0000] DEPRECATION: The support for cgroup v1 is deprecated since containerd v2.2 and will be removed by no later than May 2029. Upgrade the host to use cgroup v2.
> docker.io/library/nanzi-ai-agent:1.0.14.0        saved
> application/vnd.oci.image.manifest.v1+json sha256:771d707e4ff84d6f3552a31a9ac98b6fbd97ae41fde9ff511658022c24fd4b4f
> Importing       elapsed: 131.3s total:   0.0 B  (0.0 B/s)
> ```
>
> 其中 `WARN ... cgroup v1 is deprecated` 仅是 containerd 的兼容性提示，可忽略；
> 看到 `... saved` 与 manifest 行表示镜像已成功导入节点 containerd。

### 三、确认已导入

```bash
./install.sh images nanzi-ai-agent      # 或
crictl images | grep nanzi-ai-agent
```

确认能看到 `nanzi-ai-agent:1.2.0` 后，Deployment 使用的镜像（`kustomization.yaml` 的
`newName/newTag` 或镜像仓库地址）与之一致即可正常拉起应用 Pod；若标签不一致，按
`upgrade.md` 的滚动升级流程更新镜像并 `rollout restart`。

## 给第一次部署的人：先按这 9 步做

如果你不熟悉 Kubernetes，可以只按本节操作；后面的章节用于解释细节和排查问题。

### 推荐快捷向导：使用 `install.sh` 一键交互式部署

为降低手动编辑 YAML 易出现的格式错误与命名空间遗漏风险，目录提供了向导式安装器 [install.sh](./install.sh)。它会自动自检环境、探测本地镜像、引导填写数据库/Redis 参数并按步骤安全幂等应用：

```bash
cd k8s_deploy

# 方式 1：交互向导部署（推荐，逐项确认并提供智能默认值）
./install.sh

# 方式 2：快速镜像升级（平滑滚动发布，自动跳过中间件向导）
./install.sh --upgrade            # 交互探测并确认，或直接指定版本：./install.sh --upgrade 1.0.15.0

# 方式 3：模拟演练模式（Try / Dry-Run，仅做配置生成与语法预检，不下发真实集群变更）
./install.sh --try                # 或 ./install.sh --dry-run

# 方式 4：免交互极速部署（适合已有配置快速生效）
./install.sh -y

# 查看帮助
./install.sh --help
```

> **升级执行示例**（真实输出：`./install.sh upgrade 1.0.14.0`，目标 Tag 与当前运行版本相同，
> 因此自动走 `kubectl rollout restart` 重新加载已重新导入的同名镜像；若 Tag 有变化则会走
> `kubectl set image` 滚动发布，详见 `upgrade.md`）：
>
> ```text
> root@yunshu-test2:/app/k8s/k8s_deploy# ./install.sh upgrade 1.0.14.0
>
> ╔══════════════════════════════════════════════════════════════════╗
> ║       NanZi AI Agent Platform - Kubernetes 部署与升级向导        ║
> ╚══════════════════════════════════════════════════════════════════╝
>
> [第 0 步] Kubernetes 集群环境自检
> ℹ  正在连接 Kubernetes API Server...
> ✔  Kubernetes 集群连接正常！当前节点列表：
> yunshu-test2   Ready    control-plane   12h   v1.36.4+k3s1   10.90.10.64  ...  containerd://2.3.4-k3s1.36
>
> ┌──────────────────────────────────────────────────────────────────┐
> │ 🚀 NanZi 应用镜像快速升级与滚动发布
> └──────────────────────────────────────────────────────────────────┘
> ℹ  当前集群运行镜像: nanzi-ai-agent:1.0.14.0
> ✔  在当前节点容器运行时中发现 NanZi 镜像版本：
>     • nanzi-ai-agent:1.0.14.0 (当前运行中)
> ℹ  使用命令行指定的目标版本: nanzi-ai-agent:1.0.14.0
> ✔  目标镜像 nanzi-ai-agent:1.0.14.0 已在节点容器运行时中就绪 ✓
>
> ⚠  目标镜像 Tag 与当前运行版本相同 (nanzi-ai-agent:1.0.14.0)。
> ℹ  触发 kubectl rollout restart 重新加载已重新导入的同名镜像...
> deployment.apps/nanzi-ai-agent restarted
> ℹ  正在等待滚动发布完成 (timeout 180s)...
> Waiting for deployment "nanzi-ai-agent" rollout to finish: 0 out of 1 new replicas have been updated...
> deployment "nanzi-ai-agent" successfully rolled out
>
> ✔  🎉 NanZi 应用镜像滚动发布成功！
>
> 当前最新 Pod 运行状态：
> nanzi-ai-agent-5788bb4549-z9stx   1/1     Running   0   22s   10.42.0.139   yunshu-test2   ...
> ```

> **安装演练示例**（真实输出：`sh install.sh --try`）——**首次部署建议先模拟演练一遍**，
> 熟悉各步骤与参数；演练只生成配置并用 `kubectl --dry-run=client` 做语法预检，**绝不向集群下发真实变更**：
>
> ```text
> root@yunshu-test2:/app/k8s/k8s_deploy# sh install.sh --try
>
> ╔══════════════════════════════════════════════════════════════════╗
> ║       NanZi AI Agent Platform - Kubernetes 部署与升级向导        ║
> ╚══════════════════════════════════════════════════════════════════╝
> 【🧪 模拟演练模式已激活 (--try / --dry-run)】
> 本轮仅演练参数收集与本地配置生成，通过 kubectl --dry-run=client 做预检，绝不向集群下发真实变更。
>
> [第 0 步] Kubernetes 集群环境自检
> ℹ  正在连接 Kubernetes API Server...
> ✔  Kubernetes 集群连接正常！当前节点列表：
> yunshu-test2   Ready    control-plane   12h   v1.36.4+k3s1   10.90.10.64  ...  containerd://2.3.4-k3s1.36
>
> [第 1/6 步] 命名空间与 ServiceAccount 声明
>   [DRY-RUN 演练] 验证指令: kubectl apply -f namespace.yaml --dry-run=client
> ✔  命名空间 (nanzi-ai-agent) [YAML 声明生成且非空，语法预检通过]
>   [DRY-RUN 演练] 验证指令: kubectl apply -f serviceaccount.yaml --dry-run=client
> ⚠  应用 ServiceAccount (nanzi-ai-agent-sa) [目标文件待进一步核对]
>
>   ? 是否同时部署云原生 Pod 安全沙箱 RBAC (sandbox-rbac.example.yaml)？ [Y/n]:
> ```
>
> 演练过程仍会按步骤收集你的交互输入（如上方的 Y/n 确认），但每个资源只打印
> `[DRY-RUN 演练]` 的校验指令，不会真正 `apply`；确认流程没问题后再去掉 `--try` 正式安装。

向导具备以下特性：
* **环境自检与智能分流**：检测 `kubectl` 连通性；若检测到 NanZi 已在集群平稳运行，直接运行 `./install.sh` 会主动提示您是否仅升级镜像；
* **本地镜像检测**：自动探测当前节点容器运行时（K3s containerd / crictl / docker）中的 `nanzi-ai-agent` 镜像 Tag 并自动推荐为默认版本；若未导入则提供单行导入命令；
* **幂等执行**：支持随时中断并安全重入，已存在的 PVC 和 Secret 会受到安全保护。

日常运维管理可配合使用 [nanzi-k8s.sh](./nanzi-k8s.sh)：
* `./nanzi-k8s.sh status`：一览本机 K3s 服务（K3s 节点）、集群节点、主平台及 `agent-sandboxes` 沙箱 Pod 与 PVC 状态；
* `./nanzi-k8s.sh sandboxes`：专门监控沙箱命名空间下的活跃 Pod 与动态持久卷；
* `./nanzi-k8s.sh restart-pod`：平滑滚动重启 NanZi Pod 并等待就绪；
* `./nanzi-k8s.sh restart-pod-force`：**强制滚动重启以加载节点上最新同名镜像**（适合“先手动 build + 导入覆盖 `nanzi-ai-agent:latest`，再让 Pod 换到新镜像”的场景）；重启前会探测本机容器运行时确认镜像已导入，未导入会告警并可中止；
* `./nanzi-k8s.sh restart-k3s`：重启 K3s 服务并等待 API Server 自动恢复（**仅 K3s 环境**）；
* `./nanzi-k8s.sh logs`：实时跟踪 300 条容器日志；
* `./nanzi-k8s.sh test`：快速探测 Service Endpoint 与 ClusterIP 连通性。
* 镜像更新与滚动发布详见 [upgrade.md](./upgrade.md)。

#### K8s 沙箱“网关预置镜像”（可选加速）

K8s 沙箱（`sandbox_policy = k8s`）每次冷启动都会初始化 AgentScope 网关环境（在 Pod 内装 venv/依赖），较慢。可选用 [build-k8s-sandbox-image.sh](./build-k8s-sandbox-image.sh) 手动构建一个“网关预置镜像”（把网关环境与 gateway 脚本打进镜像），构建/导入后把系统配置 `sandbox_k8s_image` 填为 `nanzi-sandbox-k8s:<版本>`，新沙箱 Pod 冷启动将直接复用、从数十秒降到秒级。

```bash
# 在可访问 Docker 的构建机（本目录）执行：构建 + 导出 + 自动导入节点 containerd
./build-k8s-sandbox-image.sh --version 1.0.0
./build-k8s-sandbox-image.sh --dry-run            # 只预览 Dockerfile 与命令
# 复核节点是否已导入：
./install.sh check-sandbox-image nanzi-sandbox-k8s:1.0.0
```

* 不构建预置镜像也能正常使用：默认 `python:3.11-slim` 由集群直接拉取，AgentScope 会在 Pod 内自动初始化网关环境。
* 详细说明（构建内容/依赖版本/升级后重建时机）见 [upgrade.md](./upgrade.md) 顶部“可选加速”章节；系统配置页 `sandbox_k8s_image` 下方也有操作提示。

---

### 手动逐步部署：第 0 步：确认你手里有什么

部署需要同时具备下面几类东西：

| 需要的东西 | 用途 | 谁负责准备 |
| --- | --- | --- |
| K8S 集群和 `kubectl` | 创建 Pod、Service、PVC 等资源 | 用户/运维方 |
| NanZi Docker 镜像 | K8S 真正运行的应用 | 用户构建，或从项目制品下载 |
| MySQL 或 PostgreSQL | 保存平台用户、配置和业务数据 | 用户/运维方 |
| Redis Stack/RediSearch | 缓存、会话状态、分布式锁和向量索引 | 用户/运维方 |
| 可用 StorageClass | 为 `/app/data` 创建 PVC | K8S 集群管理员 |
| 域名和 TLS 证书 | 让浏览器通过 HTTPS 访问，可选 | 用户/运维方 |
| RAGFlow、LLM、SSO 等服务 | 按需启用的外部能力 | 用户/运维方 |

### 第 1 步：确认 `kubectl` 连的是目标集群

在部署机器上执行：

```bash
kubectl config current-context
kubectl get nodes
kubectl get storageclass
```

确认显示的是目标集群和目标节点。不要在不确定上下文时执行 `kubectl apply`，否则可能
把应用部署到另一个集群。确认至少有一个可用的默认 `StorageClass`；如果没有，部署前
在 `pvc.yaml` 中填写集群管理员提供的 `storageClassName`。

### 第 2 步：准备 NanZi 镜像

K8S 不会自动从 GitHub 源码生成镜像，必须先准备好镜像。推荐使用镜像仓库；单节点测试
也可以下载 `.tar` 后导入。

| 场景 | 做法 | 适用范围 |
| --- | --- | --- |
| 有镜像仓库 | 构建镜像、推送仓库，再在 `kustomization.yaml` 填仓库地址 | 远程集群、多节点、生产环境，推荐 |
| Docker Desktop 单节点 | 下载/构建 `.tar`，执行 `docker load`，使用本地镜像名 | 本机测试或单节点环境 |
| 多节点但没有镜像仓库 | 每个可能调度 Pod 的节点都导入同一个镜像 | 可行但维护麻烦，不推荐 |

项目已经提供官方 Release 镜像包，可以先打开
[NanZi Releases](https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases)，
在页面的 **Assets** 中下载与 K8S 节点架构匹配的镜像归档文件。

先查看集群节点架构：

```bash
kubectl get nodes \
  -o custom-columns=NAME:.metadata.name,ARCH:.status.nodeInfo.architecture
```

| 节点架构 | Release 中应下载的资产 | 说明 |
| --- | --- | --- |
| `amd64` / `x86_64` | 文件名包含 `linux-amd64` | 常见云服务器、Intel/AMD 服务器 |
| `arm64` / `aarch64` | 文件名包含 `linux-arm64` | ARM 云服务器、鲲鹏、Ampere 等 |

下载后，在能访问目标单节点 K8S 运行时的机器上导入。文件名以 Release 页面实际显示
的版本为准：

```bash
docker load -i /path/to/下载的/nanzi-ai-agent_linux-amd64_版本.tar
docker image ls nanzi-ai-agent
```

`docker load` 输出中的 `Loaded image: nanzi-ai-agent:<版本>` 就是镜像标签。把这个
`<版本>` 填入 `kustomization.yaml`：

```yaml
images:
  - name: nanzi-ai-agent
    newName: nanzi-ai-agent
    newTag: "<版本>"
```

如果从项目源码构建，项目也提供按服务器架构构建并导出镜像的脚本：

```bash
cd /path/to/nanzi-ai-agent-platform

# x86_64 K8S 节点
./docker/build_linux_x86.sh 1.2.0

# ARM64 K8S 节点
# ./docker/build_linux_arm.sh 1.2.0
```

构建脚本会在 `docker/release/` 生成带架构和版本号的 `.tar` 文件。单节点测试可导入：

```bash
docker load -i docker/release/nanzi-ai-agent_1.2.0_linux-amd64_*.tar
docker image ls nanzi-ai-agent
```

#### 构建上下文中的运行时数据

`docker/Dockerfile` 需要使用项目根目录作为构建上下文，并通过 `COPY . .` 复制源码。
根目录的 `.dockerignore` 已排除 `data/uploads`、`data/agent_workspaces`、品牌资源、浏览器配置、
生成文件、沙箱等运行时和用户数据目录，因此本地上传文件不会进入 Release 镜像。随版本
发布的公共 `data/docs` 和 `data/skills` 会保留在镜像中；部署到 K8S 后，PVC 挂载到
`/app/data` 会遮住镜像中的同路径内容，首次部署需要按第 7 步选择性同步公共文档。

不要用 `docker build` 把某个本地 `data/` 目录单独作为额外构建上下文，也不要为了让
容器“自带数据”而取消这些 `.dockerignore` 规则。旧环境的上传文件和工作区应通过备份、
对象存储或受控的 PVC 迁移流程恢复。

如果是远程或多节点集群，即使镜像包是从 GitHub Release 下载的，也不能只在本机执行
`docker load`。应将镜像导入实际节点，或者更推荐先在本机导入，再重新打标签并推送到
目标集群可访问的镜像仓库：

```bash
docker tag nanzi-ai-agent:1.2.0 registry.example.com/nanzi-ai-agent:1.2.0
docker push registry.example.com/nanzi-ai-agent:1.2.0
```

如果 GitHub Release 页面只提供源码而没有镜像归档，就按上面的构建方式生成镜像。导入
镜像后仍要确认镜像架构与 K8S 节点一致；架构不一致通常会在 Pod 事件中表现为启动失败。

### 第 3 步：修改哪些文件

第一次部署通常只需要修改 `configmap.yaml`、复制并修改 `secret.example.yaml`，以及
根据镜像来源修改 `kustomization.yaml`。其他文件先不要改。

| 文件 | 第一次是否要改 | 作用 | 小白怎么处理 |
| --- | --- | --- | --- |
| `kustomization.yaml` | 是 | 指定要部署的资源，并覆盖镜像名称/标签 | 把 `newName`、`newTag` 改成实际镜像；本地镜像保持 `nanzi-ai-agent` |
| `configmap.yaml` | 是 | 保存非敏感配置，如域名、数据库地址、Redis 地址 | 按下方配置表修改 `*.example.internal` 和域名 |
| `secret.example.yaml` | 复制后改 | 提供密码和加密密钥 | 复制为 `secret.yaml`，填写真实值；不要直接提交 |
| `pvc.yaml` | 通常不用 | 为 `/app/data` 申请持久化磁盘 | 默认 20Gi、RWO；空间不够时只改容量 |
| `deployment.yaml` | 通常不用 | 创建 NanZi 应用 Pod、探针和数据卷挂载 | 先保持单副本；资源不足时再调整 CPU/内存 |
| `service.yaml` | 不用 | 让集群内部通过 80 访问应用 8001 | 保持不变 |
| `data-init-job.example.yaml` | 需要公共文档时可用 | 一次性把镜像内的公共文档同步到 PVC | 复制为 `data-init-job.yaml`，把镜像改成与 Deployment 相同后单独应用 |
| `ingress.example.yaml` | 需要域名访问时 | 配置外部域名、TLS、SSE 超时和会话粘性 | 复制为 `ingress.yaml`，改域名和证书后单独应用 |
| `namespace.yaml` | 通常不用 | 创建独立的 `nanzi-ai-agent` 命名空间 | 保持不变 |

### 第 4 步：填写 `configmap.yaml`

下面是第一次部署最需要理解的配置。示例中的 `example.internal` 和
`nanzi.example.com` 都是占位值，不能直接当作真实地址使用。

| 配置项 | 示例值 | 是否必改 | 是干什么的 |
| --- | --- | --- | --- |
| `APP_PUBLIC_URL` | `https://nanzi.example.com` | 是 | 用户访问平台的公开地址，生成链接和部分通知会使用 |
| `ALLOWED_ORIGINS` | `["https://nanzi.example.com"]` | 是 | 浏览器 CORS 白名单，必须是 JSON 数组字符串 |
| `BROWSER_VIEWER_ALLOWED_ORIGINS` | `https://nanzi.example.com` | 是 | 浏览器人工接管/查看功能允许的来源 |
| `DATABASE_TYPE` | `mysql` | 是 | 主库类型，只能按实际使用 `mysql` 或 `postgresql` |
| `MYSQL_HOST` | `mysql.example.internal` | MySQL 时必改 | MySQL 服务地址；不能填 Pod 内不存在的 `localhost` |
| `MYSQL_PORT` | `3306` | MySQL 时按需 | MySQL 服务端口 |
| `MYSQL_DB` | `nanzi_ai_agent_platform` | MySQL 时按需 | MySQL 数据库名 |
| `POSTGRES_HOST` | `postgres.example.internal` | PostgreSQL 时必改 | PostgreSQL 服务地址 |
| `POSTGRES_PORT` | `5432` | PostgreSQL 时按需 | PostgreSQL 服务端口 |
| `POSTGRES_DB` | `nanzi_ai_agent_platform` | PostgreSQL 时按需 | PostgreSQL 数据库名 |
| `REDIS_HOST` | `redis.example.internal` | 是 | Redis Stack 服务地址 |
| `REDIS_PORT` | `6379` | 按需 | Redis 服务端口 |
| `REDIS_DB` | `0` | 建议保持 0 | 平台 RediSearch/向量索引使用的 Redis DB |
| `REDIS_ENABLE` | `true` | 是 | 是否启用 Redis；平台生产运行通常应保持 `true` |
| `USE_ORACLE_THICK_MODE` | `0` | 默认不用改 | 是否启用 Oracle Thick 模式；启用时还需要专用镜像和客户端挂载 |
| `TZ` / `PLATFORM_TIMEZONE` | `Asia/Shanghai` | 按部署地 | 系统和平台业务时区 |

只使用 MySQL 时，`POSTGRES_*` 可以保留占位值；只使用 PostgreSQL 时，`MYSQL_*` 可以
保留占位值，应用会根据 `DATABASE_TYPE` 选择对应的一套连接配置。

### 第 5 步：创建 `secret.yaml`

在项目根目录执行：

```bash
cp k8s_deploy/secret.example.yaml k8s_deploy/secret.yaml
```

然后编辑 `k8s_deploy/secret.yaml`。每个字段的作用如下：

| Secret 字段 | 是否必填 | 是干什么的 |
| --- | --- | --- |
| `MYSQL_USER` / `MYSQL_PASSWORD` | 使用 MySQL 时必填 | MySQL 登录账号和密码 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | 使用 PostgreSQL 时必填 | PostgreSQL 登录账号和密码 |
| `REDIS_PASSWORD` | Redis 开启认证时必填 | Redis 登录密码；无密码时填写空字符串 |
| `ENCRYPTION_KEY` | 必填，模板已提供默认值 | 加密平台保存的模型/API 凭据；新环境可直接保留，已有环境必须沿用原系统值 |

`secret.yaml` 不会被 `kustomization.yaml` 默认引用，也已被 `.gitignore` 排除。需要先
单独应用它。模板中的 `ENCRYPTION_KEY` 已经按项目默认值填写，新环境可以不改，不需要
额外生成；如果生产环境希望替换，也可以改成自己的 Fernet key。替换前要确认平台还没有
使用旧 key 保存加密凭据；一旦开始使用，就必须长期保存同一个值。不要把真实密码写入
`configmap.yaml`、README 或 Git 提交。

如果确实要换成新值，可在安装了项目依赖的 Python 环境中生成：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把输出值写入 `secret.yaml` 的 `ENCRYPTION_KEY`，不要把它写入 `ConfigMap`。

### 第 6 步：初始化数据库和管理员账号

数据库迁移由用户/运维方执行，不由 K8S Deployment 自动执行。只选择与
`DATABASE_TYPE` 对应的一套：

| `DATABASE_TYPE` | 使用目录 | 初始化入口 |
| --- | --- | --- |
| `mysql` | `db-prod/` | `./db-prod/apply-sql.sh` 或 `./db-prod/apply-sql-native.sh` |
| `postgresql` | `db-prod-pg/` | `./db-prod-pg/apply-sql.sh` |

例如 MySQL：

```bash
cd /path/to/nanzi-ai-agent-platform
./db-prod/apply-sql-native.sh
```

例如 PostgreSQL：

```bash
cd /path/to/nanzi-ai-agent-platform
./db-prod-pg/apply-sql.sh
```

脚本会交互询问数据库地址、端口、账号和密码。生产环境执行前先备份数据库；详细行为
和升级规则见 [MySQL 迁移说明](../db-prod/README.md) 或
[PostgreSQL 迁移说明](../db-prod-pg/README.md)。数据库迁移脚本不会读取 K8S Secret，
请手工输入与 `configmap.yaml`、`secret.yaml` 相同的数据库连接信息。不要同时执行两套
迁移。

迁移完成后必须准备一个管理员登录凭据：

- MySQL 脚本按提示选择导入初始管理员；如果保留模板中的默认 `ENCRYPTION_KEY`，脚本
  生成的初始 API Key 与平台默认配置兼容。请把脚本输出的账号和 API Key 保存到密码管理器。
- PostgreSQL 脚本按提示创建初始管理员，并保存脚本输出的 API Key。
- 如果迁移时跳过了初始管理员，按对应数据库 README 中的管理员脚本补建；执行管理员
  脚本时使用与 `secret.yaml` 完全相同的 `ENCRYPTION_KEY`。

### 第 7 步：创建 Secret、PVC 并启动 NanZi

先创建命名空间、Secret 和 PVC：

```bash
kubectl apply -f k8s_deploy/namespace.yaml
kubectl apply -f k8s_deploy/secret.yaml
kubectl apply -f k8s_deploy/pvc.yaml
kubectl -n nanzi-ai-agent get pvc nanzi-ai-agent-data
```

#### 首次初始化 `/app/data`（可选）

如果需要使用镜像内随版本发布的公共文档，建议在启动应用前执行一次下面的 Job。它只
复制 `data/docs`，不会复制本地上传文件、用户工作区或其他运行时目录：

```bash
cp k8s_deploy/data-init-job.example.yaml k8s_deploy/data-init-job.yaml
```

编辑 `k8s_deploy/data-init-job.yaml`，把 `image` 改成与 `deployment.yaml` 最终使用的
镜像（包括仓库地址和版本标签），再执行：

```bash
kubectl apply -f k8s_deploy/data-init-job.yaml
kubectl -n nanzi-ai-agent wait --for=condition=complete job/nanzi-ai-agent-data-init --timeout=10m
kubectl -n nanzi-ai-agent logs job/nanzi-ai-agent-data-init
kubectl -n nanzi-ai-agent delete job nanzi-ai-agent-data-init
```

如果不需要公共文档，可以跳过这个 Job；应用会在 PVC 中按需创建上传、工作区和生成文件
目录。旧环境已有数据不要通过重新构建镜像迁移，应使用备份或受控的存储迁移流程。

#### 部署应用

确认镜像、数据库、Redis、PVC 和 Secret 都准备好后，在项目根目录执行：

```bash
kubectl apply -k k8s_deploy
kubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent
```

看到 `successfully rolled out` 后，查看 Pod：

```bash
kubectl -n nanzi-ai-agent get pod,svc,pvc
kubectl -n nanzi-ai-agent logs deployment/nanzi-ai-agent --tail=200
```

### 第 8 步：访问平台

#### 没有域名时：临时本机访问

保持下面命令运行，再在浏览器打开 `http://127.0.0.1:8001`：

```bash
kubectl -n nanzi-ai-agent port-forward svc/nanzi-ai-agent 8001:80
```

健康检查可以执行：

```bash
curl http://127.0.0.1:8001/health
```

#### 有域名时：配置 Ingress

```bash
cp k8s_deploy/ingress.example.yaml k8s_deploy/ingress.yaml
```

编辑 `ingress.yaml` 中的域名和 TLS Secret，确认集群已安装 ingress-nginx 后执行：

```bash
kubectl apply -f k8s_deploy/ingress.yaml
kubectl -n nanzi-ai-agent get ingress
```

之后通过 `https://你的域名` 访问。Ingress 示例不是默认资源，不执行这一步也不影响
集群内的 Service 和 `port-forward` 访问。

## 首次登录后的可选能力配置

`Pod Ready` 和 `/health` 只代表应用进程、基础配置和探针正常，不代表模型、知识库或
第三方系统已经连通。登录后按实际需要在平台管理界面配置：

| 能力 | 配置位置 | 说明 |
| --- | --- | --- |
| LLM/模型 | 系统配置或模型管理 | 新增模型的 Provider、Base URL、模型名和 API Key，并设置默认模型 |
| RAGFlow 知识库 | 系统配置 → 知识库设置 | 开启知识库，填写 RAGFlow API 地址、API Key 和默认知识库 ID |
| 代码安全沙箱 | 系统配置 → 安全沙箱 | 切换为 `k8s` 策略，为 Agent 提供 Kubernetes 原生 Pod 隔离执行环境 |
| SSO、Jira 等 | 对应第三方集成配置 | 先确认外部服务、网络和凭据，再按功能页面启用 |

当前 K8S `ConfigMap` 只放基础运行参数，不会自动把 RAGFlow 或模型凭据注入应用；这些
敏感配置由平台保存并使用 `ENCRYPTION_KEY` 加密。基础部署完成后，至少分别验证健康检查、
管理员登录、模型调用和（启用时）知识库检索。

## K3s 单机实操(仅测试)：在一台 Linux 测试服务器上运行 NanZi

如果手上只有一台 Linux 服务器，K3s 是很适合本项目测试部署的轻量 Kubernetes 发行版。
它不是模拟器，单个 `k3s server` 节点本身就是完整的 Kubernetes 集群，同时承担
control-plane 和工作负载。K3s 默认带有 containerd、Flannel、CoreDNS、Traefik、
ServiceLB 和 Local Path Provisioner；单 Server 默认可以使用 SQLite 保存集群数据。

这和本目录的边界要区分开：K3s 负责承载 Kubernetes 资源，但本项目的业务数据库和
Redis 仍按上面的外部依赖方案准备。不要因为 K3s 自带 SQLite，就把它当成 NanZi 的
MySQL/PostgreSQL 业务库。

### 1. 适合什么配置

K3s 官方 Server 基线是 2 核 CPU / 2 GB 内存，这个数字不包含 NanZi、数据库、Redis
和其他业务 Pod。对本项目可以按下面估算：

| 场景 | 建议 | 说明 |
| --- | --- | --- |
| 只验证 K3s 和基础 YAML | 2C / 4G | 可以跑系统组件和简单测试 Pod |
| NanZi + 外部 MySQL/Redis | 4C / 8G 起步 | 仍需看模型调用、Playwright 和并发 |
| NanZi、数据库、Redis 也同机 | 8C / 16G 或更高 | 业务容器资源应与 K3s 资源分开评估 |

建议使用 SSD。K3s 的 Local Path 存储默认写入服务器本地的
`/var/lib/rancher/k3s/storage`，PVC 会绑定到这个节点；这适合单机测试，不等于多节点
共享存储。

### 2. 安装 K3s

以下命令在目标 Linux 服务器上执行，需要 root 或 `sudo` 权限。官方安装脚本会安装
systemd 服务、`k3s`、`kubectl`、`crictl` 和 `ctr`，并把管理员 kubeconfig 写入
`/etc/rancher/k3s/k3s.yaml`：

```bash
curl -sfL https://get.k3s.io | sh -
```

如果服务器访问 GitHub 较慢，可以使用你当前已经验证过的国内镜像安装方式：

```bash
curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh \
  | INSTALL_K3S_MIRROR=cn sh -
```

安装后先不要马上部署业务，等待 K3s 系统组件完成启动：

```bash
sudo systemctl status k3s --no-pager
sudo kubectl get nodes -o wide
sudo kubectl get pods -A
sudo kubectl get storageclass
```

你当前服务器 `yunshu-test` 的安装结果是正常的，类似下面这样即可：

```text
NAME          STATUS   ROLES           AGE   VERSION
yunshu-test   Ready    control-plane   1m    v1.36.3+k3s1
```

刚安装后的 `coredns`、`local-path-provisioner`、`metrics-server` 或
`helm-install-traefik-*` 短时间显示 `ContainerCreating` 是正常的，先等待一两分钟再看：

```bash
sudo kubectl get pods -A -w
```

至少确认 `coredns`、`local-path-provisioner`、`metrics-server` 最终为 `Running`，两个
Traefik 安装 Job 成功完成或消失。非 root 用户需要使用 K3s 管理集群时，把 kubeconfig
复制到自己的目录；这个文件具有集群管理员权限，只应复制到可信机器：

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u):$(id -g)" ~/.kube/config
chmod 600 ~/.kube/config
kubectl get nodes
```

### 3. 先做一个 K3s 冒烟测试（可选）

如果想先确认 K3s 能拉镜像、创建 Pod 和暴露 Service，可以临时部署 nginx：

```bash
kubectl create deployment k3s-smoke --image=nginx:stable-alpine
kubectl expose deployment k3s-smoke --type=NodePort --port=80
kubectl get pods,svc -o wide
```

测试完成后清理临时资源：

```bash
kubectl delete service k3s-smoke
kubectl delete deployment k3s-smoke
```

如果 nginx 一直拉取失败，先不要判断 K3s 本身故障，检查服务器的外网访问、DNS 和
镜像仓库配置；NanZi 也可以改用项目 Release 镜像或企业镜像仓库。

### 4. K3s 单机导入 NanZi 镜像

K3s 默认使用 containerd。`docker load` 只会把镜像加载到 Docker daemon，不能保证
K3s 的 kubelet 能看到它；在 K3s 单机上，推荐直接导入 K3s 的 containerd：

```bash
sudo k3s ctr images import /path/to/nanzi-ai-agent_版本_linux-amd64.tar
sudo k3s ctr images list | grep nanzi-ai-agent
```

也可以把 Docker 镜像归档放入 K3s 的预导入目录，K3s 会自动导入：

```bash
sudo mkdir -p /var/lib/rancher/k3s/agent/images
sudo cp /path/to/nanzi-ai-agent_版本_linux-amd64.tar \
  /var/lib/rancher/k3s/agent/images/
sudo k3s ctr images list | grep nanzi-ai-agent
```

看到目标版本后，把 `k8s_deploy/kustomization.yaml` 的 `newTag` 改成相同版本，并保持
Deployment 的 `imagePullPolicy: IfNotPresent`。如果使用镜像仓库，则直接把 `newName`
改成仓库地址，不需要在节点手工导入：

```yaml
images:
  - name: nanzi-ai-agent
    newName: registry.example.com/nanzi-ai-agent
    newTag: "1.2.0"
```

远程或私有仓库场景还要确保 K3s containerd 能访问仓库；需要认证、私有 CA 或镜像代理
时，按节点配置 `/etc/rancher/k3s/registries.yaml`，并重启 K3s 后再检查 Pod 事件。

### 5. 检查 K3s 存储并部署 NanZi

K3s 通常会提供名为 `local-path` 的默认 StorageClass：

```bash
kubectl get storageclass
kubectl get storageclass local-path -o yaml
```

看到 `local-path (default)` 后，本目录的 `pvc.yaml` 可以直接使用，不需要填写
`storageClassName`。如果没有默认 StorageClass，先检查：

```bash
kubectl -n kube-system get pods -l app=local-path-provisioner
kubectl -n kube-system logs deployment/local-path-provisioner --tail=100
```

确认 K3s 已经 Ready、镜像已导入或仓库可访问后，回到下面的“第一次部署”流程，按顺序
执行：

```bash
cp k8s_deploy/secret.example.yaml k8s_deploy/secret.yaml
# 编辑 configmap.yaml、secret.yaml 和 kustomization.yaml

kubectl apply -f k8s_deploy/namespace.yaml
kubectl apply -f k8s_deploy/secret.yaml
kubectl apply -f k8s_deploy/pvc.yaml
kubectl apply -k k8s_deploy
kubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent
```

如果需要把镜像内公共文档同步到 K3s 的 Local Path PVC，先按下面主流程的“第 7 步”执行
`data-init-job.example.yaml`，再执行上面的 `kubectl apply -k`。K3s 单节点的
`ReadWriteOnce` PVC 和本项目的 `Recreate` 策略是匹配的，但升级期间会有短暂不可用。

部署完成后用 K3s 本机访问最简单：

```bash
kubectl -n nanzi-ai-agent port-forward svc/nanzi-ai-agent 8001:80
```

另开一个终端检查：

```bash
curl http://127.0.0.1:8001/health
kubectl -n nanzi-ai-agent get pod,svc,pvc
```

### 6. K3s 单机常见问题

| 现象 | 检查命令 | 常见原因 |
| --- | --- | --- |
| K3s 启动失败/不断自动重启 | `sudo journalctl -u k3s -n 100 --no-pager` | 宿主机使用 cgroup v1，新版 K3s (>= v1.31) 默认禁止 Kubelet 在 cgroup v1 上运行 |
| 系统 Pod 长时间 `ContainerCreating` | `sudo journalctl -u k3s -n 200 --no-pager` | 镜像下载、DNS、磁盘或 CNI 尚未完成 |
| NanZi `ImagePullBackOff` | `kubectl -n nanzi-ai-agent describe pod <pod名>` | 只执行了 `docker load`，但镜像没有导入 K3s containerd，或标签不一致 |
| PVC 一直 `Pending` | `kubectl -n nanzi-ai-agent describe pvc nanzi-ai-agent-data` | `local-path` 未 Ready、没有默认 StorageClass 或磁盘空间不足 |
| Pod Ready 但访问失败 | `kubectl -n nanzi-ai-agent get svc,pod` | 端口转发、Service 选择器、应用探针或外部 DB/Redis 配置错误 |
| Ingress 占用 80/443 | `kubectl -n kube-system get pods,svc | grep -i traefik` | K3s 默认 Traefik/ServiceLB 与服务器已有 Nginx 或网关冲突 |

#### 典型排查：宿主机 cgroup v1 导致 K3s 启动失败与循环重启

**问题原因：**

通过以下命令检查宿主机的 cgroup 驱动模式：

```bash
stat -fc %T /sys/fs/cgroup
```

如果输出为 `tmpfs`，说明服务器仍在使用旧的 **cgroup v1**（如果是 cgroup v2 会输出 `cgroup2fs`）。  
新安装的高版本 K3s（如 `v1.36.4+k3s1`）所携带的 Kubelet 默认启用了对 cgroup v1 的校验拦截，禁止在 cgroup v1 宿主机上启动，导致 systemd 中 K3s 不断崩溃并自动重启，在日志中报出明确错误：

```text
kubelet is configured to not run on a host using cgroup v1
```

**最终解决方案（无需重装 K3s，也无需重启服务器）：**

通过 Kubelet drop-in 配置目录显式声明允许 cgroup v1：

1. 创建 Kubelet 附加配置目录：

```bash
sudo mkdir -p /var/lib/rancher/k3s/agent/etc/kubelet.conf.d
```

2. 新建配置文件 `/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-cgroup-v1.conf`：

```bash
sudo tee /var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-cgroup-v1.conf << 'EOF'
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
failCgroupV1: false
EOF
```

3. 重载配置并重启 K3s：

```bash
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

4. 验证服务与集群状态：

```bash
sudo systemctl status k3s --no-pager
sudo kubectl get nodes -o wide
sudo kubectl get pods -A
```

单机只使用 `port-forward` 时不需要额外配置 Ingress；如果服务器已有 80/443 服务，
可以继续使用 `port-forward`，或由运维方在安装 K3s 时明确规划 Traefik、ServiceLB 和
现有网关的端口边界。不要为了让 NanZi 能访问就直接删除 K3s 系统组件。

### 7. 从单机扩展到多节点时要注意

增加 agent 节点不需要重装现有 Server，但必须准备唯一 hostname、节点间网络和同版本
K3s。Server 上的加入令牌位于：

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

在新节点执行加入命令时，把 `<server-ip>` 和 `<token>` 替换为实际值；不要把 token
提交到代码仓库或发到聊天记录：

```bash
curl -sfL https://get.k3s.io \
  | K3S_URL=https://<server-ip>:6443 K3S_TOKEN='<token>' sh -
```

多节点至少确认 Server 可达 TCP `6443`，Flannel VXLAN 节点间可达 UDP `8472`；这些
端口只对必要的节点/安全组开放，不要暴露到公网。K3s 的 `local-path` 仍然是节点本地
存储，NanZi 当前 PVC 是 `ReadWriteOnce`，所以加 agent 不等于可以把 NanZi 扩成多副本。

官方资料：

- [K3s 官方快速开始](https://docs.k3s.io/quick-start)
- [K3s 安装要求与网络端口](https://docs.k3s.io/installation/requirements)
- [K3s 镜像导入](https://docs.k3s.io/add-ons/import-images)
- [K3s 存储与 Local Path Provisioner](https://docs.k3s.io/add-ons/storage)
- [K3s 集群访问与 kubeconfig](https://docs.k3s.io/cluster-access)

## 云原生安全沙箱配置（Kubernetes 原生 Pod 隔离）

在 Kubernetes 生产环境中，**严禁将宿主机 `/var/run/docker.sock` 挂载到应用 Pod**（避免容器逃逸与节点特权扩散）。
NanZi 平台提供了**云原生 Pod 安全沙箱策略（`sandbox_policy = "k8s"`）**，直接通过 Kubernetes API 动态拉起独立隔离的 Pod 为智能体执行 Python / Shell 代码、数据分析与工件生成。

### 1. 核心架构与原理
- **免 Docker Socket**：智能体执行环境完全解耦宿主机 Docker daemon，原生适配 containerd、CRI-O 等所有标准 Kubernetes 运行时与多节点集群调度；
- **MCP 协议通信**：Pod 内部以后台子进程运行 FastMCP Gateway 服务，上层智能体通过标准 MCP 协议调用 `sandbox::bash`、`sandbox::read` 等工具；
- **共享持久卷 subPath 挂载（体验与 Docker 100% 对齐）**：
  - 配置 `sandbox_k8s_existing_pvc` 指向平台的主 PVC（如 `nanzi-ai-agent-data`）；
  - 自动通过 `subPath: agent_workspaces/{user_key}/sandbox` 挂载用户隔离私有目录，并以只读方式挂载 `docs` 文档库；
  - 智能体在沙箱内生成的图表、CSV 数据和文件工件，平台主服务毫秒级直读并生成下载链接；
- **生命周期保护**：
  - 会话结束或 30 分钟无交互超时后，自动销毁沙箱 Pod，释放集群 CPU / 内存资源；
  - 平台定制适配器（NanZiK8sAdapter）保证在 Pod 销毁时**绝不误删共享 PVC**；若未指定已有 PVC 采用独立动态 PVC，可通过配置控制是否随 Pod 连带清理。

### 2. 配置与开启步骤

#### 步骤一：创建 ServiceAccount 与 RBAC 授权
应用 Pod 需要在沙箱命名空间内具备创建、查看和删除 Pod/PVC 的权限。参考 `k8s_deploy/sandbox-rbac.example.yaml`：

```bash
kubectl apply -f k8s_deploy/sandbox-rbac.example.yaml
```

默认 Deployment 已绑定该 ServiceAccount；如果使用自定义 Deployment，也必须保留以下字段：
```yaml
spec:
  template:
    spec:
      serviceAccountName: nanzi-ai-agent-sa
```

#### 步骤二：在管理控制台启用 K8S 沙箱
管理员登录平台，进入 **系统管理 → 系统配置 → 参数配置** 面板：
1. 找到【安全沙箱】分组；
2. 将 **沙箱策略（`sandbox_policy`）** 切换为 **`k8s`（Kubernetes Pod 沙箱）**；
3. 根据集群环境调整参数：
   - `sandbox_k8s_namespace`：沙箱 Pod 运行的命名空间（默认 `nanzi-ai-agent`）；
   - `sandbox_k8s_image`：沙箱基础镜像（默认 `python:3.11-slim`，或企业已安装数据科学包的镜像）；
   - `sandbox_k8s_existing_pvc`：推荐填写平台主 PVC 名称（例如 `nanzi-ai-agent-data`）；
   - `sandbox_k8s_cpu_limit` / `sandbox_k8s_memory_limit`：单 Pod 资源配额限制（如 `1` / `1Gi`）；
   - `sandbox_k8s_delete_pvc_on_close`：关闭沙箱时是否清理独立 PVC（使用已有 PVC 时不受此影响）；
4. 保存配置即可生效，无需重启 NanZi 主服务。

## 启动后常用操作

| 目的 | 命令 | 说明 |
| --- | --- | --- |
| 查看应用状态 | `kubectl -n nanzi-ai-agent get pod` | `Running` 且 `READY` 为 `1/1` 才是基础正常 |
| 查看沙箱 Pod | `kubectl -n nanzi-ai-agent get pod -l app.kubernetes.io/managed-by=agentscope` | 查看当前正在运行的智能体代码执行 Pod |
| 查看启动日志 | `kubectl -n nanzi-ai-agent logs deployment/nanzi-ai-agent` | 优先看数据库、Redis 和必填配置错误 |
| 查看详细事件 | `kubectl -n nanzi-ai-agent describe pod <pod名>` | 排查镜像拉取、PVC、探针失败 |
| 临时停止应用 | `kubectl -n nanzi-ai-agent scale deployment/nanzi-ai-agent --replicas=0` | 不删除 PVC，数据保留 |
| 恢复应用 | `kubectl -n nanzi-ai-agent scale deployment/nanzi-ai-agent --replicas=1` | 当前建议保持单副本 |
| 修改环境变量后生效 | `kubectl -n nanzi-ai-agent rollout restart deployment/nanzi-ai-agent` | ConfigMap/Secret 变化不会自动注入已有进程 |
| 查看历史版本 | `kubectl -n nanzi-ai-agent rollout history deployment/nanzi-ai-agent` | 用于确认升级记录 |
| 回滚应用 | `kubectl -n nanzi-ai-agent rollout undo deployment/nanzi-ai-agent` | 不会回滚 PVC 中的数据 |

### nanzi-k8s.sh 快捷运维

上面多数操作（以及沙箱监控、K3s/平台重启、日志/事件等）都已封装到 [nanzi-k8s.sh](./nanzi-k8s.sh)，
一键即可完成。直接运行（无参数）会打印帮助（真实输出）：

> **非 K3s 集群也能用吗？可以。** 除 `restart-k3s`、`restart-all` 外的所有子命令都是纯
> `kubectl` 操作，只要本机 kubeconfig 指向任意标准 K8s 集群（kubeadm / RKE / EKS / ACK 等）
> 即可直接使用。脚本启动时会自动探测本机是否运行 K3s（`k3s` 命令、`k3s.service` systemd
> 单元或 `/run/k3s/containerd/containerd.sock`）：非 K3s 环境下 `status` 会自动跳过第 1 节
> systemctl 检查，`restart-k3s` / `restart-all` 会被拒绝并提示改用集群自身的控制面维护方式。

```text
NanZi AI Agent Platform - K8s / K3s 快捷运维工具
用法: nanzi-k8s.sh <子命令>

常用运维指令：
  status        查看集群节点、NanZi 资源与沙箱 Pod/PVC 状态（K3s 节点另含本机服务状态）
  sandboxes     专门监控 agent-sandboxes 命名空间下的沙箱 Pod 与 PVC
  restart-pod   通过 Deployment 平滑滚动重启 NanZi 业务 Pod
  restart-pod-force  强制滚动重启，使新 Pod 换到节点容器运行时中最新导入的同名镜像并等待就绪
  restart-k3s   重启底层 K3s 服务并等待 API Server 自动恢复（仅 K3s 环境）
  restart-all   先重启 K3s 并在 API 就绪后自动滚动重启业务 Pod（仅 K3s 环境）
  logs          持续追踪 NanZi Pod 最新的 300 条容器日志 (-f)
  events        按时间倒序查看主平台与沙箱的 Kubernetes 调度事件
  test          测试 Service Endpoint 与 ClusterIP 80 端口 HTTP 连通性
```

`status` 执行示例（真实输出，K3s 单机；`K3s 服务状态` 中的长 systemd 进程树与 journal 明细已省略，
其间的 `failed to read memory cgroup ...` / `cgroup v1` 相关提示属 cgroup v1 环境噪音，可忽略）：

```text
root@yunshu-test2:/app/k8s/k8s_deploy# sh nanzi-k8s.sh status

┌──────────────────────────────────────────────────────────────────┐
│ NanZi AI Agent 平台 & K3s 集群运行状态
└──────────────────────────────────────────────────────────────────┘

⚡ 1. K3s 系统服务状态 (systemctl)
────────────────────────────────────────────────────────────────────
● k3s.service - Lightweight Kubernetes
     Loaded: loaded (/etc/systemd/system/k3s.service; enabled ...)
     Active: active (running) since Wed 2026-09-09 20:40:57 CST; 9h ago
   Main PID: 956 (k3s-server)
     ... (containerd / containerd-shim 进程明细省略)

🖥 2. 集群节点列表 (Nodes)
────────────────────────────────────────────────────────────────────
NAME           STATUS   ROLES   AGE   VERSION        INTERNAL-IP   EXTERNAL-IP   OS-IMAGE ...
yunshu-test2   Ready    control-plane   13h   v1.36.4+k3s1   10.90.10.64   <none>   Ubuntu 20.04.1 LTS ...

🚀 3. NanZi 平台应用资源 (Namespace: nanzi-ai-agent)
────────────────────────────────────────────────────────────────────
pod/nanzi-ai-agent-5788bb4549-z9stx   1/1   Running   0   6m3s   10.42.0.139   yunshu-test2   ...
service/nanzi-ai-agent   ClusterIP   10.43.67.129   <none>   80/TCP   12h   ...
ingress.networking.k8s.io/nanzi-ai-agent   traefik   *   10.90.10.64   80   11h

📦 4. 沙箱工作区资源 (Namespace: agent-sandboxes)
────────────────────────────────────────────────────────────────────
（当前无运行中的沙箱 Pod 或活跃 PVC）

✔ 状态检查完毕
```

> 提示：`restart-pod` / `restart-k3s` / `restart-all` 会先弹 y/N 二次确认；沙箱与平台滚动
> 重启、镜像滚动发布等详细操作见 `upgrade.md`。

不要执行 `kubectl delete pvc nanzi-ai-agent-data` 作为普通排障操作；删除 PVC 可能导致
上传文件、用户工作区和生成文件丢失。

## 当前支持结论

- **单副本 K8S 部署：支持作为基础方案验证。** 镜像监听 8001，应用配置通过环境变量
  注入，`/app/data` 通过 PVC 保存。
- **多副本基础设施：可以继续建设，但当前不能直接视为完整高可用。** 浏览器运行时、
  SSE/人工接管事件和部分执行状态仍有进程内注册表；调度器虽然使用数据库 JobStore
  和部分 Redis 锁，但当前启动方式不是完整的单 Leader 调度。
- **默认不启用宿主机 Docker Socket。** 推荐在 K8S 中直接使用 `sandbox_policy=k8s`
  原生 Pod 安全沙箱，通过 Kubernetes API 和最小 RBAC 细粒度控制 Pod 生命周期，无需向容器暴露宿主机 Docker 控制权。

## 常见问题与注意事项

排查时建议按“Pod 状态 → 应用日志 → 数据库/Redis → PVC → Ingress”的顺序进行，不要一
开始就删除 Pod、PVC 或重新初始化数据库。

| 现象 | 常见原因 | 检查与处理 |
| --- | --- | --- |
| `CreateContainerConfigError` | Secret 不存在、名称不一致或命名空间错误 | 确认先创建 `nanzi-ai-agent-secret`，并检查 `kubectl -n nanzi-ai-agent describe pod ...` |
| `ImagePullBackOff` | 镜像地址/标签错误、私有仓库未认证、节点无法访问仓库 | 检查 `image`、`imagePullSecrets` 和 Pod 事件；使用 `docker load` 时确认镜像已导入实际调度节点及其容器运行时 |
| `Pending` 或 PVC 挂载失败 | 集群没有默认 StorageClass、容量不足或 RWO 卷仍被旧 Pod 占用 | 检查 `kubectl -n nanzi-ai-agent describe pvc nanzi-ai-agent-data`；不要为了排障删除 PVC |
| Pod 正常但公共文档/上传文件消失 | PVC 挂载 `/app/data` 后遮住了镜像内同路径内容，或 PVC 没有初始化 | 按“初始化 `/app/data`”章节把必要的 `data/` 内容同步到 PVC |
| `CrashLoopBackOff` | 应用启动阶段连接数据库/Redis失败、必填环境变量缺失或镜像启动异常 | 先看 `kubectl -n nanzi-ai-agent logs deployment/nanzi-ai-agent --previous`，再核对 ConfigMap、Secret、DNS、端口和网络策略 |
| 沙箱 Bash/exec 报 `HTTP 500: No module named 'xxx'`（如 `docstring_parser`/`jinja2`） | 网关预置镜像内的 agentscope 缺少工具链核心依赖（官方 `_GATEWAY_BASE_REQUIREMENTS` 清单遗漏，Bash/MCP 工具加载 `agentscope.tool` 时触发；缺 `docstring_parser` 最常见，补上后可能继续缺 `jinja2` 等） | 用新版 `build-k8s-sandbox-image.sh` 重建并重新导入预置镜像（`BASE_REQS` 已补齐并经 smoke import 验证）；已运行 Pod 可临时 `kubectl exec` 补装：`kubectl exec -it -n agent-sandboxes <pod> -- /root/.agentscope/.venv/bin/pip install docstring_parser jinja2 aiofiles tree_sitter tree_sitter_bash python-frontmatter` |
| 数据库连接失败或表不存在 | 地址、端口、账号、数据库类型错误，或迁移没有完成 | 确认 `DATABASE_TYPE` 与实际数据库一致；MySQL 和 PostgreSQL 迁移分别执行一次，不要混用 |
| Redis 连接成功但向量/知识库功能异常 | Redis 不是 Redis Stack/RediSearch、密码错误或 `REDIS_DB` 不为 0 | 检查 Redis 版本、认证和 `REDIS_DB=0`；所有副本必须连接同一个 Redis |
| Ingress 返回 404/502/504 | Ingress Class、域名、TLS、Service 端口或后端 Pod 不匹配 | 先绕过 Ingress 用 `port-forward` 验证 Service，再检查 Ingress 事件和 Controller 日志 |
| SSE 中途断开、浏览器人工接管异常 | 代理缓冲/超时、会话粘性未配置或连接经过多个代理 | 使用示例中的超时和关闭缓冲设置；非 NGINX Controller 按其语法配置，并验证长连接 |
| 修改 ConfigMap/Secret 后应用仍使用旧值 | 环境变量只在进程启动时读取 | 修改后执行 `kubectl -n nanzi-ai-agent rollout restart deployment/nanzi-ai-agent`，再检查新 Pod 日志 |
| 代码执行沙箱不可用或报 Docker 权限错误 | 集群内未挂载 Docker Socket（默认安全边界） | 推荐在系统配置中将沙箱策略切换为 `k8s`，并应用 `sandbox-rbac.example.yaml` 授予必要权限，无需任何 Docker Socket 挂载 |
| K8S 沙箱 Pod 创建报 403 Forbidden | 应用 Pod 未绑定具沙箱命名空间权限的 ServiceAccount | 检查 ServiceAccount 是否已绑定 `sandbox-rbac.example.yaml` 中定义的 RoleBinding |
| 多副本后任务重复、会话丢失或取消不生效 | 调度器和部分浏览器/执行状态仍然是进程级状态 | 不要只修改 `replicas`；先完成共享存储、会话路由、Scheduler Leader 和跨 Pod 运行态验证 |
| Pod 被 OOMKilled 或请求超时 | Playwright、Agent 执行和并发模型调用需要更多 CPU/内存 | 查看 `kubectl describe pod` 的退出原因，根据压测调整 requests/limits 和并发策略 |
| 多副本后任务重复、会话丢失或取消不生效 | 调度器和部分浏览器/执行状态仍然是进程级状态 | 不要只修改 `replicas`；先完成共享存储、会话路由、Scheduler Leader 和跨 Pod 运行态验证 |
| Pod 被 OOMKilled 或请求超时 | Playwright、Agent 执行和并发模型调用需要更多 CPU/内存 | 查看 `kubectl describe pod` 的退出原因，根据压测调整 requests/limits 和并发策略 |

### 上线前最小检查清单

- [ ] 镜像使用明确版本标签，且目标节点或镜像仓库可拉取该版本。
- [ ] `DATABASE_TYPE`、数据库地址、账号和迁移版本匹配。
- [ ] Redis 为共享的 Redis Stack/RediSearch，`REDIS_DB=0`，网络和认证可用。
- [ ] 新环境确认是否保留模板默认 `ENCRYPTION_KEY`；已有环境沿用原系统值，且未写入 ConfigMap 或 Git 提交。
- [ ] PVC 已绑定，`/app/data` 中的公共文档、上传目录和用户数据已确认。
- [ ] `APP_PUBLIC_URL`、CORS 域名和 Ingress/TLS 域名一致。
- [ ] 已用 `/health`、登录、文件读写、模型调用和 SSE 分别验证，而不是只看 Pod 为 `Ready`。
- [ ] 当前仍保持单副本；若要扩容，已完成“多副本前置条件”中的专项验证。
- [ ] 已确认是否需要 Docker 沙箱；需要时已经过独立安全评审，不直接挂载宿主机 Socket。

## 升级和回滚

### 推荐：用封装好的 `install.sh upgrade`

镜像升级优先使用目录自带的 [install.sh](./install.sh) `upgrade` 命令，它会自动完成：
集群/镜像自检 → 探测节点已导入的镜像 Tag → 判断 Tag 是否变化 → Tag 变化走
`kubectl set image`、同 Tag 走 `rollout restart` → 自动等待滚动就绪 → 同步
`kustomization.yaml`。无需手工敲 kubectl：

```bash
cd k8s_deploy

# 交互式升级（自动探测 containerd 中新导入的 Tag 并确认）
./install.sh upgrade

# 或直接指定目标版本一步到位
./install.sh upgrade 1.0.15.0

# 升级前想先看一遍会执行的逻辑（仅演练，不下发）：
./install.sh upgrade --try
```

> 前置：目标镜像需先导入节点（见「手动导入 NanZi 镜像」），否则脚本会提示“目标镜像未在
> 节点容器运行时中找到”并给出导入引导。

### 熟悉 Kubernetes 的原生方式（备选）

如果你熟悉 K8s，也可以直接操作 Deployment：

**仅改镜像标签**：更新后滚动，无需多余重启

```bash
kubectl -n nanzi-ai-agent set image deployment/nanzi-ai-agent api=nanzi-ai-agent:1.0.15.0
kubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent
```

**同 Tag 重导镜像 / 改了 ConfigMap、Secret**：ConfigMap/Secret 只在 Pod 启动时读取，
需要重启 Deployment 才生效

```bash
kubectl apply -f k8s_deploy/secret.yaml            # Secret 单独应用
kubectl apply -k k8s_deploy
kubectl -n nanzi-ai-agent rollout restart deployment/nanzi-ai-agent
kubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent
```

> 注意：当前 Deployment 为单副本 + `Recreate`，升级期间会有短暂不可用窗口，但可避免
> `ReadWriteOnce` PVC 被新旧 Pod 同时挂载。

### 回滚

```bash
kubectl -n nanzi-ai-agent rollout undo deployment/nanzi-ai-agent
kubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent
```

PVC 不随 Deployment 回滚，回滚前应确认新版本没有改变数据格式；删除 PVC 会造成用户文件、
上传内容和工作区数据丢失，禁止把删除 PVC 当作常规排障步骤。

更详细的镜像 Tag 变动/不变场景与沙箱镜像滚动等实操，请参考 [upgrade.md](./upgrade.md)。

## 多副本前置条件

只有满足下表条件后，才应把 `deployment.yaml` 的 `replicas` 改大：

| 项目 | 当前情况 | 多副本要求 |
| --- | --- | --- |
| 数据库 | 外部共享数据库 | 所有 Pod 使用同一主库/读写拓扑 |
| Redis | 外部 Redis，保存缓存、状态和分布式锁 | 所有 Pod 使用同一可用 Redis；按需建设 Sentinel/Cluster |
| 文件 | 默认单 PVC、RWO | 改为可靠的 RWX 共享存储或对象存储，并验证并发读写 |
| 浏览器 | Worker 和事件订阅包含进程内注册表 | Ingress 会话粘性只能缓解，仍需验证断线/重连/人工接管 |
| 会话运行 | 部分活动运行和取消状态进程内 | 需验证跨 Pod 请求、取消和 SSE 连接行为 |
| 调度器 | 每个应用进程启动调度器；仅部分任务使用 Redis 锁 | 需要单 Leader/独立 Scheduler 或完整分布式去重机制 |
| Docker 沙箱 | Docker Compose 依赖宿主机 Socket | K8S 中需单独设计安全执行器，不能直接照搬 Socket 挂载 |

因此，当前文档只把单副本作为完整功能基线；多副本属于后续架构改造和验收事项，不是
修改一个 `replicas` 数字即可完成的部署动作。

## 本地静态验证

在提交资源前可执行：

```bash
kubectl kustomize k8s_deploy
kubectl apply --dry-run=client -k k8s_deploy
venv/bin/python -m pytest tests/test_dockerignore_contract.py tests/test_k8s_deploy_docs_contract.py -q
```

如果本机没有 `kubectl`，至少使用 YAML 解析器检查语法，并人工确认：默认资源不含真实
Secret、Ingress 示例未被默认引用、没有 Helm 模板、没有 Docker Socket、没有数据库迁移 Job。

本目录不代表已经完成真实集群验收；镜像拉取、PVC 绑定、数据库/Redis 连通性、Ingress
TLS、浏览器运行时和长连接行为仍需在目标集群由部署人员验证。
