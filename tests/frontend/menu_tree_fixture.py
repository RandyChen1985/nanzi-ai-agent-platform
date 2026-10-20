"""权限树的测试夹具。

菜单数据已入库（`ai_agent_menus`），Python 侧的唯一种子源是
`app/services/menu_seed.py`。前端 `constants/permissions.ts` 只剩类型与纯函数，
因此测试不能再从那里读树，统一在这里把种子转换成前端的 `PermissionNode` 形状：

    { id: 权限码（无码节点用 code，如分组容器）, label, children }

这样「数据源只有写操作权限点」「用户管理能级联到 4 个功能点」这类断言仍然打真实数据，
而不是复制一份树到测试里——复制出来的树会和上线内容悄悄分叉。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.services.menu_seed import BUILTIN_MENU_SEED

ROOT = Path(__file__).resolve().parents[2]


def frontend_permission_tree() -> list[dict]:
    """按父子关系把内置种子转成三层权限树（分组 → 页面 → 功能点）。"""
    by_parent: dict[str | None, list] = {}
    for node in BUILTIN_MENU_SEED:
        by_parent.setdefault(node.parent_code, []).append(node)
    for siblings in by_parent.values():
        siblings.sort(key=lambda item: (item.sort_order, item.code))

    def build(parent_code: str | None) -> list[dict]:
        return [
            {
                "id": node.permission_code or node.code,
                "label": node.label,
                "children": build(node.code),
            }
            for node in by_parent.get(parent_code, [])
        ]

    return build(None)


def run_node(expression: str):
    """在 Node 里加载前端纯函数 + 注入权限树，执行一段表达式并返回其结果。

    调用方可直接使用 `TREE`（权限树）与 `api.*`（`constants/permissions.ts`
    与 `utils/permissionLists.ts` 的导出）。
    """
    tree_literal = json.dumps(frontend_permission_tree(), ensure_ascii=False)
    script = f"""
(async () => {{
const ts = require('./frontend/node_modules/typescript');
const fs = require('fs');
const load = (file) => {{
  const source = fs.readFileSync(file, 'utf8');
  const code = ts.transpileModule(source, {{
    compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
  }}).outputText;
  const moduleRef = {{ exports: {{}} }};
  new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
  return moduleRef.exports;
}};
const api = Object.assign(
  {{}},
  load('frontend/src/constants/permissions.ts'),
  load('frontend/src/utils/permissionLists.ts'),
);
const TREE = {tree_literal};
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)
