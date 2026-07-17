-- NanZi AI Agent Platform - Core Schema
-- Date: 2025-12-31

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- 1. Table: ai_agent_users
-- ----------------------------
CREATE TABLE IF NOT EXISTS ai_agent_users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_name VARCHAR(64) NOT NULL UNIQUE,
    api_key_encrypted TEXT COMMENT '加密存储的 API Key（Fernet 加密，可解密）',
    api_key_hash VARCHAR(64) NOT NULL UNIQUE COMMENT 'SHA256 哈希值（用于快速验证）',
    role VARCHAR(32) DEFAULT 'user' COMMENT 'admin or user',
    remark VARCHAR(500) DEFAULT NULL COMMENT '备注',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status TINYINT DEFAULT 1
);

-- ----------------------------
-- 2. Table: ai_agent_access_logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS ai_agent_access_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    trace_id VARCHAR(64) NOT NULL,
    user_id BIGINT,
    user_name VARCHAR(64),
    endpoint VARCHAR(255) NOT NULL,
    method VARCHAR(10) NOT NULL,
    query_params TEXT,
    status_code INT NOT NULL,
    process_time_ms FLOAT NOT NULL,
    client_ip VARCHAR(45),
    request_params TEXT COMMENT '请求参数(JSON格式)',
    response_body TEXT COMMENT '响应内容(JSON格式)',
    error_message TEXT COMMENT '错误信息',
    user_agent VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trace_id (trace_id),
    INDEX idx_logs_created_at (created_at),
    INDEX idx_logs_status_code (status_code),
    INDEX idx_logs_method (method),
    INDEX idx_logs_user_name (user_name)
);

SET FOREIGN_KEY_CHECKS = 1;
