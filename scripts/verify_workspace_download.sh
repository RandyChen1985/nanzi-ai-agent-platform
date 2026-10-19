#!/usr/bin/env bash
# 工作区「下载」端到端验证：上传 zip → 下载 → 比对字节 → 对照 preview 仍拒绝。
#
# 用法（先由你在控制台执行 ./dev.sh 启动后端）：
#   BASE=http://127.0.0.1:8001 KEY=<访问凭证> bash scripts/verify_workspace_download.sh
#
# KEY 可在平台「个人中心 → 访问凭证（API KEY）」查看，
# 也可用 scripts/create_key.py <用户名> 生成。
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8001}"
KEY="${KEY:-}"
if [ -z "$KEY" ]; then
  echo "缺少 KEY。用法：BASE=$BASE KEY=<访问凭证> bash $0" >&2
  exit 2
fi

enc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== 1. 定位当前用户的私有工作区 =="
WS="$(curl -sS -H "X-API-Key: $KEY" "$BASE/api/v1/chat/fs/list" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
if payload.get("code") != 200:
    raise SystemExit(f"列目录失败: {payload}")
items = payload["data"]["items"]
print(next(i["path"] for i in items if i.get("is_user_workspace")))')"
echo "   workspace = $WS"

echo "== 2. 造一个 zip，并通过真实上传接口落盘 =="
NAME="verify-download-$(date +%s).zip"
python3 - "$TMP/src.zip" <<'PY'
import sys, zipfile

with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("readme.txt", "hello from verify script")
    archive.writestr("data.csv", "a,b\n1,2\n")
PY
UP="$(curl -sS -H "X-API-Key: $KEY" -F "file=@$TMP/src.zip;filename=$NAME" \
  "$BASE/api/v1/chat/fs/upload?parent_path=$(enc "$WS")")"
STORED="$(printf '%s' "$UP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["stored_name"])')"
TARGET="$WS/$STORED"
echo "   uploaded  = $TARGET"

echo "== 3. GET /api/v1/chat/fs/download（本轮新增端点）=="
curl -sS -D "$TMP/headers.txt" -o "$TMP/down.zip" -H "X-API-Key: $KEY" \
  "$BASE/api/v1/chat/fs/download?path=$(enc "$TARGET")"
grep -iE '^(HTTP/|content-type:|content-disposition:|content-length:)' "$TMP/headers.txt" | sed 's/^/   /'

echo "== 4. 字节一致性 =="
SRC_SHA="$(shasum -a 256 "$TMP/src.zip" | cut -d' ' -f1)"
DOWN_SHA="$(shasum -a 256 "$TMP/down.zip" | cut -d' ' -f1)"
if [ "$SRC_SHA" = "$DOWN_SHA" ]; then
  echo "   OK   sha256=$SRC_SHA"
else
  echo "   FAIL 下载内容与源文件不一致：$SRC_SHA != $DOWN_SHA" >&2
  exit 1
fi

echo "== 5. 对照：预览接口对 zip 仍应 400（白名单语义未被放宽）=="
PREVIEW_CODE="$(curl -sS -o "$TMP/preview.txt" -w '%{http_code}' -H "X-API-Key: $KEY" \
  "$BASE/api/v1/chat/fs/preview?path=$(enc "$TARGET")")"
echo "   preview -> HTTP $PREVIEW_CODE（期望 400）"
sed 's/^/   /' "$TMP/preview.txt"; echo

echo "== 6. 清理：把测试文件移入回收站 =="
curl -sS -o /dev/null -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"path": sys.argv[1]}))' "$TARGET")" \
  "$BASE/api/v1/chat/fs/delete-entry"
echo "   done（测试文件在回收站，可自行清空）"

if [ "$PREVIEW_CODE" != "400" ]; then
  echo
  echo "注意：preview 返回 $PREVIEW_CODE 而非 400，请确认预览语义是否被改动。" >&2
  exit 1
fi

echo
echo "全部通过：zip 可下载（attachment + 原始字节），预览仍拒绝二进制。"
