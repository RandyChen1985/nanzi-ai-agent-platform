import sys
import os
import json

import pytest

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.executors.common import parse_xml_tool_calls


pytestmark = pytest.mark.no_infrastructure


def test_xml_parsing():
    content = """我来帮您查询所有机房的列表。首先让我获取相关的元数据信息。

<function_calls>
<invoke name="get_dataset_schema">
<parameter name="query">机房 列表 机房信息</parameter>
</invoke>
</function_calls>"""

    tool_calls = parse_xml_tool_calls(content)
    
    print(f"Parsed Tool Calls: {json.dumps(tool_calls, indent=2, ensure_ascii=False)}")
    
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_dataset_schema"
    assert tool_calls[0]["args"]["query"] == "机房 列表 机房信息"
    assert tool_calls[0]["id"].startswith("call_")
    print("Test passed!")

if __name__ == "__main__":
    test_xml_parsing()
