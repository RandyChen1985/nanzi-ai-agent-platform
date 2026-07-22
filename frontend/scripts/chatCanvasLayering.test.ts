import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const readSource = (path: string) => readFileSync(resolve(root, path), "utf8");

const chatCanvas = readSource("src/components/embed/ChatCanvas.vue");
const datasetPortalDrawer = readSource("src/components/chatbi/DatasetPortalDrawer.vue");
const knowledgePortalDrawer = readSource("src/components/knowledge/KnowledgePortalDrawer.vue");
const workspaceBrowserDrawer = readSource("src/components/embed/WorkspaceBrowserDrawer.vue");
const memoryBrowserDrawer = readSource("src/components/embed/MemoryBrowserDrawer.vue");
const embedChat = readSource("src/views/EmbedChat.vue");
const agentDebug = readSource("src/views/AgentDebug.vue");

const maxZ = (source: string) => {
  const matches = Array.from(source.matchAll(/z-\[(\d+)\]/g), (match) => Number(match[1]));
  assert.ok(matches.length > 0, "expected component to declare at least one explicit z-index utility");
  return Math.max(...matches);
};

const drawerMaxZ = Math.max(
  maxZ(datasetPortalDrawer),
  maxZ(knowledgePortalDrawer),
  maxZ(workspaceBrowserDrawer),
  maxZ(memoryBrowserDrawer),
);

const expectCanvasLayer = (label: string, pattern: RegExp) => {
  const match = chatCanvas.match(pattern);
  assert.ok(match, `missing ${label} canvas z-index`);
  const value = Number(match[1]);
  assert.ok(
    value > drawerMaxZ,
    `${label} canvas z-index ${value} should be above drawer z-index ${drawerMaxZ}`,
  );
};

expectCanvasLayer(
  "left dock",
  /return 'fixed left-0[\s\S]*?z-\[(\d+)\][\s\S]*?border-r border-l-0'/,
);
expectCanvasLayer(
  "right dock",
  /return 'fixed right-0[\s\S]*?z-\[(\d+)\][\s\S]*?border-l border-r-0'/,
);

assert.match(
  chatCanvas,
  /const useBodyTeleport = computed\(\(\) =>\s*!\s*props\.overlay \|\| isFullscreen\.value \|\| isMobile\.value,\s*\);/,
  "workspace overlay preview should stay mounted in the chat area instead of covering the whole page",
);

assert.match(
  chatCanvas,
  /if \(props\.overlay\) \{\s*return 'absolute inset-0 z-\[\d+\]/,
  "workspace overlay preview should be scoped to the chat area",
);

assert.match(
  chatCanvas,
  /: 'absolute inset-0 z-\[\d+\]'/,
  "workspace overlay backdrop should be scoped to the chat area",
);

const expectRagPreviewAbovePinnedDrawers = (label: string, source: string) => {
  assert.match(
    source,
    /<teleport to="body">\s*<!-- Canvas RAG 原地物理高亮预览抽屉 -->/,
    `${label} RAG source-document preview should be teleported to body`,
  );

  const match = source.match(
    /<!-- Canvas RAG 原地物理高亮预览抽屉 -->[\s\S]*?class="[^"]*z-\[(\d+)\][^"]*"/,
  );
  assert.ok(match, `${label} RAG source-document preview should declare an explicit z-index`);

  const value = Number(match[1]);
  assert.ok(
    value > drawerMaxZ,
    `${label} RAG source-document preview z-index ${value} should be above drawer z-index ${drawerMaxZ}`,
  );
};

expectRagPreviewAbovePinnedDrawers("EmbedChat", embedChat);
expectRagPreviewAbovePinnedDrawers("AgentDebug", agentDebug);

console.log("chatCanvasLayering.test.ts passed");
