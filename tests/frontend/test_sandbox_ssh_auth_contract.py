from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "frontend/src/views/SystemConfig.vue"


def test_ssh_auth_type_is_a_two_option_select_with_dependent_credentials():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "sandboxSshAuthType" in source
    assert "sandbox_ssh_auth_type" in source
    assert '<option value="password">' in source
    assert '<option value="key">' in source
    assert "sandbox_ssh_password" in source
    assert "sandbox_ssh_private_key" in source
    assert "private_key" in source
    assert "sandboxSshAuthType.value === 'key'" in source
    assert "sandboxSshAuthType.value !== 'key'" in source


def test_ssh_private_key_field_uses_textarea_with_validation_and_examples():
    source = SETTINGS.read_text(encoding="utf-8")

    # 1. 验证使用专用多行文本域而非普通单行密码输入框
    assert "item.key === 'sandbox_ssh_private_key'" in source
    assert "<textarea" in source
    assert "rows=\"7\"" in source or "rows='7'" in source

    # 2. 验证私钥格式校验函数与误填公钥防护
    assert "validateSshPrivateKey" in source
    assert "public_key" in source
    assert "ssh-rsa" in source
    assert "ssh-ed25519" in source
    assert "BEGIN" in source and "PRIVATE KEY" in source
    assert "END" in source and "PRIVATE KEY" in source

    # 3. 验证格式示例与操作能力
    assert "sshPrivateKeyExampleExpanded" in source
    assert "copySshPrivateKeyExample" in source
    assert "normalizeSshPrivateKey" in source
    assert "ssh-keygen -t ed25519" in source

    # 4. 验证测试连接前置拦截非法私钥与公钥
    assert "testSandboxConnection" in source
    assert "无法发起测试" in source

    # 5. 验证参数「？」问号说明弹层与快速配置助手
    assert "'sandbox_ssh_private_key': `【SSH 私钥认证核心原理】" in source
    assert "sshPrivateKeySetupCommand" in source
    assert "copySshSetupCommand" in source
    assert "copySshKeygenCommand" in source
    assert "activeExplanationItem.key === 'sandbox_ssh_private_key'" in source

    # 6. 验证已保存私钥的脱敏掩码识别与保护（防止显示格式异常或被误拦截）
    assert "'masked'" in source
    assert "已脱敏保护" in source
    assert "trimmed.includes('****')" in source


