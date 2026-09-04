# NanZi MCP UserContext 接入指南

本文面向需要接收 NanZi 用户身份的自有 MCP。UserContext 是 MCP 级别的可选能力：只有某个 MCP 开启后，NanZi 才会发送 `X-Nanzi-User-Assertion`；未开启的 MCP 完全沿用旧的固定 Header 调用方式。

## 1. MCP 级配置

平台 MCP 和用户 MCP 都是一条独立的 MCP 配置记录：

```text
平台 CRM MCP
  fixed_token_encrypted
  user_assertion_private_key_encrypted
  user_assertion_key_id
  user_assertion_issuer
  user_assertion_audience

用户 GitLab MCP
  fixed_token_encrypted
  user_assertion_private_key_encrypted
  user_assertion_key_id
  user_assertion_issuer
  user_assertion_audience
```

不同 MCP 不共享固定 Authorization Bearer Token、签名私钥或 Audience。平台 MCP 由管理员维护，用户 MCP 由所属用户维护。签名私钥在 NanZi 后端按 MCP 加密保存，业务 MCP 只保存对应的公钥。

## 2. 请求约定

开启 UserContext 的 MCP 收到：

```http
Authorization: Bearer <该 MCP 的 Token 值>
X-Nanzi-User-Assertion: <该 MCP 对应私钥签发的 JWS>
X-Request-ID: <request-id>
```

未开启 UserContext 的 MCP 只收到原有认证 Header，不会生成或发送 JWS。

用户身份不放在工具 `arguments` 中。固定 Authorization Bearer Token 证明请求来自已登记的 NanZi 客户端，签名断言证明当前用户信息来自 NanZi 且没有被篡改。

## 3. MCP 管理页面

开启某个 MCP 的 UserContext 时配置：

1. 在独立的 Authorization 开关中选择是否启用；开启后只填写 Token 内容，页面自动补全 `Bearer` 前缀；
2. 在“其他 Header”区域按需配置除 Authorization 外的 Header；
3. 按需打开“开启用户身份传递”开关；
4. 保存 MCP。系统会自动生成当前 MCP 的 Audience、Key ID 和签名私钥；Issuer 固定为 `nanzi-platform`。

编辑时 Authorization Token 和其他 Header 值都不会回显，只显示开关状态或 `********`。点击“编辑”后填写新值才会替换原配置；不编辑直接保存会沿用原值。私钥不让用户在页面中填写，由平台为当前 MCP 生成并加密保存。

保存后，MCP 管理页面会以只读方式显示当前 MCP 的 Audience、Issuer 和“公钥获取地址（JWKS）”，并提供复制按钮。把 Audience 复制到业务 MCP 的 `aud` 校验配置，把 Issuer 复制到 `iss` 校验配置，把 JWKS 地址配置到公钥发现配置中。Key ID 不需要单独复制，业务方根据 JWT Header 的 `kid` 从 JWKS 自动选择公钥。

页面下方提供“一键生成调用模拟代码”。点击后选择 Python 或 Java，系统会自动把当前 MCP 的三个只读值带入示例代码。复制全部代码后，将其放入业务 MCP 的认证中间件，并按业务框架补充请求头读取、固定 Authorization Bearer Token 的 Secret 读取和 `jti` 防重放存储。示例代码的核心结果是拿到 `user_context.user_id`，再用它关联业务系统用户。

在 NanZi 的 MCP 工具测试台中，点击“运行测试”时也会按当前登录用户生成并发送 `X-Nanzi-User-Assertion`。测试结果中的“本次调用认证信息”只显示：

```text
X-Nanzi-User-Assertion: ********
Audience / Issuer / Key ID
```

这里的星号只是前端脱敏展示，不是实际发送的值。完整 JWS 只存在于 NanZi 后端发往 MCP 的请求中，不返回浏览器，也不写入普通日志。关闭 UserContext 的 MCP 测试只使用原有认证 Header。

## 4. 公钥发现

业务 MCP 只需要获取本 MCP 对应的公钥。页面显示的地址格式如下：

```http
GET https://nanzi.example.com/.well-known/nanzi/mcp/{mcp_server_id}/jwks.json
```

该地址只返回公钥、`kid`、算法和用途，不返回私钥。业务 MCP 必须按照 JWT Header 中的 `kid` 选择公钥，找不到 `kid` 时拒绝请求。

