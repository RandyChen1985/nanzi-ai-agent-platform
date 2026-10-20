/**
 * 真渲染 MenuAccessTree.vue（含递归子节点 MenuTreeNode.vue）：
 * 验证三层结构（菜单 → 页面 → 功能点）、三态勾选、折叠属性与「角色」只读标注。
 *
 * 为什么用真编译而不是字符串断言：这是单文件组件，Node 不能直接 require。
 * 这里用 vue/compiler-sfc 编译真实源码、esbuild（自定义 .vue loader）剥 TS、
 * @vue/server-renderer 真渲染，不复制任何模板逻辑。交互（点击折叠、只读拦截）SSR
 * 渲染不出来，由 test_user_menu_inherited_scope_behavior.py（纯函数）与
 * test_menu_access_tree_contract.py（源码契约）覆盖。
 *
 * 输出 JSON（断言交给 pytest）。cwd 必须是 frontend。
 */
const fs = require("fs");
const path = require("path");

const fromFrontend = (name) => require(require.resolve(name, { paths: [process.cwd()] }));
const { parse, compileScript, compileTemplate } = fromFrontend("vue/compiler-sfc");
const Vue = fromFrontend("vue");
const { renderToString } = fromFrontend("@vue/server-renderer");
const esbuild = fromFrontend("esbuild");

const COMPONENT = path.resolve("src/components/user/MenuAccessTree.vue");
const frontendRequire = (id) => require(require.resolve(id, { paths: [process.cwd()] }));

const toComponentOptions = (code) =>
  code.replace("export default", "const __sfc =").replace("export function render", "function render");

/** 编译任意 .vue（含自引用的 MenuTreeNode.vue），输出可直接被 esbuild 打包的 TS。 */
function compileVueFile(filename) {
  const source = fs.readFileSync(filename, "utf8");
  const { descriptor, errors } = parse(source, { filename });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "menu-access-tree" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: "menu-access-tree",
    compilerOptions: { bindingMetadata: script.bindings },
  });
  if (tpl.errors.length) throw new Error("template: " + tpl.errors.map(String).join("; "));

  return [
    toComponentOptions(script.content),
    toComponentOptions(tpl.code),
    "__sfc.render = render;",
    "export default __sfc;",
  ].join("\n");
}

const vueSfcPlugin = {
  name: "vue-sfc",
  setup(build) {
    build.onLoad({ filter: /\.vue$/ }, (args) => ({
      contents: compileVueFile(args.path),
      loader: "ts",
      resolveDir: path.dirname(args.path),
    }));
  },
};

async function buildComponent() {
  const bundled = await esbuild.build({
    stdin: {
      contents: `import MenuAccessTree from ${JSON.stringify(COMPONENT)}; export default MenuAccessTree;`,
      loader: "ts",
      resolveDir: path.dirname(COMPONENT),
      sourcefile: "MenuAccessTree.entry.ts",
    },
    bundle: true,
    write: false,
    format: "cjs",
    platform: "node",
    target: "node18",
    external: ["vue"],
    plugins: [vueSfcPlugin],
    logLevel: "silent",
  });

  const moduleRef = { exports: {} };
  new Function("module", "exports", "require", bundled.outputFiles[0].text)(
    moduleRef,
    moduleRef.exports,
    frontendRequire,
  );
  return moduleRef.exports.default || moduleRef.exports;
}

(async () => {
  const App = await buildComponent();

  const render = async (props) => {
    const app = Vue.createSSRApp({ render: () => Vue.h(App, props) });
    return renderToString(app);
  };

  // 场景：直配「用户管理」子菜单 (menu:system:users) + 它的一个功能点 (view_key)，
  // 直配「数据源管理」入口但不配它任何功能点（验证「只给入口」），
  // 直配「检索测试」入口 + 它唯一的功能点（验证整棵子树全选 → 按钮变「清空」）；
  // 角色授予「角色管理」子菜单、两个用户管理功能点，以及一级菜单「提示词工坊」。
  // 其余保持未授予。
  const html = await render({
    menus: ["menu:system:users", "menu:data_sources", "menu:knowledge_retrieval_test"],
    elements: ["element:user:view_key", "element:knowledge:test_retrieval"],
    inheritedMenus: ["menu:system:roles", "menu:prompts"],
    inheritedElements: ["element:user:edit", "element:user:delete"],
    disabled: false,
  });

  const disabledHtml = await render({
    menus: [],
    elements: [],
    inheritedMenus: [],
    inheritedElements: ["element:user:edit"],
    disabled: true,
  });

  const editRow = html.split("编辑用户信息")[0] || "";

  process.stdout.write(
    JSON.stringify({
      checks: {
        rendersAllTopLevelMenus: [
          "系统概览",
          "智能助手 (AI Chat)",
          "数据源管理",
          "元数据管理",
          "系统管理",
          "提示词工坊",
        ].every((label) => html.includes(label)),
        keepsLevelLegend:
          html.includes("菜单权限") && html.includes("页面") && html.includes("功能点权限"),
        collapseButtonsMatchMenusWithChildren: (html.match(/aria-expanded="true"/g) || []).length >= 10,
        rendersRoleBadges: (html.match(/>角色</g) || []).length >= 3,
        inheritedMenuRowIsDimmed: html.includes("px-2.5 py-2 bg-slate-50"),
        inheritedElementIsDimmed: editRow.includes("bg-slate-50"),
        directElementIsChecked: html.includes("查看用户密钥"),
        rendersSubmenuPageBadges: (html.match(/>页面<\/span>/g) || []).length === 4,
        indentsThirdLevel: html.includes("padding-left:28px"),
        showsGrantedLeafCounters: /功能点\s*\d+\/\d+/.test(html),
        entryOnlyCounterIsZeroOfTwo: /数据源管理[\s\S]{0,600}功能点\s*0\/2/.test(html),
        partialCounterIsThreeOfFour: /功能点\s*3\/4/.test(html),
        hasCascadeSelectButtons: html.includes("全选</button>"),
        hasCascadeClearButtons: html.includes("清空</button>"),
        explainsEntryOnlySemantics: html.includes("勾菜单只授予入口"),
        disabledPropagates: disabledHtml.includes('aria-disabled="true"'),
        noWriteChannelInMarkup: !html.includes("permissionData"),
      },
      html,
      roleBadgeCount: (html.match(/>角色</g) || []).length,
      pageBadgeCount: (html.match(/>页面<\/span>/g) || []).length,
      collapseCount: (html.match(/aria-expanded="true"/g) || []).length,
    }),
  );
})();
