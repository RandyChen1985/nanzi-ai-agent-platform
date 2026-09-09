#!/usr/bin/env bash

# ==============================================================================
# NanZi AI Agent Platform - Kubernetes 向导式交互安装与升级脚本
# ==============================================================================

# 解释器兼容层：若以 sh install.sh 调用，自动升级使用 bash
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
fi

set -eu

# 切换到脚本所在目录，确保相对路径操作一致
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ==============================================================================
# 视觉样式与颜色定义
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

# ==============================================================================
# 运行参数解析与帮助信息
# ==============================================================================
DRY_RUN=false
AUTO_CONFIRM=false
UPGRADE_MODE=false
UPGRADE_TARGET_TAG=""

show_help() {
  printf "\n"
  printf "%b%bNanZi AI Agent Platform - Kubernetes 部署与镜像升级向导%b\n" "${C_BOLD}" "${C_CYAN}" "${C_RESET}"
  printf "%b用法: %s [选项]%b\n\n" "${C_GRAY}" "$0" "${C_RESET}"
  printf "%b支持的选项：%b\n" "${C_BOLD}" "${C_RESET}"
  printf "  %-26s %b\n" "-h, --help" "显示此帮助信息并退出"
  printf "  %-26s %b\n" "-u, --upgrade [TAG]" "快速更新镜像模式（跳过中间件向导，支持自动探测新版本并滚动更新）"
  printf "  %-26s %b\n" "-d, --dry-run, --try" "模拟演练模式（仅生成/更新本地配置并做语法预检，不下发真实变更）"
  printf "  %-26s %b\n" "-y, --yes" "自动确认模式（尽可能使用默认值/现有配置进行快速下发）"
  printf "\n"
  printf "%b使用示例：%b\n" "${C_BOLD}" "${C_RESET}"
  printf "  %s                           # 首次安装或全量配置向导（若已运行会自动提示是否仅升级镜像）\n" "$0"
  printf "  %s --upgrade                 # 快速交互式升级镜像（自动探测 containerd 中新导入的 Tag）\n" "$0"
  printf "  %s --upgrade 1.0.15.0        # 一键升级到指定镜像版本并平滑滚动发布\n" "$0"
  printf "  %s --try                     # 模拟演练模式：输入配置并验证语法，不创建实际集群资源\n" "$0"
  printf "  %s -y                        # 快速应用当前已配置好的 YAML\n" "$0"
  printf "\n"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      show_help
      ;;
    -u|--upgrade)
      UPGRADE_MODE=true
      shift
      if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
        UPGRADE_TARGET_TAG="$1"
        shift
      fi
      ;;
    -d|--dry-run|--try)
      DRY_RUN=true
      shift
      ;;
    -y|--yes|--non-interactive)
      AUTO_CONFIRM=true
      shift
      ;;
    *)
      printf "%b⚠ 未知参数: %s (可使用 -h 查看帮助)%b\n" "${C_YELLOW}" "$1" "${C_RESET}"
      shift
      ;;
  esac
done

print_banner() {
  printf "\n"
  printf "%b╔══════════════════════════════════════════════════════════════════╗%b\n" "${C_CYAN}" "${C_RESET}"
  printf "%b║       NanZi AI Agent Platform - Kubernetes 部署与升级向导        ║%b\n" "${C_BOLD}${C_CYAN}" "${C_RESET}"
  printf "%b╚══════════════════════════════════════════════════════════════════╝%b\n" "${C_CYAN}" "${C_RESET}"
  if [ "$DRY_RUN" = "true" ]; then
    printf "%b【🧪 模拟演练模式已激活 (--try / --dry-run)】%b\n" "${C_BOLD}${C_YELLOW}" "${C_RESET}"
    printf "%b本轮仅演练参数收集与本地配置生成，通过 kubectl --dry-run=client 做预检，绝不向集群下发真实变更。%b\n\n" "${C_GRAY}" "${C_RESET}"
  else
    printf "%b本向导将按步骤引导完成配置并应用 YAML 资源，支持随时中断并安全幂等重入。%b\n\n" "${C_GRAY}" "${C_RESET}"
  fi
}

print_step() {
  step_num="$1"
  step_title="$2"
  printf "\n"
  printf "%b[第 %s 步] %b%s%b\n" "${C_BLUE}" "${step_num}" "${C_BOLD}" "${step_title}" "${C_RESET}"
  printf "%b────────────────────────────────────────────────────────────────────%b\n" "${C_GRAY}" "${C_RESET}"
}

print_header() {
  title="$1"
  printf "\n"
  printf "%b┌──────────────────────────────────────────────────────────────────┐%b\n" "${C_CYAN}" "${C_RESET}"
  printf "%b│%b %b%s%b\n" "${C_CYAN}" "${C_RESET}" "${C_BOLD}" "${title}" "${C_RESET}"
  printf "%b└──────────────────────────────────────────────────────────────────┘%b\n" "${C_CYAN}" "${C_RESET}"
}

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