## 5. Payload 示例

验签后的 Payload 示例：

```json
{
  "iss": "nanzi-platform",
  "aud": "mcp:5b8d9c2e-2ef5-4f4a-8d0c-1a2b3c4d5e6f",
  "sub": "nanzi:user:123",
  "user_context": {
    "user_id": "123",
    "user_name": "zhangsan",
    "real_name": "张三",
    "dept_code": "sales",
    "org_path": "/集团/销售部"
  },
  "custom_attributes": {
    "employee_level": "L3",
    "region_code": "east"
  },
  "agent_id": "agent-sales-assistant",
  "agent_version_id": "agent-version-2026-01",
  "agent_name": "销售助手",
  "request_id": "req-20260901-001",
  "jti": "assertion-uuid",
  "iat": 1788230000,
  "exp": 1788230060
}
```

示例中的 `aud` 是按当前 MCP `server_id` 自动生成的示例值，实际使用时以页面显示的只读 Audience 为准。当前第一期不传 `tenant_id` 和 `scope`。`custom_attributes` 来源于 NanZi 服务端用户资料中的扩展 JSON，平台按默认规则过滤敏感字段；第一期页面不提供扩展字段白名单输入。

### 5.1 字段完整说明

| 字段位置 | 字段 | 是否必有 | 业务方使用方式 |
| --- | --- | --- | --- |
| HTTP Header | `X-Nanzi-User-Assertion` | 开启时必有 | 读取完整 JWS 并交给验签中间件 |
| HTTP Header | `X-Request-ID` | 必有 | 关联两侧请求日志 |
| JWT Header | `alg` | 必有 | 当前为 `EdDSA`（Ed25519） |
| JWT Header | `kid` | 必有 | 从 JWKS 选择对应公钥 |
| JWT Header | `typ` | 必有 | 当前为 `JWT` |
| JWT Payload | `iss` | 必有 | 校验是否为 `nanzi-platform` |
| JWT Payload | `aud` | 必有 | 校验是否为当前 MCP 的系统生成 Audience |
| JWT Payload | `sub` | 必有 | 稳定主体标识，格式为 `nanzi:user:{user_id}` |
| `user_context` | `user_id` | 必有 | 关联业务系统用户 |
| `user_context` | `user_name`、`real_name` | 有值时 | 获取登录名和姓名 |
| `user_context` | `dept_code`、`org_path` | 有值时 | 获取部门和组织信息 |
| `custom_attributes` | 安全扩展 key-value | 必有，可为空对象 | 获取用户资料扩展信息 |
| JWT Payload | `agent_id` | 必有 | 识别发起调用的智能体 |
| JWT Payload | `agent_version_id`、`agent_name` | 有值时 | 识别智能体版本和名称 |
| JWT Payload | `request_id` | 必有 | 关联本次请求链路 |
| JWT Payload | `jti` | 必有 | 防止同一个断言被重复使用 |
| JWT Payload | `iat`、`exp` | 必有 | 校验签发时间和过期时间，默认有效期 60 秒 |

密码、Token、API Key、Cookie、Secret、私钥、Session Token 等敏感字段不会进入 `custom_attributes`。第一期不传 `tenant_id`、`scope` 和完整权限树。

## 6. 业务 MCP 中间件

业务 MCP 中间件应先校验当前 MCP 自己的固定 Authorization Bearer Token，再使用当前 MCP 的 JWKS、公钥、Issuer 和 Audience 验证 User Assertion。下面示例中的配置都属于当前 MCP，不是全局配置。

### 6.1 Python 版本

依赖：

```text
PyJWT>=2.8
cryptography>=42
httpx>=0.27
redis>=5
```

