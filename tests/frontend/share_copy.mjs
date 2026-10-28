// 用 Node 真跑分享文案拼装：这段文本是用户直接粘贴到微信/飞书里的，
// 多一个空行、少一行密码都会被当成「你漏发了」。
import assert from 'node:assert/strict'

import { FALLBACK_SHARE_TITLE, buildShareCopyText } from '../../frontend/src/utils/shareCopy.ts'

const URL = 'https://ai.example.com/share/AbCdEf123456'

// 密钥分享：主题 + 链接 + 密码，三段齐全且顺序固定
assert.equal(
  buildShareCopyText({ title: '我要查询天气呢', url: URL, secret: 'k3m9x2qp' }),
  ['快来看我分享的会话', '主题：我要查询天气呢', '链接：' + URL, '密码：k3m9x2qp'].join('\n'),
)

// 公开分享：不得出现空的「密码：」，否则对方会以为漏发
const publicText = buildShareCopyText({ title: '公开会话', url: URL })
assert.equal(publicText, ['快来看我分享的会话', '主题：公开会话', '链接：' + URL].join('\n'))
assert.ok(!publicText.includes('密码'))

// 主题缺失/纯空白 -> 兜底文案，不能出现「主题：」后面空着
assert.ok(buildShareCopyText({ title: '', url: URL }).includes('主题：' + FALLBACK_SHARE_TITLE))
assert.ok(buildShareCopyText({ title: '   ', url: URL }).includes('主题：' + FALLBACK_SHARE_TITLE))

// 主题里的换行/制表必须压成单个空格，多行主题会把文案撑散
assert.ok(
  buildShareCopyText({ title: '第一行\n第二行\t带缩进', url: URL }).includes('主题：第一行 第二行 带缩进'),
)

// 密码两侧空白要吃掉（手输时容易带空格），但密码本身不改写
assert.ok(
  buildShareCopyText({ title: 't', url: URL, secret: '  ab12cd34  ' }).includes('密码：ab12cd34'),
)
// 空字符串密码等同公开分享
assert.ok(!buildShareCopyText({ title: 't', url: URL, secret: '   ' }).includes('密码'))

// 链接为空直接返回空串，调用方据此禁用复制按钮（不能拼出「链接：」空行）
assert.equal(buildShareCopyText({ title: 't', url: '   ' }), '')
assert.equal(buildShareCopyText({ title: 't', url: '' }), '')

console.log('会话分享文案测试全部通过')
