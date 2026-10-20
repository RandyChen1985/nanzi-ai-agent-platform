<template>
  <component :is="icon" class="flex-shrink-0" aria-hidden="true" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Bars3Icon } from '@heroicons/vue/24/outline'
import { MENU_ICON_COMPONENTS } from '../../constants/menuIcons'

/**
 * 侧栏图标。
 *
 * 改造前这些 SVG 是 `Dashboard.vue` 模板里 20 多个 `v-else-if="item.icon === 'xxx'"`，
 * 菜单入库后图标只剩一个 key，因此按 key 查组件渲染。图标目录见
 * `constants/menuIcons.ts`（与后端 `menu_seed.ALLOWED_ICON_KEYS` 对账）。
 *
 * 尺寸**只由调用方给**（`h-4 w-4` / `h-[18px] w-[18px]`）：这里再写一个尺寸会和调用方的
 * 工具类打架（同为 0,1,0 优先级，谁生效取决于 Tailwind 产物顺序）。
 * 未知 key 渲染成 ≡ 而不是空白，便于一眼看出"这个菜单的图标配错了"。
 */
const props = withDefaults(defineProps<{ name?: string | null }>(), { name: null })

const icon = computed(() => MENU_ICON_COMPONENTS[(props.name || '').trim()] || Bars3Icon)
</script>
