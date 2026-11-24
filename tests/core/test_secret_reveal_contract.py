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
    # 脚本通道。这两处和「注入」不同：值**确实要交给脚本**，平台拦不住后续的
    # 打印与外发，只留调用记录（设计见 docs/superpowers/specs/2026-11-24-script-credential-access-design.md）。
    # 允许它们 reveal，前提是每一处都紧跟 register_injected——由下面
    # test_script_channel_reveals_are_registered 守住。
    "app/api/internal/script_credential.py": (
        "脚本取值接口：把明文交给沙箱里的 helper，这是它唯一的职责"
    ),
    "app/services/ai/runtime/agentscope/workspace.py": (
        "Bash 结果脱敏：按票据记录重新解密，只为把结果里的值换成掩码"
    ),
}

#: 新增的 reveal 出口：必须「取完立刻登记」，否则回显兜底对这条链路失效。
REVEAL_SITES_REQUIRING_REGISTER = (
    "app/api/internal/script_credential.py",
    "app/services/ai/runtime/agentscope/workspace.py",
)


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


def test_script_channel_reveals_are_registered():
    """脚本通道的每个 reveal() 后面必须紧跟 register_injected。

    这条链路不经过注入层，也就没有注入层顺手做的那次登记。漏登记的后果是
    「脚本 print 出来的值原样进模型上下文」——而用户看不见这个差别。
    """
    problems: list[str] = []
    for rel in REVEAL_SITES_REQUIRING_REGISTER:
        source = (ROOT / rel).read_text(encoding="utf-8")
        lines = source.splitlines()
        for index, line in enumerate(lines):
            if not re.search(r"\.reveal\(\)", line.strip()) or line.strip().startswith("#"):
                continue
            # 同一行紧跟，或紧接着的几行里登记（够覆盖「先取值再处理」的写法）
            window = "\n".join(lines[index : index + 4])
            if "register_injected(" not in window:
                problems.append(f"{rel}:{index + 1}: {line.strip()[:70]}")
    assert not problems, "这些 reveal() 之后没有登记掩码：\n" + "\n".join(problems)
