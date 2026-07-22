import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");
const readSource = (path: string) => readFileSync(resolve(root, path), "utf8");
const helpPopoverPath = resolve(root, "src/components/GroundingHelpPopover.vue");
const helpPopover = existsSync(helpPopoverPath) ? readFileSync(helpPopoverPath, "utf8") : "";

const settings = readSource("src/components/embed/ChatSettings.vue");
const embedChat = readSource("src/views/EmbedChat.vue");
const debugPanel = readSource("src/components/DebugConfigPanel.vue");
const agentDebug = readSource("src/views/AgentDebug.vue");

assert.doesNotMatch(settings, />模型选择</, "settings should remove the duplicate model selector");
assert.match(settings, /反幻觉校验/, "settings should expose the grounding toggle");
assert.match(settings, /handleSetGrounding/, "settings should allow toggling grounding");
assert.match(
  settings,
  /@update:modelValue="handleSetGrounding"/,
  "settings should bind the grounding Switch to handleSetGrounding",
);
assert.match(embedChat, /enableGrounding:\s*true/, "EmbedChat grounding should default to enabled");
assert.match(
  embedChat,
  /grounding_enabled:\s*config\.enableGrounding/,
  "every chat request should send an explicit grounding boolean",
);
assert.match(
  embedChat,
  /const resetSession[\s\S]*?config\.enableGrounding\s*=\s*true/,
  "starting a new conversation should reset grounding to the default enabled state",
);

assert.doesNotMatch(
  debugPanel,
  /模型覆盖 \(Model Override\)/,
  "AgentDebug panel should remove the duplicate model override",
);
assert.match(debugPanel, />反幻觉校验</, "AgentDebug panel should expose the grounding toggle");
assert.match(
  debugPanel,
  /v-model="config\.enableGrounding"/,
  "AgentDebug panel should bind the shared grounding setting",
);
assert.match(agentDebug, /enableGrounding:\s*false/, "AgentDebug should default grounding to disabled");
assert.match(
  agentDebug,
  /grounding_enabled:\s*debugConfig\.enableGrounding/,
  "AgentDebug should send an explicit grounding boolean",
);
assert.match(
  agentDebug,
  /const generateNewConversation[\s\S]*?debugConfig\.enableGrounding\s*=\s*false/,
  "a new AgentDebug conversation should reset grounding to disabled",
);
assert.match(
  agentDebug,
  /:selected-model="debugConfig\.model"[\s\S]*?@update:selected-model="debugConfig\.model = \$event"/,
  "the AgentDebug input model selector should remain available",
);

assert.equal(
  existsSync(helpPopoverPath),
  true,
  "the shared GroundingHelpPopover component should exist",
);
assert.match(
  settings,
  /import GroundingHelpPopover[\s\S]*?<GroundingHelpPopover\s*\/>/,
  "chat settings should mount the shared grounding help popover",
);
assert.match(
  debugPanel,
  /import GroundingHelpPopover[\s\S]*?<GroundingHelpPopover\s*\/>/,
  "AgentDebug should mount the same grounding help popover",
);
assert.match(helpPopover, /查询当前销售额/, "the help should show one concrete example question");
assert.match(helpPopover, /关闭/, "the help should explain disabled behavior");
assert.match(helpPopover, /开启且证据匹配/, "the help should explain verified behavior");
assert.match(helpPopover, /开启但证据不足/, "the help should explain insufficient-evidence behavior");
assert.match(helpPopover, /风险提示/, "the insufficient-evidence example should mention the warning");
assert.match(helpPopover, /@mouseenter=/, "desktop hover should open the help");
assert.match(helpPopover, /@click\.stop=/, "click should toggle the help without toggling its parent control");
assert.match(helpPopover, /event\.key === "Escape"/, "Escape should close the help");
assert.match(
  helpPopover,
  /trigger\.value\?\.closest\("\.dark"\)/,
  "the teleported help should preserve the surrounding dark theme",
);
assert.match(
  helpPopover,
  /document\.addEventListener\("pointerdown"/,
  "clicking outside should close the help",
);

for (const [source, surface] of [
  [settings, "chat settings"],
  [debugPanel, "AgentDebug"],
] as const) {
  assert.match(
    source,
    /import \{ useToast \} from ["']@\/composables\/useToast["']/,
    `${surface} should reuse the global Toast composable`,
  );
  assert.match(source, /反幻觉校验已开启/, `${surface} should confirm that grounding was enabled`);
  assert.match(source, /反幻觉校验已关闭/, `${surface} should confirm that grounding was disabled`);
  assert.match(
    source,
    /enabled \? ["']success["'] : ["']info["']/,
    `${surface} should use success for enabled and info for disabled`,
  );
}
assert.match(
  settings,
  /if \(props\.config\.enableGrounding === enabled\)[\s\S]*?saveAndClose\(\);[\s\S]*?return;/,
  "chat settings should not show a Toast when the state did not change",
);
assert.match(
  debugPanel,
  /@change="handleGroundingChange"/,
  "AgentDebug should show feedback after the checkbox value changes",
);

console.log("groundingToggle.test.ts passed");