```python
from dataclasses import dataclass
from typing import Any
import hmac
import os

import jwt
from jwt import PyJWKClient


MCP_FIXED_AUTHORIZATION_BEARER_TOKEN = "从当前 MCP 的 Secret 读取"
MCP_ISSUER = "nanzi-platform"
MCP_AUDIENCE = os.environ["NANZI_MCP_AUDIENCE"]  # 使用页面复制的只读 Audience
MCP_JWKS_URL = os.environ["NANZI_MCP_JWKS_URL"]  # 使用页面复制的 JWKS 地址

# 生产环境应按 JWKS URL 缓存 PyJWKClient，不能每次请求都重新创建。
jwk_client = PyJWKClient(MCP_JWKS_URL)


@dataclass(frozen=True)
class McpPrincipal:
    user_id: str
    user_name: str | None
    real_name: str | None
    custom_attributes: dict[str, Any]
    agent_id: str
    agent_version_id: str | None
    request_id: str
    jti: str


def resolve_nanzi_principal(
    authorization: str | None,
    assertion: str | None,
) -> McpPrincipal:
    expected_authorization = f"Bearer {MCP_FIXED_AUTHORIZATION_BEARER_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected_authorization):
        raise PermissionError("invalid MCP client token")
    if not assertion:
        raise PermissionError("missing NanZi user assertion")

    # PyJWKClient 会根据 JWT Header 的 kid 从当前 MCP 的 JWKS 选择公钥。
    signing_key = jwk_client.get_signing_key_from_jwt(assertion).key
    claims = jwt.decode(
        assertion,
        key=signing_key,
        algorithms=["EdDSA"],
        issuer=MCP_ISSUER,
        audience=MCP_AUDIENCE,
        options={"require": ["iss", "aud", "sub", "jti", "iat", "exp"]},
    )
    context = claims.get("user_context")
    if not isinstance(context, dict) or not context.get("user_id"):
        raise PermissionError("missing user context")
    if claims["sub"] != f"nanzi:user:{context['user_id']}":
        raise PermissionError("user subject mismatch")

    # 生产环境还要把 jti 写入短期 Redis/缓存；重复 jti 必须拒绝。
    assert_jti_is_not_replayed(claims["jti"], claims["exp"])
    return McpPrincipal(
        user_id=str(context["user_id"]),
        user_name=context.get("user_name"),
        real_name=context.get("real_name"),
        custom_attributes=claims.get("custom_attributes") or {},
        agent_id=str(claims["agent_id"]),
        agent_version_id=claims.get("agent_version_id"),
        request_id=str(claims["request_id"]),
        jti=str(claims["jti"]),
    )
```

`assert_jti_is_not_replayed` 必须由业务 MCP 自己实现。下面是 Redis 的最小实现示意；生产环境必须使用 Redis `SET NX EX` 等原子操作，不能使用单机内存 Set：

```python
import time
from redis import Redis

# 业务 MCP 启动时创建一个可复用的 Redis 客户端，例如：
redis_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

def assert_jti_is_not_replayed(jti: str, exp: int) -> None:
    ttl = max(1, int(exp - time.time()))
    if not redis_client.set(f"mcp:user-assertion:{jti}", "1", nx=True, ex=ttl):
        raise PermissionError("replayed NanZi user assertion")
```

### 6.2 Java 版本

以下示例使用 Nimbus JOSE + JWT：

```xml
<dependency>
  <groupId>com.nimbusds</groupId>
  <artifactId>nimbus-jose-jwt</artifactId>
  <version>10.8</version>
</dependency>
```

