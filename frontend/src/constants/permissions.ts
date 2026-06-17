/**
 * 权限树统一定义
 * 用于用户管理和角色管理中的权限分配展示
 */
export const MENU_TREE = [
    {
        id: 'menu:dashboard',
        label: '系统概览',
        children: []
    },
    {
        id: 'menu:ai_chat',
        label: '智能助手 (AI Chat)',
        children: []
    },
    {
        id: 'menu:data_sources',
        label: '数据源管理',
        children: [
            { id: 'element:metadata:import', label: '管理数据源与读取 DDL' }
        ]
    },
    {
        id: 'menu:metadata',
        label: '元数据管理',
        children: [
            { id: 'element:metadata:sync', label: '同步到 RAGFlow' },
            { id: 'element:metadata:import', label: '智能导入数据' },
            { id: 'element:metadata:edit', label: '编辑数据集' },
            { id: 'element:metadata:delete', label: '删除数据集' },
            { id: 'element:metadata:view_yaml', label: '查看 YAML' },
            { id: 'element:metadata:edit_table', label: '配置表结构' },
            { id: 'element:metadata:delete_table', label: '删除物理表' }
        ]
    },
    {
        id: 'menu:agent_management',
        label: '智能体中心',
        children: [
            { id: 'element:agent:create', label: '创建智能体' },
            { id: 'element:agent:edit', label: '编辑配置' },
            { id: 'element:agent:delete', label: '删除智能体' }
        ]
    },
    {
        id: 'menu:skills_management',
        label: '技能工作台',
        children: []
    },
    {
        id: 'menu:memory_management',
        label: '记忆工作台',
        children: [
            { id: 'element:memory:config_save', label: '保存服务配置' },
            { id: 'element:memory:config_index', label: '索引检查与重建' },
            { id: 'element:memory:view_data', label: '查看记忆数据' },
            { id: 'element:memory:delete', label: '删除记忆' },
            { id: 'element:memory:view_all_users', label: '按任意用户筛选' },
            { id: 'element:memory:test_search', label: '记忆检索测试' },
        ]
    },
    {
        id: 'menu:chatbi_examples',
        label: '案例集管理',
        children: [
            { id: 'element:chatbi_example:audit', label: '审核反馈' },
            { id: 'element:chatbi_example:sync', label: '同步至 RAGFlow' },
            { id: 'element:chatbi_example:delete', label: '废弃/删除' }
        ]
    },
    {
        id: 'menu:knowledge_management',
        label: '知识库管理',
        children: [
            { id: 'element:knowledge:create', label: '创建知识库' },
            { id: 'element:knowledge:edit', label: '编辑知识库元数据' },
            { id: 'element:knowledge:delete', label: '删除知识库' },
            { id: 'element:knowledge:upload_document', label: '上传文档' },
            { id: 'element:knowledge:delete_document', label: '删除文档' },
            { id: 'element:knowledge:parse_document', label: '解析文档' }
        ]
    },
    {
        id: 'menu:knowledge_retrieval_test',
        label: '检索测试',
        children: [
            { id: 'element:knowledge:test_retrieval', label: '执行检索测试' }
        ]
    },
    {
        id: 'menu:agent_debug',
        label: '智能体测评',
        children: []
    },
    {
        id: 'menu:playground',
        label: '接口调试台',
        children: []
    },
    {
        id: 'menu:chat_logs',
        label: '聊天日志',
        children: [
            { id: 'element:chat_logs:export', label: '导出日志' }
        ]
    },
    {
        id: 'menu:system',
        label: '系统管理',
        children: [
            { id: 'menu:system:users', label: '用户管理' },
            { id: 'element:user:view_key', label: '查看用户密钥' },
            { id: 'element:user:reset_key', label: '重置用户密钥' },
            { id: 'element:user:edit', label: '编辑用户信息' },
            { id: 'element:user:delete', label: '注销用户' },
            { id: 'menu:system:roles', label: '角色管理' },
            { id: 'element:role:edit', label: '编辑角色' },
            { id: 'element:role:delete', label: '删除角色' },
            { id: 'menu:system:config', label: '系统配置' },
            { id: 'element:system:config_save', label: '保存系统配置' },
            { id: 'menu:system:audit', label: '审计日志' }
        ]
    },
    {
        id: 'menu:prompts',
        label: '提示词工坊',
        children: [
            { id: 'element:prompts:optimize', label: 'AI 优化建议' }
        ]
    },
    {
        id: 'menu:task_center',
        label: '任务调度台',
        children: [
            { id: 'element:task:manage', label: '任务管理 (增删改执行)' }
        ]
    },
    {
        id: 'menu:widget_debug',
        label: '组件调试台',
        children: []
    }
];
