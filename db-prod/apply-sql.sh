#!/bin/bash
# Wrapper to run apply_sql.py with explicit connection parameters.

# 确保脚本在非 bash 环境下（如使用 sh 执行时）能够自动重新唤起并用 bash 执行
if [ -z "$BASH_VERSION" ]; then
    if command -v bash >/dev/null 2>&1; then
        exec bash "$0" "$@"
    else
        echo "❌ 本脚本需要 bash 支持，但系统未找到 bash。"
        exit 1
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CALLER_DIR="$PWD"
cd "$ROOT_DIR"


# 终端色彩与视觉样式配置 (在有终端交互时启用绚丽高亮，非 tty 管道静默回退)
if [ -t 1 ] || [ -n "${FORCE_COLOR:-}" ]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_RED=$'\033[38;5;196m'
    C_GREEN=$'\033[38;5;46m'
    C_YELLOW=$'\033[38;5;220m'
    C_BLUE=$'\033[38;5;39m'
    C_MAGENTA=$'\033[38;5;207m'
    C_CYAN=$'\033[38;5;51m'
    C_WHITE=$'\033[1;37m'
    C_GRAY=$'\033[38;5;245m'
    C_BG_BLUE=$'\033[48;5;24;37m'
    C_BG_GREEN=$'\033[48;5;28;37m'
    C_BG_YELLOW=$'\033[48;5;136;30m'
    C_BG_RED=$'\033[48;5;160;37m'
else
    C_RESET=""
    C_BOLD=""
    C_DIM=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_BLUE=""
    C_MAGENTA=""
    C_CYAN=""
    C_WHITE=""
    C_GRAY=""
    C_BG_BLUE=""
    C_BG_GREEN=""
    C_BG_YELLOW=""
    C_BG_RED=""
fi

# 自动优先探测并激活虚拟环境，寻找 Python 解释器
PYTHON_BIN=""
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -f "$ROOT_DIR/venv/bin/activate" ]; then
    source "$ROOT_DIR/venv/bin/activate"
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo -e "${C_RED}❌ 未检测到 Python 运行环境！${C_RESET}"
    echo -e "${C_YELLOW}💡 请先安装 Python 3.11 并配置 PATH，或初始化项目虚拟环境：${C_RESET}"
    echo "   python3 -m venv .venv"
    echo "   source .venv/bin/activate"
    echo "   pip install -r requirements.txt"
    if [ -t 0 ] && [ -t 1 ] && command -v python3 >/dev/null 2>&1; then
        read -r -p "💡 检测到系统存在 python3，是否自动创建 .venv 并安装依赖？[y/N]: " auto_init
        if [[ "$auto_init" =~ ^[Yy]$ ]]; then
            echo "⚙️ 正在创建虚拟环境 $ROOT_DIR/.venv ..."
            python3 -m venv "$ROOT_DIR/.venv"
            source "$ROOT_DIR/.venv/bin/activate"
            PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
            echo "📦 正在安装依赖..."
            "$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt"
        else
            exit 1
        fi
    else
        exit 1
    fi
fi

# 前置依赖检查：在提示输入数据库连接信息前检测必要依赖
if ! "$PYTHON_BIN" -c "import aiomysql" >/dev/null 2>&1; then
    CURRENT_PY=$("$PYTHON_BIN" -c "import sys; print(sys.executable)" 2>/dev/null || echo "$PYTHON_BIN")
    echo -e "${C_RED}❌ Python 环境依赖检查失败：未检测到 'aiomysql' 模块。${C_RESET}"
    echo -e "🔍 当前使用的 Python 解释器: ${C_CYAN}$CURRENT_PY${C_RESET}"
    echo -e "${C_YELLOW}💡 请按以下步骤解决：${C_RESET}"
    echo "   1. 激活已安装依赖的项目虚拟环境（推荐）："
    echo "      source .venv/bin/activate   # 或 source venv/bin/activate"
    echo "      pip install -r requirements.txt"
    echo "   2. 或者在当前 Python 环境中单独安装："
    echo "      $PYTHON_BIN -m pip install aiomysql"
    if [ -t 0 ] && [ -t 1 ]; then
        read -r -p "💡 是否立即自动为您安装 'aiomysql'？[y/N]: " auto_install
        if [[ "$auto_install" =~ ^[Yy]$ ]]; then
            echo "📦 正在执行: $PYTHON_BIN -m pip install aiomysql ..."
            if "$PYTHON_BIN" -m pip install aiomysql; then
                echo -e "${C_GREEN}✓ 'aiomysql' 依赖安装成功！${C_RESET}"
            else
                echo -e "${C_RED}❌ 安装失败，请检查网络或权限后手动安装。${C_RESET}"
                exit 1
            fi
        else
            exit 1
        fi
    else
        exit 1
    fi
