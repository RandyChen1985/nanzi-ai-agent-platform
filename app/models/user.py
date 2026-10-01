from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base
from app.models.permission import Role, UserRoleRelation  # noqa: F401

# 用户状态。
# 2 = 待审核：账号自主注册申请提交后的初始态，管理员审核通过后变为 1、被拒后变为 0。
# 禁用（0）是永久终态，账号名不释放，同名账号不可重新申请。
USER_STATUS_DISABLED = 0
USER_STATUS_ENABLED = 1
USER_STATUS_PENDING_REVIEW = 2


class User(Base):
    __tablename__ = "ai_agent_users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_name = Column(String(50), unique=True, nullable=False, index=True)
    real_name = Column(String(50), nullable=True)
    email = Column(String(254), nullable=True, index=True, comment='邮箱（小写归一化存储，全局唯一；NULL=未填写）')
    role = Column(String(20), default="user") # admin, user
    dept_code = Column(String(50), nullable=True, comment='部门代码')
    org_path = Column(String(255), nullable=True, comment='组织结构全路径 (例如: yovole/sh/dc1)')
    extra_data = Column(Text, nullable=True, comment='预留扩展字段 (存储 JSON 格式信息)')
    api_key_encrypted = Column(Text, nullable=True)
    api_key_hash = Column(String(64), index=True, nullable=True)
    password_hash = Column(String(128), nullable=True)
    password_updated_at = Column(DateTime, nullable=True, comment='密码最后修改时间')
    last_login_at = Column(DateTime, nullable=True, comment='上次登录时间')
    remark = Column(String(255))
    status = Column(Integer, default=1)  # 0=disabled, 1=enabled, 2=pending_review
    two_factor_enabled = Column(Boolean, default=False, nullable=False, comment='是否启用两步验证(2FA)')
    two_factor_secret = Column(String(512), nullable=True, comment='两步验证TOTP密钥')
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    roles = relationship("Role", secondary="ai_agent_user_role_relations", back_populates="users", lazy="selectin")
