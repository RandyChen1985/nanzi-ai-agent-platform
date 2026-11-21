// 用 Node 真跑 parentDirectoryOf：它决定「复制并分享」后跳到哪个目录，
// 算错就会跳到不存在的路径（列表空白或 400），源码字符串断言看不出来。
import assert from 'node:assert/strict'

import { parentDirectoryOf } from '../../frontend/src/utils/workspacePath.ts'

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

console.log('工作区路径父目录测试全部通过')
