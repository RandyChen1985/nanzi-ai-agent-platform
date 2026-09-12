#!/usr/bin/env bash

# 允许用户沿用 MySQL 导入脚本的调用方式：sh apply-sql.sh。
# 后续逻辑使用 Bash 数组、[[ ]] 等语法，因此被 sh 解释时必须尽早切回 Bash。
if [ -z "$BASH_VERSION" ]; then
    if command -v bash >/dev/null 2>&1; then
        exec bash "$0" "$@"
    else
        echo "❌ 本脚本需要 bash 支持，但系统未找到 bash。" >&2
        exit 1
    fi
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CALLER_DIR="$PWD"
cd "$ROOT_DIR"

PYTHON_BIN=""
if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
    source "$ROOT_DIR/.venv/bin/activate"
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -f "$ROOT_DIR/venv/bin/activate" ]]; then
    source "$ROOT_DIR/venv/bin/activate"
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

if [[ -z "$PYTHON_BIN" ]] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "❌ 未检测到 Python 运行环境！" >&2
    echo "💡 请先安装 Python 3.11 并配置 PATH，或初始化项目虚拟环境：" >&2
    echo "   python3 -m venv .venv" >&2
    echo "   source .venv/bin/activate" >&2
    echo "   pip install -r requirements.txt" >&2
    exit 1
fi

# 前置依赖检查：在提示输入数据库连接信息前检测必要依赖
if ! "$PYTHON_BIN" -c "import psycopg" >/dev/null 2>&1; then
    CURRENT_PY=$("$PYTHON_BIN" -c "import sys; print(sys.executable)" 2>/dev/null || echo "$PYTHON_BIN")
    echo "❌ Python 环境依赖检查失败：未检测到 'psycopg' 模块。" >&2
    echo "🔍 当前使用的 Python 解释器: $CURRENT_PY" >&2
    echo "💡 请按以下步骤解决：" >&2
    echo "   1. 激活已安装依赖的项目虚拟环境（推荐）：" >&2
    echo "      source .venv/bin/activate   # 或 source venv/bin/activate" >&2
    echo "      pip install -r requirements.txt" >&2
    echo "   2. 或者在当前 Python 环境中单独安装：" >&2
    echo "      $PYTHON_BIN -m pip install 'psycopg[pool,binary]>=3.2,<4'" >&2
    exit 1
fi

SQL_FILES=()
BASELINE_INCLUDED=false
if [[ $# -eq 0 ]]; then
    # 版本文件名由项目约定为 V*.sql，不包含空格；使用命令替换可让 sh
    # 在切回 Bash 前也能解析整个入口脚本。
    for sql_file in $(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'V*.sql' -print | sort -V); do
        SQL_FILES+=("$sql_file")
    done
else
    for sql_file in "$@"; do
        if [[ "$sql_file" = /* ]]; then
            resolved_sql_file="$sql_file"
        elif [[ -f "$CALLER_DIR/$sql_file" ]]; then
            resolved_sql_file="$CALLER_DIR/$sql_file"
        elif [[ -f "$ROOT_DIR/$sql_file" ]]; then
            resolved_sql_file="$ROOT_DIR/$sql_file"
        elif [[ -f "$SCRIPT_DIR/$sql_file" ]]; then
            resolved_sql_file="$SCRIPT_DIR/$sql_file"
        else
            # 保留原始路径，让后面的错误信息继续显示用户传入的值。
            resolved_sql_file="$sql_file"
        fi
        SQL_FILES+=("$resolved_sql_file")
    done
fi

if [[ ${#SQL_FILES[@]} -eq 0 ]]; then
    echo "❌ 未在 $SCRIPT_DIR 中找到 V*.sql 文件。" >&2
    exit 1
fi

for sql_file in "${SQL_FILES[@]}"; do
    if [[ ! -f "$sql_file" ]]; then
        echo "❌ SQL file not found: $sql_file" >&2
        exit 1
    fi
    if [[ "$(basename "$sql_file")" == "V0-baseline.sql" ]]; then
        BASELINE_INCLUDED=true
    fi
done

read -r -p "PostgreSQL host [localhost]: " PG_HOST
read -r -p "PostgreSQL port [5432]: " PG_PORT
read -r -p "PostgreSQL user: " PG_USER
read -r -s -p "PostgreSQL password: " PG_PASSWORD
echo
read -r -p "Target database: " PG_DATABASE
PG_PORT="${PG_PORT:-5432}"
PG_HOST="${PG_HOST:-localhost}"

if [[ -z "$PG_USER" || -z "$PG_DATABASE" ]]; then
    echo "❌ User、Target database 都必须手动输入。" >&2
    exit 1
fi

echo "---------------------------------------------------"
echo "请确认本次 SQL 执行目标："
echo "  Host     : $PG_HOST"
echo "  Port     : $PG_PORT"
echo "  User     : $PG_USER"
echo "  Database : $PG_DATABASE"
echo "  SQL files : 本次共 ${#SQL_FILES[@]} 个脚本需要导入"
echo "  Password : ******"
read -r -p "确认无误请输入 YES 继续执行：" CONFIRM_INPUT
case "$CONFIRM_INPUT" in
    [Yy][Ee][Ss]) ;;
    *)
        echo "❌ 已取消，未执行 SQL。"
        exit 1
        ;;
esac

COMMON_ARGS=(
    --host "$PG_HOST"
    --port "$PG_PORT"
    --user "$PG_USER"
    --password "$PG_PASSWORD"
    --database "$PG_DATABASE"
    --yes
)

for ((index = 0; index < ${#SQL_FILES[@]}; index++)); do
    sql_file="${SQL_FILES[$index]}"
    script_number=$((index + 1))
    echo "---------------------------------------------------"
    echo "🚀 正在导入第 ${script_number}/${#SQL_FILES[@]} 个脚本..."
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/apply_sql.py" "$sql_file" "${COMMON_ARGS[@]}"; then
        echo "❌ 导入失败：$(basename "$sql_file")" >&2
        exit 1
    fi
done

echo "---------------------------------------------------"
echo "✅ 所有 PostgreSQL 版本 SQL 文件执行成功。"

if [[ "$BASELINE_INCLUDED" != "true" ]]; then
    exit 0
fi

echo "---------------------------------------------------"
read -r -p "是否需要顺带创建默认管理员 admin 并生成新的 API Key？ (推荐首次部署时创建) [Y/N]: " RUN_INIT_ADMIN
case "$RUN_INIT_ADMIN" in
    [Yy]|[Yy][Ee][Ss]) ;;
    *)
        echo "💡 已跳过管理员创建，可稍后运行 ./db-prod-pg/create-admin-user.sh。"
        exit 0
        ;;
esac

echo "🚀 正在创建默认管理员账号..."
if DATABASE_TYPE=postgresql \
    POSTGRES_HOST="$PG_HOST" \
    POSTGRES_PORT="$PG_PORT" \
    POSTGRES_USER="$PG_USER" \
    POSTGRES_PASSWORD="$PG_PASSWORD" \
    POSTGRES_DB="$PG_DATABASE" \
    "$PYTHON_BIN" "$ROOT_DIR/scripts/create_admin_user.py"; then
    echo "✅ 默认管理员账号创建完成。"
    echo "   如需重新生成 API Key：./db-prod-pg/create-admin-key.sh"
    echo "   如需设置登录密码：./db-prod-pg/reset-admin-password.sh"
else
    status=$?
    echo "❌ 默认管理员账号创建失败。" >&2
    exit "$status"
fi
