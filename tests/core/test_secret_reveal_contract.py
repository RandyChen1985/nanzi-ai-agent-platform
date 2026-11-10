"""`reveal()` 的出口必须收敛。

明文只该出现在「真正发请求」的那一行。分散出去之后，每一处都是新的泄漏面，
而且没法保证都登记了 `register_injected`——漏一处，回显兜底就对那条链路失效。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]

#: 允许调用 reveal() 的地方，及理由
ALLOWED = {
    "app/services/secrets/injection.py": "注入层：取明文填进出站请求，并登记掩码兜底",
    "app/services/secrets/policy.py": "定义与文档示例",
}


def test_reveal_only_in_injection_and_policy():
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # 只看真实调用，不看文档/注释里的 `.reveal()` 字样
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if re.search(r"\.reveal\(\)", stripped):
                offenders.append(f"{rel}: {stripped[:80]}")
    assert not offenders, "reveal() 出现在了不允许的位置：\n" + "\n".join(offenders)


def test_every_injection_site_registers_for_redaction():
    """每个 reveal() 旁边都要有登记，否则那条链路的结果不会被打码。"""
    source = (ROOT / "app/services/secrets/injection.py").read_text(encoding="utf-8")

    reveals = source.count(".reveal()")
    registers = source.count("register_injected(plain)")
    assert reveals == 5, f"注入点数量变了（{reveals}），确认是否需要同步调整契约"
    assert registers == reveals, f"有 {reveals} 处 reveal，却只有 {registers} 处登记"
