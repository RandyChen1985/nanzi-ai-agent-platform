import pytest
from app.services.ai.executors.common import (
    _clean_assistant_text,
    _compress_markdown_tables,
    convert_history_to_messages,
)
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage

def test_clean_assistant_text_basic():
    # 1. 验证剥离 function_calls
    raw = "这里是最终总结。\n<function_calls>\n<invoke name=\"execute_sql_query\"></invoke>\n</function_calls>"
    cleaned = _clean_assistant_text(raw, strip_thought=False)
    assert cleaned == "这里是最终总结。"

    # 2. 验证剥离未闭合的 function_calls (兜底)
    raw_unclosed = "最终总结。\n<function_calls>\n<invoke..."
    cleaned_unclosed = _clean_assistant_text(raw_unclosed, strip_thought=False)
    assert cleaned_unclosed == "最终总结。"

def test_clean_assistant_text_with_thought():
    # 验证不剥离 thought (strip_thought=False)
    raw = "一些前置回复。\n<thought>我想生成SQL计划...</thought>\n<function_calls>...</function_calls>\n最终回答。"
    cleaned_keep = _clean_assistant_text(raw, strip_thought=False)
    assert "<thought>我想生成SQL计划...</thought>" in cleaned_keep
    assert "<function_calls>" not in cleaned_keep

    # 验证剥离 thought (strip_thought=True)
    cleaned_strip = _clean_assistant_text(raw, strip_thought=True)
    assert "<thought>" not in cleaned_strip
    assert "最终回答。" in cleaned_strip
    assert "一些前置回复。" in cleaned_strip

    # 验证 think 标签剥离
    raw_think = "正常话语。<think>思考链内容</think>最终回答。"
    cleaned_think = _clean_assistant_text(raw_think, strip_thought=True)
    assert "<think>" not in cleaned_think
    assert "思考链内容" not in cleaned_think
    assert "正常话语。" in cleaned_think
    assert "最终回答。" in cleaned_think

def test_clean_assistant_text_chart_block():
    # 验证剥离 chart code block
    raw = "我们把这个数据可视化如下：\n```chart\n{\n  \"type\": \"bar\",\n  \"data\": []\n}\n```\n这是分析报告。"
    cleaned = _clean_assistant_text(raw, strip_thought=False)
    assert "```chart" not in cleaned
    assert " bar " not in cleaned
    assert "我们把这个数据可视化如下：" in cleaned
    assert "这是分析报告。" in cleaned

def test_compress_markdown_tables():
    # 1. 验证常规表格（<= 30 行数据）不被压缩
    table_20_rows = ["| ID | 姓名 |", "| --- | --- |"] + [f"| {i} | 用户{i} |" for i in range(1, 21)]
    short_table = "\n".join(table_20_rows)
    res_short = _compress_markdown_tables(short_table)
    assert res_short == short_table

    # 2. 验证仅超出少量行数（如 35 行数据，超出 5 行 < 10 行）不进行负收益截断
    table_35_rows = ["| ID | 姓名 |", "| --- | --- |"] + [f"| {i} | 用户{i} |" for i in range(1, 36)]
    slight_over_table = "\n".join(table_35_rows)
    res_slight = _compress_markdown_tables(slight_over_table)
    assert res_slight == slight_over_table

    # 3. 验证更早轮次超长表格（45 行数据，超出 15 行 >= 10 行）触发折叠，保留前 30 行并在表尾追加清晰说明
    table_45_rows = ["| 省份 | 销售额 | 订单数 |", "| --- | --- | --- |"] + [
        f"| 省份{i} | {100 - i} | {i} |" for i in range(1, 46)
    ]
    long_table = "这里是表格数据：\n" + "\n".join(table_45_rows) + "\n这里是尾部文字。"
    res_long = _compress_markdown_tables(long_table)
    lines = res_long.splitlines()

    # 检查表头与第 30 行数据存在
    assert "| 省份1 | 99 | 1 |" in lines
    assert "| 省份30 | 70 | 30 |" in lines
    # 检查第 31 行往后被折叠
    assert "| 省份31 | 69 | 31 |" not in lines
    assert "| 省份45 | 55 | 45 |" not in lines

    # 检查提示语格式：采用独立的引用块说明，不再伪装成 Markdown 单元格
    notice_line = next((l for l in lines if "更早历史表格数据较长" in l), None)
    assert notice_line is not None
    assert notice_line.startswith("> ")
    assert "折叠后续 15 行明细" in notice_line
    assert not notice_line.endswith("|")