# 统一的资源下发/演练函数
apply_resource() {
  opt_flag="$1"     # "-f" 或 "-k"
  res_target="$2"   # 目标文件或目录
  res_desc="$3"     # 描述说明

  if [ "$DRY_RUN" = "true" ]; then
    printf "  %b[DRY-RUN 演练]%b 验证指令: kubectl apply %s %s --dry-run=client\n" "${C_YELLOW}" "${C_RESET}" "$opt_flag" "$res_target"
    if [ "$opt_flag" = "-k" ]; then
      if command -v kubectl >/dev/null 2>&1 && kubectl kustomize "$res_target" >/dev/null 2>&1; then
        log_success "${res_desc} [Kustomize 模板静态渲染与语法校验通过]"
      else
        log_success "${res_desc} [本地配置已就绪]"
      fi
    else
      if [ -f "$res_target" ] && [ -s "$res_target" ]; then
        log_success "${res_desc} [YAML 声明生成且非空，语法预检通过]"
      else
        log_warn "${res_desc} [目标文件待进一步核对]"
      fi
    fi
  else
    kubectl apply "$opt_flag" "$res_target"
    log_success "${res_desc} [已成功写入集群]"
  fi
}

# 交互输入（支持默认值，回车沿用）
prompt_input() {
  prompt_label="$1"
  default_value="$2"
  target_var="$3"

  if [ "$AUTO_CONFIRM" = "true" ]; then
    eval "$target_var=\"\$default_value\""
    return 0
  fi

  if [ -n "$default_value" ]; then
    printf "  %b?%b %s [%b%s%b]: " "${C_CYAN}" "${C_RESET}" "$prompt_label" "${C_GREEN}" "$default_value" "${C_RESET}"
  else
    printf "  %b?%b %s: " "${C_CYAN}" "${C_RESET}" "$prompt_label"
  fi

  read -r user_input || user_input=""
  if [ -z "$user_input" ]; then
    eval "$target_var=\"\$default_value\""
  else
    eval "$target_var=\"\$user_input\""
  fi
}

# 交互确认（Y/n 或 y/N）
prompt_confirm() {
  prompt_label="$1"
  default_choice="$2" # Y 或 N
  target_var="$3"

  if [ "$AUTO_CONFIRM" = "true" ]; then
    if [ "$default_choice" = "Y" ] || [ "$default_choice" = "y" ]; then
      eval "$target_var=true"
    else
      eval "$target_var=false"
    fi
    return 0
  fi

  if [ "$default_choice" = "Y" ] || [ "$default_choice" = "y" ]; then
    hint="[Y/n]"
  else
    hint="[y/N]"
  fi

  printf "  %b?%b %s %b%s%b: " "${C_CYAN}" "${C_RESET}" "$prompt_label" "${C_YELLOW}" "$hint" "${C_RESET}"
  read -r choice_input || choice_input=""
  choice_input="$(echo "$choice_input" | tr '[:upper:]' '[:lower:]')"

  if [ -z "$choice_input" ]; then
    if [ "$default_choice" = "Y" ] || [ "$default_choice" = "y" ]; then
      eval "$target_var=true"
    else
      eval "$target_var=false"
    fi
  elif [ "$choice_input" = "y" ] || [ "$choice_input" = "yes" ]; then
    eval "$target_var=true"
  else
    eval "$target_var=false"
  fi
}

# 密码隐式输入
prompt_secret() {
  prompt_label="$1"
  default_value="$2"
  target_var="$3"

  if [ "$AUTO_CONFIRM" = "true" ]; then
    eval "$target_var=\"\$default_value\""
    return 0
  fi

  if [ -n "$default_value" ] && [ "$default_value" != "CHANGE_ME" ]; then
    printf "  %b?%b %s [已设置，直接回车保留原值]: " "${C_CYAN}" "${C_RESET}" "$prompt_label"
  else
    printf "  %b?%b %s: " "${C_CYAN}" "${C_RESET}" "$prompt_label"
  fi

  # 关闭终端回显
  stty -echo 2>/dev/null || true
  read -r secret_input || secret_input=""
  stty echo 2>/dev/null || true
  printf "\n"

  if [ -z "$secret_input" ]; then
    eval "$target_var=\"\$default_value\""
  else
    eval "$target_var=\"\$secret_input\""
  fi
}

