/**
 * 真渲染 DocWorkspace.vue：验证文件夹层级、默认展开、空状态确实渲染出来。
 *
 * 为什么不用字符串断言：`v-for` 递归展开、`watch(immediate)` 决定默认展开哪些层，
 * 这些只有真跑 setup 才看得出；源码里写了 v-for 不等于列表里真有那几行。
 *
 * 局限：SSR 不触发点击，所以搜索/筛选/拖拽仍由 behavior 与源码契约覆盖。
 * 输出 JSON，断言交给 pytest。cwd 必须是 frontend。
 */
const fs = require("fs");
const path = require("path");

const fromFrontend = (name) => require(require.resolve(name, { paths: [process.cwd()] }));
const { parse, compileScript, compileTemplate } = fromFrontend("vue/compiler-sfc");
const Vue = fromFrontend("vue");
const { renderToString } = fromFrontend("@vue/server-renderer");
const esbuild = fromFrontend("esbuild");

const COMPONENT = path.resolve("src/components/knowledge/DocWorkspace.vue");

function buildComponent(source) {
  const { descriptor, errors } = parse(source, { filename: COMPONENT });
  if (errors.length) throw new Error("parse: " + errors.map(String).join("; "));

  const script = compileScript(descriptor, { id: "doc-workspace" });
  const tpl = compileTemplate({
    source: descriptor.template.content,
    filename: COMPONENT,
    id: "doc-workspace",
    compilerOptions: {
      bindingMetadata: script.bindings,
      expressionPlugins: ["typescript"],
    },
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
      sourcefile: "DocWorkspace.entry.ts",
    },
    bundle: true,
    write: false,
    format: "cjs",
    platform: "node",
    target: "node18",
    external: ["vue"],
    alias: { "@": path.resolve("src") },
    logLevel: "silent",
  });

  const moduleRef = { exports: {} };
  const frontendRequire = (id) => require(require.resolve(id, { paths: [process.cwd()] }));
  new Function("module", "exports", "require", bundled.outputFiles[0].text)(
    moduleRef,
    moduleRef.exports,
    frontendRequire,
  );
  return moduleRef.exports.default || moduleRef.exports;
}

async function render(source, props) {
  const app = Vue.createSSRApp({ render: () => Vue.h(buildComponent(source), props) });
  return await renderToString(app);
}

const doc = (id, name, extra = {}) => ({
  id,
  name,
  chunk_count: 12,
  size: 1024 * 300,
  update_time: Date.now() - 3600_000,
  run: "DONE",
  ...extra,
});

const structure = {
  "制度文件": [],
  "制度文件/2025 年度": ["d1"],
  "培训资料": ["d2"],
};
const docs = [doc("d1", "员工手册.pdf"), doc("d2", "新员工入职培训.pptx"), doc("d3", "组织架构图.png", { run: "FAIL" })];

const summary = (html) => ({
  hasTopFolder: html.includes("制度文件"),
  hasChildFolder: html.includes("2025 年度"),
  hasSiblingFolder: html.includes("培训资料"),
  // 顶层文件夹默认展开：直接挂在「培训资料」下的 d2 应当可见
  showsDocInExpandedTop: html.includes("新员工入职培训.pptx"),
  // 子文件夹默认收起：d1 不应出现
  hidesDocInCollapsedChild: !html.includes("员工手册.pdf"),
  // 未分类文档（不在任何文件夹）永远可见
  showsUnclassified: html.includes("组织架构图.png"),
  folderCountBadge: html.includes("1 份"),
  hasParseFailed: html.includes("解析失败"),
  hasUploadCta: html.includes("上传文档"),
});

(async () => {
  const source = fs.readFileSync(COMPONENT, "utf8");

  const out = {
    nested: summary(
      await render(source, {
        docs,
        folderStructure: structure,
        canUpload: true,
        canManage: true,
      }),
    ),
    flat: await (async () => {
      const html = await render(source, {
        docs: [doc("x1", "未归类一.docx")],
        folderStructure: {},
        canUpload: true,
      });
      return {
        showsUnclassifiedDoc: html.includes("未归类一.docx"),
        // 没有文件夹时不该出现文件夹行
        noFolderRow: !html.includes("个子文件夹"),
      };
    })(),
    empty: (await render(source, { docs: [], folderStructure: {} })).includes("这个知识库还没有文档"),
    loading: (await render(source, { docs: [], loading: true })).includes("正在读取文档"),
    readOnly: !(await render(source, { docs, folderStructure: structure })).includes("上传文档"),
  };

  process.stdout.write(JSON.stringify(out, null, 2));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
