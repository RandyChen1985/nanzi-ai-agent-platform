/**
 * 真渲染 TimelineToolArgsBlock.vue，验证「参数 / 命令」区块的折叠开关真的接在正文上。
 *
 * 为什么不用 markdown 那种 require 真实模块的方式：这是单文件组件，Node 无法直接加载。
 * 这里用 vue/compiler-sfc 编译**真实源码**、esbuild 剥掉 TS 注解、@vue/server-renderer
 * 真渲染（不复制任何模板逻辑）。
 *
 * 为什么折叠态要靠「把默认值改成 false 再渲染」来验证：SSR 没有 DOM 事件，点不了按钮。
 * 于是判定标准变成「v-show 是否真的接在 bodyExpanded 上」—— 若哪天正文改成无条件渲染、
 * 或 v-show 绑成了常量，收起态渲染出的 <pre> 就不会带 display:none，用例立刻变红。
 *
 * 输出 JSON（断言交给 pytest）：
 *   { expanded: {...}, collapsed: {...}, command: {...} }
 * cwd 必须是 frontend（require('vue/compiler-sfc')、require('esbuild') 才能解析到）。
 */
const fs = require("fs");
const path = require("path");

// 依赖装在 frontend/node_modules，而本脚本住在 tests/frontend：require 按脚本所在目录解析，
// 所以显式按 cwd（必须是 frontend）解析，脚本才能独立运行。
const fromFrontend = (name) => require(require.resolve(name, { paths: [process.cwd()] }));
const { parse, compileScript, compileTemplate } = fromFrontend("vue/compiler-sfc");
const Vue = fromFrontend("vue");
const { renderToString } = fromFrontend("@vue/server-renderer");
const esbuild = fromFrontend("esbuild");

const COMPONENT = path.resolve("src/components/chat/TimelineToolArgsBlock.vue");
const DEFAULT_STATUS = "const bodyExpanded = ref(true);";
const COLLAPSED_STATUS = "const bodyExpanded = ref(false);";

const VUE_IMPORT = /import\s*\{([^}]*)\}\s*from\s*['"]vue['"];?/g;

/** 把 SFC 源码编译成可用的组件定义（剥 import 到 Vue 形参、剥 TS 注解）。 */
function buildComponent(source) {
  const { descriptor, errors } = parse(source, { filename: COMPONENT });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "tool-args-block" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename: COMPONENT,
    id: "tool-args-block",
    compilerOptions: { bindingMetadata: script.bindings },
  });
  if (tpl.errors.length) throw new Error("template: " + tpl.errors.map(String).join("; "));

  const toDestructure = (code) =>
    code
      .replace(VUE_IMPORT, (_match, names) => `const {${names.replace(/\s+as\s+/g, ": ")}} = Vue;`)
      .replace("export default", "const __sfc =")
      .replace("export function render", "function render");

  const tsCode = toDestructure(script.content) + "\n" + toDestructure(tpl.code);
  const jsCode = esbuild.transformSync(tsCode, { loader: "ts", format: "esm" }).code;
  return new Function("Vue", `${jsCode}\n__sfc.render = render;\nreturn __sfc;`)(Vue);
}

async function render(props) {
  const app = Vue.createSSRApp({ render: () => Vue.h(buildComponent(props.source), props.props) });
  const html = await renderToString(app);

  const attributes = (pattern) => {
    const match = html.match(pattern);
    return match ? match[1] : null;
  };
  const preTag = html.match(/<pre([^>]*)>/);

  return {
    bodyHidden: /\bdisplay:\s*none/.test(preTag ? preTag[1] : ""),
    ariaExpanded: attributes(/aria-expanded="([^"]*)"/),
    toggleTitle: attributes(/aria-expanded="[^"]*"\s+title="([^"]*)"/),
    label: attributes(/<span>(参数|命令)<\/span>/),
    copyAriaLabel: attributes(/aria-label="([^"]*)"/),
    bodyRendered: html.includes("chat-bi"),
  };
}

const baseProps = {
  text: '{"agent_name":"chat-bi","query":"查询各智能体调用次数排名"}',
  toolName: "sub_agent_call",
  copyKey: "child-args-1",
  copiedKey: null,
};

(async () => {
  const source = fs.readFileSync(COMPONENT, "utf8");
  if (!source.includes(DEFAULT_STATUS)) {
    throw new Error(`默认折叠态不再是 ${DEFAULT_STATUS}，请同步本探针`);
  }

  const expanded = await render({ props: baseProps, source });
  const collapsed = await render({ props: baseProps, source: source.replace(DEFAULT_STATUS, COLLAPSED_STATUS) });
  const command = await render({ props: { ...baseProps, toolName: "工具完成: Bash (1200ms)" }, source });

  process.stdout.write(JSON.stringify({ expanded, collapsed, command }, null, 2));
})();
