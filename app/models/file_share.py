"""工作区文件在线分享表。

与会话分享（``ai_conversation_shares``）同一套语义，但**冻结的东西不一样**：

- 会话分享冻结的是正文快照（``content_json``），源会话被改被删都不影响已发出的分享；
- 文件分享**不复制文件**，只冻结三样东西——路径、大小、内容校验和（``content_sha256``）。
  下载前重新算一次哈希，对不上就拒绝并提示「文件已更新，请重新分享」。

这样做的好处是不占第二份磁盘空间，也不会出现「分享副本与源文件各自漂移、谁也说不清
对方拿到的是哪一版」；代价是源文件被更新后旧链接自动失效——这正是我们要的语义：
分享出去的内容不会在对方眼皮底下悄悄变掉。

路径存 ``(workspace_key, rel_path)`` 两段，而不是绝对路径与不是 ``user_id``：

- 绝对路径会随工作区根目录配置（``agentscope_workspace_root``）变化而失效；
- ``user_id`` 拼不出目录名，用户目录名由 ``resolve_workspace_user_key`` 生成（如
  ``admin__1``），用户改名后目录名不跟着变，所以把创建当时的目录名原样冻下来最稳。

刻意不建物理外键，与 ``ai_conversation_shares`` 保持一致：删用户由管理侧统一清理。

索引与迁移脚本（``db-prod/V222-create-ai-file-shares.sql`` /
``db-prod-pg/V122-create-ai-file-shares.sql``）保持同名同列，避免「迁移建了一套、
模型写着另一套」这种只在某个环境才暴露的漂移。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String

from app.core.orm import Base


class FileShare(Base):
    """一条文件分享记录：路径 + 内容指纹 + 访问控制 + 下载计数。"""

    __tablename__ = "ai_file_shares"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    share_id = Column(String(32), nullable=False, unique=True, comment="对外链接 ID")
    owner_user_id = Column(String(64), nullable=False, comment="分享人用户 ID")
    owner_username = Column(String(64), nullable=True, comment="分享人用户名（展示用）")
    workspace_key = Column(
        String(128),
        nullable=False,
        comment="分享人工作区目录名（创建时冻结，如 admin__1）",
    )
    rel_path = Column(
        String(1024),
        nullable=False,
        comment="相对分享人工作区根的路径，如 docs/报表.xlsx",
    )
    file_name = Column(String(255), nullable=False, comment="展示用文件名")
    file_ext = Column(String(32), nullable=True, comment="小写扩展名，含点，如 .xlsx")
    file_size = Column(BigInteger, nullable=False, default=0, comment="分享时文件字节数")
    content_sha256 = Column(
        String(64),
        nullable=True,
        comment="分享时内容指纹；下载前比对，对不上即视为文件已变更",
    )
    mime_type = Column(String(128), nullable=True, comment="下载时响应的媒体类型")
    access_mode = Column(String(16), nullable=False, default="secret", comment="public / secret")
    secret_cipher = Column(String(512), nullable=True, comment="访问密码密文（Fernet）")
    expires_at = Column(DateTime, nullable=True, comment="失效时间，NULL=永久")
    status = Column(String(16), nullable=False, default="active", comment="active / revoked")
    revoked_at = Column(DateTime, nullable=True, comment="作废时间")
    view_count = Column(Integer, nullable=False, default=0, comment="在线预览次数（打开链接即渲染即计数）")
    last_viewed_at = Column(DateTime, nullable=True, comment="最近一次在线预览")
    download_count = Column(Integer, nullable=False, default=0, comment="成功下载次数（存到本地）")
    last_downloaded_at = Column(DateTime, nullable=True, comment="最近一次成功下载")
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="创建时间")

    __table_args__ = (
        Index("idx_ai_file_shares_owner", "owner_user_id", "created_at"),
        Index("idx_ai_file_shares_created_at", "created_at"),
        # 刻意不建 (owner_user_id, rel_path) 索引：rel_path 是 VARCHAR(1024)，utf8mb4 下
        # 4096 字节，加 owner_user_id 后超过 InnoDB 3072 字节键长上限（Error 1071）；
        # PG 的 btree 索引行也有约 2704 字节上限。该查询先按 owner 过滤再比对路径，
        # 单用户分享记录只有几十条，走 owner 索引足够。
    )
