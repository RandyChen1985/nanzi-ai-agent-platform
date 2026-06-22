import pytest

pytestmark = pytest.mark.no_infrastructure


def test_parse_semantic_intent_payload_keeps_filter_roles():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    payload = """
    {"keywords":"机房 剩余机柜数 上海 区域",
     "goal":"查询上海区域所有机房的剩余机柜数",
     "metrics":["剩余机柜数"],
     "dimensions":["机房"],
     "filters":[
       {"phrase":"上海区域","semantic_type":"geographic_region",
        "expected_column_types":["区域","地域","gxqy","region","area"],
        "avoid_column_types":["机房名称","shipName"],
        "relation":"parent_region_or_scope"}
     ],
     "time_range":"无",
     "grain":"机房"}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查询上海区域所有机房的剩余机柜数")

    assert intent.keywords == "机房 剩余机柜数 上海 区域"
    assert intent.metrics == ["剩余机柜数"]
    assert intent.dimensions == ["机房"]
    assert intent.filters[0].phrase == "上海区域"
    assert intent.filters[0].semantic_type == "geographic_region"
    assert "gxqy" in intent.filters[0].expected_column_types
    assert "shipName" in intent.filters[0].avoid_column_types
    assert intent.filters[0].relation == "parent_region_or_scope"


def test_format_semantic_intent_context_guides_field_binding():
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
        format_semantic_intent_context,
    )

    intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
        grain="机房",
    )

    block = format_semantic_intent_context(intent)

    assert "结构化业务意图" in block
    assert "上海区域" in block
    assert "geographic_region" in block
    assert "优先绑定字段语义" in block
    assert "gxqy" in block
    assert "避免误绑" in block
    assert "shipName" in block
    assert "不是已确认物理表名或字段名" in block
    assert "必须以 get_dataset_schema 返回为准" in block


def test_build_semantic_intent_prompt_warns_intent_frame_is_not_schema():
    from app.services.ai.data_query_semantic_intent import build_semantic_intent_prompt

    prompt = build_semantic_intent_prompt(
        "查询上海区域所有机房的剩余机柜数",
        "查询上海区域所有机房的剩余机柜数",
        "",
    )

    assert "DataQueryIntentFrame 不是数据库 Schema" in prompt
    assert "不得编造物理表名" in prompt
    assert "SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准" in prompt


def test_parse_semantic_intent_payload_keeps_all_scope_list_query_narrow():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    payload = """
    {"keywords":"机房，列表，设施管理，数据中心，物理位置",
     "goal":"获取所有机房的基础信息列表",
     "metrics":[],
     "dimensions":["机房名称","机房位置","机房状态","机房ID"],
     "filters":[
       {"phrase":"所有机房","semantic_type":"entity",
        "expected_column_types":["机房名称","机房ID","facility_name","dc_name"],
        "avoid_column_types":["机房状态","机房等级","具体机房名称"],
        "relation":"parent_region_or_scope"}
     ],
     "time_range":"无",
     "grain":"明细粒度"}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查一下所有机房的列表")

    assert intent.keywords == "机房 列表"
    assert intent.filters == []
    assert intent.dimensions == ["机房"]
    assert "设施管理" not in intent.keywords
    assert "数据中心" not in intent.keywords
    assert "物理位置" not in intent.keywords


def test_parse_semantic_intent_payload_cleans_space_separated_expanded_keywords():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    payload = """
    {"keywords":"机房 列表 设施管理 数据中心 物理位置",
     "goal":"查一下所有机房的列表",
     "metrics":[],
     "dimensions":["机房名称","物理位置"],
     "filters":[{"phrase":"所有机房","relation":"parent_region_or_scope"}]}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查一下所有机房的列表")

    assert intent.keywords == "机房 列表"
    assert intent.filters == []


def test_parse_semantic_intent_payload_handles_full_scope_particle():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    payload = """
    {"keywords":"全部的机房 列表 数据中心",
     "goal":"查一下全部的机房列表",
     "dimensions":["机房名称"],
     "filters":[{"phrase":"全部的机房","relation":"parent_region_or_scope"}]}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查一下全部的机房列表")

    assert intent.keywords == "机房 列表"
    assert intent.filters == []


def test_build_semantic_intent_prompt_warns_not_to_expand_list_query_scope():
    from app.services.ai.data_query_semantic_intent import build_semantic_intent_prompt

    prompt = build_semantic_intent_prompt(
        "查一下所有机房的列表",
        "查一下所有机房的列表",
        "",
    )

    assert "不要扩大用户问题范围" in prompt
    assert "所有/全部" in prompt
    assert "不要把全量范围词输出为 filters" in prompt


def test_format_empty_result_semantic_repair_context_mentions_parent_child_relationship():
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
        format_empty_result_semantic_repair_context,
    )

    intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
    )

    block = format_empty_result_semantic_repair_context(
        intent,
        diagnostics=[
            {
                "column": "shipName",
                "used_values": ["上海"],
                "candidates": ["外高桥", "金桥B8", "临港123期", "唐镇"],
                "alternative_columns": ["cc_username", "ccname", "gxqy"],
            }
        ],
    )

    assert "空结果语义复核" in block
    assert "父级/范围条件" in block
    assert "上海区域" in block
    assert "shipName" in block
    assert "gxqy" in block
    assert "不能仅因候选值不包含原词就判定无数据" in block
