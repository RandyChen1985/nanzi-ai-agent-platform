/**
 * 权限树统一定义
 * 用于用户管理和角色管理中的权限分配展示
 *
 * 三层结构：一级菜单 → 子菜单（能点进去的页面，`menu:*`）→ 功能点（页面内的按钮，`element:*`）。
 *
 * 菜单数据本身已经入库（`ai_agent_menus`），由 `GET /api/portal/menus/tree` 下发；
 * 本文件只保留节点类型与纯函数，不再持有菜单内容。
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
export const getMenuDescendantIds = (tree: PermissionNode[], menuId: string): string[] => {
    const node = findPermissionNode(tree, menuId);
    return node ? collectDescendantIds(node) : [];
};

/** 在整棵树里按 id 查节点（含子菜单），供「部分选中」等状态计算使用。 */
export const findPermissionNodeById = (
    tree: PermissionNode[],
    nodeId: string,
): PermissionNode | undefined => findPermissionNode(tree, nodeId);
