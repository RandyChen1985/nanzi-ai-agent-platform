"""凭据中心的四张表 + 用户偏好。

值本身永远以 Fernet 密文躺在 ``value_encrypted`` 里；``value_fingerprint`` 是
sha256 前 16 位，供落库扫描比对（见 ``app/services/secrets/registry.py``）。

**没有**任何 ORM 方法返回明文——解密只发生在 ``resolver.py`` 判定通过之后，
而且结果包在 ``SensitiveValue`` 里。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, JSON, String, Text

from app.core.orm import Base


class UserSecret(Base):
    """一条凭据（用户级或系统级）。"""

    __tablename__ = "sys_secrets"

    id = Column(String(36), primary_key=True)
    #: ``user`` / ``system``（``dept`` 预留，V1 不开放）
    owner_scope = Column(String(16), nullable=False, default="user")
    #: ``user`` 时为 user_id；``system`` 时为空串（不用 NULL，避免唯一约束失效）
    owner_id = Column(String(64), nullable=False, default="")
    secret_key = Column(String(128), nullable=False)
    name = Column(String(128), nullable=False)
    #: ``credential`` 永不可读 / ``variable`` 可读可复制
    secret_type = Column(String(16), nullable=False, default="credential")
    value_encrypted = Column(Text, nullable=True)
    value_fingerprint = Column(String(16), nullable=True)
    #: 仅 ``variable`` 存前 8 字符供列表展示；``credential`` 必须为 None
    value_preview = Column(String(24), nullable=True)
    allowed_hosts = Column(JSON, nullable=True)
    allowed_scenarios = Column(JSON, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    expires_at = Column(DateTime, nullable=True)
    #: 轮换过渡期的旧值，双写 7 天后由后台任务清空
    previous_value_encrypted = Column(Text, nullable=True)
    previous_expires_at = Column(DateTime, nullable=True)
    category = Column(String(64), nullable=True)
    remark = Column(String(500), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_rotated_at = Column(DateTime, nullable=True)


class SecretGrant(Base):
    """系统级凭据的可见范围。"""

    __tablename__ = "sys_secret_grants"

    id = Column(String(36), primary_key=True)
    # 刻意不声明外键：与 ``sys_secret_usage_logs`` 同理。业务库里这张表要能
    # 单独导出/导入（跨环境搬运时外键会强制父表先存在，导入顺序一错就失败），
    # 级联删除改由 ``center.delete_secret`` 显式清理。
    secret_id = Column(String(36), nullable=False)
    #: ``user`` / ``dept``（V1 只这两种）
    grantee_type = Column(String(16), nullable=False)
    grantee_id = Column(String(64), nullable=False)
    granted_by = Column(String(64), nullable=True)
    granted_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


class SecretBinding(Base):
    """凭据能被谁取到：白名单，默认零授权。"""

    __tablename__ = "sys_secret_bindings"

    id = Column(String(36), primary_key=True)
    # 刻意不声明外键：与 ``sys_secret_usage_logs`` 同理。业务库里这张表要能
    # 单独导出/导入（跨环境搬运时外键会强制父表先存在，导入顺序一错就失败），
    # 级联删除改由 ``center.delete_secret`` 显式清理。
    secret_id = Column(String(36), nullable=False)
    #: ``mcp_server`` / ``api_tool`` / ``script_tool`` / ``agent``
    consumer_type = Column(String(24), nullable=False)
    consumer_id = Column(String(36), nullable=False)
    #: ``header:X-Api-Key`` / ``query:token`` / ``body:password`` / ``env:DB_PASSWORD``
    slot = Column(String(128), nullable=False)
    #: 空串 = 管理员全局绑定（对所有调用者生效）；否则只对绑定者生效。
    #: 用空串而不用 NULL：唯一索引 uk_binding_slot 含这一列，NULL 在唯一索引里
    #: 互不相等，多行 NULL 能共存，等于没有约束。物理 DDL 已改成 NOT NULL
    #: DEFAULT ''（V209 / V109），模型跟着对齐，避免写法上再产生 NULL。
    bound_by_user_id = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SecretUsageLog(Base):
    """使用流水。刻意不设 secret_id 外键：删凭据不删记录。"""

    __tablename__ = "sys_secret_usage_logs"

    id = Column(String(36), primary_key=True)
    secret_id = Column(String(36), nullable=True)
    #: 凭据被删后仍可溯源
    secret_key_snapshot = Column(String(128), nullable=True)
    owner_scope = Column(String(16), nullable=True)
    trace_id = Column(String(64), nullable=True)
    conversation_id = Column(String(64), nullable=True)
    user_id = Column(String(64), nullable=True)
    #: ``mcp`` / ``api_tool`` / ``script_env`` / ``scheduled_task`` / ``agent``
    scene = Column(String(24), nullable=False)
    consumer_type = Column(String(24), nullable=True)
    consumer_id = Column(String(36), nullable=True)
    slot = Column(String(128), nullable=True)
    target_host = Column(String(255), nullable=True)
    #: 成功为 ``success``，其余是拒绝原因
    result = Column(String(32), nullable=False, default="success")
    created_at = Column(DateTime, default=datetime.utcnow)


class SecretAuditLog(Base):
    """变更审计。与使用流水分开：一个记「谁改了配置」，一个记「谁用了凭据」。

    合成一张表会让两类查询互相拖累——排查「这条 token 谁轮换过」时，得在几万条
    调用记录里翻。刻意不设外键，删凭据不删审计，否则出事后最该看的东西没了。
    """

    __tablename__ = "sys_secret_audit_logs"

    id = Column(String(36), primary_key=True)
    secret_id = Column(String(36), nullable=True)
    #: 凭据被删后仍可溯源
    secret_key_snapshot = Column(String(128), nullable=True)
    owner_scope = Column(String(16), nullable=True)
    actor_id = Column(String(64), nullable=True)
    #: 操作者当时的显示名，避免用户改名后审计失去可读性
    actor_name = Column(String(128), nullable=True)
    #: create / update / rotate / delete / disable / enable / grant / revoke / bind / unbind
    action = Column(String(24), nullable=False)
    #: 补充信息（授权的对象、绑定的槽位、轮换的生效时间等），JSON 字符串
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SecretUserPref(Base):
    """用户级注入偏好（三层开关的第 ② 层）。只做收紧。"""

    __tablename__ = "sys_secret_user_prefs"

    user_id = Column(String(64), primary_key=True)
    allow_script_env = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
