"""migrate_api_tool_param_slots 不能误删合法的 rule 绑定。"""
from pathlib import Path

SRC = (Path(__file__).parents[2] / "app/services/secrets/center.py").read_text(encoding="utf-8")
BODY = SRC[SRC.index("existing = {(row.slot"):SRC.index("    if moved:")]


class TestSlotMigrationKeepsStrategy:
    def test_existing_set_carries_strategy(self):
        """唯一索引按 (slot, bound_by_user_id, strategy) 去重，集合也必须按三维比。

        漏掉 strategy 时：迁完 fixed 行后 existing 里有 (target, uid)，
        紧接着处理同参数的 rule 行就会命中「已有人绑」分支，把合法绑定删掉。
        """
        assert "(row.slot, row.bound_by_user_id, row.strategy) for row in rows" in BODY

    def test_discard_uses_the_original_key(self):
        """`row.slot = target` 之后再 discard 等于删掉刚加进去的那个键。"""
        # 旧键必须在改写前算出来
        assert BODY.index("old_key = (row.slot") < BODY.index("row.slot = target")
        # 且不再用被改写的 row.slot 做 discard
        assert "existing.discard((row.slot" not in BODY
        assert BODY.count("existing.discard(old_key)") == 2
