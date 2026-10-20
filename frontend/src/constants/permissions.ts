/**
 * 权限树统一定义
 * 用于用户管理和角色管理中的权限分配展示
 *
 * 三层结构：一级菜单 → 子菜单（能点进去的页面，`menu:*`）→ 功能点（页面内的按钮，`element:*`）。
 * 只有「系统管理」目前存在子菜单这一层；没有子菜单的菜单，children 直接是功能点。
 * 层级用 children 表达，不要靠 id 前缀去猜归属——展示与级联都以这个结构为准。
 */
/**
 * 与多个菜单共用的功能点（一个权限码被多个页面依赖）。
 *
 * 这是已知的粒度缺陷，不是刻意设计：拆分它需要同时改后端端点的依赖并迁移已有授权，
 * 所以先显式登记，让 `test_permission_tree_ownership` 能拦住「新增的意外共用」。
 * 目前没有共用项：数据源连接管理已拆成 element:data_sources:edit/delete（只读跟菜单走）。
 */
export const SHARED_ELEMENT_IDS: string[] = [];

export interface PermissionNode {
    id: string;
    label: string;
    /** 子菜单或功能点；叶子节点没有 children */
    children?: PermissionNode[];
}

export const MENU_TREE: PermissionNode[] = [
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
        // 只读能力跟菜单走：有「数据源管理」菜单就能看列表与表画像，
        // 功能点只控制写操作（与 MCP 工具集的口径一致）。
        children: [
            { id: 'element:data_sources:edit', label: '新增/编辑数据源、测试连接与 SQL 预览' },
            { id: 'element:data_sources:delete', label: '删除数据源' }
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
        children: [
            { id: 'element:skills:admin', label: '管理平台技能与审核' }
        ]
    },
    {
        id: 'menu:mcp_management',
        label: 'MCP 工具集',
        // 后端 mcp.py 的写端点已加权限校验；读端点仍由菜单权限控制
        children: [
            { id: 'element:mcp_management:create', label: '创建 MCP 服务' },
            { id: 'element:mcp_management:edit', label: '编辑服务配置与连接测试' },
            { id: 'element:mcp_management:delete', label: '删除 MCP 服务' },
            { id: 'element:mcp_management:sync', label: '同步服务工具清单' },
            { id: 'element:mcp_management:execute', label: '执行（测试）工具' },
            { id: 'element:mcp_management:publish', label: '发布 / 下线工具' }
        ]
    },
    {
        id: 'menu:mcp_service',
        label: 'MCP 服务台',
        children: [
            { id: 'element:mcp_service:overview:read', label: '查看服务总览' },
            { id: 'element:mcp_service:config:read', label: '查看服务配置' },
            { id: 'element:mcp_service:config:edit', label: '修改服务开关' },
            { id: 'element:mcp_service:client:read', label: '查看外部 Client' },
            { id: 'element:mcp_service:client:manage', label: '管理外部 Client' },
            { id: 'element:mcp_service:client:secret_reset', label: '重置 Client Secret' },
            { id: 'element:mcp_service:client:token_issue', label: '生成当前用户 Access Token' },
            { id: 'element:mcp_service:capability:read', label: '查看能力与 Scope' },
            { id: 'element:mcp_service:capability:manage', label: '管理能力与 Scope' },
            { id: 'element:mcp_service:grant:read', label: '查看用户授权' },
            { id: 'element:mcp_service:grant:revoke', label: '撤销用户授权' },
            { id: 'element:mcp_service:audit:read', label: '查看调用审计' }
        ]
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
        label: '智能体调试',
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
            {
                id: 'menu:system:users',
                label: '用户管理',
                children: [
                    { id: 'element:user:view_key', label: '查看用户密钥' },
                    { id: 'element:user:reset_key', label: '重置用户密钥' },
                    { id: 'element:user:edit', label: '编辑用户信息' },
                    { id: 'element:user:delete', label: '注销用户' }
                ]
            },
            {
                id: 'menu:system:roles',
                label: '角色管理',
                children: [
                    { id: 'element:role:edit', label: '编辑角色' },
                    { id: 'element:role:delete', label: '删除角色' }
                ]
            },
            {
                id: 'menu:system:config',
                label: '系统配置',
                children: [
                    { id: 'element:system:config_save', label: '保存系统配置' },
                    { id: 'element:system:langfuse_save', label: '保存 Langfuse 链路追踪配置' }
                ]
            },
            { id: 'menu:system:audit', label: '审计日志' }
        ]
    },
    {
        id: 'menu:prompts',
        label: '提示词工坊',
        children: [
            { id: 'element:prompts:optimize', label: 'AI 优化建议' },
            // 后端 POST /api/portal/prompts/save 要求此码；树里缺失会让该权限无法授出
            { id: 'element:prompts:edit', label: '保存提示词' }
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

const findPermissionNode = (nodes: PermissionNode[], nodeId: string): PermissionNode | undefined => {
    for (const node of nodes) {
        if (node.id === nodeId) return node;
        const hit = findPermissionNode(node.children || [], nodeId);
        if (hit) return hit;
    }
    return undefined;
};

const collectDescendantIds = (node: PermissionNode, acc: string[] = []): string[] => {
    (node.children || []).forEach((child) => {
        acc.push(child.id);
        collectDescendantIds(child, acc);
    });
    return acc;
};

/**
 * 取某个菜单/子菜单的全部后代 id（含更深层级）。
 *
 * 三层之后「勾子菜单」必须能级联到它自己的功能点，只取直接子项会漏掉一层。
 */
export const getMenuDescendantIds = (menuId: string): string[] => {
    const node = findPermissionNode(MENU_TREE, menuId);
    return node ? collectDescendantIds(node) : [];
};

/** 在整棵树里按 id 查节点（含子菜单），供「部分选中」等状态计算使用。 */
export const findPermissionNodeById = (nodeId: string): PermissionNode | undefined =>
    findPermissionNode(MENU_TREE, nodeId);
