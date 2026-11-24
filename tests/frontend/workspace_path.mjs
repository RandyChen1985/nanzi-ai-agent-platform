// 用 Node 真跑 parentDirectoryOf：它决定「复制并分享」后跳到哪个目录，
// 算错就会跳到不存在的路径（列表空白或 400），源码字符串断言看不出来。
import assert from 'node:assert/strict'

import {
  buildWorkspaceBreadcrumbs,
  inferDataBaseDir,
  isProtectedWorkspaceEntry,
  isWithinDirectory,
  parentDirectoryOf,
} from '../../frontend/src/utils/workspacePath.ts'

// 顶层文件：父目录是工作区虚拟根，用空串表达
assert.equal(parentDirectoryOf('/a.md'), '')
// 一层、两层
assert.equal(parentDirectoryOf('/dir/a.md'), '/dir')
assert.equal(parentDirectoryOf('/dir/sub/a.md'), '/dir/sub')
// 中文目录与空格（真实工作区里都有）
assert.equal(parentDirectoryOf('/我的目录/docs/会议纪要.md'), '/我的目录/docs')
assert.equal(parentDirectoryOf('/我的目录/a b.md'), '/我的目录')
// 容忍尾斜杠：/dir/ 本身是目录，父目录回到根
assert.equal(parentDirectoryOf('/dir/a.md/'), '/dir')
assert.equal(parentDirectoryOf('/dir/'), '')
// 相对路径没有父目录信息，不能瞎猜成 '/' 之类
assert.equal(parentDirectoryOf('a.md'), '')
assert.equal(parentDirectoryOf('docs/a.md'), 'docs')
// 空值与根
assert.equal(parentDirectoryOf(''), '')
assert.equal(parentDirectoryOf('/'), '')

// 回归点：后端返回的 path 是绝对路径，父目录必须仍是绝对路径
assert.ok(parentDirectoryOf('/agent_workspaces/admin__1/copy_of_x.md').startsWith('/'))

// 前缀边界：u1 绝不能误匹配 u10
assert.equal(isWithinDirectory('/app/data/agent_workspaces/u1/docs', '/app/data/agent_workspaces/u1'), true)
assert.equal(isWithinDirectory('/app/data/agent_workspaces/u10/docs', '/app/data/agent_workspaces/u1'), false)
assert.equal(inferDataBaseDir('/app/data/agent_workspaces/u1_alice'), '/app/data')

// 面包屑：本人目录与子目录
const USER_ROOT = '/app/data/agent_workspaces/u1_alice'
assert.deepEqual(
  buildWorkspaceBreadcrumbs({ currentPath: `${USER_ROOT}/docs/sub`, userWorkspaceRoot: USER_ROOT }),
  [
    { label: '我的目录', path: USER_ROOT },
    { label: 'docs', path: `${USER_ROOT}/docs` },
    { label: 'sub', path: `${USER_ROOT}/docs/sub` },
  ],
)

// 面包屑：进入公共目录 docs/guide，不得拼到 userWorkspaceRoot 下面（原 Bug 会拼成 u1_alice/app/data/docs）
assert.deepEqual(
  buildWorkspaceBreadcrumbs({ currentPath: '/app/data/docs/guide', userWorkspaceRoot: USER_ROOT }),
  [
    { label: '公共资源', path: '' },
    { label: 'docs', path: '/app/data/docs' },
    { label: 'guide', path: '/app/data/docs/guide' },
  ],
)

// 面包屑：管理员浏览 data 根目录或其他用户目录
assert.deepEqual(
  buildWorkspaceBreadcrumbs({ currentPath: '/app/data', userWorkspaceRoot: USER_ROOT }),
  [{ label: '公共资源', path: '' }],
)
assert.deepEqual(
  buildWorkspaceBreadcrumbs({ currentPath: '/app/data/agent_workspaces/u2_bob', userWorkspaceRoot: USER_ROOT }),
  [
    { label: '公共资源', path: '' },
    { label: 'agent_workspaces', path: '/app/data/agent_workspaces' },
    { label: 'u2_bob', path: '/app/data/agent_workspaces/u2_bob' },
  ],
)

// 面包屑：平台外部 skills 根（本地开发 ~/.agents/skills）
assert.deepEqual(
  buildWorkspaceBreadcrumbs({ currentPath: '/Users/dev/.agents/skills/my-skill', userWorkspaceRoot: USER_ROOT }),
  [
    { label: '公共资源', path: '' },
    { label: 'skills', path: '/Users/dev/.agents/skills' },
    { label: 'my-skill', path: '/Users/dev/.agents/skills/my-skill' },
  ],
)

// 保护目录判定：只保护根级保留目录与用户根目录，不误伤子目录同名 docs / uploads
assert.equal(
  isProtectedWorkspaceEntry({ name: 'docs', path: `${USER_ROOT}/docs`, is_dir: true }, USER_ROOT),
  true,
)
assert.equal(
  isProtectedWorkspaceEntry({ name: '.trash', path: `${USER_ROOT}/.trash`, is_dir: true }, USER_ROOT),
  true,
)
assert.equal(
  isProtectedWorkspaceEntry({ name: 'docs', path: '/app/data/docs', is_dir: true, is_public: true }, USER_ROOT),
  true,
)
assert.equal(
  isProtectedWorkspaceEntry({ name: 'u2_bob', path: '/app/data/agent_workspaces/u2_bob', is_dir: true }, USER_ROOT),
  true,
)
assert.equal(
  isProtectedWorkspaceEntry({ name: 'docs', path: `${USER_ROOT}/my-project/docs`, is_dir: true }, USER_ROOT),
  false,
)
assert.equal(
  isProtectedWorkspaceEntry({ name: 'uploads', path: `${USER_ROOT}/my-project/uploads`, is_dir: true }, USER_ROOT),
  false,
)

console.log('工作区路径父目录测试全部通过')
