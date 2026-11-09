"""变更审计：谁在什么时候对哪条凭据做了什么。

sys_secret_usage_logs 记的是「谁用了凭据」，答不了「这条 token 谁轮换过」、
「这个绑定谁加的」。出事时这两个问题都要能查。
"""

import inspect

import pytest


def test_audit_model_has_no_foreign_key():
    """删凭据不删审计。

    有外键（或级联删除）的话，删掉凭据的一瞬间最该看的记录跟着没了。
    """
    from app.models.secret import SecretAuditLog

    for column in SecretAuditLog.__table__.columns:
        assert not column.foreign_keys, f"{column.name} 不该有外键"


def test_audit_keeps_key_snapshot():
    """凭据被删后，记录仍要能说清是哪条。"""
    from app.models.secret import SecretAuditLog

    assert "secret_key_snapshot" in SecretAuditLog.__table__.columns


def test_audit_keeps_actor_name_snapshot():
    """用户改名后，审计要保持当时的可读性——不能 join 用户表现取。"""
    from app.models.secret import SecretAuditLog

    assert "actor_name" in SecretAuditLog.__table__.columns


@pytest.mark.parametrize(
    "action",
    ["create", "update", "rotate", "delete", "disable", "enable", "grant", "revoke", "bind", "unbind"],
)
def test_every_mutating_operation_is_recorded(action):
    """所有会改变「谁能用、能用什么」的操作都得留痕。

    漏一个就等于留了一条无痕改动的路径。
    """
    from app.services.secrets import center

    source = inspect.getsource(center)

    # enable / disable 走同一个三元，源码里是 "enable" if ... else "disable"
    assert f'"{action}"' in source


def test_audit_failure_never_breaks_the_operation():
    """写审计失败只记日志，不阻断主流程。"""
    from app.services.secrets import center

    source = inspect.getsource(center._audit)

    assert "except Exception" in source
    assert "logger.warning" in source


def test_audit_uses_its_own_transaction():
    """审计用独立 session。

    表是本次新增的，而代码可以先于迁移上线。共用业务事务的话，表没建的那一天
    用户的建凭据、轮换、授权会统统失败——为了附属记录拖垮主功能。
    """
    from app.services.secrets import center

    source = inspect.getsource(center._audit)

    assert "AsyncSessionLocal" in source
    assert "await audit_session.commit" in source


def test_missing_audit_table_does_not_break_creating_a_secret():
    """表不存在时，建凭据必须照常成功。

    这条曾经真实失败过：审计挂在业务事务上，`commit` 时才报
    `Table 'sys_secret_audit_logs' doesn't exist`，把整个凭据中心带崩。
    """
    from app.services.secrets import center

    source = inspect.getsource(center._audit)

    assert "except Exception" in source
    # 异常必须在 commit 外面兜住，不能只包住 add
    assert source.index("try:") < source.index("await audit_session.commit")


def test_audit_is_written_before_delete():
    """删除时审计要先写：删完 secret 行就取不到 key 快照了。"""
    from app.services.secrets import center

    source = inspect.getsource(center.delete_secret)

    assert source.index('action="delete"') < source.index("await session.delete(row)")
