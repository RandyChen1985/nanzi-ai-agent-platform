import json
import re
from typing import Any, Dict, List, Union

# 精确匹配的敏感键名
SENSITIVE_KEYS_FULL = {
    "authorization", "auth", "password", "passwd", "pwd", "token", 
    "api_key", "apikey", "secret", "sk", "private_key",
    # 会话 / 票据类凭据：签发接口会把它们放在**响应体**里
    # （user_apikey 与 ticket 兑换都返回 session_token），漏了就明文落进审计表。
    # `ticket` 只做精确匹配——`ticket_id` 之类业务字段不是凭据，不应被误伤。
    "session_token", "admin_token", "embed_session", "ticket",
}

# 子串匹配的敏感词（只要键名包含这些词就脱敏）
SENSITIVE_KEYS_SUB = {
    "password", "passwd", "pwd",
    "access_token", "refresh_token",
    "api_key", "apikey",
    "client_secret", "app_secret",
    # 覆盖 session_token / sessionToken(camel) / admin_token / embed_session 及其变体
    "session_token", "sessiontoken",
    "admin_token", "admintoken",
    "embed_session", "embedsession",
}

# 凭据值的可辨识前缀（见 app/utils/encryption.py 与各签发服务）：
#   nzi_ 长期 API Key / sess_ 门户会话 / emb_ses_ 嵌入会话 / emt_ 嵌入票据。
# 键名匹配负责已知字段；这里按「值形态」兜底，即便字段名没被收录、或凭据出现在
# 自由文本与 query 里也能抹掉。emb_ses_ 不含 sess_（是 ses_），两个分支不会互伤。
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\b(?:emb_ses_|sess_|emt_|nzi_)[A-Za-z0-9_\-]{6,}"
)


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
            key_name = data["key"].lower()
            if key_name in SENSITIVE_KEYS_FULL or any(sk in key_name for sk in SENSITIVE_KEYS_SUB):
                return {**data, "value": "******"}

        new_dict = {}
        for k, v in data.items():
            k_lower = k.lower()
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
    keys_pattern = "|".join(sorted_keys)
    
    masked_text = text
    
    # 1. 匹配 Query params 或 简单键值对: key=value 或 key:value
    kv_pattern = rf'((?:^|[&? \t])(?:{keys_pattern}))(?:[:=])([^& \t\n\r]+)'
    masked_text = re.sub(kv_pattern, r'\1=******', masked_text, flags=re.IGNORECASE)
    
    # 2. 匹配 JSON 样式的字符串: "key": "value"
    json_like_pattern = rf'("(?i:{keys_pattern})"\s*[:=]\s*")([^"]+)(")'
    masked_text = re.sub(json_like_pattern, r'\1******\3', masked_text, flags=re.IGNORECASE)
    
    # 3. 按凭据前缀兜底：字段名未收录时也能抹掉凭据值
    return _mask_credential_values(masked_text)