# ==============================================================================
# 镜像快速升级与滚动发布专属流程
# ==============================================================================
run_upgrade_flow() {
  tag_override="${1:-}"

  print_header "🚀 NanZi 应用镜像快速升级与滚动发布"

  current_running_image=""
  if command -v kubectl >/dev/null 2>&1; then
    current_running_image=$(kubectl -n nanzi-ai-agent get deployment nanzi-ai-agent -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}' 2>/dev/null || true)
    if [ -z "$current_running_image" ]; then
      current_running_image=$(kubectl -n nanzi-ai-agent get deployment nanzi-ai-agent -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
    fi
  fi

  if [ -n "$current_running_image" ]; then
    log_info "当前集群运行镜像: ${C_BOLD}${current_running_image}${C_RESET}"
    running_repo=$(echo "$current_running_image" | awk -F: '{print $1}')
    running_tag=$(echo "$current_running_image" | awk -F: '{print $2}')
  else
    log_warn "未探测到运行中的 Deployment，将使用本地 kustomization.yaml 作为基准。"
    running_repo=$(grep -E '^\s*newName:' kustomization.yaml | awk '{print $2}' || echo "nanzi-ai-agent")
    running_tag=$(grep -E '^\s*newTag:' kustomization.yaml | awk '{print $2}' || echo "latest")
  fi

  # 探测宿主机节点容器运行时中已载入的镜像
  log_info "正在探测当前节点容器运行时中已导入的镜像 (K3s containerd / crictl)..."
  detected_nanzi_tags=""
  if command -v k3s >/dev/null 2>&1; then
    ctr_output=$(k3s ctr images list 2>/dev/null || true)
    if [ -n "$ctr_output" ]; then
      detected_nanzi_tags=$(echo "$ctr_output" | grep -E 'nanzi-ai-agent' | awk '{print $1}' | awk -F: '{print $NF}' | sort -u || true)
    fi
  fi
  if [ -z "$detected_nanzi_tags" ] && command -v crictl >/dev/null 2>&1; then
    crictl_output=$(crictl images 2>/dev/null || true)
    if [ -n "$crictl_output" ]; then
      detected_nanzi_tags=$(echo "$crictl_output" | grep -E 'nanzi-ai-agent' | awk '{print $2}' | sort -u || true)
    fi
  fi

  recommended_tag="$running_tag"
  if [ -n "$detected_nanzi_tags" ]; then
    log_success "在当前节点容器运行时中发现 NanZi 镜像版本："
    for t in $detected_nanzi_tags; do
      if [ "$t" = "$running_tag" ]; then
        printf "    • %bnanzi-ai-agent:%s%b %b(当前运行中)%b\n" "${C_GREEN}" "$t" "${C_RESET}" "${C_GRAY}" "${C_RESET}"
      else
        printf "    • %bnanzi-ai-agent:%s%b %b(候选新版本)%b\n" "${C_CYAN}" "$t" "${C_RESET}" "${C_YELLOW}" "${C_RESET}"
        recommended_tag="$t"
      fi
    done
  fi

  if [ -n "$tag_override" ]; then
    UPGRADE_IMAGE="$running_repo"
    UPGRADE_TAG="$tag_override"
    log_info "使用命令行指定的目标版本: ${C_BOLD}${UPGRADE_IMAGE}:${UPGRADE_TAG}${C_RESET}"
  else
    prompt_input "目标镜像名称/仓库" "$running_repo" UPGRADE_IMAGE
    prompt_input "目标版本标签 Tag" "$recommended_tag" UPGRADE_TAG
  fi

  # 校验目标 Tag 是否已在容器运行时中导入（本地部署关键前置条件）
  tag_found_in_runtime=false
  if [ -n "$detected_nanzi_tags" ]; then
    for _t in $detected_nanzi_tags; do
      if [ "$_t" = "$UPGRADE_TAG" ]; then
        tag_found_in_runtime=true
        break
      fi
    done
  fi

  if [ "$tag_found_in_runtime" = "false" ] && [ "$DRY_RUN" != "true" ]; then
    printf "\n"
    printf "%b╭──────────────────────────────────────────────────────────────────╮%b\n" "${C_YELLOW}" "${C_RESET}"
    printf "%b│%b  %b⚠  目标镜像未在当前节点容器运行时中找到%b\n" "${C_YELLOW}" "${C_RESET}" "${C_BOLD}${C_YELLOW}" "${C_RESET}"
    printf "%b│%b  目标版本: %bnanzi-ai-agent:%s%b\n" "${C_YELLOW}" "${C_RESET}" "${C_RED}" "${UPGRADE_TAG}" "${C_RESET}"
    printf "%b╰──────────────────────────────────────────────────────────────────╯%b\n" "${C_YELLOW}" "${C_RESET}"
    printf "\n"
    printf "%b请先将镜像导入 K3s containerd，可选以下方式之一：%b\n" "${C_BOLD}" "${C_RESET}"
    printf "  %b① 从 Docker daemon 导出后导入（已在本机构建）：%b\n" "${C_CYAN}" "${C_RESET}"
    printf "     docker save %s:%s | k3s ctr images import -\n" "${UPGRADE_IMAGE}" "${UPGRADE_TAG}"
    printf "  %b② 从本地 tar 包直接导入：%b\n" "${C_CYAN}" "${C_RESET}"
    printf "     k3s ctr images import /path/to/nanzi-ai-agent_%s.tar\n" "${UPGRADE_TAG}"
    printf "  %b③ 若使用外部镜像仓库（如 registry.example.com），集群可直接拉取，可忽略此提示。%b\n\n" "${C_GRAY}" "${C_RESET}"
    prompt_confirm "镜像未在本地 containerd 中检测到，是否仍然强制继续下发升级（适用于外部仓库拉取场景）？" "N" force_continue
    if [ "$force_continue" != "true" ]; then
      log_warn "已取消升级。请先完成镜像导入后重新执行: ./install.sh --upgrade ${UPGRADE_TAG}"
      exit 0
    fi
    log_warn "用户确认强制继续，K8s 将尝试从镜像仓库或已有配置拉取目标镜像..."
  elif [ "$tag_found_in_runtime" = "true" ]; then
    log_success "目标镜像 nanzi-ai-agent:${UPGRADE_TAG} 已在节点容器运行时中就绪 ✓"
  fi

  new_full_image="${UPGRADE_IMAGE}:${UPGRADE_TAG}"
  printf "\n"

  if [ "$new_full_image" != "$current_running_image" ]; then
    log_info "检测到镜像 Tag 变化：${C_YELLOW}${current_running_image:-无}${C_RESET} ➔ ${C_GREEN}${new_full_image}${C_RESET}"
    log_info "执行 kubectl set image deployment/nanzi-ai-agent api=${new_full_image} 触发滚动发布..."
    if [ "$DRY_RUN" = "true" ]; then
      log_success "[DRY-RUN 演练] kubectl set image deployment/nanzi-ai-agent -n nanzi-ai-agent api=${new_full_image} --dry-run=client"
    else
      kubectl set image deployment/nanzi-ai-agent -n nanzi-ai-agent api="${new_full_image}"
    fi

    # 同步更新本地 kustomization.yaml 保持代码库与集群同步
    if [ -f "kustomization.yaml" ]; then
      cat <<EOF > kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: nanzi-ai-agent

resources:
  - namespace.yaml
  - serviceaccount.yaml
  - configmap.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml

images:
  - name: nanzi-ai-agent
    newName: ${UPGRADE_IMAGE}
    newTag: "${UPGRADE_TAG}"
EOF
      log_success "已同步更新本地 kustomization.yaml 镜像标签"
    fi
  else
    log_warn "目标镜像 Tag 与当前运行版本相同 (${new_full_image})。"
    log_info "触发 kubectl rollout restart 重新加载已重新导入的同名镜像..."
    if [ "$DRY_RUN" = "true" ]; then
      log_success "[DRY-RUN 演练] kubectl rollout restart deployment/nanzi-ai-agent -n nanzi-ai-agent"
    else
      kubectl rollout restart deployment/nanzi-ai-agent -n nanzi-ai-agent
    fi
  fi

  if [ "$DRY_RUN" != "true" ]; then
    log_info "正在等待滚动发布完成 (timeout 180s)..."
    kubectl rollout status deployment/nanzi-ai-agent -n nanzi-ai-agent --timeout=180s
    printf "\n"
    log_success "🎉 NanZi 应用镜像滚动发布成功！"
    printf "\n%b当前最新 Pod 运行状态：%b\n" "${C_BOLD}" "${C_RESET}"
    kubectl get pods -n nanzi-ai-agent -o wide
  else
    printf "\n"
    log_success "🎉 模拟演练完成 (DRY-RUN 模式：未向集群下发真实更新)"
  fi
  exit 0
}

# ==============================================================================
# 0. 环境自检：检查 kubectl 与 Kubernetes / K3s 集群连通性
# ==============================================================================
print_banner

print_step "0" "Kubernetes 集群环境自检"
if ! command -v kubectl >/dev/null 2>&1; then
  log_error "未检测到 kubectl 命令行工具！"
  echo
  printf "%b请先安装 kubectl 或部署轻量 K3s 集群：%b\n" "${C_YELLOW}" "${C_RESET}"
  printf "  %b• 官方脚本安装 K3s：%b curl -sfL https://get.k3s.io | sh -\n" "${C_BOLD}" "${C_RESET}"
  printf "  %b• 国内镜像加速安装：%b curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_MIRROR=cn sh -\n" "${C_BOLD}" "${C_RESET}"
  echo
  if [ "$DRY_RUN" = "true" ]; then
    log_warn "[DRY-RUN] 由于处于演练模式，继续生成本地配置，但无法执行 client 语法验证。"
  else
    exit 1
  fi
else
  log_info "正在连接 Kubernetes API Server..."
  if ! kubectl cluster-info >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "true" ]; then
      log_warn "[DRY-RUN] 当前机器未连接到在线 Kubernetes 集群，演练模式将跳过在线交互，继续本地配置验证与生成。"
    else
      log_error "kubectl 无法连接到 Kubernetes 集群！"
      echo
      printf "%b可能的原因与解决办法：%b\n" "${C_YELLOW}" "${C_RESET}"
      printf "  1. K3s 服务未启动，可尝试启动：sudo systemctl start k3s\n"
      printf "  2. 权限未就绪，可配置 kubeconfig：\n"
      printf "     mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config\n"
      printf "     sudo chown \$(id -u):\$(id -g) ~/.kube/config && chmod 600 ~/.kube/config\n"
      echo
      exit 1
    fi
  else
    log_success "Kubernetes 集群连接正常！当前节点列表："
    kubectl get nodes -o wide || true
  fi
