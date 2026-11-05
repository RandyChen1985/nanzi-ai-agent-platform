"""知识库的 JSON 扩展配置不再出现在界面上。

``extra_config`` 是开发者的逃生舱口：只有 ``folder_structure``（虚拟文件夹）与
``custom_questions``（门户快捷提问）会被读取，其余键写了没人用。它曾经以
「高级设置」折叠块的形式摆在三处（创建弹窗、编辑弹窗、详情抽屉），用户的原话是
「这块 json 的数据隐藏，界面上不需要让用户看到」。

隐藏入口带来一个必须守住的前提：**保存不能再整份覆盖**。以前保存是把 textarea
里那坨 JSON 原样提交，一旦回填没走到（或有人手滑删了键），``folder_structure``
就没了，所有文档掉回「未分类」且无法撤销。现在改成「从原值出发展开、只覆盖
custom_questions」，这两条测试分别守住"界面看不到"和"保存不丢键"。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
SERVICE = ROOT / "app/services/knowledge_base_service.py"
ENDPOINTS = ROOT / "app/api/portal/endpoints/ragflow.py"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_no_json_editor_on_any_surface(page):
    """创建弹窗、编辑弹窗、详情抽屉三处入口都要消失。"""
    assert "业务元数据扩展配置" not in page
    assert "业务扩展配置 JSON" not in page
    assert "额外扩展配置 JSON" not in page


def test_no_leftover_extra_config_state(page):
    """入口删了，配套的 state 与解析函数也不能留成死代码。"""
    assert "extraConfigText" not in page
    assert "parseExtraConfig" not in page
    assert "advancedConfigOpen" not in page
    assert "createAdvancedConfigOpen" not in page
    assert "detailConfigOpen" not in page


def test_save_only_sends_custom_questions(page):
    """保存只提交 custom_questions 一个键，绝不整份回写 extra_config。

    整份回写（哪怕是"从原值展开"）仍然是把打开弹窗那一刻的快照写回去：
    期间别人做过的目录调整会被静默回滚，这正是项目此前在文件夹操作上
    修过的 lost update，这里是它的另一半。
    """
    # 以行首的 const 作为切片终点，否则会在行内的 const 处提前截断
    update = page.split("const updateMetadata =", 1)[1].split("\nconst ", 1)[0]
    assert "custom_questions: customQuestionsPayload" in update
    assert "extra_config:" not in update
    # 也不该再有任何 extra_config 快照（那是 lost update 的来源）
    assert "editingExtraConfig" not in page


def test_create_does_not_send_extra_config(page):
    """新建库没有需要保留的键；不传时后端按"不设置"处理。"""
    create = page.split("await axios.post('/api/portal/ragflow/datasets'", 1)[1].split("})", 1)[0]
    assert "extra_config" not in create


def test_custom_questions_keep_their_own_editor(page):
    """快捷提问必须有独立入口，否则藏掉 JSON 后用户就没法维护它了。"""
    assert "custom_questions" in page
    assert "添加提问" in page
    assert "customQuestions" in page


def test_backend_treats_missing_extra_config_as_no_change():
    """前端不传 extra_config 时后端不能把已有键清空（这是隐藏入口的前提）。"""
    source = SERVICE.read_text(encoding="utf-8")
    update_block = source.split("async def upsert_metadata", 1)[1]
    assert "if extra_config is not None:" in update_block
    assert "record.extra_config = extra_config" in update_block


def test_backend_merges_custom_questions_by_key():
    """端点必须用键级写，否则 folder_structure 仍会被整份覆盖。"""
    endpoint = ENDPOINTS.read_text(encoding="utf-8").split(
        "async def update_dataset_metadata", 1
    )[1].split("@router.", 1)[0]
    assert '"custom_questions" in payload.model_fields_set' in endpoint
    assert 'key="custom_questions"' in endpoint
    # 判断依据是"字段是否出现"，不是"值是否非空"——清空快捷提问也是合法操作
    assert "payload.custom_questions or []" in endpoint
