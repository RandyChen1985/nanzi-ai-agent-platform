-- V86 智能体分享（发送副本邀请，接收方接受后生成自己的副本）
-- Date: 2026-10-24
-- Purpose:
--   分享**不是**授权共用，而是「把当前配置发一份给对方」。所有者把自己的智能体分享给某个
--   人，对方接受后拿到一个全新的、属于自己的智能体，需要重新命名并走向导确认配置，
--   复制过程中剔除接收方无权使用的工具/MCP/技能。
--
--   为什么不做成「授权可见」：
--   授权共用会让两个用户共享同一份线上配置，改配置时谁能改、改了影响谁都会变模糊；
--   而「给你一份副本」边界清楚，源与副本互不影响，也不需要额外的编辑权设计。
--
--   系统级智能体不提供分享：
--   系统级的可用范围由管理员统一授权治理（下架时要按授权表统计受影响人数、发通知）。
--   如果所有者能自己往外发副本，等于绕开这套治理，且「谁在用生产基线」答不上来。
--   所以 is_system=true 时既隐藏入口，后端也直接拒绝。
--
--   新增表 ai_agent_share_requests：
--     snapshot   分享那一刻的完整配置快照（JSON）。用快照而不是接受时现读源，
--                是为了让「源后来被改 / 被删 / 被提升为系统级」都不影响已经发出的分享。
--     status     PENDING / ACCEPTED / REJECTED / EXPIRED / REVOKED / WITHDRAWN
--                REVOKED 用于「源在对方接受前变成了系统级」——此时该分享作废，
--                并提示对方改为向管理员申请使用权限。
--     expires_at 默认 7 天，过期由读取时惰性判定，不依赖定时任务。
--
--   刻意不建物理外键：与 ai_agent_system_requests / skill_publications 同一惯例，
--   避免删除用户或智能体时撞 RESTRICT，清理由应用层负责。
--
--   幂等：建表与建索引均 IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS public.ai_agent_share_requests (
    id character varying(36) NOT NULL,
    agent_id character varying(36) NOT NULL,
    agent_name character varying(255),
    agent_display_name character varying(255),
    owner_user_id integer,
    owner_user_name character varying(255),
    recipient_user_id integer NOT NULL,
    recipient_user_name character varying(255),
    snapshot text,
    snapshot_version integer NOT NULL DEFAULT 1,
    status character varying(20) NOT NULL DEFAULT 'PENDING',
    share_message character varying(500),
    share_reason character varying(500),
    new_agent_id character varying(36),
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone,
    responded_at timestamp without time zone,
    CONSTRAINT ai_agent_share_requests_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE public.ai_agent_share_requests IS '智能体分享记录（副本邀请）';
COMMENT ON COLUMN public.ai_agent_share_requests.snapshot IS '分享时的完整配置快照（JSON：元数据 + 版本配置）';
COMMENT ON COLUMN public.ai_agent_share_requests.status IS 'PENDING / ACCEPTED / REJECTED / EXPIRED / REVOKED / WITHDRAWN';
COMMENT ON COLUMN public.ai_agent_share_requests.share_message IS '分享留言（可选）';
COMMENT ON COLUMN public.ai_agent_share_requests.share_reason IS '状态变更原因（如作废原因）';

CREATE INDEX IF NOT EXISTS idx_agent_share_recipient
    ON public.ai_agent_share_requests USING btree (recipient_user_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_share_agent
    ON public.ai_agent_share_requests USING btree (agent_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_share_owner
    ON public.ai_agent_share_requests USING btree (owner_user_id, created_at);
