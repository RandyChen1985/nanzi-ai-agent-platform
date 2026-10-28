-- V97: 会话在线分享（与 db-prod/V197 同结构，PG 方言；V95/V96 预留给 knowledge-base-hardening 计划）
CREATE TABLE IF NOT EXISTS ai_conversation_shares (
  id BIGSERIAL PRIMARY KEY,
  share_id VARCHAR(32) NOT NULL,
  conversation_id VARCHAR(50),
  owner_user_id VARCHAR(64) NOT NULL,
  owner_username VARCHAR(64),
  agent_id VARCHAR(36),
  agent_name VARCHAR(128),
  title VARCHAR(500),
  access_mode VARCHAR(16) NOT NULL DEFAULT 'secret',
  secret_cipher VARCHAR(512),
  content_json TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  turn_count INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMP,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  revoked_at TIMESTAMP,
  view_count INTEGER NOT NULL DEFAULT 0,
  last_viewed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_ai_conversation_shares_share_id
  ON ai_conversation_shares (share_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_owner
  ON ai_conversation_shares (owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_conversation
  ON ai_conversation_shares (conversation_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_created_at
  ON ai_conversation_shares (created_at);