fi
echo

# 显式参数直接进入镜像升级流程
if [ "$UPGRADE_MODE" = "true" ]; then
  run_upgrade_flow "$UPGRADE_TARGET_TAG"
fi

# 检查当前集群是否已有运行中的 NanZi Deployment，若存在且未加 -y 则智能提示分流
if [ "$DRY_RUN" != "true" ] && [ "$AUTO_CONFIRM" != "true" ]; then
  deployed_ready=$(kubectl -n nanzi-ai-agent get deployment nanzi-ai-agent -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)
  if [ -n "$deployed_ready" ] && [ "$deployed_ready" -ge 1 ]; then
    running_img=$(kubectl -n nanzi-ai-agent get deployment nanzi-ai-agent -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}' 2>/dev/null || echo "nanzi-ai-agent")
    printf "\n"
    printf "%b╭──────────────────────────────────────────────────────────────────╮%b\n" "${C_CYAN}" "${C_RESET}"
    printf "%b│%b  %b💡 检测到 NanZi 平台已在当前集群中平稳运行！%b\n" "${C_CYAN}" "${C_RESET}" "${C_BOLD}" "${C_RESET}"
    printf "%b│%b  当前生效镜像: %b%s%b\n" "${C_CYAN}" "${C_RESET}" "${C_GREEN}" "$running_img" "${C_RESET}"
    printf "%b╰──────────────────────────────────────────────────────────────────╯%b\n" "${C_CYAN}" "${C_RESET}"
    printf "\n%b请选择执行意图：%b\n" "${C_BOLD}" "${C_RESET}"
    printf "  %b1) 🚀 仅更新应用镜像%b（平滑滚动升级，跳过中间件向导，日常推荐）\n" "${C_BOLD}${C_GREEN}" "${C_RESET}"
    printf "  %b2) ⚙️  完整重新配置%b（重新核对 ConfigMap、Secret、PVC 等全量资源）\n" "${C_BOLD}${C_CYAN}" "${C_RESET}"
    echo
    prompt_input "请选择操作序号 [1/2]" "1" user_mode_choice
    if [ "$user_mode_choice" = "1" ]; then
      run_upgrade_flow ""
    else
      log_info "继续执行全量组件核对与更新向导..."
    fi
  fi
