# Yovole SSO 登录集成指南

本文档旨在说明如何在本系统中集成 Yovole 统一认证系统（SSO），以便其他模块参考或复用。

## 1. 设计思路
本系统采用 **“SSO 负责认证，本地数据库负责权限”** 的解耦方案：
1. **身份认证 (Authentication)**：用户输入用户名和密码，系统通过后台调用 Yovole SSO 接口验证身份。
2. **账号授权 (Authorization)**：认证通过后，系统检查本地数据库 `api_users` 表。只有在本地已开通账号且状态为“启用”的用户，才能最终获得系统的访问令牌（API Key / Token）。

## 2. SSO 接口规范 (Yovole Laplace)

### 2.1 接口信息
- **URL**: `https://yovole.net/api/v1/user/check/login`
- **Method**: `POST`
- **Content-Type**: `application/json;charset=UTF-8`

### 2.2 请求头 (Headers)
必须包含一个固定的访问令牌：
- `YOVOLE-LAPLACE-API-ACCESS-TOKEN`: `laplace` (或根据环境配置)

### 2.3 请求体 (Payload)
```json
{
  "requestSystem": "NANZI_API_DATA_PLATFORM",
  "requestBusiness": "USER-LOGIN",
  "operationType": "LOGIN",
  "userName": "your_username",
  "password": "your_password"
}
```

### 2.4 响应体 (Response)
- **认证通过**: `{"data": true}`
- **认证失败**: `{"data": false}` 或其他错误信息

## 3. 后端集成 (Python 示例)

### 3.1 环境配置 (`.env`)
```bash
# SSO Configuration
SSO_API_URL=https://yovole.net/api/v1/user/check/login
SSO_ACCESS_TOKEN=laplace
SSO_REQUEST_SYSTEM=NANZI_API_DATA_PLATFORM
SSO_REQUEST_BUSINESS=USER-LOGIN
SSO_TIMEOUT=30
```

### 3.2 核心服务代码 (`auth_service.py`)
```python
import httpx
import json

async def authenticate_sso_user(username, password):
    # 1. 调用 SSO 接口
    api_request = {
        'requestSystem': settings.SSO_REQUEST_SYSTEM,
        'requestBusiness': settings.SSO_REQUEST_BUSINESS,
        'operationType': 'LOGIN',
        'userName': username,
        'password': password
    }
    
    headers = {
        'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
        'Content-Type': 'application/json;charset=UTF-8'
    }

    async with httpx.AsyncClient(timeout=settings.SSO_TIMEOUT, verify=False) as client:
        response = await client.post(settings.SSO_API_URL, headers=headers, json=api_request)
        if response.status_code != 200 or not response.json().get('data'):
            return None # 认证失败

    # 2. 认证通过后，查询本地数据库映射权限
    async with get_db_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, user_name, role, status FROM api_users WHERE user_name = %s",
                (username,)
            )
            user = await cursor.fetchone()
            
            if not user:
                return {"error": "user_not_found"} # 已通过SSO，但本地未开通
            if user[3] != 1:
                return {"error": "user_disabled"} # 账号被禁用
                
            return {"user_id": user[0], "user_name": user[1], "role": user[2]}
```

### 3.3 API 端点实现 (`auth.py`)
```python
@router.post("/sso/login")
async def sso_login(request: SSOLoginRequest):
    user = await AuthService.authenticate_sso_user(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="SSO 认证失败")
    if user.get("error") == "user_not_found":
        raise HTTPException(status_code=401, detail="请联系管理员开通系统权限")
    # ... 发放 Token 逻辑
```

## 4. 前端实现逻辑 (Vue.js)

### 4.1 登录逻辑
```javascript
const handleSSOLogin = async () => {
  try {
    const response = await axios.post('/api/portal/auth/sso/login', {
      username: username.value,
      password: password.value
    });
    // 存储用户信息和 API Key
    localStorage.setItem('user_info', JSON.stringify(response.data.data));
    router.push('/dashboard');
  } catch (e) {
    error.value = e.response?.data?.detail || '登录失败';
  }
};
```

## 5. 安全建议
1. **SSL 验证**：在生产环境中，应确保 `verify=True`（或配置正确的 CA 证书）以防止中间人攻击。
2. **敏感信息**：切勿在日志中打印 SSO 密码或 Access Token。
3. **超时控制**：SSO 接口可能响应较慢，务必设置合理的 `timeout`（建议 30s）。