fi

CURRENT_PY=$("$PYTHON_BIN" -c "import sys; print(sys.executable)" < /dev/null 2>/dev/null || echo "$PYTHON_BIN")
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" < /dev/null 2>/dev/null || echo "3.x")

# 环境校验通过提示 (若直接传 -h/--help 则不输出环境信息以保持帮助文档纯净)
if [[ "$*" != *"--help"* ]] && [[ "$*" != *"-h"* ]]; then
    echo -e "${C_GREEN}✓ 运行环境校验通过${C_RESET}: Python ${C_BOLD}${PY_VER}${C_RESET} (${C_GRAY}${CURRENT_PY}${C_RESET}) | 依赖库: ${C_CYAN}aiomysql${C_RESET} ${C_GREEN}[已就绪]${C_RESET}"
fi

DB_DIR="db-prod"
LAST_RECORD_FILE="$SCRIPT_DIR/.last_applied_sql"
RECORDED_LAST_FILE=""
if [ -f "$LAST_RECORD_FILE" ]; then
    RECORDED_LAST_FILE=$(head -n 1 "$LAST_RECORD_FILE" 2>/dev/null | tr -d '\r\n ')
fi

show_help() {
    cat << EOF
${C_CYAN}╭──────────────────────────────────────────────────────────────────────╮${C_RESET}
${C_CYAN}│${C_RESET}  ${C_BOLD}${C_WHITE}NanZi AI Agent Platform - MySQL 数据库迁移执行工具 (apply-sql.sh)${C_RESET}    ${C_CYAN}│${C_RESET}
${C_CYAN}╰──────────────────────────────────────────────────────────────────────╯${C_RESET}

${C_BOLD}${C_YELLOW}用法:${C_RESET}
  ./db-prod/apply-sql.sh [选项] [SQL文件...]

${C_BOLD}${C_YELLOW}选项说明:${C_RESET}
  ${C_GREEN}-h, --help${C_RESET}               显示本帮助信息并退出
  ${C_GREEN}-a, --all${C_RESET}                重跑/执行所有迁移脚本 (${C_GRAY}db-prod/V*.sql${C_RESET})
  ${C_GREEN}-l, --last${C_RESET}               从上次执行记录的脚本位置开始继续执行
                           (${C_GRAY}读取 db-prod/.last_applied_sql 记录${C_RESET})
  ${C_GREEN}-s, --spec <范围或名称>${C_RESET}  指定脚本执行范围或名称
                           - ${C_BOLD}版本范围${C_RESET} (格式 vX-vY, VX-VY, X-Y):
                             示例: ${C_CYAN}--spec v1-v31${C_RESET} 或 ${C_CYAN}--spec 1-31${C_RESET}
                           - ${C_BOLD}单个版本${C_RESET} (格式 vX, VX, X):
                             示例: ${C_CYAN}--spec v155${C_RESET} 或 ${C_CYAN}--spec 155${C_RESET}
                           - ${C_BOLD}具体文件名${C_RESET}:
                             示例: ${C_CYAN}--spec V155-add_metadata_quality_score.sql${C_RESET}
                           - ${C_BOLD}逗号组合${C_RESET}:
                             示例: ${C_CYAN}--spec v1-v5,v10-v15,v155${C_RESET}
  ${C_GREEN}[SQL文件...]${C_RESET}             直接传入一个或多个具体的 .sql 文件路径
                           示例: ${C_CYAN}./db-prod/apply-sql.sh db-prod/V155-add_metadata_quality_score.sql${C_RESET}

${C_BOLD}${C_YELLOW}交互模式:${C_RESET}
  若未指定任何参数直接运行，将默认先打印此帮助说明，并引导您选择：
    ${C_GREEN}[1] all${C_RESET}  : 重跑/执行所有迁移脚本
    ${C_YELLOW}[2] spec${C_RESET} : 指定版本范围或脚本名称
    ${C_BLUE}[3] last${C_RESET} : 从上次记录的位置继续执行 (若存在历史记录)
  选择完毕并确认目标数据库信息后，必须输入 ${C_BOLD}${C_RED}YES${C_RESET} 才会正式执行。
${C_CYAN}──────────────────────────────────────────────────────────────────────${C_RESET}
EOF
}