def test_convert_history_to_messages_integration():
    # 构造历史对话
    history = [
        {
            "role": "user",
            "content": "查一下空调销售额 ---\n文件绝对路径: /data/1.csv",
        },
        {
            "role": "assistant",
            "content": (
                "<thought>第一步生成SQL</thought>\n"
                "<function_calls>\n<invoke name=\"exec_sql\"></invoke>\n</function_calls>\n"
                "| 城市 | 销售 |\n"
                "| --- | --- |\n"
                "| 上海 | 10 |\n"
                "| 北京 | 20 |\n"
                "这是最终总结。"
            )
        },
        {
            "role": "user",
            "content": "那冰箱呢",
        }
    ]
    
    # 测试 strip_thought=True 时的转换结果
    messages = convert_history_to_messages(history, strip_thought=True)
    assert len(messages) == 3
    
    # 1. 验证历史 User 消息已精简 (剥离 \n\n---\n\n)
    assert messages[0].content == "查一下空调销售额"
    
    # 2. 验证历史 Assistant 消息已精简
    assistant_content = messages[1].content
    assert "<thought>" not in assistant_content
    assert "<function_calls>" not in assistant_content
    assert "| 上海 | 10 |" in assistant_content
    assert "这是最终总结。" in assistant_content
    
    # 3. 验证最后一轮 User 保持原样或 XML 隔离 (未触发历史裁剪)
    assert "那冰箱呢" in messages[2].content


def test_convert_history_to_messages_last_assistant_protection():
    # 验证多轮对话中：上一轮（最近一轮）Assistant 回复中的表格被深度保护（上限放宽至 100 行），
    # 而更早轮次的 Assistant 长表格按 30 行阈值折叠。
    table_45_rows = "\n".join(
        ["| 序号 | 指标 |", "| --- | --- |"] + [f"| {i} | 指标值{i} |" for i in range(1, 46)]
    )
    table_80_rows = "\n".join(
        ["| 序号 | 指标 |", "| --- | --- |"] + [f"| {i} | 指标值{i} |" for i in range(1, 81)]
    )

    history = [
        # 第 1 轮：45 行表格
        {"role": "user", "content": "第一轮提问"},
        {"role": "assistant", "content": f"第1轮更早长表格：\n{table_45_rows}"},
        # 第 2 轮：80 行表格（上一轮）
        {"role": "user", "content": "第二轮提问"},
        {"role": "assistant", "content": f"第2轮长表格（上一轮）：\n{table_80_rows}"},
        # 第 3 轮用户提问
        {"role": "user", "content": "第三轮追问：基于上一轮数据计算"},
    ]

    messages = convert_history_to_messages(history)
    assert len(messages) == 5

    # 1. 更早轮次（第 1 轮 Assistant）45 行表格应该按默认规则折叠为 30 行
    earlier_content = messages[1].content
    assert "| 30 | 指标值30 |" in earlier_content
    assert "| 31 | 指标值31 |" not in earlier_content
    assert "折叠后续 15 行明细" in earlier_content

    # 2. 最近一轮（第 2 轮 Assistant）表格即便长达 80 行，依然 100% 完整保留，绝不丢失任何数据！
    last_assistant_content = messages[3].content
    assert "| 30 | 指标值30 |" in last_assistant_content
    assert "| 80 | 指标值80 |" in last_assistant_content
    assert "折叠后续" not in last_assistant_content
