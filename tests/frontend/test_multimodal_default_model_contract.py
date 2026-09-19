"""Contract: system default multimodal model config is seeded and selectable."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
MYSQL_MIG = ROOT / "db-prod/V120-add_default_multimodal_model_config.sql"
PG_MIG = ROOT / "db-prod-pg/V20-add_default_multimodal_model_config.sql"


def test_system_config_exposes_default_multimodal_model_dropdown():
    assert "multimodal_model_name" in SYSTEM_CONFIG
    assert "multimodalModelsForConfig" in SYSTEM_CONFIG
    assert "未配置（不支持识图时提示用户）" in SYSTEM_CONFIG
    assert "'llm_model_name',\n      'multimodal_model_name'" in SYSTEM_CONFIG or (
        "llm_model_name" in SYSTEM_CONFIG and "multimodal_model_name" in SYSTEM_CONFIG
    )


def test_default_multimodal_model_migrations_seed_agent_category():
    mysql = MYSQL_MIG.read_text(encoding="utf-8")
    pg = PG_MIG.read_text(encoding="utf-8")
    for text in (mysql, pg):
        assert "multimodal_model_name" in text
        assert "'agent'" in text or '"agent"' in text
        assert "识图" in text or "多模态" in text


def test_default_llm_dropdown_also_lists_multimodal_models():
    """默认大模型（llm_model_name）下拉必须同时放行 llm 与多模态模型。

    多模态模型本身具备文本对话能力，常被直接用作平台默认底座；此前只列
    type === 'llm'，导致它们在配置页根本选不到。
    """
    block = re.search(
        r"const llmModelsForConfig = computed\(\(\) =>(.*?)\n\)",
        SYSTEM_CONFIG,
        re.DOTALL,
    )
    assert block, "未找到 llmModelsForConfig 定义"

    body = block.group(1)
    assert "'llm'" in body, "默认大模型候选必须仍包含 llm 类型"
    assert "isMultimodalModel" in body, "默认大模型候选必须放行多模态模型"

    # 模板必须真正消费该候选，而不是就地再写一遍过滤
    assert 'v-for="m in llmModelsForConfig"' in SYSTEM_CONFIG
    assert "x.type === 'llm' && x.is_active" not in SYSTEM_CONFIG


def test_multimodal_type_list_has_a_single_source():
    """多模态类型判定收敛为单一常量，避免下拉与判定两处列表各自漂移。"""
    assert "MULTIMODAL_MODEL_TYPES = ['multimodal', 'vision', 'image2text']" in SYSTEM_CONFIG
    assert "['multimodal', 'vision', 'image2text'].includes" not in SYSTEM_CONFIG
