"""Contract: Feishu notification support in PersonalCenter and TaskCenter."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTIFICATION_CONFIGS = ROOT / "frontend/src/components/personal/NotificationConfigs.vue"
NOTIFICATION_CHANNEL_GROUPS = (
    ROOT / "frontend/src/components/personal/NotificationChannelGroups.vue"
)
TASK_CENTER = ROOT / "frontend/src/views/TaskCenter.vue"


def test_feishu_personal_center_notification_contract():
    content = NOTIFICATION_CONFIGS.read_text(encoding="utf-8")
    groups = NOTIFICATION_CHANNEL_GROUPS.read_text(encoding="utf-8")

    # 初始状态与渠道名仍留在父组件（渠道列表与文案）
    assert "feishu: { is_enabled: false, webhook_url: '', secret: '' }" in content
    assert "case 'feishu': return '飞书'" in content

    # 飞书卡片标题与「按渠道渲染多群列表」的接线
    assert "飞书群机器人通知" in content
    assert 'channel-type="feishu"' in content

    # 表单字段与测试/保存按钮已下沉到 NotificationChannelGroups：
    # 三个群渠道除了「怎么发出去」，选哪个群的逻辑完全相同，没必要复制三份表单。
    assert 'v-model="group.webhook_url"' in groups
    assert 'v-model="group.secret"' in groups
    assert 'v-model="group.name"' in groups
    # 测试状态按条目引用而非数组下标：请求进行中删掉前面的群，下标会整体前移
    assert "testGroup(group)" in groups
    assert "@click=\"save\"" in groups

    # Guide modal triggers for each channel
    assert "@click.stop=\"openGuide('dingtalk')\"" in content
    assert "@click.stop=\"openGuide('wechat_work')\"" in content
    assert "@click.stop=\"openGuide('feishu')\"" in content
    assert "@click.stop=\"openGuide('email')\"" in content

    # Guide modal component and official links
    assert ":show=\"showGuideModal\"" in content
    assert "open.feishu.cn/document/client-docs/bot-v3/add-custom-bot" in content
    assert "open.dingtalk.com/document/robots/custom-robot-access" in content
    assert "developer.work.weixin.qq.com/document/path/91770" in content
    assert "service.mail.qq.com/detail/0/75" in content


def test_feishu_task_center_notification_contract():
    content = TASK_CENTER.read_text(encoding="utf-8")

    # Channel options
    assert "{ value: 'feishu', label: '飞书' }" in content

    # Hints mapping
    assert "feishu: ['飞书', 'feishu', 'lark']" in content

    # Ready check
    assert "channel === 'feishu'" in content


def test_feishu_agent_management_contract():
    agent_management = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")
    drawer = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    modal = (ROOT / "frontend/src/components/agent/FeishuConfigModal.vue").read_text(encoding="utf-8")
    mysql_sql = (ROOT / "db-prod/V150-register_feishu_notification_tool.sql").read_text(encoding="utf-8")
    pg_sql = (ROOT / "db-prod-pg/V51-register_feishu_notification_tool.sql").read_text(encoding="utf-8")

    assert "import FeishuConfigModal from \"../components/agent/FeishuConfigModal.vue\"" in agent_management
    assert "showFeishuModal" in agent_management
    assert "openFeishuConfig" in agent_management
    assert "<FeishuConfigModal" in agent_management

    assert "openFeishuConfig: [name: string]" in drawer
    assert "tool.name === 'send_feishu_message'" in drawer

    assert "飞书通知工具配置" in modal
    assert "个人中心" in modal

    assert "send_feishu_message" in mysql_sql
    assert "send_feishu_message" in pg_sql
