#!/bin/bash
# 免 Python 依赖的 PostgreSQL SQL 导入工具。
# 依靠系统已安装的 psql 命令行客户端。
# 支持 --help, --all, --spec, --last 等多模式执行，并在执行后输出迁移统计看板。

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

# 检查是否有 psql 客户端
if ! command -v psql >/dev/null 2>&1; then
    echo -e "${C_RED}❌ 错误: 未在系统 PATH 中找到 'psql' 命令行客户端。${C_RESET}"
    echo -e "${C_YELLOW}💡 请先安装 PostgreSQL 客户端工具：${C_RESET}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "   macOS (Homebrew): brew install libpq && brew link --force libpq"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "   Ubuntu / Debian : sudo apt-get update && sudo apt-get install -y postgresql-client"
    elif command -v yum >/dev/null 2>&1; then
        echo "   CentOS / RHEL   : sudo yum install -y postgresql"
    fi
    echo -e "${C_CYAN}💡 提示：您也可以直接改用 Python 驱动脚本: ./db-prod-pg/apply-sql.sh${C_RESET}"
    exit 1
fi

PSQL_PATH=$(command -v psql)

# 环境校验通过提示 (若直接传 -h/--help 则不输出环境信息以保持帮助文档纯净)
if [[ "$*" != *"--help"* ]] && [[ "$*" != *"-h"* ]]; then
    echo -e "${C_GREEN}✓ 运行环境校验通过${C_RESET}: 原生客户端 ${C_CYAN}psql${C_RESET} [${C_GRAY}${PSQL_PATH}${C_RESET}] ${C_GREEN}[已就绪]${C_RESET} | 免 Python 驱动模式"
fi

DB_DIR="db-prod-pg"
LAST_RECORD_FILE="$SCRIPT_DIR/.last_applied_sql"
RECORDED_LAST_FILE=""
if [ -f "$LAST_RECORD_FILE" ]; then
    RECORDED_LAST_FILE=$(head -n 1 "$LAST_RECORD_FILE" 2>/dev/null | tr -d '\r\n ')
fi

show_help() {
    cat << EOF
${C_CYAN}╭──────────────────────────────────────────────────────────────────────╮${C_RESET}
${C_CYAN}│${C_RESET}  ${C_BOLD}${C_WHITE}NanZi AI Agent Platform - PostgreSQL 原生迁移执行工具 (apply-sql-native.sh)${C_RESET}${C_CYAN}│${C_RESET}
${C_CYAN}│${C_RESET}  ${C_GRAY}(免 Python 依赖，直接通过系统已安装的 psql 命令行客户端执行)${C_RESET}       ${C_CYAN}│${C_RESET}
${C_CYAN}╰──────────────────────────────────────────────────────────────────────╯${C_RESET}

${C_BOLD}${C_YELLOW}用法:${C_RESET}
  ./db-prod-pg/apply-sql-native.sh [选项] [SQL文件...]

${C_BOLD}${C_YELLOW}选项说明:${C_RESET}
  ${C_GREEN}-h, --help${C_RESET}               显示本帮助信息并退出
  ${C_GREEN}-a, --all${C_RESET}                重跑/执行所有迁移脚本 (${C_GRAY}db-prod-pg/V*.sql${C_RESET})
  ${C_GREEN}-l, --last${C_RESET}               从上次执行记录的脚本位置开始继续执行
                           (${C_GRAY}读取 db-prod-pg/.last_applied_sql 记录${C_RESET})
  ${C_GREEN}-s, --spec <范围或名称>${C_RESET}  指定脚本执行范围或名称
                           - ${C_BOLD}版本范围${C_RESET} (格式 vX-vY, VX-VY, X-Y):
                             示例: ${C_CYAN}--spec v0-v56${C_RESET} 或 ${C_CYAN}--spec 0-56${C_RESET}
                           - ${C_BOLD}单个版本${C_RESET} (格式 vX, VX, X):
                             示例: ${C_CYAN}--spec v56${C_RESET} 或 ${C_CYAN}--spec 56${C_RESET}
                           - ${C_BOLD}具体文件名${C_RESET}:
                             示例: ${C_CYAN}--spec V56-add_metadata_quality_score.sql${C_RESET}
                           - ${C_BOLD}逗号组合${C_RESET}:
                             示例: ${C_CYAN}--spec v0-v5,v10-v15,v56${C_RESET}
  ${C_GREEN}[SQL文件...]${C_RESET}             直接传入一个或多个具体的 .sql 文件路径
                           示例: ${C_CYAN}./db-prod-pg/apply-sql-native.sh db-prod-pg/V56-add_metadata_quality_score.sql${C_RESET}

${C_BOLD}${C_YELLOW}交互模式:${C_RESET}
  若未指定任何参数直接运行，将默认先打印此帮助说明，并引导您选择：
    ${C_GREEN}[1] all${C_RESET}  : 重跑/执行所有迁移脚本
    ${C_YELLOW}[2] spec${C_RESET} : 指定版本范围或脚本名称
    ${C_BLUE}[3] last${C_RESET} : 从上次记录的位置继续执行 (若存在历史记录)
  选择完毕并确认目标数据库信息后，必须输入 ${C_BOLD}${C_RED}YES${C_RESET} 才会正式执行。
${C_CYAN}──────────────────────────────────────────────────────────────────────${C_RESET}
EOF
}

