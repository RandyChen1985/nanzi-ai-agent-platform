/**
 * 真渲染 SearchableSelect.vue：验证触发器文案、占位、空选项、禁用态，以及未展开时不产生浮层。
 *
 * 为什么用真编译而不是字符串断言：这是单文件组件，Node 不能直接 require。
 * 这里用 vue/compiler-sfc 编译真实源码、esbuild 剥 TS、@vue/server-renderer 真渲染，
 * 不复制任何模板逻辑。
 *
 * 打开浮层后的搜索、分组、键盘导航属于交互，SSR 渲染不出来，那部分由
 * test_relationship_searchable_select_contract.py（源码契约）覆盖。
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

const COMPONENT = path.resolve("src/components/common/SearchableSelect.vue");
const frontendRequire = (id) => require(require.resolve(id, { paths: [process.cwd()] }));

function buildComponent(source) {
  const { descriptor, errors } = parse(source, { filename: COMPONENT });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "searchable-select" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename: COMPONENT,
    id: "searchable-select",
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
      sourcefile: "SearchableSelect.entry.ts",
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

const GROUPS = [
  {
    label: "当前数据集",
    options: [
      { value: 1, label: "cm_asset_cpu (CMDB CPU资产台账表)", keywords: ["cm_asset_cpu", "CMDB CPU资产台账表"] },
      { value: 2, label: "cm_asset_disk (CMDB 磁盘台账表)", keywords: ["cm_asset_disk", "CMDB 磁盘台账表"] },
    ],
  },
];

async function render(source, props) {
  const app = Vue.createSSRApp({ render: () => Vue.h(buildComponent(source), props) });
  const ctx = {};
  const html = await renderToString(app, ctx);
  const teleport = (ctx.teleports && ctx.teleports.body) || "";
  return { html, teleport };
}

const summary = ({ html, teleport }) => ({
  html,
  hasListbox: /role="listbox"/.test(html) || /role="listbox"/.test(teleport),
  teleportEmpty: teleport.trim() === "",
});

(async () => {
  const source = fs.readFileSync(COMPONENT, "utf8");

  const selected = summary(await render(source, { modelValue: 2, groups: GROUPS }));
  const placeholder = summary(await render(source, {
    modelValue: null,
    groups: GROUPS,
    placeholder: "选择源表...",
  }));
  const emptyOption = summary(await render(source, {
    modelValue: "",
    groups: [{ options: [{ value: "id", label: "id [主键]" }] }],
    allowEmpty: true,
    emptyLabel: "-- 选择字段 --",
    emptyValue: "",
    size: "sm",
  }));
  const disabled = summary(await render(source, {
    modelValue: 1,
    groups: GROUPS,
    disabled: true,
    ariaLabel: "源表",
  }));

  process.stdout.write(JSON.stringify({
    selected: {
      showsSelectedLabel: selected.html.includes("cm_asset_disk (CMDB 磁盘台账表)"),
      closedNoListbox: !selected.hasListbox,
    },
    placeholder: { showsPlaceholder: placeholder.html.includes("选择源表...") },
    emptyOption: {
      showsEmptyLabel: emptyOption.html.includes("-- 选择字段 --"),
      closedNoListbox: !emptyOption.hasListbox,
    },
    disabled: {
      triggerDisabled: /<button[^>]*disabled/.test(disabled.html),
      keepsAriaLabel: disabled.html.includes('aria-label="源表"'),
    },
  }, null, 2));
})();
