-- V79: 扩展 meta_changelog.resource_id 长度
-- 表级变更日志使用 "{dataset_id}:{physical_name}"，物理表名最长 255，需容纳前缀

ALTER TABLE `meta_changelog`
    MODIFY COLUMN `resource_id` VARCHAR(270) NOT NULL COMMENT '资源ID';
