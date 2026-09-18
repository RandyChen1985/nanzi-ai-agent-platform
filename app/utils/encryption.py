"""
API Key Encryption and Management
使用 Fernet 对称加密实现 API Key 的安全存储和检索
"""
import base64
import hashlib
import secrets
from cryptography.fernet import Fernet
from typing import Tuple
from app.core.config import settings


class APIKeyManager:
    """API Key 加密管理器"""

    #: 新签发 API Key 的固定前缀，便于在日志与抓包中与其它凭据一眼区分
    #: （sess_ 门户会话 / emb_ses_ 嵌入会话 / emt_ 嵌入票据）。
    #:
    #: 注意：该前缀**不参与校验判断**——verify_api_key 只对传入串做 SHA256 再查库，
    #: 不解析格式。因此历史上已发放的无前缀 Key 继续有效，加前缀不会破坏既有集成，
    #: 也刻意不引入「前缀不符即拒绝」的前置校验。
    API_KEY_PREFIX = "nzi_"

    def __init__(self):
        """初始化加密管理器，从环境变量读取密钥"""
        encryption_key = settings.ENCRYPTION_KEY
        
        if not encryption_key:
            raise ValueError(
                "ENCRYPTION_KEY environment variable is not set. "
                "Please generate one using: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        
        try:
            # Fernet 需要 bytes 类型的密钥
            self.cipher = Fernet(encryption_key.encode())
        except Exception as e:
            raise ValueError(f"Invalid encryption key format: {e}")
    
    def generate_api_key(self) -> Tuple[str, str, str]:
        """
        生成新的 API Key（明文、加密、哈希）
        
        Returns:
            Tuple[str, str, str]: (plaintext_key, encrypted_key, hashed_key)
            - plaintext_key: 原始 API Key（返回给用户，仅此一次）
            - encrypted_key: 加密后的 API Key（存储到数据库 api_key_encrypted 字段）
            - hashed_key: SHA256 哈希值（存储到数据库 api_key_hash 字段，用于快速验证）
        """
        # 1. 生成 32 字节随机体的 API Key，并叠加可辨识前缀
        #    前缀只是叠加，随机部分保持 token_urlsafe(32)（43 字符 / 256 bit），不减熵。
        api_key = f"{self.API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        
        # 2. 加密存储（可解密）
        encrypted = self.cipher.encrypt(api_key.encode())
        encrypted_b64 = base64.urlsafe_b64encode(encrypted).decode()
        
        # 3. 计算哈希值（用于快速验证）
        hashed = hashlib.sha256(api_key.encode()).hexdigest()
        
        return api_key, encrypted_b64, hashed
    
    def encrypt_api_key(self, plaintext_key: str) -> str:
        """
        加密 API Key
        
        Args:
            plaintext_key: 原始 API Key 明文
        
        Returns:
            str: Base64 编码的加密 API Key
        """
        encrypted = self.cipher.encrypt(plaintext_key.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def decrypt_api_key(self, encrypted_key: str) -> str:
        """
        解密 API Key
        
        Args:
            encrypted_key: Base64 编码的加密 API Key
        
        Returns:
            str: 原始 API Key 明文
        
        Raises:
            ValueError: 解密失败（密钥错误或数据损坏）
        """
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_key.encode())
            decrypted = self.cipher.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt API Key: {e}")
    
    def hash_api_key(self, api_key: str) -> str:
        """
        计算 API Key 的 SHA256 哈希值
        
        Args:
            api_key: 原始 API Key
        
        Returns:
            str: SHA256 哈希值（十六进制字符串）
        """
        return hashlib.sha256(api_key.encode()).hexdigest()
    
    def verify_api_key(self, plaintext_key: str, hashed_key: str) -> bool:
        """
        验证 API Key 是否匹配
        
        Args:
            plaintext_key: 用户提供的 API Key
            hashed_key: 数据库中存储的哈希值
        
        Returns:
            bool: 是否匹配
        """
        computed_hash = self.hash_api_key(plaintext_key)
        return computed_hash == hashed_key


# 全局单例实例
_api_key_manager = None


def get_api_key_manager() -> APIKeyManager:
    """获取 APIKeyManager 单例实例"""
    global _api_key_manager
    if _api_key_manager is None:
        _api_key_manager = APIKeyManager()
    return _api_key_manager
