-- V81 发布表作者字段解绑（删除用户时保留发布审计）
-- Date: 2026-10-21
-- Purpose:
--   skill_publications.source_user_id 与 skill_publication_versions.submitted_by 此前是
--   NOT NULL 外键。管理员删除「发布过技能的用户」时，db.delete(user) 会撞上 RESTRICT
--   外键直接报错（500），这条路径一直没被覆盖。
--
--   删除用户的语义在这里是「保留发布记录、只解绑作者」：审核轨迹（谁提交、谁审核、
--   审核意见）、platform_skill_id（唯一索引，丢了就永远对不上历史引用）都必须留下。
--   因此两列改为可空；delete_user 会先按「下架」语义处理该作者已发布的版本
--   （删除物化目录、版本置 REVOKED），再把 source_user_id / submitted_by 置 NULL。
--
-- 幂等：DROP NOT NULL 可重复执行。

ALTER TABLE skill_publications
    ALTER COLUMN source_user_id DROP NOT NULL;

ALTER TABLE skill_publication_versions
    ALTER COLUMN submitted_by DROP NOT NULL;

COMMENT ON COLUMN "skill_publications"."source_user_id" IS '原个人技能所属用户 ID；作者被删除后为 NULL（发布记录保留）';
COMMENT ON COLUMN "skill_publication_versions"."submitted_by" IS '提交审核的用户 ID；提交人被删除后为 NULL（版本记录保留）';