```java
import com.nimbusds.jose.JWSVerifier;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.crypto.Ed25519Verifier;
import com.nimbusds.jose.jwk.JWK;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.OctetKeyPair;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;

import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Date;
import java.util.Map;
import java.util.Set;

public final class NanZiMcpIdentity {
    private final String fixedToken;
    private final String issuer;
    private final String audience;
    private final URL jwksUrl;

    public NanZiMcpIdentity(String fixedToken, String issuer, String audience, URL jwksUrl) {
        this.fixedToken = fixedToken;
        this.issuer = issuer;
        this.audience = audience;
        this.jwksUrl = jwksUrl;
    }

    public McpPrincipal resolve(String authorization, String assertion) throws Exception {
        String expected = "Bearer " + fixedToken;
        if (authorization == null || !MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                authorization.getBytes(StandardCharsets.UTF_8))) {
            throw new SecurityException("invalid MCP client token");
        }
        if (assertion == null || assertion.isBlank()) {
            throw new SecurityException("missing NanZi user assertion");
        }

        SignedJWT jwt = SignedJWT.parse(assertion);
        if (!JWSAlgorithm.EdDSA.equals(jwt.getHeader().getAlgorithm())) {
            throw new SecurityException("unsupported UserContext algorithm");
        }
        String kid = jwt.getHeader().getKeyID();
        JWK jwk = JWKSet.load(jwksUrl).getKeyByKeyId(kid);
        if (!(jwk instanceof OctetKeyPair keyPair)) {
            throw new SecurityException("unknown or invalid UserContext key");
        }

        JWSVerifier verifier = new Ed25519Verifier(keyPair.toPublicJWK(), Set.of());
        if (!jwt.verify(verifier)) {
            throw new SecurityException("invalid UserContext signature");
        }

        JWTClaimsSet claims = jwt.getJWTClaimsSet();
        if (!issuer.equals(claims.getIssuer())) {
            throw new SecurityException("invalid issuer");
        }
        if (claims.getAudience() == null || claims.getAudience().size() != 1
                || !audience.equals(claims.getAudience().get(0))) {
            throw new SecurityException("invalid audience");
        }
        Date expiresAt = claims.getExpirationTime();
        Date issuedAt = claims.getIssueTime();
        Instant now = Instant.now();
        if (expiresAt == null || issuedAt == null
                || now.isAfter(expiresAt.toInstant())
                || now.plusSeconds(30).isBefore(issuedAt.toInstant())) {
            throw new SecurityException("expired or not-yet-valid UserContext");
        }

        Map<String, Object> context = (Map<String, Object>) claims.getClaim("user_context");
        if (context == null || context.get("user_id") == null) {
            throw new SecurityException("missing user context");
        }
        String userId = String.valueOf(context.get("user_id"));
        if (!String.valueOf(claims.getSubject()).equals("nanzi:user:" + userId)) {
            throw new SecurityException("user subject mismatch");
        }

        // 生产环境应使用 Redis SETNX(jti, exp) 防止重放。
        assertJtiIsNotReplayed(claims.getJWTID(), expiresAt);
        return new McpPrincipal(
                userId,
                (String) context.get("user_name"),
                (String) context.get("real_name"),
                claims.getClaim("custom_attributes") instanceof Map
                        ? (Map<String, Object>) claims.getClaim("custom_attributes")
                        : Map.of(),
                String.valueOf(claims.getClaim("agent_id")),
                (String) claims.getClaim("agent_version_id"),
                String.valueOf(claims.getClaim("request_id")),
                claims.getJWTID());
    }

    private static void assertJtiIsNotReplayed(String jti, Date expiresAt) {
        // 必须接入业务方 Redis 的 SETNX + EXPIRE 原子实现；重复 jti 必须抛出异常。
        throw new UnsupportedOperationException(
                "请在业务 MCP 中实现 Redis SETNX + EXPIRE 的重放防护");
    }

    public record McpPrincipal(
            String userId,
            String userName,
            String realName,
            Map<String, Object> customAttributes,
            String agentId,
            String agentVersionId,
            String requestId,
            String jti) {}
}
```

生产环境应缓存 `JWKSet`，在缓存过期或遇到未知 `kid` 时刷新；示例中的 `assertJtiIsNotReplayed` 必须替换为 Redis、数据库或其他具备原子性的短期去重实现。

实际中间件还必须：

- 先校验当前 MCP 自己的固定 Authorization Bearer Token；
- 校验 `iss`、`aud`、`iat`、`exp` 和 `jti`；
- 防止同一个 `jti` 在有效期内重复使用；
- 校验 `sub` 与 `user_context.user_id` 一致；
- 从 `McpPrincipal.user_id` 查询业务用户并执行资源级权限判断；
- 日志只记录用户 ID、智能体 ID、请求 ID 和结果，不记录完整 Token/JWS。

## 7. 工具业务逻辑

```python
async def query_customer(ctx, customer_id: str):
    principal = ctx.mcp_principal
    await crm_permission_service.check_customer_access(
        user_id=principal.user_id,
        customer_id=customer_id,
    )
    return await crm_service.query_customer(customer_id)
```

不要从参数中的 `user_id` 判断当前操作人，也不要仅凭 `custom_attributes` 授予权限。

## 8. 不支持 UserContext 的 MCP

第三方 MCP 如果不解析该 Header，可以关闭当前 MCP 的 UserContext 开关，继续使用原来的固定 Authorization Bearer Token。即使第三方 MCP 收到并忽略自定义 Header，也不会影响正常协议调用；但它无法获得可信的 NanZi 用户身份。

## 9. 密钥轮换

密钥轮换按 MCP 单独执行：先让该 MCP 的 JWKS 同时发布旧、新公钥，再切换该 MCP 的 Key ID；等待旧断言过期后删除旧公钥。一个 MCP 的密钥轮换不会影响其他 MCP。
