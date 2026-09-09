# NanZi AI Agent Platform：K8s / K3s 镜像更新与滚动发布指南

不建议手动删除 Pod。更新镜像后，应该通过 Deployment 做滚动更新。

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

## 镜像 Tag 发生变化

例如从：

```text
nanzi-ai-agent:1.0.14.0
```

升级到：

```text
nanzi-ai-agent:1.0.15.0
```

先把镜像导入 K3s：

```bash
docker save nanzi-ai-agent:1.0.15.0 \
  | k3s ctr images import -
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
docker save nanzi-ai-agent:1.0.14.0 \
  | k3s ctr images import -

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
