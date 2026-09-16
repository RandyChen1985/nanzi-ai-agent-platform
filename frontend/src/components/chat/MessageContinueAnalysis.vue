<template>
  <div
    ref="chooserRoot"
    class="relative shrink-0"
    @mouseenter="cancelScheduledClose"
    @mouseleave="scheduleClose"
    @focusout="handleFocusOut"
    @keydown.esc="closeChooser"
  >
    <button
      type="button"
      class="flex h-7 shrink-0 items-center justify-center gap-1 rounded-md px-1.5 sm:px-2 text-[10px] font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
      :class="{ 'w-7': props.isMobile, 'w-7 sm:w-auto': !props.isMobile }"
      :aria-expanded="open"
      aria-haspopup="menu"
      aria-label="继续分析"
      @click="open = true"
    >
      <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
      </svg>
      <span class="hidden sm:inline whitespace-nowrap">继续分析</span>
      <svg class="hidden sm:inline h-3 w-3 transition-transform" :class="{ 'rotate-180': open }" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m6 9 6 6 6-6" />
      </svg>
    </button>

    <template v-if="open">
      <div v-if="props.isMobile" class="fixed inset-0 z-[260] flex items-end bg-black/35 backdrop-blur-[1px]" @click.self="closeChooser">
        <div class="w-full rounded-t-2xl border-t border-gray-100 bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-2xl dark:border-gray-800 dark:bg-gray-900">
          <div class="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 dark:bg-gray-600" />
          <div class="mb-2 flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100">继续分析</h3>
              <p class="mt-0.5 text-[11px] text-gray-400">将当前回答沉淀成可复用内容</p>
            </div>
            <button type="button" class="rounded-full p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800" aria-label="关闭继续分析" @click="closeChooser">
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18 18 6M6 6l12 12" /></svg>
            </button>
          </div>
          <div class="space-y-1">
            <button v-for="action in actions" :key="action.id" type="button" class="group w-full rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-gray-100 dark:hover:bg-gray-800" @click="selectAction(action)">
              <div class="text-sm font-semibold text-gray-700 group-hover:text-gray-900 dark:text-gray-200 dark:group-hover:text-gray-100">{{ action.label }}</div>
              <div class="mt-0.5 text-xs leading-relaxed text-gray-400">{{ action.description }}</div>
            </button>
          </div>
        </div>
      </div>

      <div v-else class="absolute bottom-full right-0 z-[80] mb-2 w-72 overflow-hidden rounded-xl border border-gray-200/80 bg-white/95 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-gray-700 dark:bg-gray-900/95" role="menu">
        <div class="flex items-center justify-between gap-3 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
          <div class="min-w-0">
            <div class="text-xs font-bold text-gray-800 dark:text-gray-100">继续分析</div>
            <div class="mt-0.5 truncate text-[10px] text-gray-400">选择一个通用的沉淀方向</div>
          </div>
          <button type="button" class="shrink-0 rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200" aria-label="关闭继续分析" @click="closeChooser">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="space-y-0.5 p-1.5">
          <button v-for="action in actions" :key="action.id" type="button" class="group flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-gray-100 focus:bg-gray-100 focus:outline-none dark:hover:bg-gray-800 dark:focus:bg-gray-800" role="menuitem" @click="selectAction(action)">
            <span class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-[11px] text-gray-500 group-hover:bg-gray-200 group-hover:text-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:group-hover:bg-gray-700 dark:group-hover:text-gray-200">{{ actionIcon(action.id) }}</span>
            <span class="min-w-0">
              <span class="block text-xs font-semibold text-gray-700 group-hover:text-gray-900 dark:text-gray-200 dark:group-hover:text-gray-100">{{ action.label }}</span>
              <span class="mt-0.5 block truncate text-[10px] text-gray-400">{{ action.description }}</span>
            </span>
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

type ContinueAction = {
  id: string;
  label: string;
  description: string;
  query: string;
};

const props = defineProps<{ isMobile: boolean }>();
const emit = defineEmits<{ (event: "select", query: string): void }>();
const chooserRoot = ref<HTMLElement | null>(null);
const open = ref(false);
let closeTimer: ReturnType<typeof setTimeout> | null = null;

const actions: ContinueAction[] = [
  {
    id: "visual_report",
    label: "生成可视化分析报告",
    description: "基于当前回复中的数据生成图表和结构化结论",
    query: "请基于当前这条 AI 回复中已经存在的文本、Markdown 表格和可核验数据，生成一份结构化可视化分析报告。请先说明数据范围、数据来源、数据完整性和缺失项，再提炼关键发现、趋势、对比、异常和可能原因。适合可视化的数据请使用合法的 ```chart ECharts 代码块输出，并在图表后解释图表结论。图表数据必须完全来自当前回复，不得编造数据；如果数据不足以生成可靠图表，请不要强行出图，并明确说明原因。最后输出结论、风险和建议。报告应包含：分析概览、数据说明、关键发现、可视化图表、图表解读、结论与建议。",
  },
  {
    id: "save_markdown",
    label: "保存为 Markdown",
    description: "保存到工作区 docs 目录并返回实际路径",
    query: "请将刚才这条 AI 回复完整保存为 Markdown 文档，文件名根据内容自动生成，保存到当前工作区的 docs 目录下。保存完成后请返回实际写入的完整路径，并确认文件已经成功保存。不要只给我建议路径或示例路径。",
  },
  {
    id: "save_word",
    label: "保存为 Word",
    description: "生成 .docx 文件并返回下载地址",
    query: "请将刚才这条 AI 回复整理并保存为 Word 文档（.docx），文件名根据内容自动生成，优先使用当前工作区的 docs 目录。请调用可用的 Word 文档工具实际生成文件；完成后原样返回工具提供的实际下载地址或打开地址，并确认文件已经成功生成，不要虚构物理路径。",
  },
  {
    id: "create_skill",
    label: "提炼生成 Skill",
    description: "按 create_skills 工具规范生成个人 Skill",
    query: "请根据刚才这条 AI 回复的内容，直接调用 create_skills 工具提炼并创建一个个人 Skill。请按照工具要求生成合法的 skill_id、名称、用途描述和完整的 SKILL.md 内容：文件必须以包含 name 和 description 的 YAML Frontmatter 开始，后续使用清晰、可执行的 imperative 指令。scope 使用 personal。不要只输出 Skill 草稿，必须实际调用工具创建。创建成功后请返回 Skill 名称、skill_id、作用域和工具返回的完整物理路径，并说明如何使用。",
  },
];

const cancelScheduledClose = () => {
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = null;
};
const closeChooser = () => {
  cancelScheduledClose();
  open.value = false;
};
const scheduleClose = () => {
  if (props.isMobile) return;
  cancelScheduledClose();
  closeTimer = setTimeout(closeChooser, 180);
};
const handleFocusOut = (event: FocusEvent) => {
  const next = event.relatedTarget as Node | null;
  if (!next || !chooserRoot.value?.contains(next)) scheduleClose();
};
const handleDocumentPointerDown = (event: PointerEvent) => {
  if (open.value && !chooserRoot.value?.contains(event.target as Node)) closeChooser();
};
const actionIcon = (id: string) => ({ visual_report: "▥", save_markdown: "M", save_word: "W", create_skill: "✦" }[id] || "→");
const selectAction = (action: ContinueAction) => {
  closeChooser();
  emit("select", action.query);
};

onMounted(() => document.addEventListener("pointerdown", handleDocumentPointerDown));
onUnmounted(() => {
  cancelScheduledClose();
  document.removeEventListener("pointerdown", handleDocumentPointerDown);
});
</script>