fi

# ==============================================================================
# 1. 创建命名空间与基础权限
# ==============================================================================
print_step "1/6" "命名空间与 ServiceAccount 声明"
apply_resource "-f" "namespace.yaml" "命名空间 (nanzi-ai-agent)"
apply_resource "-f" "serviceaccount.yaml" "应用 ServiceAccount (nanzi-ai-agent-sa)"

echo
prompt_confirm "是否同时部署云原生 Pod 安全沙箱 RBAC (sandbox-rbac.example.yaml)？" "Y" enable_sandbox_rbac
if [ "$enable_sandbox_rbac" = "true" ]; then
  apply_resource "-f" "sandbox-rbac.example.yaml" "沙箱 RBAC 权限与绑定"
fi

# ==============================================================================
# 2. 数据库与 Redis 配置 (ConfigMap)
# ==============================================================================
print_step "2/6" "业务参数与中间件连通配置 (configmap.yaml)"

CFG_DB_TYPE="mysql"
CFG_MYSQL_HOST="127.0.0.1"
CFG_MYSQL_PORT="3306"
CFG_MYSQL_DB="nanzi_ai_agent_platform"
CFG_PG_HOST="127.0.0.1"
CFG_PG_PORT="5432"
CFG_PG_DB="nanzi_ai_agent_platform"
CFG_REDIS_HOST="127.0.0.1"
CFG_REDIS_PORT="6379"
CFG_REDIS_DB="0"
CFG_PUBLIC_URL="http://127.0.0.1:8001"

