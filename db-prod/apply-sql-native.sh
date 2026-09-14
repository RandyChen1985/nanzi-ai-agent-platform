#!/bin/bash
# 免 Python 依赖的 MySQL SQL 导入工具。
# 依靠系统已安装的 mysql 命令行客户端。
# 实现了与 Python 脚本相同的幂等性过滤机制（忽略 1007, 1050, 1054, 1060, 1061, 1062, 1091 等错误码）。

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

# 检查是否有 mysql 命令行客户端
if ! command -v mysql >/dev/null 2>&1; then
    echo -e "${C_RED}❌ 错误: 未在系统 PATH 中找到 'mysql' 命令行客户端。${C_RESET}"
    echo -e "${C_YELLOW}💡 请先安装 MySQL 客户端工具：${C_RESET}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "   macOS (Homebrew): brew install mysql-client"
        echo "   并加入 PATH: echo 'export PATH=\"/opt/homebrew/opt/mysql-client/bin:\$PATH\"' >> ~/.zshrc"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "   Ubuntu / Debian : sudo apt-get update && sudo apt-get install -y default-mysql-client"
    elif command -v yum >/dev/null 2>&1; then
        echo "   CentOS / RHEL   : sudo yum install -y mysql"
    fi
    echo -e "${C_CYAN}💡 提示：您也可以直接改用 Python 驱动脚本: ./db-prod/apply-sql.sh${C_RESET}"
    exit 1
fi

MYSQL_PATH=$(command -v mysql)

# 环境校验通过提示 (若直接传 -h/--help 则不输出环境信息以保持帮助文档纯净)
if [[ "$*" != *"--help"* ]] && [[ "$*" != *"-h"* ]]; then
    echo -e "${C_GREEN}✓ 运行环境校验通过${C_RESET}: 原生客户端 ${C_CYAN}mysql${C_RESET} [${C_GRAY}${MYSQL_PATH}${C_RESET}] ${C_GREEN}[已就绪]${C_RESET} | 免 Python 驱动模式"
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
${C_CYAN}│${C_RESET}  ${C_BOLD}${C_WHITE}NanZi AI Agent Platform - MySQL 原生迁移执行工具 (apply-sql-native.sh)${C_RESET}   ${C_CYAN}│${C_RESET}
${C_CYAN}│${C_RESET}  ${C_GRAY}(免 Python 依赖，直接通过系统已安装的 mysql 命令行客户端执行)${C_RESET}       ${C_CYAN}│${C_RESET}
${C_CYAN}╰──────────────────────────────────────────────────────────────────────╯${C_RESET}

${C_BOLD}${C_YELLOW}用法:${C_RESET}
  ./db-prod/apply-sql-native.sh [选项] [SQL文件...]

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
                           示例: ${C_CYAN}./db-prod/apply-sql-native.sh db-prod/V155-add_metadata_quality_score.sql${C_RESET}

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
                echo "❌ 选项 $1 缺少参数 (例如: --spec v1-v31)"
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
            echo "❌ 未知选项: $1"
            echo "💡 请使用 --help 查看使用帮助"
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

# 默认端口
MYSQL_PORT_INPUT=3306

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

MYSQL_BASE_CMD="mysql -h $MYSQL_HOST_INPUT -P $MYSQL_PORT_INPUT -u $MYSQL_USER_INPUT -p$MYSQL_PASSWORD_INPUT --protocol=TCP --default-character-set=utf8mb4 --connect-timeout=30 --max-allowed-packet=64M"

MYSQL_DATABASE_SQL=$(printf '%s' "$MYSQL_DATABASE_INPUT" | sed "s/'/''/g")
CHARSET_QUERY_SQL="SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '$MYSQL_DATABASE_SQL';"
CHARSET_ERR=$(mktemp)
CHARSET_INFO=$(printf '%s\n' "$CHARSET_QUERY_SQL" | $MYSQL_BASE_CMD --batch --skip-column-names 2>"$CHARSET_ERR")
CHARSET_STATUS=$?
if [ "$CHARSET_STATUS" -ne 0 ]; then
    echo -e "${C_RED}❌ 目标数据库字符集检查失败，未执行 SQL。${C_RESET}"
    if [ -s "$CHARSET_ERR" ]; then
        echo -e "${C_YELLOW}—— MySQL 原始错误 ——${C_RESET}"
        cat "$CHARSET_ERR"
    fi
    rm -f "$CHARSET_ERR"
    exit 1
fi
rm -f "$CHARSET_ERR"

