# 裸文件名被当成域名：`北京…清单.md` 点开是 DNS 解析失败

- 日期：2026-10-11
- 相关：`frontend/src/utils/markdownBareFilename.ts`（新增）、`frontend/src/utils/markdown.ts`（`md` 与 `mdPreview` 各注册一次）
- 测试：`tests/frontend/markdown_bare_filename.mjs`（行为）、`tests/frontend/test_markdown_bare_filename_contract.py`（契约）

## 1. 现象（用户实测）

正文「文件信息」表格里，「文件名」单元格的 `北京到上海高铁车次参考清单.md` 被渲染成蓝色链接，后面还跟着一个「打开」按钮。点链接后浏览器地址栏变成 `北京到上海高铁车次参考清单.md`，报 `DNS_PROBE_FINISHED_NXDOMAIN`（无法访问此网站）。

用户的原话判断是对的：**「只有一个文件名的也会渲染可以点击打开？没有路径应该不需要渲染」**。

## 2. 根因：linkify-it 把所有 2 字符 ccTLD 都当顶级域

markdown-it 开了 `linkify: true`，其 fuzzy link 由 linkify-it 提供。linkify-it 的 TLD 来源有两处：

1. 显式列表 `tlds_default`（`node_modules/linkify-it/index.mjs:116`）：`biz|com|edu|gov|net|org|pro|web|xxx|aero|asia|coop|info|museum|name|shop|рф`；
2. **自动生成的所有 2 字符 ccTLD 正则** `tlds_2ch_src_re`（同文件 `:113`），其中 `m[acdeghklmnopqrstuvwxyz]` 就包含 **`md`**。

于是「名字.md」满足「`<label>.<tld>`」的 fuzzy 域名形态。实测（Node，markdown-it + linkify）：

```
'北京到上海高铁车次参考清单.md' => <a href="http://xn--fhq3o80gm0a7ipwy22kx5dumb851i5m9abpnbvs.md">北京到上海高铁车次参考清单.md</a>
'script.py'                    => <a href="http://script.py">script.py</a>       # .py = 巴拉圭
'setup.sh'                     => <a href="http://setup.sh">setup.sh</a>          # .sh = 圣赫勒拿
'报告.txt'                     => 报告.txt                                        # .txt 不是 TLD
```

中文名被 punycode 成 `xn--…md`，浏览器再去解析这个「域名」→ NXDOMAIN，与用户截图完全吻合。

顺带说明截图里的「打开」按钮：`appendBrowserOpenActions`（`frontend/src/utils/messageBrowserLinks.ts:78`）只给 `<a>` 且 `href` 以 `http(s)://` 开头（`isBrowserOpenableUrl`，同文件 `:61`）的链接追加按钮 —— 也就是**同样是这个假链接触发的**，修掉 linkify 后按钮随之消失。

## 3. 为什么不改 linkify-it 的 TLD 列表

看起来最直接的做法是「把 `.md` 从 TLD 列表里去掉」，但实测这条路是坏的：

```js
const md2 = new MarkdownIt({ linkify: true });
md2.linkify.tlds('md', false);   // 以为"去掉 md"
md2.renderInline('www.example.com');   // => www.example.com（纯文本！链接能力没了）
md2.renderInline('北京…清单.md');       // => 仍然被链接（假的域名依旧）
```

原因：`tlds(list, keepOld)` 是**整体替换**语义（源码 `linkify-it/index.mjs:597`），传 `keepOld = false` 会置上 `__tlds_replaced__`，从而**不再追加那份 2 字符 ccTLD 正则**（`:151`）—— 于是 `.com` 之类反而失效，而 `md` 因为被塞进了显式列表仍被当成 TLD。linkify-it 也没有「移除单个 TLD」的公开 API。

而且「把所有 2 字符 ccTLD 一刀切」会误伤 `.cn`/`.io`/`.ai` 这些真实域名（它们同样是 2 字符）。**不能为了修文件名而砍掉真实域名的链接能力。**

## 4. 修法：在 linkify 之后按扩展名纠偏

新增零依赖模块 `frontend/src/utils/markdownBareFilename.ts`：

