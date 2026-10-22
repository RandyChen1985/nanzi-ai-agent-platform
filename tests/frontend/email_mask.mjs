import assert from "node:assert/strict"
import { maskEmail } from "../../frontend/src/utils/maskEmail.ts"

assert.equal(maskEmail("zhangsan@corp.example.com"), "z***@corp.example.com")
assert.equal(maskEmail("  A@b.cn  "), "A***@b.cn")
assert.equal(maskEmail("a@b.cn"), "a***@b.cn")
assert.equal(maskEmail(""), "")
assert.equal(maskEmail(null), "")
assert.equal(maskEmail(undefined), "")
assert.equal(maskEmail("no-at-sign"), "***")
assert.equal(maskEmail("user@"), "***")
assert.equal(maskEmail("@corp.com"), "***")

console.log("ok")
