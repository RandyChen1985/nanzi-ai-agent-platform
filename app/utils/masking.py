import json
import re
from typing import Any, Dict, List, Union

# 精确匹配的敏感键名
SENSITIVE_KEYS_FULL = {
    "authorization", "auth", "password", "passwd", "pwd", "token", 
    "api_key", "apikey", "secret", "sk", "private_key",
    # 请求签名密钥：只在创建/更新/试跑请求体里出现，绝不能进审计库
    "signing_secret", "signingsecret",
    # 会话 / 票据类凭据：签发接口会把它们放在**响应体**里
    # （user_apikey 与 ticket 兑换都返回 session_token），漏了就明文落进审计表。
    # `ticket` 只做精确匹配——`ticket_id` 之类业务字段不是凭据，不应被误伤。
    "session_token", "portal_session", "embed_session", "ticket",
    # 响应头里的会话票据：试跑会把响应头展示到界面/审计里，不能明文带出。
    "set_cookie",
    # 旧名保留：历史日志里已存在该键，重新脱敏时不应对它视而不见。
    "admin_token",
}

# 子串匹配的敏感词（只要键名包含这些词就脱敏）
SENSITIVE_KEYS_SUB = {
    "password", "passwd", "pwd",
    "access_token", "refresh_token",
    # 带 `auth` 的 token 写法（X-Auth-Token / authToken）——与 access_token 同类，
    # 原先只在 FULL 里有精确的 `auth`，`x_auth_token` 归一化后仍不命中任何子串。
    "auth_token", "authtoken",
    "api_key", "apikey",
    "client_secret", "app_secret",
    "signing_secret", "signingsecret",
    # 覆盖 session_token / sessionToken(camel) / portal_session / embed_session 及其变体
    "session_token", "sessiontoken",
    "portal_session", "portalsession",
    "embed_session", "embedsession",
    # 旧名保留，理由同上
    "admin_token", "admintoken",
}

# 凭据值的可辨识前缀（见 app/utils/encryption.py 与各签发服务）：
#   nzi_ 长期 API Key / sess_ 门户会话 / emb_ses_ 嵌入会话 / emt_ 嵌入票据。
# 键名匹配负责已知字段；这里按「值形态」兜底，即便字段名没被收录、或凭据出现在
# 自由文本与 query 里也能抹掉。emb_ses_ 不含 sess_（是 ses_），两个分支不会互伤。
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\b(?:emb_ses_|sess_|emt_|nzi_)[A-Za-z0-9_\-]{6,}"
)


def _normalize_key_name(key: Any) -> str:
    """把键名归一化后再比对名单。

    HTTP 头习惯用连字符（``X-Api-Key`` / ``X-Auth-Token``），而名单是按**下划线**
    写的（``api_key``）。不归一化的话，只要用户用了连字符写法，密钥就会以明文落进
    审计库——``Authorization`` 恰好被精确命中，反而是 ``X-Api-Key`` 这种更常见的
    写法漏掉。``Content-Type`` 归一化后是 ``content_type``，不命中任何敏感词，不会误伤。
    """
    return str(key).lower().replace("-", "_").replace(" ", "_")


def _mask_credential_values(text: str) -> str:
    """按凭据前缀抹除文本中的凭据值（键名匹配之外的兜底）。"""
    if not text:
        return text
    return _CREDENTIAL_VALUE_PATTERN.sub("******", text)

def mask_sensitive_data(data: Any) -> Any:
    """
    递归对数据进行脱敏处理。
    支持 dict, list, 以及 JSON 字符串。
    """
    if data is None:
        return None

    # 如果是字符串，尝试解析为 JSON
    if isinstance(data, str):
        data_trimmed = data.strip()
        if (data_trimmed.startswith('{') and data_trimmed.endswith('}')) or \
           (data_trimmed.startswith('[') and data_trimmed.endswith(']')):
            try:
                parsed = json.loads(data)
                masked_parsed = mask_sensitive_data(parsed)
                return json.dumps(masked_parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                return _regex_mask_string(data)
        else:
            return _regex_mask_string(data)

    if isinstance(data, dict):
        # 特殊处理 {"key": "llm_api_key", "value": "..."} 这种配置模式
        if "key" in data and "value" in data and isinstance(data["key"], str):
            key_name = _normalize_key_name(data["key"])
            if key_name in SENSITIVE_KEYS_FULL or any(sk in key_name for sk in SENSITIVE_KEYS_SUB):
                return {**data, "value": "******"}

        new_dict = {}
        for k, v in data.items():
            k_lower = _normalize_key_name(k)
            # 检查键名是否敏感
            is_sensitive = k_lower in SENSITIVE_KEYS_FULL or any(sk in k_lower for sk in SENSITIVE_KEYS_SUB)
            
            if is_sensitive:
                new_dict[k] = "******"
            elif isinstance(v, str):
                # 键名没被收录时，仍按「值形态」兜底（如 sessionToken、自定义字段名）
                new_dict[k] = _mask_credential_values(v)
            else:
                new_dict[k] = mask_sensitive_data(v)
        return new_dict

    if isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]

    return data

def _regex_mask_string(text: str) -> str:
    """
    针对非 JSON 字符串（如 query params 或日志文本）进行脱敏。
    """
    if not text:
        return text
        
    # 合并所有可能的敏感词进行正则匹配
    all_keys = SENSITIVE_KEYS_FULL | SENSITIVE_KEYS_SUB
    sorted_keys = sorted(list(all_keys), key=len, reverse=True)
    # 名单按下划线书写，正则里同时接受连字符，覆盖 `X-Api-Key: xxx` 这类头写法
    keys_pattern = "|".join(key.replace("_", "[-_]") for key in sorted_keys)
    
    masked_text = text
    
    # 1. 匹配 Query params 或 简单键值对: key=value 或 key:value
    # 键名前可能还带前缀（`X-Api-Key` 的 `X-`、`my_api_key` 的 `my_`）；
    # 不给前缀留位置的话，`(?:^|[&? \t])` 会把这一类常见写法整条放过。
    kv_pattern = rf'((?:^|[&? \t])[A-Za-z0-9_\-]*?(?:{keys_pattern}))(?:[:=])([^& \t\n\r]+)'
    masked_text = re.sub(kv_pattern, r'\1=******', masked_text, flags=re.IGNORECASE)
    
    # 2. 匹配 JSON 样式的字符串: "key": "value"
    json_like_pattern = rf'("(?i:[A-Za-z0-9_\-]*?(?:{keys_pattern}))"\s*[:=]\s*")([^"]+)(")'
    masked_text = re.sub(json_like_pattern, r'\1******\3', masked_text, flags=re.IGNORECASE)
    
    # 3. 按凭据前缀兜底：字段名未收录时也能抹掉凭据值
    return _mask_credential_values(masked_text)