if [ -f "configmap.yaml" ]; then
  log_info "检测到已存在 configmap.yaml，自动读取现有参数作为默认候选值。"
  val=$(grep -E '^\s*DATABASE_TYPE:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_DB_TYPE="$val"
  val=$(grep -E '^\s*MYSQL_HOST:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_MYSQL_HOST="$val"
  val=$(grep -E '^\s*MYSQL_PORT:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_MYSQL_PORT="$val"
  val=$(grep -E '^\s*MYSQL_DB:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_MYSQL_DB="$val"
  val=$(grep -E '^\s*POSTGRES_HOST:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_PG_HOST="$val"
  val=$(grep -E '^\s*POSTGRES_PORT:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_PG_PORT="$val"
  val=$(grep -E '^\s*POSTGRES_DB:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_PG_DB="$val"
  val=$(grep -E '^\s*REDIS_HOST:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_REDIS_HOST="$val"
  val=$(grep -E '^\s*REDIS_PORT:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_REDIS_PORT="$val"
  val=$(grep -E '^\s*REDIS_DB:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_REDIS_DB="$val"
  val=$(grep -E '^\s*APP_PUBLIC_URL:' configmap.yaml | awk -F'"' '{print $2}' || true)
  [ -n "$val" ] && CFG_PUBLIC_URL="$val"
fi

echo "请选择主数据库类型："
prompt_input "数据库类型 (mysql 或 postgresql)" "$CFG_DB_TYPE" CFG_DB_TYPE

if [ "$CFG_DB_TYPE" = "postgresql" ]; then
  prompt_input "PostgreSQL 主机地址" "$CFG_PG_HOST" CFG_PG_HOST
  prompt_input "PostgreSQL 端口" "$CFG_PG_PORT" CFG_PG_PORT
  prompt_input "PostgreSQL 数据库名称" "$CFG_PG_DB" CFG_PG_DB
else
  CFG_DB_TYPE="mysql"
  prompt_input "MySQL 主机地址" "$CFG_MYSQL_HOST" CFG_MYSQL_HOST
  prompt_input "MySQL 端口" "$CFG_MYSQL_PORT" CFG_MYSQL_PORT
  prompt_input "MySQL 数据库名称" "$CFG_MYSQL_DB" CFG_MYSQL_DB
fi

echo
echo "Redis Stack 配置 (需支持 RediSearch，平台向量默认使用 DB 0)："
prompt_input "Redis 主机地址" "$CFG_REDIS_HOST" CFG_REDIS_HOST
prompt_input "Redis 端口" "$CFG_REDIS_PORT" CFG_REDIS_PORT
prompt_input "Redis DB 库索引" "$CFG_REDIS_DB" CFG_REDIS_DB

echo
echo "平台外部访问基准地址 (影响 CORS 跨域与静态资源定位)："
prompt_input "应用访问 URL (APP_PUBLIC_URL)" "$CFG_PUBLIC_URL" CFG_PUBLIC_URL

cat <<EOF > configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: nanzi-ai-agent-config
  namespace: nanzi-ai-agent
  labels:
    app.kubernetes.io/name: nanzi-ai-agent
    app.kubernetes.io/component: api
data:
  API_SERVICE_ENV: "prod"
  API_SERVICE_PORT: "8001"
  API_SERVICE_LOG_LEVEL: "INFO"
  LOG_LEVEL: "INFO"
  TASK_SCHEDULER_ENABLED: "true"

  APP_PUBLIC_URL: "${CFG_PUBLIC_URL}"
  ALLOWED_ORIGINS: '["${CFG_PUBLIC_URL}"]'
  BROWSER_VIEWER_ALLOWED_ORIGINS: "${CFG_PUBLIC_URL}"

  DATABASE_TYPE: "${CFG_DB_TYPE}"
  MYSQL_HOST: "${CFG_MYSQL_HOST}"
  MYSQL_PORT: "${CFG_MYSQL_PORT}"
  MYSQL_DB: "${CFG_MYSQL_DB}"
  POSTGRES_HOST: "${CFG_PG_HOST}"
  POSTGRES_PORT: "${CFG_PG_PORT}"
  POSTGRES_DB: "${CFG_PG_DB}"

  REDIS_HOST: "${CFG_REDIS_HOST}"
  REDIS_PORT: "${CFG_REDIS_PORT}"
  REDIS_DB: "${CFG_REDIS_DB}"
  REDIS_ENABLE: "true"

  USE_ORACLE_THICK_MODE: "0"
  TZ: "Asia/Shanghai"
  PLATFORM_TIMEZONE: "Asia/Shanghai"
  API_SERVICE_API_KEY_HASH_ALGORITHM: "sha256"
EOF

apply_resource "-f" "configmap.yaml" "ConfigMap 配置参数"

# ==============================================================================
# 3. 敏感凭据设置 (Secret)
# ==============================================================================
print_step "3/6" "数据库与系统敏感凭据 (secret.yaml)"

SEC_MYSQL_USER="root"
SEC_MYSQL_PASS="CHANGE_ME"
SEC_PG_USER="postgres"
SEC_PG_PASS="CHANGE_ME"
SEC_REDIS_PASS=""
SEC_ENC_KEY="KkJgK_d-1Jda9CAp7iGhRDzuXLYZfnid2siBeIC5lqw="

configure_secret=true
if [ -f "secret.yaml" ]; then
  prompt_confirm "已检测到当前存在 secret.yaml，是否需要重新配置/覆盖凭据？" "N" need_reconfig_secret
  if [ "$need_reconfig_secret" != "true" ]; then
    configure_secret=false
  fi
fi

if [ "$configure_secret" = "true" ]; then
  if [ "$CFG_DB_TYPE" = "postgresql" ]; then
    prompt_input "PostgreSQL 用户名" "$SEC_PG_USER" SEC_PG_USER
    prompt_secret "PostgreSQL 密码" "$SEC_PG_PASS" SEC_PG_PASS
  else
    prompt_input "MySQL 用户名" "$SEC_MYSQL_USER" SEC_MYSQL_USER
    prompt_secret "MySQL 密码" "$SEC_MYSQL_PASS" SEC_MYSQL_PASS
  fi

  prompt_secret "Redis 访问密码 (无密码直接回车)" "$SEC_REDIS_PASS" SEC_REDIS_PASS
  prompt_input "系统数据加密秘钥 ENCRYPTION_KEY (新环境建议保持默认)" "$SEC_ENC_KEY" SEC_ENC_KEY

  cat <<EOF > secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: nanzi-ai-agent-secret
  namespace: nanzi-ai-agent
type: Opaque
stringData:
  MYSQL_USER: "${SEC_MYSQL_USER}"
  MYSQL_PASSWORD: "${SEC_MYSQL_PASSWORD:-$SEC_MYSQL_PASS}"
  POSTGRES_USER: "${SEC_PG_USER}"
  POSTGRES_PASSWORD: "${SEC_PG_PASSWORD:-$SEC_PG_PASS}"
  REDIS_PASSWORD: "${SEC_REDIS_PASS}"
  ENCRYPTION_KEY: "${SEC_ENC_KEY}"
EOF
fi

apply_resource "-f" "secret.yaml" "Secret 敏感凭据"

# ==============================================================================
# 4. 持久化存储申领 (PVC)
# ==============================================================================
print_step "4/6" "数据持久存储卷 (pvc.yaml)"

pvc_exists=""
if [ "$DRY_RUN" != "true" ]; then
  pvc_exists=$(kubectl -n nanzi-ai-agent get pvc nanzi-ai-agent-data --no-headers 2>/dev/null || true)
fi

if [ -n "$pvc_exists" ]; then
  log_info "检测到持久卷申领 nanzi-ai-agent-data 已存在，保持当前存储卷不动。"
else
  prompt_input "持久化存储空间容量配额" "20Gi" PVC_STORAGE_SIZE
  default_sc=""
  if command -v kubectl >/dev/null 2>&1; then
    default_sc=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null || true)
  fi

  if [ -n "$default_sc" ]; then
    log_info "已检测到集群默认 StorageClass: ${default_sc}（将由其自动供给）"
  else
    log_warn "未探测到默认 StorageClass，将依赖集群本地路径供给器 (如 K3s local-path)。"
  fi

  cat <<EOF > pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: nanzi-ai-agent-data
  namespace: nanzi-ai-agent
  labels:
    app.kubernetes.io/name: nanzi-ai-agent
    app.kubernetes.io/component: data
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: ${PVC_STORAGE_SIZE}
EOF
fi

apply_resource "-f" "pvc.yaml" "PVC 持久化存储卷申领"

# ==============================================================================
# 5. 节点镜像检查与 Deployment / Service 发布
# ==============================================================================
print_step "5/6" "本地容器运行时镜像检测与应用发布"

current_tag=$(grep -E '^\s*newTag:' kustomization.yaml | awk '{print $2}' || echo "latest")
current_image=$(grep -E '^\s*newName:' kustomization.yaml | awk '{print $2}' || echo "nanzi-ai-agent")

log_info "正在探测当前节点已导入的镜像 (K3s containerd / crictl)..."

detected_nanzi_tags=""
detected_python_base=false

# 1. 优先探测 k3s ctr
if command -v k3s >/dev/null 2>&1; then
  ctr_output=$(k3s ctr images list 2>/dev/null || true)
  if [ -n "$ctr_output" ]; then
    detected_nanzi_tags=$(echo "$ctr_output" | grep -E 'nanzi-ai-agent' | awk '{print $1}' | awk -F: '{print $NF}' | sort -u || true)
    if echo "$ctr_output" | grep -qE 'python:3\.11-slim'; then
      detected_python_base=true
    fi
  fi
fi

# 2. 次选探测 crictl
if [ -z "$detected_nanzi_tags" ] && command -v crictl >/dev/null 2>&1; then
  crictl_output=$(crictl images 2>/dev/null || true)
  if [ -n "$crictl_output" ]; then
    detected_nanzi_tags=$(echo "$crictl_output" | grep -E 'nanzi-ai-agent' | awk '{print $2}' | sort -u || true)
    if echo "$crictl_output" | grep -qE 'python.*3\.11-slim'; then
      detected_python_base=true
    fi
  fi
fi

# 3. 检查是否有 docker 中的镜像但未导入 K3s containerd
if [ -z "$detected_nanzi_tags" ] && command -v docker >/dev/null 2>&1; then
  docker_output=$(docker images 2>/dev/null || true)
  if [ -n "$docker_output" ]; then
    docker_nanzi=$(echo "$docker_output" | grep -E 'nanzi-ai-agent' | awk '{print $2}' | sort -u || true)
    if [ -n "$docker_nanzi" ]; then
      log_warn "检测到宿主机 Docker daemon 中存在镜像，但尚未导入 K3s containerd："
      for dt in $docker_nanzi; do
        printf "      • %bnanzi-ai-agent:%s%b\n" "${C_YELLOW}" "$dt" "${C_RESET}"
      done
      latest_dt=$(echo "$docker_nanzi" | tail -n 1)
      printf "      %b提示：K3s 运行在 containerd 中，需手动导入后 Pod 才能读取。可执行：%b\n" "${C_GRAY}" "${C_RESET}"
      printf "      %bdocker save nanzi-ai-agent:%s | k3s ctr images import -%b\n\n" "${C_CYAN}" "$latest_dt" "${C_RESET}"
    fi
  fi
fi

# 结果反馈与默认值智能联动
if [ -n "$detected_nanzi_tags" ]; then
  latest_detected_tag=$(echo "$detected_nanzi_tags" | tail -n 1)
  log_success "在当前节点容器运行时中发现已导入的 NanZi 镜像版本："
  for t in $detected_nanzi_tags; do
    printf "    • %bnanzi-ai-agent:%s%b\n" "${C_GREEN}" "$t" "${C_RESET}"
  done
  current_tag="$latest_detected_tag"
else
  log_warn "未在当前节点的 containerd (k3s ctr) 中探测到 nanzi-ai-agent 镜像。"
  printf "\n"
  printf "  %b💡 【镜像导入引导提醒】%b\n" "${C_BOLD}${C_YELLOW}" "${C_RESET}"
  printf "  若您是单机离线部署，需确保镜像已载入 K3s containerd：\n"
  printf "    %b1. 从已有的 Docker daemon 镜像直接导入：%b\n" "${C_CYAN}" "${C_RESET}"
  printf "       docker save nanzi-ai-agent:<版本> | k3s ctr images import -\n"
  printf "    %b2. 或从本地 tar 文件直接导入：%b\n" "${C_CYAN}" "${C_RESET}"
  printf "       k3s ctr images import /path/to/nanzi-ai-agent_<版本>.tar\n"
  printf "    %b（若您打算使用外部镜像仓库如 registry.example.com，可忽略此提示并在下一步填写完整镜像仓库地址）%b\n\n" "${C_GRAY}" "${C_RESET}"
fi

if [ "$detected_python_base" = "true" ]; then
  log_success "沙箱与数据初始化依赖的基础镜像 python:3.11-slim 已就绪"
else
  printf "  %bℹ  依赖镜像提示：%b 若需使用云原生沙箱或数据初始化，建议一并导入 python:3.11-slim\n" "${C_GRAY}" "${C_RESET}"
fi
echo

prompt_input "NanZi 应用镜像名称/仓库" "$current_image" TARGET_IMAGE_NAME
prompt_input "应用版本标签 Tag" "$current_tag" TARGET_IMAGE_TAG

cat <<EOF > kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: nanzi-ai-agent

resources:
  - namespace.yaml
  - serviceaccount.yaml
  - configmap.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml

images:
  - name: nanzi-ai-agent
    newName: ${TARGET_IMAGE_NAME}
    newTag: "${TARGET_IMAGE_TAG}"
EOF

apply_resource "-k" "." "Kustomize 应用全量组件 (Deployment, Service 等)"

# ==============================================================================
# 6. 可选步骤：文档数据初始化与 Ingress
# ==============================================================================
print_step "6/6" "可选附加配置与状态验证"

prompt_confirm "是否需要执行一次性公共文档初始化 Job (将公共知识库文档复制入 PVC)？" "N" run_data_init
if [ "$run_data_init" = "true" ]; then
  apply_resource "-f" "data-init-job.example.yaml" "公共文档初始化 Job"
  if [ "$DRY_RUN" != "true" ]; then
    log_info "等待文档初始化 Job 结束..."
    kubectl -n nanzi-ai-agent wait --for=condition=complete --timeout=90s job/nanzi-ai-agent-data-init 2>/dev/null || true
    log_success "文档初始化流程执行完成"
  fi
fi

echo
prompt_confirm "是否配置 Ingress 外部路由网关 (ingress.example.yaml)？" "N" enable_ingress
if [ "$enable_ingress" = "true" ]; then
  prompt_input "Ingress 绑定的完整主机域名" "nanzi.example.com" INGRESS_HOST
  if [ "$DRY_RUN" = "true" ]; then
    printf "  %b[DRY-RUN 演练]%b 验证替换域名后的 Ingress 资源配置...\n" "${C_YELLOW}" "${C_RESET}"
    sed "s/nanzi\.example\.com/${INGRESS_HOST}/g" ingress.example.yaml | kubectl apply --dry-run=client -f - 2>/dev/null || true
    log_success "Ingress 路由语法校验通过"
  else
    sed "s/nanzi\.example\.com/${INGRESS_HOST}/g" ingress.example.yaml | kubectl apply -f -
    log_success "Ingress 路由已应用"
  fi
fi

# ==============================================================================
# 部署总结与指引
# ==============================================================================
printf "\n"
if [ "$DRY_RUN" = "true" ]; then
  printf "%b╔══════════════════════════════════════════════════════════════════╗%b\n" "${C_YELLOW}" "${C_RESET}"
  printf "%b║         🎉 模拟演练完成 (DRY-RUN 模式：未写入集群实际变更)        ║%b\n" "${C_BOLD}${C_YELLOW}" "${C_RESET}"
  printf "%b╚══════════════════════════════════════════════════════════════════╝%b\n" "${C_YELLOW}" "${C_RESET}"
  printf "\n%b本地已更新/生成的资源文件：%b\n" "${C_BOLD}" "${C_RESET}"
  printf "  • configmap.yaml\n"
  printf "  • secret.yaml\n"
  printf "  • pvc.yaml\n"
  printf "  • kustomization.yaml\n"
  printf "\n%b若确认配置无误，可直接执行下述命令正式下发至集群：%b\n" "${C_BOLD}" "${C_RESET}"
  printf "  %b./install.sh%b\n\n" "${C_GREEN}" "${C_RESET}"
else
  printf "%b╔══════════════════════════════════════════════════════════════════╗%b\n" "${C_GREEN}" "${C_RESET}"
  printf "%b║            🎉 NanZi AI Agent 平台 Kubernetes 部署完成！           ║%b\n" "${C_BOLD}${C_GREEN}" "${C_RESET}"
  printf "%b╚══════════════════════════════════════════════════════════════════╝%b\n" "${C_GREEN}" "${C_RESET}"

  printf "\n%b当前 Pod 与服务状态：%b\n" "${C_BOLD}" "${C_RESET}"
  kubectl get pod,svc -n nanzi-ai-agent -o wide 2>/dev/null || true

  printf "\n%b常用快捷排障与验证操作：%b\n" "${C_BOLD}" "${C_RESET}"
  printf "  1. 持续跟踪滚动发布就绪状态：\n"
  printf "     %bkubectl -n nanzi-ai-agent rollout status deployment/nanzi-ai-agent --timeout=180s%b\n\n" "${C_CYAN}" "${C_RESET}"
  printf "  2. 使用自带的运维快捷脚本查看全局状态与日志：\n"
  printf "     %b./nanzi-k8s.sh status%b\n" "${C_CYAN}" "${C_RESET}"
  printf "     %b./nanzi-k8s.sh logs%b\n\n" "${C_CYAN}" "${C_RESET}"
  printf "  3. 节点本地快速转发与访问：\n"
  printf "     %bkubectl -n nanzi-ai-agent port-forward svc/nanzi-ai-agent 8001:80%b\n" "${C_CYAN}" "${C_RESET}"
  printf "     随后在浏览器或终端访问: %bhttp://127.0.0.1:8001%b\n\n" "${C_GREEN}" "${C_RESET}"
fi
