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
    echo "❌ 未检测到 Python 运行环境！"
    echo "💡 请先安装 Python 3.11 并配置 PATH，或初始化项目虚拟环境："
    echo "   python3 -m venv .venv"
    echo "   source .venv/bin/activate"
    echo "   pip install -r requirements.txt"
    exit 1
fi

# 前置依赖检查：在提示输入数据库连接信息前检测必要依赖
if ! "$PYTHON_BIN" -c "import aiomysql" >/dev/null 2>&1; then
    CURRENT_PY=$("$PYTHON_BIN" -c "import sys; print(sys.executable)" 2>/dev/null || echo "$PYTHON_BIN")
    echo "❌ Python 环境依赖检查失败：未检测到 'aiomysql' 模块。"
    echo "🔍 当前使用的 Python 解释器: $CURRENT_PY"
    echo "💡 请按以下步骤解决："
    echo "   1. 激活已安装依赖的项目虚拟环境（推荐）："
    echo "      source .venv/bin/activate   # 或 source venv/bin/activate"
    echo "      pip install -r requirements.txt"
    echo "   2. 或者在当前 Python 环境中单独安装："
    echo "      $PYTHON_BIN -m pip install aiomysql"
    exit 1
fi

SQL_FILES=()
if [ $# -gt 0 ]; then
    for sql_file in "$@"; do
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
    done
fi

read -r -p "MySQL host [localhost]: " MYSQL_HOST_INPUT
read -r -p "MySQL port [3306]: " MYSQL_PORT_INPUT
read -r -p "MySQL user: " MYSQL_USER_INPUT
read -r -s -p "MySQL password: " MYSQL_PASSWORD_INPUT
echo
read -r -p "Target database: " MYSQL_DATABASE_INPUT

MYSQL_HOST_INPUT=${MYSQL_HOST_INPUT:-localhost}
MYSQL_PORT_INPUT=${MYSQL_PORT_INPUT:-3306}

if [ -z "$MYSQL_USER_INPUT" ] || [ -z "$MYSQL_DATABASE_INPUT" ]; then
    echo "❌ User、Target database 都必须手动输入。"
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

echo "🔍 正在检查目标数据库字符集..."
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
    echo "❌ 字符集检查失败，未执行 SQL。"
    exit 1
fi

echo "---------------------------------------------------"
echo "请确认本次 SQL 执行目标："
echo "  Host     : $MYSQL_HOST_INPUT"
echo "  Port     : $MYSQL_PORT_INPUT"
echo "  User     : $MYSQL_USER_INPUT"
echo "  Database : $MYSQL_DATABASE_INPUT"
echo "  Password : ******"
if [ $# -eq 0 ]; then
    echo "  SQL files: db-prod/V*.sql"
else
    echo "  SQL file : ${SQL_FILES[*]}"
fi
if [ "$CHARSET_CHECK_STATUS" -eq 2 ]; then
    read -r -p "目标库字符集存在风险；确认目标并接受风险请输入 YES 继续执行：" CONFIRM_INPUT
else
    read -r -p "确认无误请输入 YES 继续执行：" CONFIRM_INPUT
fi
CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo "❌ 已取消，未执行 SQL。"
    exit 1
fi

if [ $# -eq 0 ]; then
    echo "No arguments provided. Running all SQL files from db-prod/..."
    DB_DIR="db-prod"
    
    if [ ! -d "$DB_DIR" ]; then
        echo "❌ Directory $DB_DIR not found!"
        exit 1
    fi

    # Sort files by version (V1, V2, ... V10)
    FILES=$(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V)
    
    if [ -z "$FILES" ]; then
        echo "❌ No SQL files found in $DB_DIR/"
        exit 1
    fi
    
    for f in $FILES; do
        echo "---------------------------------------------------"
        echo "🚀 Applying $f..."
        "$PYTHON_BIN" db-prod/apply_sql.py "$f" "${COMMON_ARGS[@]}"
        if [ $? -ne 0 ]; then
             echo "❌ Failed to apply $f"
             exit 1
        fi
    done
    echo "---------------------------------------------------"
    echo "✅ 所有数据库结构初始化迁移 SQL 文件执行成功。"
    
    # 交互询问是否导入管理员账号
    read -r -p "是否需要顺带导入默认管理员账号和预置 API Key 凭证？ (推荐首次部署时导入) [Y/N]: " RUN_INIT_ADMIN
    RUN_INIT_ADMIN_UPPER=$(echo "$RUN_INIT_ADMIN" | tr '[:lower:]' '[:upper:]')
    if [ "$RUN_INIT_ADMIN_UPPER" == "Y" ] || [ "$RUN_INIT_ADMIN_UPPER" == "YES" ]; then
        ADMIN_SQL="db-prod/INIT-USER-ADMIN.sql"
        if [ -f "$ADMIN_SQL" ]; then
            echo "---------------------------------------------------"
            echo "🚀 正在导入默认管理员账号数据 ($ADMIN_SQL)..."
            "$PYTHON_BIN" db-prod/apply_sql.py "$ADMIN_SQL" "${COMMON_ARGS[@]}"
            if [ $? -ne 0 ]; then
                 echo "❌ 默认管理员账号数据导入失败。"
                 exit 1
            fi
            echo "---------------------------------------------------"
            echo "✅ 默认管理员账号数据导入成功！"
            echo -e "\033[1;32m===================================================\033[0m"
            echo -e "\033[1;32m🔑 首次登录重要指引：\033[0m"
            echo -e "  - \033[1;36m默认用户名\033[0m  : admin"
            echo -e "  - \033[1;36m预置 API Key\033[0m: 5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c"
            echo -e "  - \033[1;33m登录方式\033[0m    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
            echo -e "  - \033[1;31m安全提醒\033[0m    : 首次登录成功后，请务必前往【用户管理】"
            echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
            echo -e "\033[1;32m===================================================\033[0m"
        else
            echo "⚠️ 未找到默认管理员数据文件 $ADMIN_SQL，跳过导入。"
        fi
    else
        echo "💡 已跳过默认管理员账号数据的导入。"
    fi
else
    "$PYTHON_BIN" db-prod/apply_sql.py "${SQL_FILES[@]}" "${COMMON_ARGS[@]}"
    for f in "${SQL_FILES[@]}"; do
        if [[ "$f" =~ INIT-USER-ADMIN.sql$ ]]; then
            echo -e "\033[1;32m===================================================\033[0m"
            echo -e "\033[1;32m🔑 首次登录重要指引：\033[0m"
            echo -e "  - \033[1;36m默认用户名\033[0m  : admin"
            echo -e "  - \033[1;36m预置 API Key\033[0m: 5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c"
            echo -e "  - \033[1;33m登录方式\033[0m    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
            echo -e "  - \033[1;31m安全提醒\033[0m    : 首次登录成功后，请务必前往【用户管理】"
            echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
            echo -e "\033[1;32m===================================================\033[0m"
        fi
    done
fi
