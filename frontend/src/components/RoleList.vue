<template>
  <div class="flex flex-wrap gap-1 mt-0.5 items-center">
    <!-- Display first N roles -->
    <span
      v-for="(role, idx) in visibleRoles"
      :key="idx"
      class="bg-gray-50 text-gray-600 px-2 py-0.5 text-[11px] rounded-full border border-gray-200 whitespace-nowrap shadow-sm hover:shadow transition-shadow cursor-default"
    >
      {{ role }}
    </span>

    <!-- +N Badge with Popover -->
    <div
      v-if="remainingCount > 0"
      ref="triggerRef"
      class="relative group"
      @mouseenter="showPopover = true"
      @mouseleave="showPopover = false"
    >
      <span
        class="bg-primary/5 text-primary px-1.5 py-0.5 text-[10px] rounded-full border border-primary/10 font-bold cursor-help hover:bg-primary/10 transition-colors"
      >
        +{{ remainingCount }}
      </span>

      <!-- Teleported Popover -->
      <Teleport to="body">
        <div
          v-if="showPopover"
          ref="popoverRef"
          :style="popoverStyle"
          class="fixed z-[9999] bg-white border border-gray-200 shadow-xl rounded-lg p-3 min-w-[120px] max-w-[240px] animate-fade-in pointer-events-none"
        >
          <div
            class="text-[10px] uppercase font-bold text-gray-400 mb-2 border-b border-gray-100 pb-1"
          >
            全部角色 ({{ roles.length }})
          </div>
          <div class="flex flex-wrap gap-1.5">
            <span
              v-for="(role, idx) in roles"
              :key="idx"
              class="text-xs text-gray-700 bg-gray-50 px-1.5 py-0.5 rounded border border-gray-100"
            >
              {{ role }}
            </span>
          </div>
        </div>
      </Teleport>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";

const props = defineProps({
  roles: {
    type: Array as () => string[],
    default: () => [],
  },
  limit: {
    type: Number,
    default: 2,
  },
});

const showPopover = ref(false);
const popoverPosition = ref({ top: 0, left: 0 });

const visibleRoles = computed(() => props.roles.slice(0, props.limit));
const remainingCount = computed(() =>
  Math.max(0, props.roles.length - props.limit),
);

const popoverStyle = computed(() => {
  return {
    top: `${popoverPosition.value.top}px`,
    left: `${popoverPosition.value.left}px`,
    transform: "translate(-50%, -100%)",
  };
});

const triggerRef = ref<HTMLElement | null>(null)

// Update position when showing
watch(showPopover, async (val) => {
  if (val && triggerRef.value) {
    const rect = triggerRef.value.getBoundingClientRect()
    popoverPosition.value = {
      top: rect.top - 8,
      left: rect.left + rect.width / 2
    }
  }
})
</script>

<script lang="ts">
// Need this block to fix component name if necessary or keep it simple.
// Redefining the logic to be more robust using a ref on the trigger element.
</script>

<style scoped>

</style>