# 解析 spec 表达式并从 db-prod-pg 匹配出对应的 SQL 文件
resolve_spec() {
    local spec="$1"
    local matched_files=()
    IFS="," read -ra PARTS <<< "$spec"
    for part in "${PARTS[@]}"; do
        part=$(echo "$part" | tr -d " ")
        [ -z "$part" ] && continue
        # 范围格式：如 v0-v56, V1-V31, 0-56, v1~v31, v1..v31
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
        # 单个版本号格式：如 v56, V56, 56
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
                echo "❌ 选项 $1 缺少参数 (例如: --spec v0-v56)" >&2
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
            echo "❌ 未知选项: $1" >&2
            echo "💡 请使用 --help 查看使用帮助" >&2
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
    echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_GREEN}[1] all${C_RESET}   - 执行/重跑所有迁移脚本 (${C_GRAY}db-prod-pg/V*.sql${C_RESET})"
    echo -e "${C_CYAN}│${C_RESET}  ${C_BOLD}${C_YELLOW}[2] spec${C_RESET}  - 指定脚本范围或名称 (例如: ${C_CYAN}v0-v56${C_RESET}, ${C_CYAN}V56${C_RESET}, 或具体文件名)"
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
            read -r -p "$(echo -e "${C_BOLD}${C_CYAN}请输入脚本范围或名称 (例如: v0-v56, V56, V56-xxx.sql): ${C_RESET}")" SPEC_ARG
            if [ -z "$SPEC_ARG" ]; then
                echo -e "${C_RED}❌ 输入为空，已取消执行。${C_RESET}" >&2
                exit 1
            fi
            ;;
        3|last)
            if [ -z "$RECORDED_LAST_FILE" ]; then
                echo -e "${C_RED}❌ 未检测到上次执行记录文件 ($LAST_RECORD_FILE)。${C_RESET}" >&2
                exit 1
            fi
            MODE="LAST"
            ;;
        q|quit|exit)
            echo -e "${C_GRAY}💡 已取消执行。${C_RESET}"
            exit 0
            ;;
        *)
            echo -e "${C_RED}❌ 无效的选择: $INTERACTIVE_CHOICE${C_RESET}" >&2
            exit 1
            ;;
    esac
fi

# 收集待执行的 SQL 脚本文件列表
FINAL_SQL_FILES=()
if [ "$MODE" = "ALL" ]; then
    if [ ! -d "$DB_DIR" ]; then
        echo -e "${C_RED}❌ Directory $DB_DIR not found!${C_RESET}" >&2
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
        echo -e "${C_RED}❌ 未检测到上次执行记录文件 ($LAST_RECORD_FILE)！${C_RESET}" >&2
        echo -e "${C_YELLOW}💡 请先使用 --all 或 --spec 执行迁移脚本，系统将自动记录进度。${C_RESET}" >&2
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
        echo -e "${C_YELLOW}⚠️ 在 $DB_DIR/ 中未找到与上次记录 [$RECORDED_LAST_FILE] 匹配的脚本文件！${C_RESET}" >&2
        exit 1
    fi
