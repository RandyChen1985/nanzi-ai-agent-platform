/**
 * 真渲染「我的权限 → 平台功能权限」只读树与分类卡片外壳。
 *
 * 为什么真编译：层级、容器样式、`data-granted`、`aria-expanded` 都由模板决定，
 * 字符串断言只能证明「写了这几个字」，证明不了「未授权的分支真的没渲染」。
 * 这里复用 tests/frontend/menu_access_tree_ssr.cjs 的做法：vue/compiler-sfc 编译
 * 真实源码、esbuild 剥 TS、@vue/server-renderer 真渲染，不复制任何模板逻辑。
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

const TREE = path.resolve("src/components/personal/PersonalPermissionTree.vue");
const CARD = path.resolve("src/components/personal/PermissionSectionCard.vue");
const frontendRequire = (id) => require(require.resolve(id, { paths: [process.cwd()] }));

const toComponentOptions = (code) =>
  code.replace("export default", "const __sfc =").replace("export function render", "function render");

/** 编译任意 .vue（含递归的 PersonalPermissionTreeNode.vue），输出可被 esbuild 打包的 TS。 */
function compileVueFile(filename) {
  const source = fs.readFileSync(filename, "utf8");
  const { descriptor, errors } = parse(source, { filename });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "personal-permission-tree" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: "personal-permission-tree",
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

async function buildComponents() {
  const bundled = await esbuild.build({
    stdin: {
      contents: `import Tree from ${JSON.stringify(TREE)};
import SectionCard from ${JSON.stringify(CARD)};
export { Tree, SectionCard };`,
      loader: "ts",
      resolveDir: path.dirname(TREE),
      sourcefile: "PersonalPermissionTree.entry.ts",
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
  return moduleRef.exports;
}

(async () => {
  const { Tree, SectionCard } = await buildComponents();

  const render = async (Component, props, slots) => {
    const app = Vue.createSSRApp({ render: () => Vue.h(Component, props, slots) });
    return renderToString(app);
  };

  const menu = (id, displayName) => ({ id, name: id, display_name: displayName });

  // 场景 1：只授「用户管理」子页面 + 两个功能点，父菜单「系统管理」未授 → 容器；
  // 另授「数据源管理」入口但不授功能点（验证只给入口）；带两个后端新增的未知权限码。
  const html = await render(Tree, {
    menus: [
      menu("menu:system:users", "用户管理"),
      menu("menu:data_sources", "数据源管理"),
      menu("menu:brand_new", "全新菜单"),
    ],
    elements: [
      menu("element:user:view_key", "查看用户密钥"),
      menu("element:user:edit", "编辑用户信息"),
      menu("element:new:do", "全新功能"),
    ],
  });

  // 场景 2：只授一个深层功能点，父链两级都只是分组容器。
  const deepOnlyHtml = await render(Tree, {
    menus: [],
    elements: [menu("element:system:config_save", "保存系统配置")],
  });

  const emptyHtml = await render(Tree, { menus: [], elements: [] });

  const expandedCard = await render(
    SectionCard,
    { title: "智能体 (Agents)", collapsed: false },
    { default: () => "卡片内容", count: () => "3" },
  );
  const collapsedCard = await render(
    SectionCard,
    { title: "知识库", collapsed: true },
    { default: () => "卡片内容", count: () => "5" },
  );

  // 说明文案里也含「仅分组」三个字，容器数量只数徽章本身
  const countMatches = (source) => (source.match(/>仅分组<\/span>/g) || []).length;

  process.stdout.write(
    JSON.stringify({
      checks: {
        rendersGrantedMenuAndElements:
          html.includes("系统管理") &&
          html.includes("用户管理") &&
          html.includes("查看用户密钥") &&
          html.includes("编辑用户信息") &&
          html.includes("数据源管理"),
        prunesUnauthorizedSiblings:
          !html.includes("角色管理") &&
          !html.includes("审计日志") &&
          !html.includes("元数据管理") &&
          !html.includes("系统概览"),
        ungrantedParentIsContainer:
          html.includes('data-permission-id="menu:system" data-granted="false"') &&
          countMatches(html) === 1,
        pageBadgeOnlyOnGrantedSubmenu: /用户管理[\s\S]{0,400}页面/.test(html),
        indentsThreeLevels:
          /padding-left:\s*8px/.test(html) &&
          /padding-left:\s*26px/.test(html) &&
          /padding-left:\s*44px/.test(html),
        showsGrantedElementCounter: /用户管理[\s\S]{0,400}功能点\s*2/.test(html),
        collapseButtonsDefaultExpanded: (html.match(/aria-expanded="true"/g) || []).length >= 1,
        unmatchedFallbackUsesDisplayName:
          html.includes("其他（未在菜单树中登记）") &&
          html.includes("全新菜单") &&
          html.includes("全新功能"),
        deepOnlyParentChainIsAllContainers: countMatches(deepOnlyHtml) === 2,
        deepOnlyKeepsGrantedLeaf:
          deepOnlyHtml.includes("保存系统配置") &&
          deepOnlyHtml.includes('data-permission-id="element:system:config_save" data-granted="true"'),
        emptyStateExplicit: emptyHtml.includes("（暂无授权）") && !emptyHtml.includes("系统管理"),
        expandedCardIsOpen:
          expandedCard.includes('aria-expanded="true"') &&
          expandedCard.includes("aria-controls=") &&
          !expandedCard.includes("display:none"),
        collapsedCardHidesBodyButKeepsDom:
          collapsedCard.includes('aria-expanded="false"') &&
          collapsedCard.includes("display:none") &&
          collapsedCard.includes("卡片内容"),
      },
      stats: {
        containerCount: countMatches(html),
        collapseCount: (html.match(/aria-expanded="true"/g) || []).length,
      },
    }),
  );
})();
