"""当前用户的部门：必须注入**可读**的部门名，而不是 `/1/`。

线上现象：admin 问「我是什么部门的」，答"部门路径 `/1/`，没有配置具体的部门名称"。
查库后确认：admin 的主部门就是**总部**（`dept_code='main'`、`org_path='/1/'`），
部门信息其实注入了，只是注入的两个值都是机器标识：

- `org_path='/1/'` —— id 物化路径，人类不可读；
- `dept_code='main'` —— 英文业务代码，不是中文名。

`AgentServicePrompts.user_context_message` **本来就有 `dept` 参数且优先于 `org_path`**，
但 `user_info` 里从来没有 `dept_name` 这个键，于是 `department = (dept or org_path)` 永远
退化成 `/1/`。模型只能照搬，不能"翻译"——它手上根本没有可翻译的材料。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
AUTH = (ROOT / "app/services/auth_service.py").read_text(encoding="utf-8")
PROMPTS = (ROOT / "app/services/ai/agent_prompts.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/services/department_service.py").read_text(encoding="utf-8")
AGENT = (ROOT / "app/services/ai/agent_service.py").read_text(encoding="utf-8")


def test_prompt_never_falls_back_to_internal_path():
    """`org_path` 是 id 物化路径，兜底给模型只会被照搬成"部门路径 /1/"。"""
    assert "(dept or org_path" not in PROMPTS
    assert "department = (dept or \"\").strip()" in PROMPTS


def test_identity_dict_now_carries_dept_name():
    """真正的缺口：身份字典里没有 dept_name，模板的 dept 参数永远拿不到值。"""
    assert "dept_name" in AGENT, "构建用户上下文时要读 dept_name"
    assert "_attach_department_label" in AUTH, "认证产出身份字典时要补上可读部门名"


def test_all_identity_producers_attach_the_label():
    """三个产出点都要补：登录签发、API Key 校验、会话回源复核。

    只补一处的话，登录进来是"总部"、换个入口又变回 `/1/`。
    """
    assert AUTH.count("_attach_department_label(session, user_data)") >= 2
    # 会话复核分支的 session 在 finally 里就关了，部门名必须趁连接还在时取
    assert "refreshed_label" in AUTH


def test_service_builds_label_with_secondary_departments():
    """一个人可能兼属多个部门，只报主部门会漏掉兼职部门。"""
    assert "department_label_for_user" in SERVICE
    label = SERVICE.split("async def department_label_for_user", 1)[1].split("async def ", 1)[0]
    assert "兼" in label
    # 标签复用名字列表，避免个人中心与模型上下文各写一套查询
    assert "department_names_for_user" in label
    names = SERVICE.split("async def department_names_for_user", 1)[1].split("async def ", 1)[0]
    assert "is_primary" in names, "主部门排第一靠 is_primary 排序"


def test_label_lookup_degrades_instead_of_breaking_auth():
    """部门表缺失（未迁移环境）不能让人登不进来。"""
    segment = AUTH.split("async def _attach_department_label", 1)[1].split("async def ", 1)[0]
    assert "except Exception" in segment
