/**
 * SSR 探针用的子组件替身。
 *
 * `knowledge_doc_workspace_ssr.cjs` 只验证 DocWorkspace 自己的渲染结果，
 * 它 import 的子组件（例如 PaginationBar）在探针里换成这个空壳：
 * esbuild 的 buildSync 不支持插件，没法在探针里现场编译 SFC 套 SFC。
 * 子组件自己的契约由各自的探针/契约测试保证。
 */
export default {
  name: "ChildStub",
  render() {
    return null;
  },
};
