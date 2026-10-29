#!/usr/bin/env bash
# MCP 工具进度流自检：判断断点落在「后端传输 / 后端事件映射 / 前端构建」哪一层。
#
# 用法（项目根目录）：
#   bash scripts/check_mcp_progress_wiring.sh
#
# 只需一次调用 echo_slow 之后运行，最后一段会读取最近的后端日志。
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

fail=0

echo "== 1. 后端源码：Streamable HTTP 优先（否则进度通道根本不存在）"
if grep -q "Streamable HTTP initialized" app/services/ai/tools/mcp_client.py 2>/dev/null; then
  echo "   OK"
else
  echo "   FAIL  app/services/ai/tools/mcp_client.py 没有 streamable 优先逻辑"
  fail=1
fi

echo
echo "== 2. 后端源码：进度 chunk → CustomEvent 映射"
if grep -q "mcp_progress" app/services/ai/runtime/agentscope/event_stream.py 2>/dev/null; then
  echo "   OK"
else
  echo "   FAIL  event_stream.py 缺 mcp_progress 映射"
  fail=1
fi

echo
echo "== 3. 前端构建产物：是否含 tool_progress 处理"
found=""
for f in frontend/dist/assets/*.js; do
  [ -e "$f" ] || continue
  if grep -q "tool_progress" "$f" 2>/dev/null; then found="$f"; break; fi
done
if [ -n "$found" ]; then
  echo "   OK  命中 $found"
elif [ -d frontend/dist ]; then
  echo "   FAIL  dist 存在但没有任何 js 含 tool_progress → 前端没有重新构建"
  fail=1
else
  echo "   SKIP  没有 frontend/dist（可能跑的是 vite dev server，需确认它已重启）"
fi

echo
echo "== 4. 后端日志（最近 30 分钟）"
if command -v docker >/dev/null 2>&1 && docker compose ps >/dev/null 2>&1; then
  logs="$(docker compose logs --since 30m 2>/dev/null \
    | grep -E "Streamable HTTP|tool progress streaming|Standard SSE unavailable" \
    | tail -12)"
  if [ -n "$logs" ]; then
    echo "$logs"
  else
    echo "   （没抓到相关日志：调用发生在 30 分钟前，或后端没重启到最新代码）"
  fi
else
  echo "   SKIP  当前目录没有可用的 docker compose"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "结论：源码与构建产物都是新版；若界面仍无进度条，看第 4 段日志是否出现"
  echo "      「tool progress streaming」——有则是前端渲染问题，没有则是远端没上报。"
else
  echo "结论：上面标 FAIL 的项就是断点。"
fi