elif [ ${#SQL_FILES[@]} -gt 0 ]; then
    for f in "${SQL_FILES[@]}"; do
        FINAL_SQL_FILES+=("$f")
    done
fi

TOTAL_COUNT=${#FINAL_SQL_FILES[@]}
if [ "$TOTAL_COUNT" -eq 0 ]; then
    echo -e "${C_RED}❌ 未找到任何匹配的 SQL 脚本文件！${C_RESET}" >&2
    exit 1
fi

BASELINE_INCLUDED=false
for f in "${FINAL_SQL_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        echo -e "${C_RED}❌ SQL file not found: $f${C_RESET}" >&2
        exit 1
    fi
    if [[ "$(basename "$f")" == "V0-baseline.sql" ]]; then
        BASELINE_INCLUDED=true
    fi
done

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

read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🔌 PostgreSQL host${C_RESET} ${C_GRAY}[localhost]${C_RESET}: ")" PG_HOST
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🔌 PostgreSQL port${C_RESET} ${C_GRAY}[5432]${C_RESET}: ")" PG_PORT
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}👤 PostgreSQL user${C_RESET}: ")" PG_USER
read -r -s -p "$(echo -e "${C_BOLD}${C_CYAN}🔑 PostgreSQL password${C_RESET}: ")" PG_PASSWORD
echo
read -r -p "$(echo -e "${C_BOLD}${C_CYAN}🎯 Target database${C_RESET}: ")" PG_DATABASE
PG_PORT="${PG_PORT:-5432}"
PG_HOST="${PG_HOST:-localhost}"

if [[ -z "$PG_USER" || -z "$PG_DATABASE" ]]; then
    echo -e "${C_RED}❌ User、Target database 都必须手动输入。${C_RESET}" >&2
    exit 1
fi

echo -e "${C_BOLD}${C_CYAN}╭── 🛡️  请确认本次 SQL 执行目标 ──────────────────────────────────────╮${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Host     :${C_RESET} ${C_BOLD}${C_WHITE}$PG_HOST${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Port     :${C_RESET} ${C_BOLD}${C_WHITE}$PG_PORT${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}User     :${C_RESET} ${C_BOLD}${C_WHITE}$PG_USER${C_RESET}"
echo -e "${C_CYAN}│${C_RESET}  ${C_GRAY}Database :${C_RESET} ${C_BOLD}${C_YELLOW}$PG_DATABASE${C_RESET}"
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
read -r -p "$(echo -e "${C_BOLD}${C_YELLOW}确认无误请输入 ${C_GREEN}YES${C_YELLOW} 继续执行: ${C_RESET}")" CONFIRM_INPUT
CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo -e "${C_RED}❌ 已取消，未执行 SQL。${C_RESET}"
    exit 1
fi

# 检查并确保目标数据库存在
echo -e "🔌 ${C_GRAY}正在连接 PostgreSQL 并确保目标数据库已存在...${C_RESET}"
DB_CHECK_CMD="SELECT 1 FROM pg_database WHERE datname = '$PG_DATABASE';"
DB_EXISTS=$(PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres -t -A -c "$DB_CHECK_CMD" 2>/dev/null || true)

if [ "$DB_EXISTS" != "1" ]; then
    CREATE_DB_CMD="CREATE DATABASE \"$PG_DATABASE\" WITH ENCODING 'UTF8';"
    if ! PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres -c "$CREATE_DB_CMD" >/dev/null 2>&1; then
        if ! PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -c "SELECT 1;" >/dev/null 2>&1; then
            echo -e "${C_RED}❌ 数据库连接或创建失败，请检查连接参数（如 Host、User、Password）或目标数据库权限。${C_RESET}" >&2
            exit 1
        fi
    fi
fi

# 获取迁移执行前数据库中的数据表列表
BEFORE_TABLES=()
BEFORE_TABLES_RAW=$(PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" 2>/dev/null || true)
while IFS= read -r tbl; do
    [ -n "$tbl" ] && BEFORE_TABLES+=("$tbl")
done <<< "$BEFORE_TABLES_RAW"

TOTAL_EXEC_STMTS=0
ALTERED_TABLES_LIST=()

# 逐个执行 SQL 文件
CURRENT_INDEX=0
echo -e "🚀 ${C_BOLD}${C_CYAN}开始执行 PostgreSQL 迁移脚本...${C_RESET}"
for f in "${FINAL_SQL_FILES[@]}"; do
    CURRENT_INDEX=$((CURRENT_INDEX + 1))
    echo -e "${C_GRAY}────────────────────────────────────────────────────────────────────${C_RESET}"
    echo -e "${C_BG_BLUE}${C_WHITE} [$CURRENT_INDEX/$TOTAL_COUNT] ${C_RESET} 🚀 ${C_BOLD}Applying${C_RESET} ${C_CYAN}$f${C_RESET}..."

    # 从 SQL 文件中提取结构变更数据表 (ALTER TABLE)
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*ALTER[[:space:]]+TABLE[[:space:]]+(ONLY[[:space:]]+)?(\"?[a-zA-Z0-9_]+\"?\.)?\"?([a-zA-Z0-9_]+)\"? ]]; then
            ALTERED_TABLES_LIST+=("${BASH_REMATCH[3]}")
        fi
    done < "$f"

    # 执行文件
    EXEC_ERR=$(mktemp)
    if ! PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>"$EXEC_ERR"; then
        echo -e "${C_RED}❌ Failed to apply $f${C_RESET}" >&2
        if [ -s "$EXEC_ERR" ]; then
            echo -e "${C_YELLOW}—— PostgreSQL 原始错误 ——${C_RESET}" >&2
            cat "$EXEC_ERR" >&2
        fi
        rm -f "$EXEC_ERR"
        exit 1
    fi
    rm -f "$EXEC_ERR"

    # 记录最后一次成功应用的 SQL 脚本
    echo "$(basename "$f")" > "$LAST_RECORD_FILE"
done

# 迁移结果统计
AFTER_TABLES=()
AFTER_TABLES_RAW=$(PGPASSWORD="$PG_PASSWORD" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" 2>/dev/null || true)
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
echo -e "  🎯 ${C_GRAY}目标数据库    :${C_RESET} ${C_BOLD}${C_YELLOW}$PG_DATABASE${C_RESET}"
echo -e "  📁 ${C_GRAY}迁移脚本总数  :${C_RESET} ${C_BOLD}${C_GREEN}$TOTAL_COUNT 个 (全部成功)${C_RESET}"
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
echo -e "  📦 ${C_GRAY}当前目标库表数:${C_RESET} ${C_BOLD}${C_CYAN}${#AFTER_TABLES[@]} 个${C_RESET}"
echo -e "${C_CYAN}──────────────────────────────────────────────────${C_RESET}"
