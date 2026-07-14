<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

const open = ref(false);
const pinned = ref(false);
const darkMode = ref(false);
const trigger = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const position = ref({ left: 12, top: 12, width: 256 });

const updatePosition = () => {
  if (!trigger.value || !panel.value) return;

  const gap = 8;
  const viewportPadding = 12;
  const triggerRect = trigger.value.getBoundingClientRect();
  const width = Math.min(256, window.innerWidth - viewportPadding * 2);
  const panelHeight = panel.value.offsetHeight;
  const fitsAbove = triggerRect.top >= panelHeight + gap + viewportPadding;
  const top = fitsAbove
    ? triggerRect.top - panelHeight - gap
    : triggerRect.bottom + gap;
  const preferredLeft = triggerRect.left + triggerRect.width / 2 - width / 2;
  const left = Math.min(
    Math.max(viewportPadding, preferredLeft),
    window.innerWidth - width - viewportPadding,
  );

  position.value = { left, top, width };
};

const show = () => {
  darkMode.value = Boolean(trigger.value?.closest(".dark"));
  open.value = true;
  void nextTick(updatePosition);
};

const close = () => {
  open.value = false;
  pinned.value = false;
};

const toggle = () => {
  if (open.value && pinned.value) {
    close();
    return;
  }

  pinned.value = true;
  show();
};

const handleMouseLeave = () => {
  if (!pinned.value) close();
};

const handlePointerDown = (event: PointerEvent) => {
  const target = event.target as Node | null;
  if (!target || trigger.value?.contains(target) || panel.value?.contains(target)) return;
  close();
};

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === "Escape") close();
};

const handleViewportChange = () => {
  if (open.value) updatePosition();
};

onMounted(() => {
  document.addEventListener("pointerdown", handlePointerDown);
  document.addEventListener("keydown", handleKeydown);
  window.addEventListener("resize", handleViewportChange);
  window.addEventListener("scroll", handleViewportChange, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handlePointerDown);
  document.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("resize", handleViewportChange);
  window.removeEventListener("scroll", handleViewportChange, true);
});
</script>

<template>
  <span
    ref="trigger"
    class="inline-flex"
    @mouseenter="show"
    @mouseleave="handleMouseLeave"
  >
    <button
      type="button"
      class="flex h-4 w-4 items-center justify-center rounded-full border border-gray-300 text-[10px] font-bold leading-none text-gray-400 transition-colors hover:border-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/30 dark:border-gray-600 dark:text-gray-400"
      aria-label="查看反幻觉校验示例"
      aria-haspopup="true"
      :aria-expanded="open"
      @click.stop="toggle"
      @focus="show"
    >
      ?
    </button>
  </span>

  <Teleport to="body">
    <Transition name="grounding-help-fade">
      <div v-if="open" :class="{ dark: darkMode }">
        <div
          ref="panel"
          role="tooltip"
          class="fixed z-[200] rounded-xl border border-gray-200 bg-white p-3 text-left shadow-xl dark:border-gray-700 dark:bg-gray-800"
          :style="{
            left: `${position.left}px`,
            top: `${position.top}px`,
            width: `${position.width}px`,
          }"
          @click.stop
        >
          <div class="mb-2 text-xs font-semibold text-gray-800 dark:text-gray-100">
            反幻觉校验示例
          </div>
          <div class="mb-2 rounded-lg bg-gray-50 px-2.5 py-2 dark:bg-gray-700/60">
            <div class="text-[10px] text-gray-400">示例问题</div>
            <div class="mt-0.5 text-xs font-medium text-gray-700 dark:text-gray-200">
              查询当前销售额
            </div>
          </div>
          <div class="space-y-1.5 text-[11px] leading-4">
            <div class="rounded-lg border border-gray-100 px-2.5 py-2 dark:border-gray-700">
              <div class="font-semibold text-gray-700 dark:text-gray-200">关闭</div>
              <div class="text-gray-500 dark:text-gray-400">
                直接展示模型回答，不校验事实来源。
              </div>
            </div>
            <div class="rounded-lg border border-emerald-100 bg-emerald-50/60 px-2.5 py-2 dark:border-emerald-900/50 dark:bg-emerald-950/20">
              <div class="font-semibold text-emerald-700 dark:text-emerald-300">
                开启且证据匹配
              </div>
              <div class="text-gray-500 dark:text-gray-400">
                工具或知识库结果能够支持回答，正常展示结果。
              </div>
            </div>
            <div class="rounded-lg border border-amber-100 bg-amber-50/70 px-2.5 py-2 dark:border-amber-900/50 dark:bg-amber-950/20">
              <div class="font-semibold text-amber-700 dark:text-amber-300">
                开启但证据不足
              </div>
              <div class="text-gray-500 dark:text-gray-400">
                保留回答，并在消息末尾追加“风险提示”，不会阻断输出。
              </div>
            </div>
          </div>
          <p class="mt-2 text-[10px] leading-4 text-gray-400">
            开启可降低无依据回答风险，但不代表结果绝对正确。
          </p>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.grounding-help-fade-enter-active,
.grounding-help-fade-leave-active {
  transition: opacity 0.15s ease;
}

.grounding-help-fade-enter-from,
.grounding-help-fade-leave-to {
  opacity: 0;
}
</style>
