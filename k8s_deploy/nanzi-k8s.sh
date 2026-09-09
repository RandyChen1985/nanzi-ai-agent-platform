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
  log_success "K3s 服务状态: $(systemctl is-active k3s 2>/dev/null || echo 'running')"
}

# ==============================================================================
# 命令路由分发
# ==============================================================================
case "${1:-}" in
  status)
    print_header "NanZi AI Agent 平台 & K3s 集群运行状态"

    print_section "⚡" "1. K3s 系统服务状态 (systemctl)"
    systemctl status k3s --no-pager 2>/dev/null || true

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
    log_info "触发 Deployment/${DEPLOYMENT} 滚动更新..."
    kubectl rollout restart deployment/"$DEPLOYMENT" -n "$NAMESPACE"

    log_info "等待新 Pod 就绪与健康检查通过..."
    kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=180s

    print_section "✨" "最新 Pod 运行状态"
    kubectl get pods -n "$NAMESPACE" -o wide
    log_success "NanZi Pod 滚动重启完成！"
    ;;

  restart-k3s)
    print_header "重启 K3s 集群服务"
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
    print_header "全量级平滑重启：K3s 守护进程 + NanZi 业务 Pod"
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
    printf "  %-15s %b\n" "${C_GREEN}status${C_RESET}" "查看 K3s 服务、集群节点、NanZi 资源与沙箱 Pod/PVC 状态"
    printf "  %-15s %b\n" "${C_GREEN}sandboxes${C_RESET}" "专门监控 agent-sandboxes 命名空间下的沙箱 Pod 与 PVC"
    printf "  %-15s %b\n" "${C_GREEN}restart-pod${C_RESET}" "通过 Deployment 平滑滚动重启 NanZi 业务 Pod"
    printf "  %-15s %b\n" "${C_GREEN}restart-k3s${C_RESET}" "重启底层 K3s 服务并等待 API Server 自动恢复"
    printf "  %-15s %b\n" "${C_GREEN}restart-all${C_RESET}" "先重启 K3s 并在 API 就绪后自动滚动重启业务 Pod"
    printf "  %-15s %b\n" "${C_GREEN}logs${C_RESET}" "持续追踪 NanZi Pod 最新的 300 条容器日志 (-f)"
    printf "  %-15s %b\n" "${C_GREEN}events${C_RESET}" "按时间倒序查看主平台与沙箱的 Kubernetes 调度事件"
    printf "  %-15s %b\n" "${C_GREEN}test${C_RESET}" "测试 Service Endpoint 与 ClusterIP 80 端口 HTTP 连通性"
    printf "\n"
    exit 1
    ;;
esac
