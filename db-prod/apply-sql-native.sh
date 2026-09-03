#!/bin/bash
# 免 Python 依赖的 MySQL SQL 导入工具。
# 依靠系统已安装的 mysql 命令行客户端。
# 实现了与 Python 脚本相同的幂等性过滤机制（忽略 1007, 1050, 1054, 1060, 1061, 1062, 1091 等错误码）。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CALLER_DIR="$PWD"
cd "$ROOT_DIR"

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

# 默认端口
MYSQL_PORT_INPUT=3306

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

# 检查是否有 mysql 客户端
if ! command -v mysql >/dev/null 2>&1; then
    echo "❌ 错误: 未在系统 PATH 中找到 'mysql' 命令行客户端。"
    echo "请先安装 mysql-client，或使用带 Python 虚拟环境的 ./apply-sql.sh 脚本。"
    exit 1
fi

# 强制 TCP：Host 为 localhost 时，mysql 客户端默认走 Unix socket（/tmp/mysql.sock），
# 在 Lima/Docker 端口转发场景下会失败；--protocol=TCP 可统一走 -P 端口。
# --default-character-set=utf8mb4：客户端按 utf8mb4 发送查询和迁移内容；目标库字符集仍需单独预检。
MYSQL_BASE_CMD="mysql -h $MYSQL_HOST_INPUT -P $MYSQL_PORT_INPUT -u $MYSQL_USER_INPUT -p$MYSQL_PASSWORD_INPUT --protocol=TCP --default-character-set=utf8mb4"

# 在创建数据库之前查询 information_schema，避免把客户端字符集误当成目标库字符集。
MYSQL_DATABASE_SQL=$(printf '%s' "$MYSQL_DATABASE_INPUT" | sed "s/'/''/g")
CHARSET_QUERY_SQL="SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '$MYSQL_DATABASE_SQL';"
CHARSET_ERR=$(mktemp)
CHARSET_INFO=$(printf '%s\n' "$CHARSET_QUERY_SQL" | $MYSQL_BASE_CMD --batch --skip-column-names 2>"$CHARSET_ERR")
CHARSET_STATUS=$?
if [ "$CHARSET_STATUS" -ne 0 ]; then
    echo "❌ 目标数据库字符集检查失败，未执行 SQL。"
    if [ -s "$CHARSET_ERR" ]; then
        echo "—— MySQL 原始错误 ——"
        cat "$CHARSET_ERR"
    fi
    rm -f "$CHARSET_ERR"
    exit 1
fi
rm -f "$CHARSET_ERR"

CHARSET_MISMATCH=0
if [ -z "$CHARSET_INFO" ]; then
    echo "ℹ️ 目标数据库不存在；确认后将以 utf8mb4 创建。"
else
    CHARSET_NAME=$(printf '%s\n' "$CHARSET_INFO" | awk 'NR == 1 {print $1}')
    COLLATION_NAME=$(printf '%s\n' "$CHARSET_INFO" | awk 'NR == 1 {print $2}')
    printf '%s\n' "ℹ️ 目标数据库默认字符集: ${CHARSET_NAME}，排序规则: ${COLLATION_NAME}"
    CHARSET_NAME_NORMALIZED=$(printf '%s' "$CHARSET_NAME" | tr '[:upper:]' '[:lower:]')
    if [ "$CHARSET_NAME_NORMALIZED" != "utf8mb4" ]; then
        echo "⚠️ 目标数据库默认字符集不是 utf8mb4，导入包含中文的 SQL 时可能出现乱码。"
        echo "⚠️ 如仍要继续，请在下面的确认提示中明确输入 YES。"
        CHARSET_MISMATCH=1
    fi
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
if [ "$CHARSET_MISMATCH" -eq 1 ]; then
    read -r -p "目标库字符集存在风险；确认目标并接受风险请输入 YES 继续执行：" CONFIRM_INPUT
else
    read -r -p "确认无误请输入 YES 继续执行：" CONFIRM_INPUT
fi
CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo "❌ 已取消，未执行 SQL。"
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
        set +e
        # 每条语句单独 session：必须每次先 SET NAMES，否则中文提示词等会乱码。
        # 标准输出静音，避免 SELECT 1 等幂等探测语句在终端刷屏；错误仍写入 err_log
        printf 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;\n%s\n' "$payload" | $MYSQL_CMD >/dev/null 2>"$err_log"
        local status=$?
        set -e
        if [ $status -ne 0 ]; then
            local err_msg
            err_msg=$(cat "$err_log")
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
                    break
                fi
            done
            if [ $is_ignored -eq 0 ]; then
                echo "❌ 执行失败（非幂等可忽略错误，需处理）："
                echo "Statement: ${preview:0:150}..."
                echo "Error message: $err_msg"
                return 1
            fi
        fi
        return 0
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
        # 若上面循环里已有业务语句执行过，这里不必再跑纯 SET；仅当文件全是 SET @ 时才需要
        # 用简单启发：若刚读完文件且从未执行业务语句——无法廉价判断，跳过即可（SET @ 单独无副作用）
        :
    fi
    
    rm -f "$err_log"
    return 0
}

if [ $# -eq 0 ]; then
    DB_DIR="db-prod"
    if [ ! -d "$DB_DIR" ]; then
        echo "❌ 找不到目录 $DB_DIR"
        exit 1
    fi

    # 按版本自然顺序排序
    FILES=$(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V)
    if [ -z "$FILES" ]; then
        echo "❌ 未在 $DB_DIR 中找到任何 V*.sql 迁移文件"
        exit 1
    fi

    for f in $FILES; do
        echo "---------------------------------------------------"
        echo "🚀 Applying $f..."
        if ! execute_sql_file "$f"; then
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
            if ! execute_sql_file "$ADMIN_SQL"; then
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
    for f in "${SQL_FILES[@]}"; do
        echo "---------------------------------------------------"
        echo "🚀 Applying $f..."
        if ! execute_sql_file "$f"; then
            echo "❌ Failed to apply $f"
            exit 1
        fi
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
