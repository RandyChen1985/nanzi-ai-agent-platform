/**
 * 「业务方 MCP 调用模拟代码」弹窗里的示例代码。
 *
 * 内容偏产品文档：告诉业务方这段验签逻辑嵌在请求链路的哪一层。
 * 因此和组件逻辑分开维护，避免把大面积字符串塞进 McpServerRegistry.vue。
 */

export interface McpCodeSampleConfig {
  audience: string
  issuer: string
  jwksUrl: string
}

/** 完整可运行的 Python Echo MCP：配置 → 验签 → 认证中间件 → echo 工具 → 启动。 */
export function buildPythonMcpSample(config: McpCodeSampleConfig): string {
  return `"""NanZi 业务方 MCP 接入示例：一个能直接跑起来的 Echo MCP。

请求链路（本文件的三个嵌入位置）：
    HTTP 请求  Authorization: Bearer <固定Token> + X-Nanzi-User-Assertion: <JWS>
      ↓ ① NanziUserAuthMiddleware    验签并校验 iss/aud/exp；不通过直接 401，进不到 MCP 协议层
      ↓ ② mcp.streamable_http_app()  MCP 协议端点 /mcp（initialize / tools/list / tools/call）
      ↓ ③ @mcp.tool echo()           业务代码：从 request.state 取已验签身份，不重复验签

安装：pip install "mcp>=2.3" pyjwt cryptography redis uvicorn
运行：MCP_FIXED_TOKEN=<固定Token> MCP_PUBLIC_HOST=<平台访问的host:port> python business_echo_mcp.py
"""

import asyncio
import hmac
import os
import time
from typing import Any

import jwt
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from redis import Redis
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

# ===== ① 配置：三个值从 NanZi「MCP 管理 → 业务方如何使用」原样复制 =====
NANZI_MCP_AUDIENCE = "${config.audience}"  # 用于校验 aud
NANZI_MCP_ISSUER = "${config.issuer}"  # 用于校验 iss
NANZI_MCP_JWKS_URL = "${config.jwksUrl}"  # 用于获取公钥
NANZI_ASSERTION_HEADER = "X-Nanzi-User-Assertion"
# 业务 MCP 原有的固定 Token。必须显式配置：留空会让下面那行
#   f"Bearer {MCP_FIXED_TOKEN}"
# 退化成 "Bearer "，任何人带一个空 Bearer 就能通过。
MCP_FIXED_TOKEN = os.environ.get("MCP_FIXED_TOKEN", "")
if not MCP_FIXED_TOKEN:
    raise SystemExit("启动失败：必须设置环境变量 MCP_FIXED_TOKEN（业务 MCP 的固定 Token）")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

jwk_client = jwt.PyJWKClient(NANZI_MCP_JWKS_URL, cache_keys=True)
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)


# ===== ② 验签：固定 Token + NanZi 断言两步都通过，才认这个用户 =====
def verify_nanzi_user(authorization: str, assertion: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (用户身份, 断言元信息)；校验失败抛 PermissionError / jwt.InvalidTokenError。"""
    # 1) 先按业务 MCP 原有方式校验 Authorization 固定 Token。
    if not MCP_FIXED_TOKEN:
        # 双保险：即使启动检查被绕过，也不接受任何请求
        raise PermissionError("server misconfigured: MCP_FIXED_TOKEN is empty")
    if not hmac.compare_digest(authorization or "", f"Bearer {MCP_FIXED_TOKEN}"):
        raise PermissionError("invalid MCP client token")
    if not assertion:
        raise PermissionError(f"missing {NANZI_ASSERTION_HEADER} header")

    # 2) 按 JWT Header 里的 kid 从 JWKS 取公钥验签，同时校验 iss / aud / exp。
    signing_key = jwk_client.get_signing_key_from_jwt(assertion).key
    claims = jwt.decode(
        assertion,
        signing_key,
        algorithms=["EdDSA"],
        issuer=NANZI_MCP_ISSUER,
        audience=NANZI_MCP_AUDIENCE,
        options={"require": ["iss", "aud", "sub", "exp", "iat", "jti", "agent_id", "request_id"]},
    )

    # 3) 校验主体一致性：sub 必须等于 nanzi:user:<user_context.user_id>。
    user_context = claims.get("user_context") or {}
    user_id = user_context.get("user_id")
    if not user_id:
        raise PermissionError("missing user_context.user_id")
    if claims.get("sub") != f"nanzi:user:{user_id}":
        raise PermissionError("user subject mismatch")

    user = {
        "user_id": str(user_id),
        "user_name": user_context.get("user_name"),
        "real_name": user_context.get("real_name"),
        "dept_code": user_context.get("dept_code"),
        "agent_id": claims.get("agent_id"),
        "agent_name": claims.get("agent_name"),
        "request_id": claims.get("request_id"),
        "custom_attributes": claims.get("custom_attributes") or {},
    }
    assertion_meta = {"jti": claims["jti"], "ttl": max(1, int(claims["exp"] - time.time()))}
    return user, assertion_meta


def consume_assertion_once(assertion_meta: dict[str, Any]) -> None:
    """防重放：一条断言只允许触发一次业务调用。NanZi 每次工具调用都会签发新断言。"""
    key = f"nanzi:mcp:jti:{assertion_meta['jti']}"
    if not redis_client.set(key, "1", nx=True, ex=int(assertion_meta["ttl"])):
        raise PermissionError("replayed NanZi user assertion")


# ===== ③ 认证中间件：包在 MCP 协议端点最外层 =====
class NanziUserAuthMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)  # lifespan / websocket 原样透传
            return

        headers = Headers(scope=scope)
        try:
            # PyJWKClient 是同步实现，放线程池执行，避免阻塞事件循环。
            nanzi_user, assertion_meta = await asyncio.to_thread(
                verify_nanzi_user,
                headers.get("authorization", ""),
                headers.get(NANZI_ASSERTION_HEADER, ""),
            )
        except (PermissionError, jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
            # PyJWKClientError 不是 InvalidTokenError 的子类：JWKS 拉取失败也必须
            # 回 401，不能让未捕获异常变成 500。
            await JSONResponse({"error": str(exc)}, status_code=401)(scope, receive, send)
            return

        # 验签结果挂到 request.state：工具里直接取，不再重复验签。
        # jti 防重放放到工具调用处消费——一次 MCP 会话有多个 HTTP 请求，
        # 在中间件里消费会把同一次调用的后续请求（tools/call）误判成重放。
        scope["state"] = {**(scope.get("state") or {}), "nanzi_user": nanzi_user,
                          "nanzi_assertion": assertion_meta}
        await self.app(scope, receive, send)


# ===== ④ 业务工具：这里才是你的业务代码 =====
mcp = MCPServer("业务方 Echo MCP", instructions="演示 NanZi 用户身份透传的最小 MCP。")


@mcp.tool(name="echo", description="回显 text，并返回本次调用已验签的 NanZi 用户身份。")
async def echo(ctx: Context, text: str = "hello") -> dict[str, Any]:
    state = ctx.request_context.request.state
    user = state.nanzi_user
    consume_assertion_once(state.nanzi_assertion)
    return {
        "echo": text,
        "nanzi_user": user,
        "message": f"已收到 {text}，当前调用用户 {user['user_id']}",
    }


# ===== ⑤ 组装：中间件 → MCP 端点 → uvicorn =====
# uvicorn.run 会执行 Starlette 的 lifespan（内部启动 MCP session manager），
# 所以不要改成手动 app.mount 后自己调 session_manager.run()，容易漏掉生命周期。
# 必须显式给 transport_security：mcp 2.x 默认只放行 localhost 的 Host，
# 而这里监听 0.0.0.0；不配置的话，用域名/LAN IP 访问会在 initialize 阶段直接
# 收到 421 Invalid Host header。allowed 列表要与「实际访问地址」一致。
from mcp.server.transport_security import TransportSecuritySettings

MCP_PUBLIC_HOST = os.environ.get("MCP_PUBLIC_HOST", "127.0.0.1:9000")
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[MCP_PUBLIC_HOST, "127.0.0.1:9000", "localhost:9000"],
        allowed_origins=[
            f"http://{MCP_PUBLIC_HOST}",
            f"https://{MCP_PUBLIC_HOST}",
            "http://127.0.0.1:9000",
        ],
    ),
)
app = NanziUserAuthMiddleware(mcp_app)

if __name__ == "__main__":
    # MCP_PUBLIC_HOST 是客户端实际访问的 host:port（如 mcp.example.com:9000），
    # 必须与上面的 allowed_hosts 一致，否则会被 DNS-rebinding 保护挡掉。
    uvicorn.run(app, host="0.0.0.0", port=9000)
`
}

