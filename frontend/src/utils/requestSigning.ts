/**
 * 请求签名 v1 的前端纯函数与接入说明常量。
 *
 * 密钥只在这个浏览器里生成，提交后服务端加密保存；任何响应都不会把密钥回显。
 * 这里不实现签名本身——签名必须由平台运行时用最终发出的请求计算，前端复制一份
 * 算法只会制造「界面算出来和实际发出去不一致」的第二套口径。
 */

export const SIGNING_ALGORITHM = 'hmac-sha256'
export const SIGNING_SECRET_PATTERN = /^[\x21-\x7e]{32,128}$/
export const SIGNING_KEY_ID_PATTERN = /^[0-9a-f]{16}$/

export type SigningFormValue = {
  enabled: boolean
  keyId: string | null
  secret: string
  configured: boolean
}

export function generateSigningKeyId(): string {
  return randomHex(8)
}

export function generateSigningSecret(): string {
  return randomHex(32)
}

function randomHex(byteLength: number): string {
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error('当前浏览器不支持安全随机数，无法生成签名密钥')
  }
  const bytes = new Uint8Array(byteLength)
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function isValidSigningKeyId(value: string | null | undefined): boolean {
  return SIGNING_KEY_ID_PATTERN.test(String(value || '').trim())
}

export function isValidSigningSecret(value: string | null | undefined): boolean {
  return SIGNING_SECRET_PATTERN.test(String(value || ''))
}

/** 保存前校验；返回第一条可读错误，空串表示通过。 */
export function validateSigningForm(value: SigningFormValue): string {
  if (!value.enabled) return ''
  if (!isValidSigningKeyId(value.keyId)) {
    return '签名 Key-Id 必须是 16 位小写十六进制，请重新生成'
  }
  if (!value.secret && !value.configured) {
    return '开启请求签名前请先生成或填写签名密钥'
  }
  if (value.secret && !isValidSigningSecret(value.secret)) {
    return '签名密钥必须是 32-128 个可打印 ASCII 字符'
  }
  return ''
}

export const SIGNING_HEADERS = [
  'X-Nanzi-Signature-Version',
  'X-Nanzi-Key-Id',
  'X-Nanzi-Timestamp',
  'X-Nanzi-Nonce',
  'X-Nanzi-Signature',
] as const

/** 帮助弹层里的固定测试向量：与后端单测共用同一组值。 */
export const SIGNING_TEST_VECTOR = {
  secret: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  keyId: '8f3c2a1b7d9e4c60',
  timestamp: '1729334695',
  nonce: '5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f',
  method: 'POST',
  path: '/api/orders',
  query: 'a=1&b=2',
  body: '{"amount":100,"applicant":"alice"}',
  bodyHash: '191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164',
  signature: '9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad',
} as const

export const SIGNING_CANONICAL_EXAMPLE = `1
POST
/api/orders
a=1&b=2
191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164
1729334695
5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f
8f3c2a1b7d9e4c60`

export const SIGNING_PYTHON_EXAMPLE = `import hashlib
import hmac
from urllib.parse import parse_qsl, quote

def canonical_query(raw_query: str) -> str:
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    pairs.sort(key=lambda pair: (
        pair[0].encode("utf-8"), pair[1].encode("utf-8")
    ))
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in pairs
    )

# request 代表你所在框架的请求适配层；下面三个值都要取**原始**字节/字符串
raw_path = raw_path_from_request(request)      # 百分号编码后的 path，不含 ?
raw_query = raw_query_from_request(request)    # 原始 query，保留重复 key 与空值
raw_body = raw_body_from_request(request)      # 原始请求体字节，不要先解析 JSON

secret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
key_id = request.headers["X-Nanzi-Key-Id"]
timestamp = request.headers["X-Nanzi-Timestamp"]
nonce = request.headers["X-Nanzi-Nonce"]

canonical = "\\n".join([
    "1",
    request.method.upper(),
    raw_path,
    canonical_query(raw_query),
    hashlib.sha256(raw_body).hexdigest(),
    timestamp,
    nonce,
    key_id,
])

expected = hmac.new(
    secret.encode("utf-8"),
    canonical.encode("utf-8"),
    hashlib.sha256,
).hexdigest()

# 用 hmac.compare_digest(expected, request.headers["X-Nanzi-Signature"]) 比较`

export const SIGNING_CURL_EXAMPLE = `curl -X POST 'https://api.example.com/api/orders?a=1&b=2' \\
  -H 'Content-Type: application/json' \\
  -H 'X-Nanzi-Signature-Version: 1' \\
  -H 'X-Nanzi-Key-Id: 8f3c2a1b7d9e4c60' \\
  -H 'X-Nanzi-Timestamp: 1729334695' \\
  -H 'X-Nanzi-Nonce: 5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f' \\
  -H 'X-Nanzi-Signature: 9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad' \\
  -d '{"amount":100,"applicant":"alice"}'`
