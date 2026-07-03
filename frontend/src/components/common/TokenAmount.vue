<template>
  <div :class="wrapperClass">
    <component :is="inline ? 'span' : 'p'" :class="mainClass">
      {{ displayText }}
    </component>
    <p
      v-if="!inline && showHint"
      class="text-[10px] text-gray-400 tabular-nums mt-0.5"
    >
      {{ formatTokenFull(value) }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  formatTokenCompact,
  formatTokenFull,
  shouldShowTokenFullHint,
} from '@/utils/tokenFormat'

const props = withDefaults(
  defineProps<{
    value: number | null | undefined
    unlimitedLabel?: string
    isUnlimited?: boolean
    inline?: boolean
    mainClass?: string
    wrapperClass?: string
    showFullHint?: boolean
  }>(),
  {
    unlimitedLabel: '不限',
    isUnlimited: false,
    inline: false,
    mainClass: '',
    wrapperClass: '',
    showFullHint: true,
  },
)

const displayText = computed(() => {
  if (props.isUnlimited || props.value == null) return props.unlimitedLabel
  return formatTokenCompact(props.value)
})

const showHint = computed(
  () => props.showFullHint && !props.isUnlimited && shouldShowTokenFullHint(props.value),
)
</script>
