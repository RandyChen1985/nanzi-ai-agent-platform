import assert from "node:assert/strict"
import { userInitialOf } from "../../frontend/src/utils/userInitial.ts"

// 英文：取前两个字母并大写，空格与标点不算字符
assert.equal(userInitialOf("demo"), "DE")
assert.equal(userInitialOf("Demo"), "DE")
assert.equal(userInitialOf("Eric Wang"), "ER")
assert.equal(userInitialOf("eric.wang@corp.com"), "ER")
assert.equal(userInitialOf("A"), "A")
assert.equal(userInitialOf("12345"), "12")

// 中文：取后两个汉字；只有两个字就是整名
assert.equal(userInitialOf("李世民"), "世民")
assert.equal(userInitialOf("曹老板"), "老板")
assert.equal(userInitialOf("俊超"), "俊超")
assert.equal(userInitialOf("王"), "王")
assert.equal(userInitialOf("  张 三  "), "张三")

// 混合：含汉字时按汉字取，空格不参与计数
assert.equal(userInitialOf("李 世民"), "世民")
assert.equal(userInitialOf("Eric 王"), "王")

// emoji 按码点处理，不会被截成半个代理对
assert.equal(userInitialOf("😀abc"), "AB")

// 缺失与兜底
assert.equal(userInitialOf(""), "U")
assert.equal(userInitialOf("   "), "U")
assert.equal(userInitialOf(null), "U")
assert.equal(userInitialOf(undefined), "U")
assert.equal(userInitialOf(undefined, "?"), "?")
assert.equal(userInitialOf(null, "US"), "US")

console.log("ok")
