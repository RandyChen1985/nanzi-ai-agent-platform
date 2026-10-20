import assert from 'node:assert/strict'
import {
  SIGNING_CANONICAL_EXAMPLE,
  SIGNING_TEST_VECTOR,
  generateSigningKeyId,
  generateSigningSecret,
  isValidSigningKeyId,
  isValidSigningSecret,
  validateSigningForm,
} from '../../frontend/src/utils/requestSigning.ts'

const keyId = generateSigningKeyId()
const secret = generateSigningSecret()
assert.match(keyId, /^[0-9a-f]{16}$/)
assert.match(secret, /^[0-9a-f]{64}$/)
assert.equal(isValidSigningKeyId(keyId), true)
assert.equal(isValidSigningSecret(secret), true)
assert.equal(isValidSigningKeyId('not-a-key'), false)
assert.equal(isValidSigningSecret('short'), false)

assert.notEqual(generateSigningSecret(), generateSigningSecret())
assert.equal(validateSigningForm({ enabled: false, keyId: null, secret: '', configured: false }), '')
assert.match(
  validateSigningForm({ enabled: true, keyId: null, secret: '', configured: false }),
  /Key-Id/,
)
assert.match(
  validateSigningForm({ enabled: true, keyId, secret: '', configured: false }),
  /密钥/,
)
assert.equal(
  validateSigningForm({ enabled: true, keyId, secret: '', configured: true }),
  '',
)
assert.equal(
  validateSigningForm({ enabled: true, keyId, secret, configured: false }),
  '',
)
assert.match(
  validateSigningForm({ enabled: true, keyId, secret: 'short', configured: true }),
  /32-128/,
)

assert.equal(SIGNING_TEST_VECTOR.signature.length, 64)
assert.equal(SIGNING_TEST_VECTOR.bodyHash.length, 64)
assert.deepEqual(SIGNING_CANONICAL_EXAMPLE.split('\n'), [
  '1',
  SIGNING_TEST_VECTOR.method,
  SIGNING_TEST_VECTOR.path,
  SIGNING_TEST_VECTOR.query,
  SIGNING_TEST_VECTOR.bodyHash,
  SIGNING_TEST_VECTOR.timestamp,
  SIGNING_TEST_VECTOR.nonce,
  SIGNING_TEST_VECTOR.keyId,
])
console.log('request signing utils: ok')
