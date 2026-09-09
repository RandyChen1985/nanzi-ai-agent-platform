#!/usr/bin/env bash

# ==============================================================================
# 解释器兼容层：如果用户使用 sh nanzi-k8s.sh 调用，自动切换到 bash 执行
# ==============================================================================
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
fi

set -eu

NAMESPACE="nanzi-ai-agent"
SANDBOX_NAMESPACE="agent-sandboxes"
DEPLOYMENT="nanzi-ai-agent"
SERVICE="nanzi-ai-agent"

# ==============================================================================
# 终端颜色与样式配置 (POSIX 规范，兼容交互与管道重定向)
# ==============================================================================
if [ -t 1 ]; then
  C_RESET="\033[0m"
  C_BOLD="\033[1m"
  C_DIM="\033[2m"
  C_BLUE="\033[1;34m"
  C_CYAN="\033[1;36m"
  C_GREEN="\033[1;32m"
  C_YELLOW="\033[1;33m"
  C_RED="\033[1;31m"
  C_PURPLE="\033[1;35m"
  C_GRAY="\033[38;5;244m"
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_BLUE=""
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
  C_PURPLE=""
  C_GRAY=""
fi

# 打印分段大标题
print_header() {
  title="$1"
  printf "\n"
  printf "%b┌──────────────────────────────────────────────────────────────────┐%b\n" "${C_CYAN}" "${C_RESET}"
  printf "%b│%b %b%s%b\n" "${C_CYAN}" "${C_RESET}" "${C_BOLD}" "${title}" "${C_RESET}"
  printf "%b└──────────────────────────────────────────────────────────────────┘%b\n" "${C_CYAN}" "${C_RESET}"
}

# 打印小节标题
print_section() {
  icon="$1"
  text="$2"
  printf "\n"
  printf "%b%s %b%s%b\n" "${C_BLUE}" "${icon}" "${C_BOLD}" "${text}" "${C_RESET}"
  printf "%b────────────────────────────────────────────────────────────────────%b\n" "${C_GRAY}" "${C_RESET}"
}

# 状态消息提示
log_info() {
  printf "%bℹ%b  %s\n" "${C_CYAN}" "${C_RESET}" "$*"
}

log_success() {
  printf "%b✔%b  %s\n" "${C_GREEN}" "${C_RESET}" "$*"
}

log_warn() {
  printf "%b⚠%b  %s\n" "${C_YELLOW}" "${C_RESET}" "$*"
}

log_error() {
  printf "%b✖%b  %s\n" "${C_RED}" "${C_RESET}" "$*"
}

# 危险操作二次确认：默认 N（回车取消），仅输入 y/yes 才放行
confirm_action() {
  prompt_label="$1"
  printf "  %b?%b %s [y/N]: " "${C_YELLOW}" "${C_RESET}" "$prompt_label"
  read -r input || input=""
  input="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')"
  if [ "$input" = "y" ] || [ "$input" = "yes" ]; then
    return 0
  fi
  printf "%b已取消，未执行任何变更。%b\n" "${C_YELLOW}" "${C_RESET}"
  return 1
}

# ==============================================================================
# 运行环境探测：K3s（本机 systemd 服务） vs 标准 Kubernetes 集群
# 说明：脚本核心子命令均基于 kubectl，任意 K8s 集群可用；
#      仅 restart-k3s / restart-all / status 第 1 节依赖本机 K3s 服务。
# ==============================================================================
is_k3s_env() {
  # 1. k3s 可执行文件在 PATH 中
  if command -v k3s >/dev/null 2>&1; then
    return 0
  fi
  # 2. systemd 中存在 k3s 服务单元（k3s server 常见部署方式）
  if systemctl list-unit-files 2>/dev/null | grep -q '^k3s\.service'; then
    return 0
  fi
  # 3. K3s 内置 containerd socket 存在
  if [ -S /run/k3s/containerd/containerd.sock ]; then
    return 0
  fi
  return 1
}

if is_k3s_env; then
  IS_K3S=1
else
  IS_K3S=0
fi

