-- V122: 工作区文件在线分享（与 db-prod/V222 同结构，PG 方言；会话分享见 V97）
-- 不复制文件本体：冻结「路径 + 大小 + 内容指纹(sha256)」，下载前重新比对，
-- 源文件被改写后旧链接自动失效，避免「分享副本与源文件各自漂移」。
-- 路径存 (workspace_key, rel_path)：绝对路径会随 agentscope_workspace_root 变化而失效；
-- workspace_key 是创建当时的用户目录名（如 admin__1），用户改名后目录名不跟着变。
CREATE TABLE IF NOT EXISTS ai_file_shares (
  id BIGSERIAL PRIMARY KEY,
  share_id VARCHAR(32) NOT NULL,
  owner_user_id VARCHAR(64) NOT NULL,
  owner_username VARCHAR(64),
  workspace_key VARCHAR(128) NOT NULL,
  rel_path VARCHAR(1024) NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  file_ext VARCHAR(32),
  file_size BIGINT NOT NULL DEFAULT 0,
  content_sha256 VARCHAR(64),
  mime_type VARCHAR(128),
  access_mode VARCHAR(16) NOT NULL DEFAULT 'secret',
  secret_cipher VARCHAR(512),
  expires_at TIMESTAMP,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  revoked_at TIMESTAMP,
  view_count INTEGER NOT NULL DEFAULT 0,
  last_viewed_at TIMESTAMP,
  download_count INTEGER NOT NULL DEFAULT 0,
  last_downloaded_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_ai_file_shares_share_id
  ON ai_file_shares (share_id);
CREATE INDEX IF NOT EXISTS idx_ai_file_shares_owner
  ON ai_file_shares (owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_file_shares_created_at
  ON ai_file_shares (created_at);
-- 刻意不建 (owner_user_id, rel_path) 索引：PG 的 btree 索引行同样有约 2704 字节上限，
-- rel_path VARCHAR(1024) 存中文路径（UTF-8 每字 3 字节）很容易触顶；该查询先按 owner 过滤
-- 再在内存里比对路径，单个用户的分享记录只有几十条，走 idx_ai_file_shares_owner 足够。
-- （MySQL 侧是同一条理由：Error 1071 键长超限。）

COMMENT ON TABLE ai_file_shares IS '工作区文件在线分享（内容指纹冻结）';
COMMENT ON COLUMN ai_file_shares.workspace_key IS '分享人工作区目录名，创建时冻结（如 admin__1）';
COMMENT ON COLUMN ai_file_shares.rel_path IS '相对分享人工作区根的路径（如 docs/报表.xlsx）';
COMMENT ON COLUMN ai_file_shares.content_sha256 IS '分享时内容指纹，下载前比对，是「冻结」的核心';
COMMENT ON COLUMN ai_file_shares.view_count IS '在线预览次数（打开链接即渲染即计数）';
COMMENT ON COLUMN ai_file_shares.last_viewed_at IS '最近一次在线预览';
COMMENT ON COLUMN ai_file_shares.download_count IS '成功下载次数（存到本地）';