CHARSET_MISMATCH=0
if [ -z "$CHARSET_INFO" ]; then
    echo -e "ℹ️  ${C_GRAY}目标数据库不存在；确认后将以 utf8mb4 创建。${C_RESET}"
else
    CHARSET_NAME=$(printf '%s\n' "$CHARSET_INFO" | awk 'NR == 1 {print $1}')
    COLLATION_NAME=$(printf '%s\n' "$CHARSET_INFO" | awk 'NR == 1 {print $2}')
    printf '%s\n' "ℹ️  目标数据库默认字符集: ${CHARSET_NAME}，排序规则: ${COLLATION_NAME}"
    CHARSET_NAME_NORMALIZED=$(printf '%s' "$CHARSET_NAME" | tr '[:upper:]' '[:lower:]')
    if [ "$CHARSET_NAME_NORMALIZED" != "utf8mb4" ]; then
        echo -e "${C_YELLOW}⚠️ 目标数据库默认字符集不是 utf8mb4，导入包含中文的 SQL 时可能出现乱码。${C_RESET}"
        echo -e "${C_YELLOW}⚠️ 如仍要继续，请在下面的确认提示中明确输入 YES。${C_RESET}"
        CHARSET_MISMATCH=1
    fi
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
if [ "$CHARSET_MISMATCH" -eq 1 ]; then
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}目标库字符集存在风险；确认目标并接受风险请输入 ${C_RED}YES${C_YELLOW} 继续执行: ${C_RESET}")" CONFIRM_INPUT
else
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}确认无误请输入 ${C_GREEN}YES${C_YELLOW} 继续执行: ${C_RESET}")" CONFIRM_INPUT
fi
CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo -e "${C_RED}❌ 已取消，未执行 SQL。${C_RESET}"
    exit 1
fi

echo "ℹ️  提示：重复执行时若看到「幂等跳过（可忽略…）」并带 MySQL ERROR 1050/1060/1061 等字样，表示对象已存在，属于正常跳过，不是失败。"
echo "   只有出现「❌ 执行失败（非幂等可忽略错误，需处理）」才需要处理。"
echo
# 定义需要忽略的 MySQL 错误码
# 1007: 数据库已存在
# 1050: 表已存在
# 1054: 未知列（如 CHANGE/DROP 时列已改名或不存在，重复执行）
# 1060: 重复的列名
# 1061: 重复的键/索引名
# 1062: 唯一性约束重复键值
# 1091: 试图删除不存在的列或键
IGNORED_ERRORS="1007|1050|1054|1060|1061|1062|1091"

# 确认后连接并尝试创建数据库（若不存在）
CREATE_DB_SQL="CREATE DATABASE IF NOT EXISTS \`$MYSQL_DATABASE_INPUT\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

echo "🔌 正在连接 MySQL 并确保目标数据库已存在..."
CONNECT_ERR=$(mktemp)
if ! echo "$CREATE_DB_SQL" | $MYSQL_BASE_CMD >/dev/null 2>"$CONNECT_ERR"; then
    echo "❌ 数据库连接或创建失败，请检查连接参数（如 Host、User、Password）或数据库服务状态。"
    if [ -s "$CONNECT_ERR" ]; then
        echo "—— MySQL 原始错误 ——"
        cat "$CONNECT_ERR"
    fi
    rm -f "$CONNECT_ERR"
    exit 1
fi
rm -f "$CONNECT_ERR"

# 数据库连接参数
MYSQL_CMD="$MYSQL_BASE_CMD $MYSQL_DATABASE_INPUT"

# 获取迁移执行前数据库中的数据表列表
BEFORE_TABLES=()
BEFORE_TABLES_RAW=$(printf "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = '%s' AND TABLE_TYPE = 'BASE TABLE';\n" "$MYSQL_DATABASE_SQL" | $MYSQL_BASE_CMD --batch --skip-column-names 2>/dev/null)
while IFS= read -r tbl; do
    [ -n "$tbl" ] && BEFORE_TABLES+=("$tbl")
done <<< "$BEFORE_TABLES_RAW"

TOTAL_EXEC_STMTS=0
TOTAL_SKIPPED_STMTS=0
ALTERED_TABLES_LIST=()