# 等待 K3s API 恢复函数
wait_for_k3s_api() {
  log_info "正在探测 K3s API Server 连通性..."
  max_retries=30
  count=0
  until kubectl get nodes >/dev/null 2>&1; do
    count=$((count + 1))
    if [ "$count" -ge "$max_retries" ]; then
      log_error "等待 K3s API 超时（已尝试 ${max_retries} 次），请检查服务日志！"
      return 1
    fi
    printf "  %b⏳ 等待 API Server 响应... (%s/%s)%b\r" "${C_YELLOW}" "${count}" "${max_retries}" "${C_RESET}"
    sleep 2
  done
  printf "  %b✔ API Server 响应成功！%b                                      \n" "${C_GREEN}" "${C_RESET}"
  if [ "$IS_K3S" = "1" ]; then
    log_success "K3s 服务状态: $(systemctl is-active k3s 2>/dev/null || echo 'unknown')"
  else
    log_success "API Server 连通正常（非 K3s 环境，跳过 systemctl 检查）"
  fi
}

# ==============================================================================
# 命令路由分发
# ==============================================================================
case "${1:-}" in
  status)
    print_header "NanZi AI Agent 平台 & K3s 集群运行状态"

    if [ "$IS_K3S" = "1" ]; then
      print_section "⚡" "1. K3s 系统服务状态 (systemctl)"
      systemctl status k3s --no-pager 2>/dev/null || true
    else
      print_section "⚡" "1. 集群类型"
      printf "  %b标准 Kubernetes 集群（未检测到本机 K3s 服务），跳过 systemctl 检查；以下状态均为纯 kubectl 查询。%b\n" "${C_GRAY}" "${C_RESET}"
    fi

    print_section "🖥" "2. 集群节点列表 (Nodes)"
    kubectl get nodes -o wide

    print_section "🚀" "3. NanZi 平台应用资源 (Namespace: ${NAMESPACE})"
    kubectl get pod,svc,ingress -n "$NAMESPACE" -o wide

    print_section "📦" "4. 沙箱工作区资源 (Namespace: ${SANDBOX_NAMESPACE})"
    if kubectl get namespace "$SANDBOX_NAMESPACE" >/dev/null 2>&1; then
      local_sandboxes=$(kubectl get pod,pvc -n "$SANDBOX_NAMESPACE" --no-headers 2>/dev/null || true)
      if [ -n "$local_sandboxes" ]; then
        kubectl get pod,pvc -n "$SANDBOX_NAMESPACE" -o wide
      else
        printf "%b（当前无运行中的沙箱 Pod 或活跃 PVC）%b\n" "${C_GRAY}" "${C_RESET}"
      fi
    else
      printf "%b（命名空间 %s 尚未创建，启动首个沙箱会话时将自动拉起）%b\n" "${C_GRAY}" "${SANDBOX_NAMESPACE}" "${C_RESET}"
    fi

    printf "\n"
    printf "%b✔ 状态检查完毕%b\n" "${C_GREEN}" "${C_RESET}"
    ;;

  sandboxes)
    print_header "沙箱专区监控 (Namespace: ${SANDBOX_NAMESPACE})"
    if kubectl get namespace "$SANDBOX_NAMESPACE" >/dev/null 2>&1; then
      print_section "📦" "活跃沙箱 Pod"
      kubectl get pod -n "$SANDBOX_NAMESPACE" -o wide || true

      print_section "💾" "沙箱持久卷申领 (PVC)"
      kubectl get pvc -n "$SANDBOX_NAMESPACE" -o wide || true
    else
      log_warn "命名空间 ${SANDBOX_NAMESPACE} 暂未创建，平台在首次调度 K8S 原生沙箱时会自动创建。"
    fi
    ;;

  restart-pod)
    print_header "滚动重启 NanZi 平台 Pod"
    if ! confirm_action "确定要滚动重启 NanZi 平台 Pod 吗？（会触发 rollout restart，期间短暂不可用）"; then
      exit 0
    fi
    log_info "触发 Deployment/${DEPLOYMENT} 滚动更新..."
    kubectl rollout restart deployment/"$DEPLOYMENT" -n "$NAMESPACE"

    log_info "等待新 Pod 就绪与健康检查通过..."
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=180s

    print_section "✨" "最新 Pod 运行状态"
    kubectl get pods -n "$NAMESPACE" -o wide
    log_success "NanZi Pod 滚动重启完成！"
    ;;

  restart-pod-force)
    print_header "强制重启 Pod 以加载节点上最新同名镜像"
    if ! confirm_action "将触发 rollout restart，使新 Pod 强制换到节点 containerd 中已导入的当前 Deployment 同名镜像。确定继续？"; then
      exit 0
    fi

    log_info "① 读取 Deployment 当前镜像引用..."
    current_image=$(kubectl get deployment/"$DEPLOYMENT" -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
    if [ -n "$current_image" ]; then
      log_info "Deployment 当前 image: ${current_image}"
      image_ref=$(printf '%s' "$current_image" | sed 's#^.*/##')      # 形如 nanzi-ai-agent:latest
      image_name=$(printf '%s' "$image_ref" | sed 's/:.*$//')          # 形如 nanzi-ai-agent
    else
      image_ref=""
      image_name=""
      log_warn "未能读取 Deployment/${DEPLOYMENT} 的镜像引用，跳过本地镜像探测。"
    fi

    log_info "② 探测本机容器运行时中的镜像（请先确认已完成新镜像导入覆盖）..."
    image_found=0
    if [ -n "$image_ref" ]; then
      if [ "$IS_K3S" = "1" ]; then
        if command -v k3s >/dev/null 2>&1; then
          k3s ctr images list 2>/dev/null | grep -F "$image_ref" && image_found=1 || true
        elif [ -S /run/k3s/containerd/containerd.sock ]; then
          ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images list 2>/dev/null | grep -F "$image_ref" && image_found=1 || true
        fi
      elif command -v crictl >/dev/null 2>&1; then
        crictl images --digests 2>/dev/null | grep -F "$image_name" && image_found=1 || true
      elif command -v ctr >/dev/null 2>&1; then
        ctr -n k8s.io images list 2>/dev/null | grep -F "$image_ref" && image_found=1 || true
      fi
    fi

    if [ "$image_found" = "1" ]; then
      log_success "已在本机容器运行时中找到 ${image_ref}，新 Pod 将解析到该最新镜像。"
    else
      log_warn "未在本机容器运行时中确认到 ${image_ref:-<未知>}：若尚未完成导入覆盖，新 Pod 可能 ImagePullBackOff 或仍是旧镜像。"
      if ! confirm_action "未确认到本地镜像，仍要强制重启吗？"; then
        exit 0
      fi
    fi

    log_info "③ 触发 Deployment/${DEPLOYMENT} 滚动更新（新 Pod 启动时按当前镜像引用解析节点本地最新 digest）..."
    kubectl rollout restart deployment/"$DEPLOYMENT" -n "$NAMESPACE"

    log_info "④ 等待新 Pod 就绪与健康检查通过..."
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=180s

    print_section "✨" "最新 Pod 运行状态"
    kubectl get pods -n "$NAMESPACE" -o wide
    printf "\n"
    log_success "强制重启完成！核对新 Pod 是否吃到最新镜像："
    log_info "  kubectl -n ${NAMESPACE} describe pod <新 Pod 名> | grep -A2 'Image:'"
    log_info "  将其中 Image ID 与上方本地镜像列表中的 DIGEST 对比，一致即已生效。"
    ;;

  restart-k3s)
    if [ "$IS_K3S" != "1" ]; then
      log_error "当前环境未检测到 K3s 服务（systemctl k3s），restart-k3s 仅适用于 K3s 节点。"
      log_info "标准 Kubernetes 集群请使用集群自身的控制面维护方式（如 drain 节点后重启 kubelet，或云厂商节点组滚动升级）。"
      exit 1
    fi
    print_header "重启 K3s 集群服务"
    if ! confirm_action "确定要重启底层 K3s 服务吗？（K3s 短暂不可用，会等待 API 自动恢复）"; then
      exit 0
    fi
    log_info "执行 systemctl restart k3s..."
    systemctl restart k3s

    wait_for_k3s_api

    print_section "🖥" "节点状态"
    kubectl get nodes -o wide

    print_section "🌐" "全集群 Pod 汇总 (All Namespaces)"
    kubectl get pods -A
    log_success "K3s 集群重启并自检成功！"
    ;;

  restart-all)
    if [ "$IS_K3S" != "1" ]; then
      log_error "当前环境未检测到 K3s 服务（systemctl k3s），restart-all 仅适用于 K3s 节点。"
      log_info "标准 Kubernetes 集群请使用集群自身的控制面维护方式（如 drain 节点后重启 kubelet，或云厂商节点组滚动升级）。"
      exit 1
    fi
    print_header "全量级平滑重启：K3s 守护进程 + NanZi 业务 Pod"
    if ! confirm_action "确定要执行全量重启吗？（先重启 K3s 服务，再滚动重启 NanZi 平台 Pod）"; then
      exit 0
    fi
    log_info "第 1 步：重启底层 K3s 服务..."
    systemctl restart k3s

    wait_for_k3s_api

    log_info "第 2 步：触发 NanZi Pod 滚动重启..."
    kubectl rollout restart deployment/"$DEPLOYMENT" -n "$NAMESPACE"

    log_info "等待应用 Pod 就绪..."
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=180s

    print_section "✨" "更新后的 Pod 列表"
    kubectl get pods -n "$NAMESPACE" -o wide
    log_success "K3s 与 NanZi 整体重启流程顺利完成！"
    ;;

  logs)
    print_header "NanZi 容器实时日志输出 (tail 300, -f)"
    log_info "正在追踪 ${NAMESPACE} / deployment/${DEPLOYMENT}，按 Ctrl+C 可退出..."
    printf "%b────────────────────────────────────────────────────────────────────%b\n" "${C_GRAY}" "${C_RESET}"
    kubectl logs \
      -n "$NAMESPACE" \
      deployment/"$DEPLOYMENT" \
      --all-containers=true \
      --tail=300 \
      -f
    ;;

  events)
    print_header "最近集群事件倒序汇总"
    print_section "🚀" "NanZi 平台事件 (${NAMESPACE})"
    kubectl get events -n "$NAMESPACE" --sort-by='.lastTimestamp'

    if kubectl get namespace "$SANDBOX_NAMESPACE" >/dev/null 2>&1; then
      print_section "📦" "沙箱执行事件 (${SANDBOX_NAMESPACE})"
      kubectl get events -n "$SANDBOX_NAMESPACE" --sort-by='.lastTimestamp'
    fi
    ;;

  test)
    print_header "NanZi 内部网络与 Service 连通性测试"
    print_section "🔌" "1. Service Endpoint 就绪情况"
    kubectl get endpoints "$SERVICE" -n "$NAMESPACE" -o wide

    print_section "🌐" "2. ClusterIP HTTP 探测"
    cluster_ip=$(kubectl get svc "$SERVICE" -n "$NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)

    if [ -z "$cluster_ip" ]; then
      log_error "未获取到 Service/${SERVICE} 的 ClusterIP！"
      exit 1
    fi

    log_info "目标 ClusterIP: ${C_BOLD}http://${cluster_ip}:80/${C_RESET}"
    printf "%b发起 HTTP 请求 (15s 超时)...%b\n\n" "${C_GRAY}" "${C_RESET}"

    if curl -s -S -i --max-time 15 "http://${cluster_ip}:80/"; then
      printf "\n"
      log_success "Service 端口连通正常！"
    else
      printf "\n"
      log_error "Service 连接异常或超时，请检查 Pod 是否就绪及容器日志。"
      exit 1
    fi
    ;;

  *)
    printf "\n"
    printf "%b%bNanZi AI Agent Platform - K8s / K3s 快捷运维工具%b\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"
    printf "%b用法: %s <子命令>%b\n\n" "${C_GRAY}" "$0" "${C_RESET}"
    printf "%b常用运维指令：%b\n" "${C_BOLD}" "${C_RESET}"
    printf "  %b%-13s%b %b\n" "${C_GREEN}" "status" "${C_RESET}" "查看集群节点、NanZi 资源与沙箱 Pod/PVC 状态（K3s 节点另含本机服务状态）"
    printf "  %b%-13s%b %b\n" "${C_GREEN}" "sandboxes" "${C_RESET}" "专门监控 agent-sandboxes 命名空间下的沙箱 Pod 与 PVC"
    printf "  %b%-18s%b %b\n" "${C_GREEN}" "restart-pod" "${C_RESET}" "通过 Deployment 平滑滚动重启 NanZi 业务 Pod"
    printf "  %b%-18s%b %b\n" "${C_GREEN}" "restart-pod-force" "${C_RESET}" "强制滚动重启，使新 Pod 换到节点容器运行时中最新导入的同名镜像并等待就绪"
    printf "  %b%-18s%b %b\n" "${C_GREEN}" "restart-k3s" "${C_RESET}" "重启底层 K3s 服务并等待 API Server 自动恢复（仅 K3s 环境）"
    printf "  %b%-18s%b %b\n" "${C_GREEN}" "restart-all" "${C_RESET}" "先重启 K3s 并在 API 就绪后自动滚动重启业务 Pod（仅 K3s 环境）"
    printf "  %b%-13s%b %b\n" "${C_GREEN}" "logs" "${C_RESET}" "持续追踪 NanZi Pod 最新的 300 条容器日志 (-f)"
    printf "  %b%-13s%b %b\n" "${C_GREEN}" "events" "${C_RESET}" "按时间倒序查看主平台与沙箱的 Kubernetes 调度事件"
    printf "  %b%-13s%b %b\n" "${C_GREEN}" "test" "${C_RESET}" "测试 Service Endpoint 与 ClusterIP 80 端口 HTTP 连通性"
    printf "\n"
    exit 1
    ;;
esac
