-- V222: 工作区文件在线分享（会话分享见 V197，本表与其同语义、不同冻结物）
-- 不复制文件本体：冻结「路径 + 大小 + 内容指纹(sha256)」，下载前重新比对，
-- 源文件被改写后旧链接自动失效，避免「分享副本与源文件各自漂移」。
-- 路径存 (workspace_key, rel_path)：绝对路径会随 agentscope_workspace_root 变化而失效；
-- workspace_key 是创建当时的用户目录名（如 admin__1），用户改名后目录名不跟着变。
-- 过期不落库（expires_at 为 NULL 表示永久，过期状态由时间计算），避免引入定时任务。
-- 预览与下载分开计数：可预览格式（HTML/图片/PDF/文本）打开链接即在页内渲染，算 view；
-- 真正存到本地才算 download。合成一个数会失真——「看了一眼」和「存走了」不是一回事。
CREATE TABLE IF NOT EXISTS `ai_file_shares` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `share_id` VARCHAR(32) NOT NULL COMMENT '对外链接 ID（secrets.token_urlsafe(18)）',
  `owner_user_id` VARCHAR(64) NOT NULL COMMENT '分享人用户 ID',
  `owner_username` VARCHAR(64) DEFAULT NULL COMMENT '分享人用户名（展示用）',
  `workspace_key` VARCHAR(128) NOT NULL COMMENT '分享人工作区目录名，创建时冻结（如 admin__1）',
  `rel_path` VARCHAR(1024) NOT NULL COMMENT '相对分享人工作区根的路径（如 docs/报表.xlsx）',
  `file_name` VARCHAR(255) NOT NULL COMMENT '展示用文件名',
  `file_ext` VARCHAR(32) DEFAULT NULL COMMENT '小写扩展名，含点（如 .xlsx）',
  `file_size` BIGINT NOT NULL DEFAULT 0 COMMENT '分享时文件字节数',
  `content_sha256` VARCHAR(64) DEFAULT NULL COMMENT '分享时内容指纹，下载前比对，是「冻结」的核心',
  `mime_type` VARCHAR(128) DEFAULT NULL COMMENT '下载响应的媒体类型',
  `access_mode` VARCHAR(16) NOT NULL DEFAULT 'secret' COMMENT 'public / secret',
  `secret_cipher` VARCHAR(512) DEFAULT NULL COMMENT '访问密码密文（Fernet，可回看）',
  `expires_at` DATETIME DEFAULT NULL COMMENT '失效时间，NULL=永久',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active / revoked',
  `revoked_at` DATETIME DEFAULT NULL COMMENT '作废时间',
  `view_count` INT NOT NULL DEFAULT 0 COMMENT '在线预览次数（打开链接即渲染即计数）',
  `last_viewed_at` DATETIME DEFAULT NULL COMMENT '最近一次在线预览',
  `download_count` INT NOT NULL DEFAULT 0 COMMENT '成功下载次数（存到本地）',
  `last_downloaded_at` DATETIME DEFAULT NULL COMMENT '最近一次成功下载',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_file_shares_share_id` (`share_id`),
  KEY `idx_ai_file_shares_owner` (`owner_user_id`, `created_at`),
  KEY `idx_ai_file_shares_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作区文件在线分享（内容指纹冻结）';
-- 刻意不建 (owner_user_id, rel_path) 索引：rel_path VARCHAR(1024) 在 utf8mb4 下是 4096 字节，
-- 加 owner_user_id 后超过 InnoDB 3072 字节的索引键上限（Error 1071）；改成前缀索引又会把
-- 「查这个文件是否已分享」变成近似匹配。该查询先按 owner 过滤、再在内存里比对路径，
-- 单个用户的分享记录通常只有几十条，走 idx_ai_file_shares_owner 完全够用。
