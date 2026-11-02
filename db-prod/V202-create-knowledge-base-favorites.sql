-- V202: 知识库收藏（我的收藏）
-- 「我的收藏」此前是前端占位，需要一个按人记录的收藏表；
-- dataset_id 存 RAGFlow Dataset ID（与 knowledge_base_metadata 同口径），
-- 唯一键锁住「一个人对一个库只收藏一次」，重复收藏走 INSERT IGNORE。
CREATE TABLE IF NOT EXISTS `knowledge_base_favorites` (
    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id` BIGINT NOT NULL COMMENT '收藏人用户ID',
    `dataset_id` VARCHAR(64) NOT NULL COMMENT 'RAGFlow Dataset ID',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '收藏时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uix_kbf_user_dataset` (`user_id`, `dataset_id`),
    KEY `ix_kbf_dataset` (`dataset_id`),
    KEY `ix_kbf_user` (`user_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '知识库收藏（我的收藏）';