- `isBareFilenameLinkText(text)`：单个令牌（不含空白、`/`、`\`、`@`）、不带协议、且扩展名属于已知文件类型（复用 `documentPreviewFormats.ts` 的 `TEXT_EXTENSIONS` / `DOCUMENT_VIEWER_EXTENSIONS` / `IMAGE_EXTENSIONS` / `OFFICE_EXTENSIONS`，再补压缩包与图片扩展名）→ 判定为文件名；
- `registerBareFilenameUnlink(md)`：注册一条 markdown-it **core 规则**，只处理 `link_open(markup === 'linkify') + text + link_close` 三元组，命中判定就把这三个 token 换成纯文本（文本原样保留）。

关键点：

| 决策 | 理由 |
| --- | --- |
| 只在 `markup === 'linkify'` 时介入 | 用户手写的 `[文字](url)` 不是 `linkify`，天然不受影响。测试里专门有一条「文字恰好是文件名的显式链接」用例 —— 早期漏掉这个判定时，它会把用户手写的链接一起拆掉 |
| 判定依据是**扩展名白名单** | `example.com` 既可能是域名也可能是文件名，无法只靠形态区分；`.com` 不在文件类型集合 → 仍按域名（保留链接）；`.md/.py/.sh` 是已知文件类型 → 按文件名 |
| 不改 TLD 列表 | 见第 3 节 |
| 零依赖 + 显式 `.ts` 后缀 | 行为测试可用 `node --experimental-strip-types` 直接加载真实代码跑真实 markdown-it，而不是复制一份逻辑 |

`markdown.ts` 里两处都注册（`md` 正文、`mdPreview` 预览），避免出现「正文正常、预览里还是打不开」的偏差。

## 5. 验证

**行为测试**（`tests/frontend/markdown_bare_filename.mjs`，真跑 markdown-it）：

- 用户场景：中文名 `.md` 不再是 `<a>`、文本一字不少；
- 同根因：`.py`、`.sh` 以及 `.docx/.json/.txt/.zip/.ts` 一律不成为链接；
- **不能砍掉的能力**：`www.example.com`、`example.com`、`https://example.com/a.md`、`[清单](…)`、`someone@example.md` 仍可点；
- 混排句子：`参见 example.com 与 report.md` 只保留 1 个 `<a>`（域名），文件名保持纯文本；
- **端到端**（含 `appendBrowserOpenActions`）：最终 HTML 里文件名既不是链接、也不带「打开」按钮，而真实域名仍带按钮；
- 判定函数边界：`path/to/report.md`、`someone@example.com`、`README`、`1.5`、`example.cn` 一律不算文件名。

**变异验证 4/4 全部被捕获且字节级还原**：

| 编号 | 变异 | 结果 |
| --- | --- | --- |
| M13 | 判定函数恒为 `false`（等于没修） | 红 |
| M14 | 不再区分 `linkify` 与显式链接（会拆掉用户手写链接） | 红 |
| M15 | 扩展名白名单整体失效 | 红 |
| M16 | 预览渲染器没装插件 | 红 |

> 注：不能「只删一个扩展名来源」来构造变异 —— `.md/.py/.sh` 同时被 `TEXT_EXTENSIONS` 与 `DOCUMENT_VIEWER_EXTENSIONS` 覆盖（冗余防御），删任何一个都不改变行为，故 M15 用「白名单整体失效」。

**回归**：前端契约测试全量 **1698 passed / 1 skipped / 0 failed**（1695 + 本次新增 3 条）；`vue-tsc -b --force` 仍为**既有 63 项**错误、无新增。

## 6. 取舍与遗留

- **`example.md` 这类「既像域名又像文件名」的按文件名处理**（不链接）。真实场景里 `.md` 作为域名的概率远低于作为文件名，且用户点开一个打不开的域名更糟。
- **实测当前只有 `.md/.py/.sh` 真会被 linkify**（其余扩展名对应的 TLD 不在 linkify-it 的列表里），扩展名白名单里其余项是防御性的：linkify-it 升级或新增 gTLD 时不会再退化。
- **不改 linkify-it 版本/打补丁**：当前做法不依赖它的内部结构（只用公开的 core 规则与 token 形态），升级 markdown-it/linkify-it 时可回归。
- 若将来希望「裸文件名也能点开对应产物」，正确做法是**由产物链接语义驱动**（正文里产物 URL 已经会被 `linkifyGeneratedFileUrls` 变成链接），而不是靠"名字像域名"这种启发式。