/** Spring Boot 片段：Filter 验签 → MCP 端点 → @Tool 读取身份。 */
export function buildJavaMcpSample(config: McpCodeSampleConfig): string {
  return `/**
 * NanZi 业务方 MCP 接入示例（Spring Boot + Spring AI MCP Server）。
 *
 * 请求链路（三段编号与 Python 示例一一对应）：
 *   HTTP 请求  Authorization: Bearer <固定Token> + X-Nanzi-User-Assertion: <JWS>
 *     ↓ [①] NanziUserAuthFilter   嵌在 MCP 端点之前；验签失败直接 401，请求进不到 MCP 协议层
 *     ↓ [②] MCP Server 端点 /mcp  initialize / tools/list / tools/call
 *     ↓ [③] @Tool echo(String)    业务代码：从当前请求属性取已验签身份，不重复验签
 *
 * 依赖：spring-boot-starter-web、spring-ai-starter-mcp-server-webmvc、
 *      com.nimbusds:nimbus-jose-jwt、spring-boot-starter-data-redis
 */
package com.example.mcp;

import java.io.IOException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.ParseException;
import java.time.Duration;
import java.util.Date;
import java.util.Map;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.crypto.Ed25519Verifier;
import com.nimbusds.jose.jwk.JWK;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.OctetKeyPair;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.stereotype.Service;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.filter.OncePerRequestFilter;

// ===== [①] 配置：三个值从 NanZi「MCP 管理 → 业务方如何使用」原样复制 =====
final class NanziMcpConfig {
    static final String AUDIENCE = "${config.audience}";  // 用于校验 aud
    static final String ISSUER = "${config.issuer}";  // 用于校验 iss
    static final String JWKS_URL = "${config.jwksUrl}"; // 用于获取公钥
    static final String ASSERTION_HEADER = "X-Nanzi-User-Assertion";
    // 业务 MCP 原有的固定 Token：生产环境从配置中心 / Secret 读取，不要提交到 Git。
    static final String FIXED_TOKEN = System.getenv("MCP_FIXED_TOKEN");

    static {
        // 没有配置时必须启动失败：否则期望值会变成 "Bearer null"，客户端带
        // "Bearer null" 也能通过，属于 fail-open。
        if (FIXED_TOKEN == null || FIXED_TOKEN.isBlank()) {
            throw new IllegalStateException("启动失败：必须设置环境变量 MCP_FIXED_TOKEN");
        }
    }
}

record NanziUser(String userId, String userName, String realName, String deptCode,
                 String agentId, String agentName, Map<String, Object> customAttributes) {}

record NanziAssertion(String jti, long ttlSeconds) {}

record NanziVerified(NanziUser user, NanziAssertion assertion) {}

// ===== [②] 认证过滤器：注册在 MCP 端点之前 =====
@Component
class NanziUserAuthFilter extends OncePerRequestFilter {

    static final String USER_ATTRIBUTE = "nanzi.user";
    static final String ASSERTION_ATTRIBUTE = "nanzi.assertion";

    private final JWKLoader jwkLoader;

    NanziUserAuthFilter(JWKLoader jwkLoader) {
        this.jwkLoader = jwkLoader;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        // 只拦 MCP 端点，健康检查等直接放行。
        return !request.getRequestURI().startsWith("/mcp");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        try {
            NanziVerified verified = verify(request.getHeader("Authorization"),
                    request.getHeader(NanziMcpConfig.ASSERTION_HEADER));
            request.setAttribute(USER_ATTRIBUTE, verified.user());
            request.setAttribute(ASSERTION_ATTRIBUTE, verified.assertion());
            chain.doFilter(request, response);
        } catch (SecurityException ex) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType("application/json;charset=UTF-8");
            response.getWriter().write("{\\"error\\":\\"" + ex.getMessage() + "\\"}");
        }
    }

    private NanziVerified verify(String authorization, String rawAssertion) {
        // 1) 先按业务 MCP 原有方式校验固定 Token。
        byte[] expected = ("Bearer " + NanziMcpConfig.FIXED_TOKEN).getBytes(StandardCharsets.UTF_8);
        if (authorization == null
                || !MessageDigest.isEqual(authorization.getBytes(StandardCharsets.UTF_8), expected)) {
            throw new SecurityException("invalid MCP client token");
        }
        if (rawAssertion == null || rawAssertion.isBlank()) {
            throw new SecurityException("missing " + NanziMcpConfig.ASSERTION_HEADER + " header");
        }

        try {
            // 2) 按 JWT Header 的 kid 从 JWKS 选公钥验签，同时校验 iss / aud / exp / iat。
            SignedJWT jwt = SignedJWT.parse(rawAssertion);
            if (!JWSAlgorithm.EdDSA.equals(jwt.getHeader().getAlgorithm())) {
                throw new SecurityException("unsupported assertion algorithm");
            }
            JWK jwk = jwkLoader.load().getKeyByKeyId(jwt.getHeader().getKeyID());
            if (!(jwk instanceof OctetKeyPair okp) || !jwt.verify(new Ed25519Verifier(okp))) {
                throw new SecurityException("invalid NanZi User Assertion signature");
            }
            JWTClaimsSet claims = jwt.getJWTClaimsSet();
            Date now = new Date();
            if (!NanziMcpConfig.ISSUER.equals(claims.getIssuer())
                    || claims.getAudience() == null
                    || !claims.getAudience().contains(NanziMcpConfig.AUDIENCE)
                    || claims.getExpirationTime() == null || claims.getExpirationTime().before(now)
                    || claims.getIssueTime() == null || claims.getIssueTime().after(now)
                    || claims.getJWTID() == null) {
                throw new SecurityException("invalid NanZi User Assertion claims");
            }

            // 3) 校验主体一致性：sub 必须等于 nanzi:user:<user_context.user_id>。
            Map<String, Object> userContext = claims.getJSONObjectClaim("user_context");
            if (userContext == null || userContext.get("user_id") == null) {
                throw new SecurityException("missing user_context.user_id");
            }
            String userId = String.valueOf(userContext.get("user_id"));
            if (!("nanzi:user:" + userId).equals(claims.getSubject())) {
                throw new SecurityException("user subject mismatch");
            }

            long ttl = Math.max(1, (claims.getExpirationTime().getTime() - now.getTime()) / 1000);
            Map<String, Object> custom = claims.getJSONObjectClaim("custom_attributes");
            NanziUser user = new NanziUser(
                    userId,
                    str(userContext.get("user_name")),
                    str(userContext.get("real_name")),
                    str(userContext.get("dept_code")),
                    str(claims.getClaim("agent_id")),
                    str(claims.getClaim("agent_name")),
                    custom == null ? Map.of() : custom);
            return new NanziVerified(user, new NanziAssertion(claims.getJWTID(), ttl));
        } catch (SecurityException ex) {
            throw ex;
        } catch (Exception ex) {
            throw new SecurityException("invalid NanZi User Assertion: " + ex.getMessage());
        }
    }

    private static String str(Object value) {
        return value == null ? null : String.valueOf(value);
    }
}

/** JWKS 缓存：公钥按 kid 选择，默认缓存 5 分钟，避免每个请求都回源 NanZi。 */
@Service
class JWKLoader {
    private static final long TTL_MS = 5 * 60 * 1000L;
    private volatile JWKSet cached;
    private volatile long cachedAt;

    JWKSet load() throws IOException, ParseException {
        long now = System.currentTimeMillis();
        if (cached == null || now - cachedAt > TTL_MS) {
            cached = JWKSet.load(new URL(NanziMcpConfig.JWKS_URL));
            cachedAt = now;
        }
        return cached;
    }
}

/** 防重放：一条断言只允许触发一次业务调用（NanZi 每次工具调用都会签发新断言）。 */
@Service
class NanziReplayGuard {
    private final StringRedisTemplate redis;

    NanziReplayGuard(StringRedisTemplate redis) {
        this.redis = redis;
    }

    void consume(NanziAssertion assertion) {
        Boolean first = redis.opsForValue().setIfAbsent(
                "nanzi:mcp:jti:" + assertion.jti(), "1", Duration.ofSeconds(assertion.ttlSeconds()));
        if (!Boolean.TRUE.equals(first)) {
            throw new SecurityException("replayed NanZi user assertion");
        }
    }
}

// ===== [③] 业务工具：方法体才是你的业务代码 =====
@Service
class EchoTools {

    private final NanziReplayGuard replayGuard;

    EchoTools(NanziReplayGuard replayGuard) {
        this.replayGuard = replayGuard;
    }

    @Tool(name = "echo", description = "回显 text，并返回本次调用已验签的 NanZi 用户身份。")
    public Map<String, Object> echo(String text) {
        // 过滤器已把身份放进当前请求属性；Spring AI 的 Sync 工具回调仍在同一个请求线程里。
        ServletRequestAttributes attributes =
                (ServletRequestAttributes) RequestContextHolder.currentRequestAttributes();
        HttpServletRequest request = attributes.getRequest();
        NanziUser user = (NanziUser) request.getAttribute(NanziUserAuthFilter.USER_ATTRIBUTE);
        if (user == null) {
            throw new SecurityException("missing NanZi user context");
        }
        replayGuard.consume((NanziAssertion) request.getAttribute(NanziUserAuthFilter.ASSERTION_ATTRIBUTE));
        String echoText = text == null ? "" : text;  // Map.of 不接受 null
        return Map.of(
                "echo", echoText,
                "nanzi_user", user,
                "message", "已收到 " + echoText + "，当前调用用户 " + user.userId());
    }
}

// ===== 注册：把工具挂到 MCP Server 上（过滤器由 @Component 自动注册） =====
@Configuration
class McpServerConfig {
    @Bean
    ToolCallbackProvider nanziToolCallbacks(EchoTools echoTools) {
        return MethodToolCallbackProvider.builder().toolObjects(echoTools).build();
    }
}
`
}
