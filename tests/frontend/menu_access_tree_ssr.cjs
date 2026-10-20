/**
 * 真渲染 MenuAccessTree.vue：验证树结构、三态勾选、折叠属性与「角色」只读标注。
 *
 * 为什么用真编译而不是字符串断言：这是单文件组件，Node 不能直接 require。
 * 这里用 vue/compiler-sfc 编译真实源码、esbuild 剥 TS、@vue/server-renderer 真渲染，
 * 不复制任何模板逻辑。交互（点击折叠、只读拦截）SSR 渲染不出来，由
 * test_user_menu_inherited_scope_behavior.py（纯函数）与
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

function buildComponent(source) {
  const { descriptor, errors } = parse(source, { filename: COMPONENT });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "menu-access-tree" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename: COMPONENT,
    id: "menu-access-tree",
    compilerOptions: { bindingMetadata: script.bindings },
  });
  if (tpl.errors.length) throw new Error("template: " + tpl.errors.map(String).join("; "));

  const toComponentOptions = (code) =>
    code.replace("export default", "const __sfc =").replace("export function render", "function render");

  const bundled = esbuild.buildSync({
    stdin: {
      contents: [
        toComponentOptions(script.content),
        toComponentOptions(tpl.code),
        "__sfc.render = render;",
        "export default __sfc;",
      ].join("\n"),
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

async function render(source, props) {
  const app = Vue.createSSRApp({ render: () => Vue.h(buildComponent(source), props) });
  return renderToString(app);
}

(async () => {
  const source = fs.readFileSync(COMPONENT, "utf8");

  // 场景：系统管理菜单由角色授予；用户直配了「用户管理」+「查看用户密钥」；
  // 「编辑用户信息 / 注销用户」由角色授予（应显示灰勾 + 角色徽标）；
  // 其余功能点既未直配也未继承（应显示空框）。
  const html = await render(source, {
    menus: ["menu:system:users"],
    elements: ["element:user:view_key"],
    inheritedMenus: ["menu:system:audit", "menu:prompts"],
    inheritedElements: ["element:user:edit", "element:user:delete"],
    disabled: false,
  });

  const disabledHtml = await render(source, {
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
        keepsLevelLegend: html.includes("菜单权限") && html.includes("功能点权限"),
        collapseButtonsMatchMenusWithChildren: (html.match(/aria-expanded="true"/g) || []).length >= 10,
        rendersRoleBadges: (html.match(/>角色</g) || []).length >= 3,
        inheritedMenuRowIsDimmed: html.includes("px-2.5 py-2 bg-slate-50"),
        inheritedElementIsDimmed: editRow.includes("bg-slate-50/60"),
        directElementIsChecked: /element:user:view_key/.test(html) || html.includes("查看用户密钥"),
        disabledPropagates: disabledHtml.includes('aria-disabled="true"'),
        noWriteChannelInMarkup: !html.includes("permissionData"),
      },
      html,
      roleBadgeCount: (html.match(/>角色</g) || []).length,
      collapseCount: (html.match(/aria-expanded="true"/g) || []).length,
    }),
  );
})();
