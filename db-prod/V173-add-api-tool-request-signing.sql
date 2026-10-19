-- V173: API 工具请求签名（MySQL）
--
-- 背景：URL 模板中的业务参数由模型生成，外部接口无法区分「平台代发」与
-- 「攻击者伪造成平台代发」。开启签名后，平台用每个工具独立的共享密钥对最终请求
-- 做 HMAC-SHA256，业务方验签即可确认来源与完整性。
--
-- 密钥只保存 Fernet 密文，接口只回显 signing_secret_configured，任何响应都不返回
-- 密钥明文。signing_key_id 全局唯一，业务方据此选择对应密钥。
--
-- 默认关闭，已有工具行为不变；关闭时保留密文，便于后续重新开启/轮换。
ALTER TABLE `sys_api_tools`
  ADD COLUMN `signing_enabled` TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '是否开启请求签名',
  ADD COLUMN `signing_key_id` VARCHAR(64) NULL
    COMMENT '签名 Key-Id，16 位小写十六进制，全局唯一',
  ADD COLUMN `signing_secret_encrypted` TEXT NULL
    COMMENT 'Fernet 加密后的签名密钥',
  ADD COLUMN `signing_algorithm` VARCHAR(32) NOT NULL DEFAULT 'hmac-sha256'
    COMMENT '签名算法，v1 仅 hmac-sha256';

-- MySQL 的唯一索引允许多个 NULL，因此未开启签名的工具不受影响。
CREATE UNIQUE INDEX `uk_sys_api_tools_signing_key_id`
  ON `sys_api_tools` (`signing_key_id`);