# 解析 spec 表达式并从 db-prod 匹配出对应的 SQL 文件
resolve_spec() {
    local spec="$1"
    local matched_files=()
    IFS="," read -ra PARTS <<< "$spec"
    for part in "${PARTS[@]}"; do
        part=$(echo "$part" | tr -d " ")
        [ -z "$part" ] && continue
        # 范围格式：如 v1-v31, V1-V31, 1-31, v1~v31, v1..v31
        if [[ "$part" =~ ^[vV]?([0-9]+)[-~.]+[vV]?([0-9]+)$ ]]; then
            local start_ver=${BASH_REMATCH[1]}
            local end_ver=${BASH_REMATCH[2]}
            if [ "$start_ver" -gt "$end_ver" ]; then
                local tmp=$start_ver; start_ver=$end_ver; end_ver=$tmp
            fi
            for f in $(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V); do
                local fname=$(basename "$f")
                if [[ "$fname" =~ ^V([0-9]+) ]]; then
                    local ver=${BASH_REMATCH[1]}
                    if (( 10#$ver >= 10#$start_ver && 10#$ver <= 10#$end_ver )); then
                        matched_files+=("$f")
                    fi
                fi
            done
        # 单个版本号格式：如 v155, V155, 155
        elif [[ "$part" =~ ^[vV]?([0-9]+)$ ]]; then
            local ver=${BASH_REMATCH[1]}
            for f in $(ls "$DB_DIR"/V${ver}-*.sql "$DB_DIR"/V${ver}_*.sql "$DB_DIR"/V${ver}.sql 2>/dev/null | sort -V); do
                [ -f "$f" ] && matched_files+=("$f")
            done
        # 完整路径或相对路径
        elif [ -f "$part" ]; then
            matched_files+=("$part")
        elif [ -f "$DB_DIR/$part" ]; then
            matched_files+=("$DB_DIR/$part")
        elif [ -f "$ROOT_DIR/$part" ]; then
            matched_files+=("$ROOT_DIR/$part")
        elif [ -f "$CALLER_DIR/$part" ]; then
            matched_files+=("$CALLER_DIR/$part")
        else
            # 模糊匹配文件名
            local found=false
            for f in $(ls "$DB_DIR"/*"$part"* 2>/dev/null | sort -V); do
                if [ -f "$f" ]; then
                    matched_files+=("$f")
                    found=true
                fi
            done
            if [ "$found" = false ]; then
                echo "⚠️ 未找到匹配 [$part] 的 SQL 脚本" >&2
            fi
        fi
    done

    if [ ${#matched_files[@]} -gt 0 ]; then
        printf "%s\n" "${matched_files[@]}" | awk '!seen[$0]++'
    fi
}

MODE=""
SPEC_ARG=""
SQL_FILES=()

# 命令行参数解析
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -a|--all)
            MODE="ALL"
            shift
            ;;
        -l|--last)
            MODE="LAST"
            shift
            ;;
        -s|--spec)
            MODE="SPEC"
            if [ -n "$2" ] && [[ ! "$2" =~ ^- ]]; then
                SPEC_ARG="$2"
                shift 2
            else
                echo -e "${C_RED}❌ 选项 $1 缺少参数 (例如: --spec v1-v31)${C_RESET}"
                exit 1
            fi
            ;;
        --spec=*)
            MODE="SPEC"
            SPEC_ARG="${1#*=}"
            shift
            ;;
        -s=*)
            MODE="SPEC"
            SPEC_ARG="${1#*=}"
            shift
            ;;
        -*)
            echo -e "${C_RED}❌ 未知选项: $1${C_RESET}"
            echo -e "${C_YELLOW}💡 请使用 --help 查看使用帮助${C_RESET}"
            exit 1
            ;;
        *)
            sql_file="$1"
            if [[ "$sql_file" = /* ]]; then
                resolved_sql_file="$sql_file"
            elif [ -f "$CALLER_DIR/$sql_file" ]; then
                resolved_sql_file="$CALLER_DIR/$sql_file"
            elif [ -f "$ROOT_DIR/$sql_file" ]; then
                resolved_sql_file="$ROOT_DIR/$sql_file"
            elif [ -f "$SCRIPT_DIR/$sql_file" ]; then
                resolved_sql_file="$SCRIPT_DIR/$sql_file"
            else
                resolved_sql_file="$sql_file"
            fi
            SQL_FILES+=("$resolved_sql_file")
            shift
            ;;
    esac
done

# 无参数输入时：先打印帮助，再引导用户交互选择
if [ -z "$MODE" ] && [ ${#SQL_FILES[@]} -eq 0 ]; then
    show_help
    echo ""
    echo -e "${C_BOLD}${C_CYAN}╭── 💡 检测到未传入参数，请选择要执行的迁移模式 ────────────────────────────╮${C_RESET}"
    echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_GREEN}[1] all${C_RESET}   - 执行/重跑所有迁移脚本 (${C_GRAY}db-prod/V*.sql${C_RESET})"
    echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_YELLOW}[2] spec${C_RESET}  - 指定脚本范围或名称 (例如: ${C_CYAN}v1-v31${C_RESET}, ${C_CYAN}V155${C_RESET}, 或具体文件名)"
    if [ -n "$RECORDED_LAST_FILE" ]; then
        echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_BLUE}[3] last${C_RESET}  - 从上次记录断点继续 (上次: ${C_MAGENTA}$RECORDED_LAST_FILE${C_RESET})"
        echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_RED}[q] exit${C_RESET}  - 取消并退出"
        echo -e "${C_CYAN}╰────────────────────────────────────────────────────────────────────╯${C_RESET}"
        read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}请选择模式 [1/2/3/q] (默认 1): ${C_RESET}")" INTERACTIVE_CHOICE
    else
        echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_RED}[q] exit${C_RESET}  - 取消并退出"
        echo -e "${C_CYAN}╰────────────────────────────────────────────────────────────────────╯${C_RESET}"
        read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}请选择模式 [1/2/q] (默认 1): ${C_RESET}")" INTERACTIVE_CHOICE
    fi
    INTERACTIVE_CHOICE=$(echo "$INTERACTIVE_CHOICE" | tr '[:upper:]' '[:lower:]' | tr -d ' ')
    INTERACTIVE_CHOICE=${INTERACTIVE_CHOICE:-1}

    case "$INTERACTIVE_CHOICE" in
        1|all)
            MODE="ALL"
            ;;
        2|spec)
            MODE="SPEC"
            read -r -p "$(echo -e "${C_BOLD}${C_CYAN}请输入脚本范围或名称 (例如: v1-v31, V155, V155-xxx.sql): ${C_RESET}")" SPEC_ARG
            if [ -z "$SPEC_ARG" ]; then
                echo -e "${C_RED}❌ 输入为空，已取消执行。${C_RESET}"
                exit 1
            fi
            ;;
        3|last)
            if [ -z "$RECORDED_LAST_FILE" ]; then
                echo -e "${C_RED}❌ 未检测到上次执行记录文件 ($LAST_RECORD_FILE)。${C_RESET}"
                exit 1
            fi
            MODE="LAST"
            ;;
        q|quit|exit)
            echo -e "${C_GRAY}💡 已取消执行。${C_RESET}"
            exit 0
            ;;
        *)
            echo -e "${C_RED}❌ 无效的选择: $INTERACTIVE_CHOICE${C_RESET}"
            exit 1
            ;;
    esac
fi

# 收集待执行的 SQL 脚本文件列表
FINAL_SQL_FILES=()
if [ "$MODE" = "ALL" ]; then
    if [ ! -d "$DB_DIR" ]; then
        echo -e "${C_RED}❌ Directory $DB_DIR not found!${C_RESET}"
        exit 1
    fi
    for f in $(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V); do
        FINAL_SQL_FILES+=("$f")
    done
elif [ "$MODE" = "SPEC" ]; then
    while IFS= read -r f; do
        [ -n "$f" ] && FINAL_SQL_FILES+=("$f")
    done < <(resolve_spec "$SPEC_ARG")
elif [ "$MODE" = "LAST" ]; then
    if [ -z "$RECORDED_LAST_FILE" ]; then
        echo -e "${C_RED}❌ 未检测到上次执行记录文件 ($LAST_RECORD_FILE)！${C_RESET}"
        echo -e "${C_YELLOW}💡 请先使用 --all 或 --spec 执行迁移脚本，系统将自动记录进度。${C_RESET}"
        exit 1
    fi
    echo -e "🔍 检测到上次记录的 SQL 脚本为: ${C_MAGENTA}$RECORDED_LAST_FILE${C_RESET}"
    local_found=false
    for f in $(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V); do
        fname=$(basename "$f")
        if [ "$fname" = "$RECORDED_LAST_FILE" ]; then
            local_found=true
        fi
        if [ "$local_found" = true ]; then
            FINAL_SQL_FILES+=("$f")
        fi
    done
    if [ "$local_found" = false ]; then
        if [[ "$RECORDED_LAST_FILE" =~ ^V([0-9]+) ]]; then
            rec_ver=${BASH_REMATCH[1]}
            for f in $(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V); do
                fname=$(basename "$f")
                if [[ "$fname" =~ ^V([0-9]+) ]]; then
                    ver=${BASH_REMATCH[1]}
                    if (( 10#$ver >= 10#$rec_ver )); then
                        FINAL_SQL_FILES+=("$f")
                        local_found=true
                    fi
                fi
            done
        fi
    fi
    if [ "$local_found" = false ]; then
        echo -e "${C_YELLOW}⚠️ 在 $DB_DIR/ 中未找到与上次记录 [$RECORDED_LAST_FILE] 匹配的脚本文件！${C_RESET}"
        exit 1
    fi
elif [ ${#SQL_FILES[@]} -gt 0 ]; then
    for f in "${SQL_FILES[@]}"; do
        FINAL_SQL_FILES+=("$f")
    done
fi

TOTAL_COUNT=${#FINAL_SQL_FILES[@]}
if [ "$TOTAL_COUNT" -eq 0 ]; then
    echo -e "${C_RED}❌ 未找到任何匹配的 SQL 脚本文件！${C_RESET}"
    exit 1
fi

# 打印本次选中的脚本清单概要
echo -e "${C_CYAN}╭────────────────────────────────────────────────────────────────────╮${C_RESET}"
echo -e "${C_CYAN}│${C_RESET} 📋 ${C_BOLD}${C_CYAN}本次选中的 SQL 迁移脚本 (共 $TOTAL_COUNT 个):${C_RESET}"
if [ "$TOTAL_COUNT" -le 12 ]; then
    for f in "${FINAL_SQL_FILES[@]}"; do
        echo -e "${C_CYAN}│${C_RESET}   ${C_CYAN}•${C_RESET} ${C_WHITE}$f${C_RESET}"
    done
else
    for ((i=0; i<5; i++)); do
        echo -e "${C_CYAN}│${C_RESET}   ${C_CYAN}•${C_RESET} ${C_WHITE}${FINAL_SQL_FILES[i]}${C_RESET}"
    done
    echo -e "${C_CYAN}│${C_RESET}   ${C_GRAY}... (中间省略 $((TOTAL_COUNT - 8)) 个脚本) ...${C_RESET}"
    for ((i=TOTAL_COUNT-3; i<TOTAL_COUNT; i++)); do
        echo -e "${C_CYAN}│${C_RESET}   ${C_CYAN}•${C_RESET} ${C_WHITE}${FINAL_SQL_FILES[i]}${C_RESET}"
    done
fi
echo -e "${C_CYAN}╰────────────────────────────────────────────────────────────────────╯${C_RESET}"

# 提示输入数据库连接信息
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🔌 MySQL host${C_RESET} ${C_GRAY}[localhost]${C_RESET}: ")" MYSQL_HOST_INPUT
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🔌 MySQL port${C_RESET} ${C_GRAY}[3306]${C_RESET}: ")" MYSQL_PORT_INPUT
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}👤 MySQL user${C_RESET}: ")" MYSQL_USER_INPUT
read -r -s -p "$(echo -e "${C_BOLD}${C_CYAN}🔑 MySQL password${C_RESET}: ")" MYSQL_PASSWORD_INPUT
echo
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🎯 Target database${C_RESET}: ")" MYSQL_DATABASE_INPUT

MYSQL_HOST_INPUT=${MYSQL_HOST_INPUT:-localhost}
MYSQL_PORT_INPUT=${MYSQL_PORT_INPUT:-3306}

if [ -z "$MYSQL_USER_INPUT" ] || [ -z "$MYSQL_DATABASE_INPUT" ]; then
    echo -e "${C_RED}❌ User、Target database 都必须手动输入。${C_RESET}"
    exit 1
fi

COMMON_ARGS=(
    --host "$MYSQL_HOST_INPUT"
    --port "$MYSQL_PORT_INPUT"
    --user "$MYSQL_USER_INPUT"
    --password "$MYSQL_PASSWORD_INPUT"
    --database "$MYSQL_DATABASE_INPUT"
    --yes
)

echo -e "🔍 ${C_GRAY}正在检查目标数据库字符集...${C_RESET}"
CHARSET_CHECK_OUTPUT=$("$PYTHON_BIN" db-prod/apply_sql.py \
    --check-charset \
    --host "$MYSQL_HOST_INPUT" \
    --port "$MYSQL_PORT_INPUT" \
    --user "$MYSQL_USER_INPUT" \
    --password "$MYSQL_PASSWORD_INPUT" \
    --database "$MYSQL_DATABASE_INPUT" 2>&1)
CHARSET_CHECK_STATUS=$?
printf '%s\n' "$CHARSET_CHECK_OUTPUT"
if [ "$CHARSET_CHECK_STATUS" -ne 0 ] && [ "$CHARSET_CHECK_STATUS" -ne 2 ]; then
    echo -e "${C_RED}❌ 字符集检查失败，未执行 SQL。${C_RESET}"
    exit 1
fi

echo -e "${C_BOLD}${C_CYAN}╭── 🛡️  请确认本次 SQL 执行目标 ──────────────────────────────────────╮${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Host     :${C_RESET} ${C_BOLD}${C_WHITE}$MYSQL_HOST_INPUT${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Port     :${C_RESET} ${C_BOLD}${C_WHITE}$MYSQL_PORT_INPUT${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}User     :${C_RESET} ${C_BOLD}${C_WHITE}$MYSQL_USER_INPUT${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Database :${C_RESET} ${C_BOLD}${C_YELLOW}$MYSQL_DATABASE_INPUT${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Password :${C_RESET} ${C_MAGENTA}******${C_RESET}"
if [ "$MODE" = "ALL" ]; then
    echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Mode     :${C_RESET} ${C_BOLD}${C_GREEN}all (重跑所有迁移脚本，共 $TOTAL_COUNT 个)${C_RESET}"
elif [ "$MODE" = "SPEC" ]; then
    echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Mode     :${C_RESET} ${C_BOLD}${C_YELLOW}spec [ $SPEC_ARG ] (共 $TOTAL_COUNT 个脚本)${C_RESET}"
elif [ "$MODE" = "LAST" ]; then
    echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Mode     :${C_RESET} ${C_BOLD}${C_BLUE}last [从 $RECORDED_LAST_FILE 开始] (共 $TOTAL_COUNT 个脚本)${C_RESET}"
else
    echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Mode     :${C_RESET} ${C_BOLD}${C_WHITE}custom (共 $TOTAL_COUNT 个脚本)${C_RESET}"
fi
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}SQL files:${C_RESET} ${C_CYAN}${FINAL_SQL_FILES[0]}${C_RESET} ~ ${C_CYAN}${FINAL_SQL_FILES[TOTAL_COUNT-1]}${C_RESET}"
echo -e "${C_CYAN}╰────────────────────────────────────────────────────────────────────╯${C_RESET}"
if [ "$CHARSET_CHECK_STATUS" -eq 2 ]; then
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}目标库字符集存在风险；确认目标并接受风险请输入 ${C_RED}YES${C_YELLOW} 继续执行: ${C_RESET}")" CONFIRM_INPUT
else
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}确认无误请输入 ${C_GREEN}YES${C_YELLOW} 继续执行: ${C_RESET}")" CONFIRM_INPUT
fi

CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo -e "${C_RED}❌ 已取消，未执行 SQL。${C_RESET}"
    exit 1
fi

# 获取迁移执行前数据库中的数据表列表
BEFORE_TABLES_JSON=$("$PYTHON_BIN" db-prod/apply_sql.py --list-tables "${COMMON_ARGS[@]}" 2>/dev/null || echo "[]")

# 统计所选脚本中涉及的 ALTER TABLE 表名
ALTERED_TABLES_LIST=()
for sql_f in "${FINAL_SQL_FILES[@]}"; do
    if [ -f "$sql_f" ]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ^[[:space:]]*ALTER[[:space:]]+TABLE[[:space:]]+[\`]?([a-zA-Z0-9_]+)[\`]? ]]; then
                ALTERED_TABLES_LIST+=("${BASH_REMATCH[1]}")
            fi
        done < "$sql_f"
    fi
done

echo -e "🚀 ${C_BOLD}${C_CYAN}开始执行 SQL 迁移脚本...${C_RESET}"
CURRENT_INDEX=0
for f in "${FINAL_SQL_FILES[@]}"; do
    CURRENT_INDEX=$((CURRENT_INDEX + 1))
    echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
    echo -e "${C_BG_BLUE}${C_WHITE} [$CURRENT_INDEX/$TOTAL_COUNT] ${C_RESET} 🚀 ${C_BOLD}Applying${C_RESET} ${C_CYAN}$f${C_RESET}..."
    "$PYTHON_BIN" db-prod/apply_sql.py "$f" "${COMMON_ARGS[@]}"
    if [ $? -ne 0 ]; then
         echo -e "${C_RED}❌ 执行失败: $f${C_RESET}"
         exit 1
    fi
    # 记录最后一次成功应用的 SQL 脚本
    echo "$(basename "$f")" > "$LAST_RECORD_FILE"
done

# 获取迁移执行后数据库中的数据表列表
AFTER_TABLES_JSON=$("$PYTHON_BIN" db-prod/apply_sql.py --list-tables "${COMMON_ARGS[@]}" 2>/dev/null || echo "[]")

echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}✅ 本次选中的 $TOTAL_COUNT 个数据库迁移 SQL 文件全部执行成功！${C_RESET}"
echo ""

# 调用 Python 生成结构化统计汇总
"$PYTHON_BIN" - << EOF
import json, sys

is_tty = sys.stdout.isatty() or '${FORCE_COLOR:-}' != ''
C_RESET = "\033[0m" if is_tty else ""
C_BOLD = "\033[1m" if is_tty else ""
C_GREEN = "\033[38;5;46m" if is_tty else ""
C_YELLOW = "\033[38;5;220m" if is_tty else ""
C_CYAN = "\033[38;5;51m" if is_tty else ""
C_WHITE = "\033[1;37m" if is_tty else ""
C_GRAY = "\033[38;5;245m" if is_tty else ""

try:
    before = set(json.loads('''$BEFORE_TABLES_JSON'''))
except Exception:
    before = set()

try:
    after = set(json.loads('''$AFTER_TABLES_JSON'''))
except Exception:
    after = set()

new_tables = sorted(list(after - before))
after_tables = sorted(list(after))
altered_tables = sorted(list(set('''${ALTERED_TABLES_LIST[*]}'''.split())))

print(f"{C_CYAN}╭──────────────────────────────────────────────────╮{C_RESET}")
print(f"{C_CYAN}│{C_RESET} {C_BOLD}{C_GREEN}📊 数据库迁移执行结果统计 (Migration Summary){C_RESET}    {C_CYAN}│{C_RESET}")
print(f"{C_CYAN}╰──────────────────────────────────────────────────╯{C_RESET}")
print(f"  🎯 {C_GRAY}目标数据库    :{C_RESET} {C_BOLD}{C_YELLOW}$MYSQL_DATABASE_INPUT{C_RESET}")
print(f"  📁 {C_GRAY}迁移脚本总数  :{C_RESET} {C_BOLD}{C_GREEN}$TOTAL_COUNT 个 (全部成功){C_RESET}")
print(f"  ✨ {C_GRAY}真实新增数据表:{C_RESET} {C_BOLD}{C_GREEN}{len(new_tables)} 个{C_RESET}")
if new_tables:
    if len(new_tables) <= 10:
        for t in new_tables:
            print(f"     {C_GREEN}+{C_RESET} {C_WHITE}{t}{C_RESET}")
    else:
        for t in new_tables[:5]:
            print(f"     {C_GREEN}+{C_RESET} {C_WHITE}{t}{C_RESET}")
        print(f"     {C_GRAY}... (省略 {len(new_tables) - 8} 个表) ...{C_RESET}")
        for t in new_tables[-3:]:
            print(f"     {C_GREEN}+{C_RESET} {C_WHITE}{t}{C_RESET}")

print(f"  🔨 {C_GRAY}结构变更数据表:{C_RESET} {C_BOLD}{C_YELLOW}{len(altered_tables)} 个{C_RESET}")
if altered_tables:
    if len(altered_tables) <= 10:
        for t in altered_tables:
            print(f"     {C_YELLOW}*{C_RESET} {C_WHITE}{t}{C_RESET}")
    else:
        for t in altered_tables[:5]:
            print(f"     {C_YELLOW}*{C_RESET} {C_WHITE}{t}{C_RESET}")
        print(f"     {C_GRAY}... (省略 {len(altered_tables) - 8} 个表) ...{C_RESET}")
        for t in altered_tables[-3:]:
            print(f"     {C_YELLOW}*{C_RESET} {C_WHITE}{t}{C_RESET}")

print(f"  📦 {C_GRAY}当前目标库表数:{C_RESET} {C_BOLD}{C_CYAN}{len(after_tables)} 个{C_RESET}")
print(f"{C_CYAN}──────────────────────────────────────────────────{C_RESET}")
EOF

# 如果执行了全量脚本，询问是否导入管理员初始账号
if [ "$MODE" = "ALL" ]; then
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}是否需要顺带导入默认管理员账号和预置 API Key 凭证？ (推荐首次部署时导入) [Y/N]: ${C_RESET}")" RUN_INIT_ADMIN
    RUN_INIT_ADMIN_UPPER=$(echo "$RUN_INIT_ADMIN" | tr '[:lower:]' '[:upper:]')
    if [ "$RUN_INIT_ADMIN_UPPER" == "Y" ] || [ "$RUN_INIT_ADMIN_UPPER" == "YES" ]; then
        ADMIN_SQL="db-prod/INIT-USER-ADMIN.sql"
        if [ -f "$ADMIN_SQL" ]; then
            echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
            echo -e "🚀 ${C_BOLD}${C_CYAN}正在导入默认管理员账号数据 ($ADMIN_SQL)...${C_RESET}"
            "$PYTHON_BIN" db-prod/apply_sql.py "$ADMIN_SQL" "${COMMON_ARGS[@]}"
            if [ $? -ne 0 ]; then
                 echo -e "${C_RED}❌ 默认管理员账号数据导入失败。${C_RESET}"
                 exit 1
            fi
            echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
            echo -e "${C_BOLD}${C_GREEN}✅ 默认管理员账号数据导入成功！${C_RESET}"
            echo -e "${C_CYAN}╭──────────────────────────────────────────────────╮${C_RESET}"
            echo -e "${C_CYAN}│${C_RESET} ${C_BOLD}${C_YELLOW}🔑 首次登录重要指引：${C_RESET}                            ${C_CYAN}│${C_RESET}"
            echo -e "${C_CYAN}╰──────────────────────────────────────────────────╯${C_RESET}"
            echo -e "  - ${C_BOLD}${C_CYAN}默认用户名${C_RESET}  : ${C_WHITE}admin${C_RESET}"
            echo -e "  - ${C_BOLD}${C_CYAN}预置 API Key${C_RESET}: ${C_GREEN}5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c${C_RESET}"
            echo -e "  - ${C_BOLD}${C_YELLOW}登录方式${C_RESET}    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
            echo -e "  - ${C_BOLD}${C_RED}安全提醒${C_RESET}    : 首次登录成功后，请务必前往【用户管理】"
            echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
            echo -e "${C_CYAN}──────────────────────────────────────────────────${C_RESET}"
        else
            echo -e "${C_YELLOW}⚠️ 未找到默认管理员数据文件 $ADMIN_SQL，跳过导入。${C_RESET}"
        fi
    else
        echo -e "${C_GRAY}💡 已跳过默认管理员账号数据的导入。${C_RESET}"
    fi
fi

# 如果选中的脚本中包含 INIT-USER-ADMIN.sql，打印登录指引
for f in "${FINAL_SQL_FILES[@]}"; do
    if [[ "$f" =~ INIT-USER-ADMIN.sql$ ]]; then
        echo -e "${C_CYAN}╭──────────────────────────────────────────────────╮${C_RESET}"
        echo -e "${C_CYAN}│${C_RESET} ${C_BOLD}${C_YELLOW}🔑 首次登录重要指引：${C_RESET}                            ${C_CYAN}│${C_RESET}"
        echo -e "${C_CYAN}╰──────────────────────────────────────────────────╯${C_RESET}"
        echo -e "  - ${C_BOLD}${C_CYAN}默认用户名${C_RESET}  : ${C_WHITE}admin${C_RESET}"
        echo -e "  - ${C_BOLD}${C_CYAN}预置 API Key${C_RESET}: ${C_GREEN}5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c${C_RESET}"
        echo -e "  - ${C_BOLD}${C_YELLOW}登录方式${C_RESET}    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
        echo -e "  - ${C_BOLD}${C_RED}安全提醒${C_RESET}    : 首次登录成功后，请务必前往【用户管理】"
        echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
        echo -e "${C_CYAN}──────────────────────────────────────────────────${C_RESET}"
        break
    fi
done