# 单个 SQL 文件执行逻辑（包含切分语句和错误捕获）
execute_sql_file() {
    local sql_file="$1"
    echo "📖 Reading $sql_file..."
    
    # 临时文件用来收集错误输出
    local err_log
    err_log=$(mktemp)
    
    # 拆分逻辑：通过维护字符串开启/闭合状态，避开多行字符串（提示词）内部的分号，安全完成语句切分
    local stmt=""
    local in_string=0
    # SET @var 属于会话级状态；每条语句单独开连接会丢失。
    # 缓冲后，对本文件后续每一条业务语句都前置执行（如 V69 多条 UPDATE 共用变量）。
    local session_prefix=""
    # PREPARE/EXECUTE/DEALLOCATE PREPARE 也依赖同一条 MySQL 会话；暂存整个块，
    # 避免预处理语句句柄在每条语句单独连接时丢失（如 V105/V108/V109）。
    local prepared_block=""
    local prepared_preview=""

    run_mysql_stmt() {
        local payload="$1"
        local preview="$2"
        local max_retries=2
        local attempt=0
        local status=0

        while true; do
            attempt=$((attempt + 1))
            TOTAL_EXEC_STMTS=$((TOTAL_EXEC_STMTS + 1))
            set +e
            # 每条语句单独 session：必须每次先 SET NAMES，否则中文提示词等会乱码。
            # 标准输出静音，避免 SELECT 1 等幂等探测语句在终端刷屏；错误仍写入 err_log
            printf 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;\n%s\n' "$payload" | $MYSQL_CMD >/dev/null 2>"$err_log"
            status=$?
            set -e

            if [ $status -eq 0 ]; then
                return 0
            fi

            local err_msg
            err_msg=$(cat "$err_log")

            # 1. 检测是否属于瞬态连接断开 (ERROR 2013 / ERROR 2006 / Lost connection / gone away)，支持自动重试
            if [[ "$err_msg" =~ (ERROR 2013|ERROR 2006|Lost connection|gone away) ]] && [ $attempt -le $max_retries ]; then
                echo "   ⚠️ 检测到数据库连接瞬态中断 (Lost connection)，正在自动重试 ($attempt/$max_retries)..."
                sleep 1
                continue
            fi

            # 2. 检测是否属于幂等可忽略错误
            local is_ignored=0
            local code
            for code in ${IGNORED_ERRORS//|/ }; do
                if [[ "$err_msg" =~ "ERROR $code" ]] || [[ "$err_msg" =~ "Error $code" ]]; then
                    # 去掉 mysql 密码警告，只保留核心错误说明，避免用户误以为失败
                    local brief
                    brief=$(echo "$err_msg" | grep -E 'ERROR [0-9]+' | head -1 | sed 's/^[[:space:]]*//')
                    [ -z "$brief" ] && brief="MySQL $code"
                    echo "   -> 幂等跳过（可忽略，对象已存在/已变更）: $brief"
                    is_ignored=1
                    TOTAL_SKIPPED_STMTS=$((TOTAL_SKIPPED_STMTS + 1))
                    break
                fi
            done

            if [ $is_ignored -eq 1 ]; then
                return 0
            fi

            # 真实错误
            echo "❌ 执行失败（非幂等可忽略错误，需处理）："
            echo "Statement: ${preview:0:150}..."
            echo "Error message: $err_msg"
            return 1
        done
    }

    is_session_setup_stmt() {
        # SET NAMES / SET CHARACTER SET 已由 run_mysql_stmt 统一注入，这里只缓冲 SET @var
        [[ "$1" =~ ^[[:space:]]*SET[[:space:]]+@ ]]
    }

    is_charset_setup_stmt() {
        [[ "$1" =~ ^[[:space:]]*SET[[:space:]]+(NAMES|CHARACTER[[:space:]]+SET)([[:space:]]|$) ]]
    }

    is_prepare_stmt() {
        [[ "$1" =~ ^[[:space:]]*PREPARE[[:space:]]+[^[:space:]]+[[:space:]]+FROM[[:space:]]+ ]]
    }

    is_deallocate_prepare_stmt() {
        [[ "$1" =~ ^[[:space:]]*DEALLOCATE[[:space:]]+PREPARE[[:space:]]+[^[:space:]]+[[:space:]]*$ ]]
    }
    
    # 用来读取 SQL 文件
    while IFS= read -r line || [[ -n "$line" ]]; do
        # 仅在非多行字符串状态下，才忽略空行和 -- 或 # 注释行
        clean_line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ $in_string -eq 0 ]; then
            if [ -z "$clean_line" ] || [[ "$clean_line" =~ ^-- ]] || [[ "$clean_line" =~ ^/\* ]]; then
                continue
            fi
        fi
        
        if [ -z "$stmt" ]; then
            stmt="$line"
        else
            stmt="${stmt}"$'\n'"$line"
        fi
        
        # 统计当前行中未转义单引号的数量，以精确跟踪跨行字符串开启/闭合状态
        # 1. 移除转义的单引号 \' 和双单引号 ''
        local temp
        temp="${line//\\\'/}"
        temp="${temp//\'\'/}"
        # 2. 去掉所有非单引号字符，统计剩下的单引号数量
        local only_quotes
        only_quotes="${temp//[^\']/}"
        local num_quotes=${#only_quotes}
        
        # 如果单引号数量为奇数，翻转多行字符串状态
        if (( num_quotes % 2 != 0 )); then
            in_string=$((1 - in_string))
        fi
        
        # 当且仅当不在多行字符串内，且行尾为分号时，说明一条完整的 SQL 语句结束了
        if [ $in_string -eq 0 ] && [[ "$clean_line" =~ \;$ ]]; then
            # 过滤 USE 或 CREATE DATABASE 语句
            if [[ "$stmt" =~ ^[[:space:]]*(CREATE[[:space:]]+DATABASE|USE)[[:space:]] ]]; then
                stmt=""
                continue
            fi
            
            # 去除末尾分号（与 Python 版 apply_sql.py 保持一致）
            local exec_stmt="$stmt"
            exec_stmt="${exec_stmt%;}"
            exec_stmt="${exec_stmt%"${exec_stmt##*[![:space:]]}"}"

            # 统计变更表 (ALTER TABLE)
            if [[ "$exec_stmt" =~ ^[[:space:]]*ALTER[[:space:]]+TABLE[[:space:]]+[\`]?([a-zA-Z0-9_]+)[\`]? ]]; then
                ALTERED_TABLES_LIST+=("${BASH_REMATCH[1]}")
            fi

            if [ -n "$prepared_block" ]; then
                # exec_stmt 已去掉末尾分号；重新拼接时必须补回分号，
                # 否则 PREPARE/EXECUTE/DEALLOCATE 会被 MySQL 当成一条语句。
                prepared_block="${prepared_block};"$'\n'"${exec_stmt}"
                if is_deallocate_prepare_stmt "$exec_stmt"; then
                    if ! run_mysql_stmt "$prepared_block" "$prepared_preview"; then
                        rm -f "$err_log"
                        return 1
                    fi
                    prepared_block=""
                    prepared_preview=""
                    # 预处理块消费完毕后重置临时前缀，避免多组 PREPARE 连续堆叠累积
                    session_prefix=""
                fi
                stmt=""
                continue
            fi

            if is_prepare_stmt "$exec_stmt"; then
                prepared_block="$exec_stmt"
                if [ -n "$session_prefix" ]; then
                    prepared_block="${session_prefix};"$'\n'"${prepared_block}"
                fi
                prepared_preview="$stmt"
                stmt=""
                continue
            fi

            if is_charset_setup_stmt "$exec_stmt"; then
                # 已由 run_mysql_stmt 统一 SET NAMES，跳过文件内重复声明
                stmt=""
                continue
            fi

            if is_session_setup_stmt "$exec_stmt"; then
                if [ -n "$session_prefix" ]; then
                    session_prefix="${session_prefix};"$'\n'"${exec_stmt}"
                else
                    session_prefix="$exec_stmt"
                fi
                stmt=""
                continue
            fi

            local payload="$exec_stmt"
            if [ -n "$session_prefix" ]; then
                # 注意：不清空 session_prefix，本文件后续语句仍需同一批 @变量
                payload="${session_prefix};"$'\n'"${exec_stmt}"
            fi

            if ! run_mysql_stmt "$payload" "$stmt"; then
                rm -f "$err_log"
                return 1
            fi
            stmt=""
        fi
    done < "$sql_file"

    if [ -n "$prepared_block" ]; then
        echo "❌ 执行失败：PREPARE 语句缺少对应的 DEALLOCATE PREPARE。"
        rm -f "$err_log"
        return 1
    fi
    
    # 扫尾：处理文件末尾可能没加分号的最后一条语句
    if [ -n "$stmt" ]; then
        local clean_stmt
        clean_stmt=$(echo "$stmt" | sed '/^[[:space:]]*--/d; /^[[:space:]]*#/d; s/^[[:space:]]*//; s/[[:space:]]*$//')
        if [ -n "$clean_stmt" ]; then
            if ! [[ "$clean_stmt" =~ ^[[:space:]]*(CREATE[[:space:]]+DATABASE|USE)[[:space:]] ]]; then
                local exec_stmt="$clean_stmt"
                exec_stmt="${exec_stmt%;}"
                exec_stmt="${exec_stmt%"${exec_stmt##*[![:space:]]}"}"

                if [[ "$exec_stmt" =~ ^[[:space:]]*ALTER[[:space:]]+TABLE[[:space:]]+[\`]?([a-zA-Z0-9_]+)[\`]? ]]; then
                    ALTERED_TABLES_LIST+=("${BASH_REMATCH[1]}")
                fi

                if is_charset_setup_stmt "$exec_stmt"; then
                    :
                elif is_session_setup_stmt "$exec_stmt"; then
                    if [ -n "$session_prefix" ]; then
                        session_prefix="${session_prefix};"$'\n'"${exec_stmt}"
                    else
                        session_prefix="$exec_stmt"
                    fi
                else
                    local payload="$exec_stmt"
                    if [ -n "$session_prefix" ]; then
                        payload="${session_prefix};"$'\n'"${exec_stmt}"
                    fi
                    if ! run_mysql_stmt "$payload" "$clean_stmt"; then
                        rm -f "$err_log"
                        return 1
                    fi
                fi
            fi
        fi
    fi

    # 仅有 SET @、没有后续业务语句时，仍执行一次（极少见）
    if [ -n "$session_prefix" ] && [ -z "$stmt" ]; then
        :
    fi
    
    rm -f "$err_log"
    return 0
}

echo -e "🚀 ${C_BOLD}${C_CYAN}开始执行 SQL 迁移脚本...${C_RESET}"
CURRENT_INDEX=0
for f in "${FINAL_SQL_FILES[@]}"; do
    CURRENT_INDEX=$((CURRENT_INDEX + 1))
    echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
    echo -e "${C_BG_BLUE}${C_WHITE} [$CURRENT_INDEX/$TOTAL_COUNT] ${C_RESET} 🚀 ${C_BOLD}Applying${C_RESET} ${C_CYAN}$f${C_RESET}..."
    if ! execute_sql_file "$f"; then
        echo -e "${C_RED}❌ Failed to apply $f${C_RESET}"
        exit 1
    fi
    # 记录最后一次成功应用的 SQL 脚本
    echo "$(basename "$f")" > "$LAST_RECORD_FILE"
done

# 迁移结果统计
AFTER_TABLES=()
AFTER_TABLES_RAW=$(printf "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = '%s' AND TABLE_TYPE = 'BASE TABLE';\n" "$MYSQL_DATABASE_SQL" | $MYSQL_BASE_CMD --batch --skip-column-names 2>/dev/null)
while IFS= read -r tbl; do
    [ -n "$tbl" ] && AFTER_TABLES+=("$tbl")
done <<< "$AFTER_TABLES_RAW"

# 计算真实新增的表
NEW_TABLES=()
for atbl in "${AFTER_TABLES[@]}"; do
    local_found=false
    for btbl in "${BEFORE_TABLES[@]}"; do
        if [ "$atbl" = "$btbl" ]; then
            local_found=true
            break
        fi
    done
    if [ "$local_found" = false ]; then
        NEW_TABLES+=("$atbl")
    fi
done

# 计算去重后的变更表
UNIQUE_ALTERED_TABLES=()
if [ ${#ALTERED_TABLES_LIST[@]} -gt 0 ]; then
    while IFS= read -r tbl; do
        [ -n "$tbl" ] && UNIQUE_ALTERED_TABLES+=("$tbl")
    done < <(printf "%s\n" "${ALTERED_TABLES_LIST[@]}" | awk '!seen[$0]++')
fi

echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}✅ 本次选中的 $TOTAL_COUNT 个数据库迁移 SQL 文件全部执行成功！${C_RESET}"
echo ""
echo -e "${C_CYAN}╭──────────────────────────────────────────────────╮${C_RESET}"
echo -e "${C_CYAN}│${C_RESET} ${C_BOLD}${C_GREEN}📊 数据库迁移执行结果统计 (Migration Summary)${C_RESET}    ${C_CYAN}│${C_RESET}"
echo -e "${C_CYAN}╰──────────────────────────────────────────────────╯${C_RESET}"
echo -e "  🎯 ${C_GRAY}目标数据库    :${C_RESET} ${C_BOLD}${C_YELLOW}$MYSQL_DATABASE_INPUT${C_RESET}"
echo -e "  📁 ${C_GRAY}迁移脚本总数  :${C_RESET} ${C_BOLD}${C_GREEN}$TOTAL_COUNT 个 (全部成功)${C_RESET}"
echo -e "  ⚡ ${C_GRAY}执行 SQL 语句 :${C_RESET} ${C_BOLD}${C_CYAN}$TOTAL_EXEC_STMTS 条${C_RESET}"
echo -e "  ✨ ${C_GRAY}真实新增数据表:${C_RESET} ${C_BOLD}${C_GREEN}${#NEW_TABLES[@]} 个${C_RESET}"
if [ ${#NEW_TABLES[@]} -gt 0 ]; then
    if [ ${#NEW_TABLES[@]} -le 10 ]; then
        for tbl in "${NEW_TABLES[@]}"; do
            echo -e "     ${C_GREEN}+${C_RESET} ${C_WHITE}$tbl${C_RESET}"
        done
    else
        for ((i=0; i<5; i++)); do
            echo -e "     ${C_GREEN}+${C_RESET} ${C_WHITE}${NEW_TABLES[i]}${C_RESET}"
        done
        echo -e "     ${C_GRAY}... (省略 $(( ${#NEW_TABLES[@]} - 8 )) 个表) ...${C_RESET}"
        for ((i=${#NEW_TABLES[@]}-3; i<${#NEW_TABLES[@]}; i++)); do
            echo -e "     ${C_GREEN}+${C_RESET} ${C_WHITE}${NEW_TABLES[i]}${C_RESET}"
        done
    fi
fi
echo -e "  🔨 ${C_GRAY}结构变更数据表:${C_RESET} ${C_BOLD}${C_YELLOW}${#UNIQUE_ALTERED_TABLES[@]} 个${C_RESET}"
if [ ${#UNIQUE_ALTERED_TABLES[@]} -gt 0 ]; then
    if [ ${#UNIQUE_ALTERED_TABLES[@]} -le 10 ]; then
        for tbl in "${UNIQUE_ALTERED_TABLES[@]}"; do
            echo -e "     ${C_YELLOW}*${C_RESET} ${C_WHITE}$tbl${C_RESET}"
        done
    else
        for ((i=0; i<5; i++)); do
            echo -e "     ${C_YELLOW}*${C_RESET} ${C_WHITE}${UNIQUE_ALTERED_TABLES[i]}${C_RESET}"
        done
        echo -e "     ${C_GRAY}... (省略 $(( ${#UNIQUE_ALTERED_TABLES[@]} - 8 )) 个表) ...${C_RESET}"
        for ((i=${#UNIQUE_ALTERED_TABLES[@]}-3; i<${#UNIQUE_ALTERED_TABLES[@]}; i++)); do
            echo -e "     ${C_YELLOW}*${C_RESET} ${C_WHITE}${UNIQUE_ALTERED_TABLES[i]}${C_RESET}"
        done
    fi
fi
if [ "$TOTAL_SKIPPED_STMTS" -gt 0 ]; then
    echo -e "  ℹ️  ${C_GRAY}幂等跳过操作  :${C_RESET} ${C_CYAN}$TOTAL_SKIPPED_STMTS 次${C_RESET} (${C_GRAY}表/列/索引已存在，安全跳过${C_RESET})"
fi
echo -e "  📦 ${C_GRAY}当前目标库表数:${C_RESET} ${C_BOLD}${C_CYAN}${#AFTER_TABLES[@]} 个${C_RESET}"
echo -e "${C_CYAN}──────────────────────────────────────────────────${C_RESET}"

# 如果执行了全量脚本，询问是否导入管理员初始账号
if [ "$MODE" = "ALL" ]; then
    read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}是否需要顺带导入默认管理员账号和预置 API Key 凭证？ (推荐首次部署时导入) [Y/N]: ${C_RESET}")" RUN_INIT_ADMIN
    RUN_INIT_ADMIN_UPPER=$(echo "$RUN_INIT_ADMIN" | tr '[:lower:]' '[:upper:]')
    if [ "$RUN_INIT_ADMIN_UPPER" == "Y" ] || [ "$RUN_INIT_ADMIN_UPPER" == "YES" ]; then
        ADMIN_SQL="db-prod/INIT-USER-ADMIN.sql"
        if [ -f "$ADMIN_SQL" ]; then
            echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
            echo -e "🚀 ${C_BOLD}${C_CYAN}正在导入默认管理员账号数据 ($ADMIN_SQL)...${C_RESET}"
            if ! execute_sql_file "$ADMIN_SQL"; then
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
