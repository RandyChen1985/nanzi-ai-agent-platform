# NanZi AI Agent Platform：K8s / K3s 镜像更新与滚动发布指南

不建议手动删除 Pod。更新镜像后，应该通过 Deployment 做滚动更新。

> [!TIP]
> **可选加速：K8s 沙箱网关预置镜像** —— AgentScope 沙箱网关环境位于 Pod 内
> `/root/.agentscope`（临时写层），每次新 Pod 冷启动都要跑 bootstrap（apt + uv + venv +
> 安装依赖），这是 K8s 沙箱比 Docker 冷启动慢的根本原因。可用
> [`build-k8s-sandbox-image.sh`](./build-k8s-sandbox-image.sh) 构建一个**网关预置镜像**，
> 把 venv 与 gateway 脚本直接打进镜像；配置 `sandbox_k8s_image` 指向它后，新 Pod 冷启动
> 直接可用，从数十秒降到秒级：
>
> ```bash
> ./build-k8s-sandbox-image.sh --version 1.0.0     # docker build + save
> ./build-k8s-sandbox-image.sh --dry-run           # 只预览 Dockerfile 与命令
> # 产物 nanzi-sandbox-k8s:1.0.0 + tar；脚本会自动尝试导入节点（ctr -n k8s.io / k3s ctr）
> # 随后把系统配置 sandbox_k8s_image 设为 nanzi-sandbox-k8s:1.0.0
> ```

> [!TIP]
> **一键自动升级（推荐）**：目录向导安装器已深度集成镜像滚动发布流程，在将镜像导入节点后，直接运行：
>
> ```bash
> ./install.sh --upgrade            # 自动探测 containerd 中新载入的镜像并交互确认升级
> # 或直接指定新版本一步到位：
> ./install.sh --upgrade 1.0.15.0
> ```
>
> 脚本会自动判断镜像 Tag 是否变动：Tag 变动时调用 `kubectl set image`，同 Tag 时调用 `rollout restart`，自动等待健康检查就绪并同步更新 `kustomization.yaml`。
>
> **导入镜像请用文件方式，不要用管道**：`docker save ... | ctr images import -` 在镜像较大时很慢且易因传输/缓冲问题中断。正确做法见下方「导入本地镜像」，本目录也提供一键工具：
>
> ```bash
> ./install.sh --import nanzi-ai-agent_1.0.15.0.tar   # 自动识别 K3s/普通 containerd 并导入
> ./install.sh --images nanzi-ai-agent                 # 确认是否已导入成功
> ```

## 导入本地镜像（K3s / 非 K3s 通用）

更新镜像前，先把它导入**节点容器运行时**。**使用文件方式导入，不要用管道**（`docker save ... | ctr images import -` 大镜像上很慢、且易中断，还可能因 `ctr import -` 的某些版本异常导致导入不完整）。

### 1. 导出为 tar 文件

```bash
docker save -o nanzi-ai-agent_1.0.15.0.tar nanzi-ai-agent:1.0.15.0
```

### 2. 导入到节点容器运行时

**K3s（k3s 自带 containerd）：**

```bash
sudo k3s ctr images import nanzi-ai-agent_1.0.15.0.tar
```

**非 K3s / 普通 containerd（Kubernetes 节点，使用其专用 namespace）：**

```bash
sudo ctr -n k8s.io images import nanzi-ai-agent_1.0.15.0.tar
```

### 3. 使用目录工具一键导入（自动识别环境）

```bash
./install.sh --import nanzi-ai-agent_1.0.15.0.tar
./install.sh --images nanzi-ai-agent      # 确认是否已导入成功
```

> 说明：如果镜像是在节点本地 `docker build` 出来的（不在 Docker daemon 里，而是已做成 tar），把 tar 放到节点后同样用 `ctr -n k8s.io images import <tar>` 或 `./install.sh --import <tar>` 导入即可，无需再经过 docker。导入后用 `./install.sh --images nanzi-ai-agent` 或 `crictl images | grep nanzi` 复核 Tag 是否一致。

## 镜像 Tag 发生变化

例如从：

```text
nanzi-ai-agent:1.0.14.0
```

升级到：

```text
nanzi-ai-agent:1.0.15.0
```

先把镜像导入节点容器运行时（**文件方式，勿用管道**）：见上方「导入本地镜像」小节；K3s 用 `sudo k3s ctr images import <tar>`，非 K3s 用 `sudo ctr -n k8s.io images import <tar>`，或直接：

```bash
./install.sh --import nanzi-ai-agent_1.0.15.0.tar
```

然后更新 Deployment：

```bash
kubectl set image deployment/nanzi-ai-agent \
  -n nanzi-ai-agent \
  api=nanzi-ai-agent:1.0.15.0
```

其中 `api` 是容器名称，可以这样确认：

```bash
kubectl get deployment nanzi-ai-agent \
  -n nanzi-ai-agent \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
```

观察发布：

```bash
kubectl rollout status deployment/nanzi-ai-agent \
  -n nanzi-ai-agent \
  --timeout=180s
```

查看新 Pod：

```bash
kubectl get pods -n nanzi-ai-agent -o wide
```

Deployment 会自动：

1. 创建使用新镜像的 Pod
2. 等待新 Pod 就绪
3. 删除旧 Pod

---

## 继续使用相同 Tag

如果镜像还是：

```text
nanzi-ai-agent:latest
```

或者仍叫：

```text
nanzi-ai-agent:1.0.14.0
```

重新导入镜像后，Deployment 不会自动发现镜像内容发生变化，需要主动滚动重启：

```bash
# 1) 重新导入镜像（文件方式，勿用管道；K3s/非 K3s 命令见上方「导入本地镜像」）
./install.sh --import nanzi-ai-agent_1.0.14.0.tar

# 2) 触发滚动重启
kubectl rollout restart deployment/nanzi-ai-agent \
  -n nanzi-ai-agent

kubectl rollout status deployment/nanzi-ai-agent \
  -n nanzi-ai-agent \
  --timeout=180s
```

同时确保 Deployment 不是：

```yaml
imagePullPolicy: Always
```

本地导入镜像建议使用：

```yaml
imagePullPolicy: IfNotPresent
```

或者：

```yaml
imagePullPolicy: Never
```

查看当前策略：

```bash
kubectl get deployment nanzi-ai-agent \
  -n nanzi-ai-agent \
  -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{": image="}{.image}{", pullPolicy="}{.imagePullPolicy}{"\n"}{end}'
```

---

## 结论

不用手动删除 Pod；镜像标签变化就 `kubectl set image`，标签不变就重新导入镜像后执行 `kubectl rollout restart`。
