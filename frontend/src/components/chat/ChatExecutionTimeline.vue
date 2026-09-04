<template>
  <div
    v-if="visible"
    class="mb-1 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem]"
  >
    <ChatThinkingHeader
      v-model:expanded="expanded"
      :is-thinking="!hasAnswer && (isThinking || hasPending)"
      :title="headerTitle"
      :step-count="countTimelineSteps(items)"
      :skill-summary="headerSkillSummary"
      :current-step="currentStep"
      :duration="duration"
      :bordered="bordered"
      :dark-mode="darkMode"
      :show-copy="Boolean(fullTimelineText)"
      :is-copied="copiedKey === 'full-timeline'"
      @copy="handleCopyAll"
    />

    <transition
      enter-active-class="transition-opacity duration-200 ease-out"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition-opacity duration-150 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-show="expanded"
        class="mt-0.5 space-y-0.5 px-1 py-0.5"
      >
        <div
          v-if="skillBadges.length"
          class="flex flex-wrap items-center gap-1 rounded-md border border-purple-100/70 bg-purple-50/70 px-1.5 py-1 text-[11px] font-semibold text-purple-700 dark:border-purple-900/30 dark:bg-purple-950/20 dark:text-purple-300"
        >
          <SparklesIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{{ skillNoticeLabel }}</span>
          <span
            v-for="skill in skillBadges"
            :key="skill.key"
            class="rounded-full border border-purple-200/70 bg-purple-100 px-1.5 py-0.5 text-[10px] font-bold dark:border-purple-800/40 dark:bg-purple-900/40"
            :title="skill.description"
          >
            {{ skill.label }}
          </span>
        </div>
        <div
          v-for="item in items"
          :key="item.id"
          class="relative"
        >
          <div v-if="item.kind === 'text' && (hasVisibleTimelineText(item.content) || item.children?.length)" class="rounded-md px-1 py-0.5 text-[12px] leading-5"
            :class="item.pending ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500'"
          >
            <div class="flex gap-2">
              <span v-if="item.pending" class="thought-status-dot mt-1 shrink-0" aria-label="进行中" title="进行中" />
              <SparklesIcon v-if="item.textKind !== 'reasoning'" class="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span
                v-if="item.sourceLabel && item.textKind !== 'reasoning'"
                class="mt-0.5 shrink-0 text-[10px] font-semibold text-gray-400 dark:text-gray-500"
              >{{ item.sourceLabel }}</span>
              <div class="min-w-0 flex-1">
                <button
                  v-if="item.textKind === 'reasoning'"
                  type="button"
                  class="mb-0.5 flex w-full items-center gap-2 text-left text-[10px] hover:text-gray-600 dark:hover:text-gray-300"
                  :aria-expanded="isReasoningBodyOpen(item)"
                  @click="item.contentExpanded = isReasoningBodyOpen(item) ? false : true"
                >
                  <CpuChipIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span class="min-w-0 flex-1">深度思考</span>
                  <span v-if="formatDuration(item.execution_time_ms)" class="shrink-0 font-mono text-[10px] text-gray-400">{{ formatDuration(item.execution_time_ms) }}</span>
                  <svg class="h-3 w-3 shrink-0 transition-transform" :class="{ 'rotate-180': isReasoningBodyOpen(item) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                  </svg>
                </button>
                <blockquote
                  v-if="item.textKind === 'reasoning'"
                  v-show="isReasoningBodyOpen(item)"
                  class="group/details relative mb-0 mt-0.5 border-l-2 border-gray-200 pl-2.5 dark:border-gray-700"
                >
                  <button
                    v-if="hasVisibleTimelineText(item.content)"
                    type="button"
                    class="absolute right-1 top-0 z-10 flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200 group-hover/details:opacity-100"
                    :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === `reasoning-${item.id}` }"
                    :title="copiedKey === `reasoning-${item.id}` ? '已复制' : '复制思考内容'"
                    @click.stop="handleCopy(`reasoning-${item.id}`, visibleTimelineText(item.content))"
                  >
                    <svg v-if="copiedKey === `reasoning-${item.id}`" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
                    </svg>
                    <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
                    </svg>
                  </button>
                  <pre class="w-fit max-w-full whitespace-pre-wrap break-words pr-6 font-sans">{{ visibleTimelineText(item.content) }}<span v-if="item.pending" class="ml-0.5 animate-pulse">▌</span></pre>
                </blockquote>
                <pre
                  v-else
                  class="w-fit max-w-full whitespace-pre-wrap break-words font-sans"
                >{{ visibleTimelineText(item.content) }}<span v-if="item.pending" class="ml-0.5 animate-pulse">▌</span></pre>
              </div>
            </div>
            <div v-if="item.children?.length" class="ml-5 mt-0.5 border-l border-gray-200/80 pl-1.5 dark:border-gray-700/80">
              <button
                type="button"
                class="mb-0 flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                :aria-expanded="item.childrenExpanded !== false"
                @click="item.childrenExpanded = item.childrenExpanded === false"
              >
                <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-180': item.childrenExpanded !== false }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                </svg>
                <span>{{ item.children.length }} 个工具调用</span>
              </button>
              <div v-show="item.childrenExpanded !== false" class="space-y-0">
                <div
                  v-for="child in item.children"
                  :key="child.id"
                  class="rounded-md px-1 py-0.5 text-[11px] leading-5 transition-colors"
                  :class="{
                    'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': child.status === 'error',
                    'text-gray-600 dark:text-gray-300': child.status === 'pending',
                    'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': child.status !== 'pending' && child.status !== 'error',
                  }"
                >
                  <button
                    type="button"
                    class="flex w-full items-center gap-2 text-left"
                    :aria-expanded="child.children?.length ? child.childrenExpanded !== false : child.isExpanded === true"
                    @click="child.children?.length ? (child.childrenExpanded = child.childrenExpanded === false) : (hasVisibleTimelineText(child.details) ? child.isExpanded = !child.isExpanded : undefined)"
                  >
                    <span v-if="child.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                    <WrenchScrewdriverIcon
                      v-if="isToolTimelineItem(child)"
                      class="h-3.5 w-3.5 shrink-0"
                      aria-hidden="true"
                    />
                    <component v-else :is="timelineIconFor(child)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(child)">
                      <span
                        v-if="child.subagent && !child.children?.length"
                        :title="formatSubagentTraceSummary(child.subagent)"
                      >子代理 · </span>
                      <span>{{ displayTimelineTitle(child) }}</span>
                    </span>
                    <span
                      v-if="child.subagent && subagentStatusLabel(child.status)"
                      class="shrink-0 text-[10px]"
                      :class="child.status === 'error' ? 'text-red-600' : child.status === 'pending' ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-400'"
                    >
                      {{ subagentStatusLabel(child.status) }}
                    </span>
                    <span v-if="child.status === 'error' && !child.subagent" class="shrink-0 text-[10px]">失败</span>
                    <span v-if="formatTimelineDuration(child)" class="shrink-0 font-mono text-[10px] text-gray-400" :title="timelineDurationTitle(child)">{{ formatTimelineDuration(child) }}</span>
                    <svg v-if="hasVisibleTimelineText(child.details) || child.children?.length" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': child.children?.length ? (child.childrenExpanded !== false) : child.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                    </svg>
                  </button>
                  <div v-if="child.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    错误原因：{{ child.error_reason }}
                  </div>
                  <div v-if="fileMetadataSummary(child.file_metadata)" class="ml-5 truncate text-[10px] text-gray-400 dark:text-gray-500">
                    {{ fileMetadataSummary(child.file_metadata) }}
                  </div>
                  <div v-if="hasVisibleTimelineText(child.details) && child.isExpanded && !child.children?.length" class="group/details relative mt-1 border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
                    <button
                      type="button"
                      class="absolute right-1 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200 group-hover/details:opacity-100"
                      :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === `child-${child.id}` }"
                      :title="copiedKey === `child-${child.id}` ? '已复制' : '复制内容'"
                      @click.stop="handleCopy(`child-${child.id}`, visibleTimelineText(child.details))"
                    >
                      <svg v-if="copiedKey === `child-${child.id}`" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
                      </svg>
                      <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
                      </svg>
                    </button>
                    <pre class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(child.details) }}</pre>
                  </div>

                  <!-- 嵌套展示子代理内部步骤 -->
                  <div v-if="child.children?.length && child.childrenExpanded !== false" class="ml-4 mt-0.5 space-y-0 border-l border-indigo-200/70 pl-2 dark:border-indigo-800/50">
                    <div
                      v-for="subStep in child.children"
                      :key="subStep.id"
                      class="rounded-md px-1 py-0.5 text-[11px] leading-5 transition-colors"
                      :class="{
                        'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': subStep.status === 'error',
                        'text-gray-600 dark:text-gray-300': subStep.status === 'pending',
                        'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': subStep.status !== 'pending' && subStep.status !== 'error',
                      }"
                    >
                      <button
                        type="button"
                        class="flex w-full items-center gap-2 text-left"
                        :aria-expanded="subStep.isExpanded === true"
                        @click="subStep.details ? subStep.isExpanded = !subStep.isExpanded : undefined"
                      >
                        <span v-if="subStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                        <WrenchScrewdriverIcon
                          v-if="isToolTimelineItem(subStep)"
                          class="h-3.5 w-3.5 shrink-0"
                          aria-hidden="true"
                        />
                        <component v-else :is="timelineIconFor(subStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(subStep)">{{ displayTimelineTitle(subStep) }}</span>
                        <span v-if="subStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                        <span v-if="formatTimelineDuration(subStep)" class="shrink-0 font-mono text-[10px] text-gray-400" :title="timelineDurationTitle(subStep)">{{ formatTimelineDuration(subStep) }}</span>
                        <svg v-if="hasVisibleTimelineText(subStep.details)" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': subStep.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                        </svg>
                      </button>
                      <div v-if="subStep.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                        错误原因：{{ subStep.error_reason }}
                      </div>
                      <div v-if="hasVisibleTimelineText(subStep.details) && subStep.isExpanded" class="group/details relative mt-1 border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
                        <button
                          type="button"
                          class="absolute right-1 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200 group-hover/details:opacity-100"
                          :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === `substep-${subStep.id}` }"
                          :title="copiedKey === `substep-${subStep.id}` ? '已复制' : '复制内容'"
                          @click.stop="handleCopy(`substep-${subStep.id}`, visibleTimelineText(subStep.details))"
                        >
                          <svg v-if="copiedKey === `substep-${subStep.id}`" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
                          </svg>
                          <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
                          </svg>
                        </button>
                        <pre class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(subStep.details) }}</pre>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div
            v-else-if="item.kind === 'log'"
            class="rounded-lg px-1 py-0.5 text-[11px] leading-5 transition-colors"
            :class="{
              'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': item.status === 'error',
              'text-gray-600 dark:text-gray-300': item.status === 'pending',
              'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': item.status !== 'pending' && item.status !== 'error',
            }"
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 text-left"
              :aria-expanded="item.children?.length ? isTimelineItemExpanded(item) : item.isExpanded === true"
              @click="item.children?.length ? toggleTimelineItem(item) : (hasVisibleTimelineText(item.details) ? item.isExpanded = !item.isExpanded : undefined)"
            >
              <span v-if="item.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
              <WrenchScrewdriverIcon
                v-if="isToolTimelineItem(item)"
                class="h-3.5 w-3.5 shrink-0"
                aria-hidden="true"
              />
              <component v-else :is="timelineIconFor(item)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(item)">
                <span
                  v-if="item.subagent && !item.children?.length"
                  :title="formatSubagentTraceSummary(item.subagent)"
                >子代理 · </span>
                <span>{{ displayTimelineTitle(item) }}</span>
              </span>
              <span
                v-if="isPreparationParent(item)"
                class="shrink-0 text-[10px] font-medium"
                :class="item.status === 'error' ? 'text-red-600 dark:text-red-400' : item.status === 'pending' ? 'text-sky-600 dark:text-sky-400' : 'text-gray-400 dark:text-gray-500'"
              >
                {{ preparationStatusLabel(item) }}
              </span>
              <span
                v-if="isPreparationParent(item) && item.children?.length"
                class="shrink-0 text-[10px] text-gray-400 dark:text-gray-500"
              >
                {{ item.children.length }} 项准备
              </span>
              <span
                v-if="item.subagent && subagentStatusLabel(item.status)"
                class="shrink-0 text-[10px]"
                :class="item.status === 'error' ? 'text-red-600' : item.status === 'pending' ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-400'"
              >
                {{ subagentStatusLabel(item.status) }}
              </span>
              <span v-if="item.status === 'error' && !item.subagent" class="shrink-0 text-[10px]">失败</span>
              <span v-if="formatTimelineDuration(item)" class="shrink-0 font-mono text-[10px] text-gray-400" :title="timelineDurationTitle(item)">
                {{ formatTimelineDuration(item) }}
              </span>
              <svg
                v-if="hasVisibleTimelineText(item.details) || item.children?.length"
                class="h-3 w-3 shrink-0 text-gray-400 transition-transform"
                :class="{ 'rotate-180': item.children?.length ? isTimelineItemExpanded(item) : item.isExpanded }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
              </svg>
            </button>
            <div v-if="item.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
              错误原因：{{ item.error_reason }}
            </div>
            <div v-if="fileMetadataSummary(item.file_metadata)" class="ml-5 truncate text-[10px] text-gray-400 dark:text-gray-500">
              {{ fileMetadataSummary(item.file_metadata) }}
            </div>
            <div v-if="hasVisibleTimelineText(item.details) && item.isExpanded && !item.children?.length" class="group/details relative mt-1 border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
              <button
                type="button"
                class="absolute right-1 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200 group-hover/details:opacity-100"
                :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === `item-${item.id}` }"
                :title="copiedKey === `item-${item.id}` ? '已复制' : '复制内容'"
                @click.stop="handleCopy(`item-${item.id}`, visibleTimelineText(item.details))"
              >
                <svg v-if="copiedKey === `item-${item.id}`" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
                </svg>
                <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
                </svg>
              </button>
              <pre class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(item.details) }}</pre>
            </div>

            <!-- 嵌套展示根级别子代理内部步骤 -->
            <div v-if="item.children?.length && item.childrenExpanded !== false" class="ml-4 mt-0.5 space-y-0 border-l border-indigo-200/70 pl-2 dark:border-indigo-800/50">
              <div
                v-for="subStep in item.children"
                :key="subStep.id"
                class="rounded-md px-1 py-0.5 text-[11px] leading-5 transition-colors"
                :class="{
                  'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': subStep.status === 'error',
                  'text-gray-600 dark:text-gray-300': subStep.status === 'pending',
                  'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': subStep.status !== 'pending' && subStep.status !== 'error',
                }"
              >
                <button
                  type="button"
                  class="flex w-full items-center gap-2 text-left"
                  :aria-expanded="subStep.children?.length ? subStep.childrenExpanded !== false : subStep.isExpanded === true"
                  @click="subStep.children?.length ? (subStep.childrenExpanded = subStep.childrenExpanded === false) : (hasVisibleTimelineText(subStep.details) ? subStep.isExpanded = !subStep.isExpanded : undefined)"
                >
                  <span v-if="subStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                  <WrenchScrewdriverIcon
                    v-if="isToolTimelineItem(subStep)"
                    class="h-3.5 w-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  <component v-else :is="timelineIconFor(subStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(subStep)">{{ displayTimelineTitle(subStep) }}</span>
                  <span v-if="subStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                  <span v-if="formatTimelineDuration(subStep)" class="shrink-0 font-mono text-[10px] text-gray-400" :title="timelineDurationTitle(subStep)">{{ formatTimelineDuration(subStep) }}</span>
                  <svg v-if="hasVisibleTimelineText(subStep.details) || subStep.children?.length" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': subStep.children?.length ? (subStep.childrenExpanded !== false) : subStep.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                  </svg>
                </button>
                <div v-if="hasVisibleTimelineText(subStep.details) && subStep.isExpanded && !subStep.children?.length" class="group/details relative mt-1 border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
                  <button
                    type="button"
                    class="absolute right-1 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-60 transition-all hover:bg-gray-200/70 hover:text-gray-700 hover:opacity-100 dark:hover:bg-gray-700/70 dark:hover:text-gray-200 group-hover/details:opacity-100"
                    :class="{ 'text-emerald-500 hover:text-emerald-600 dark:text-emerald-400': copiedKey === `substep-${subStep.id}` }"
                    :title="copiedKey === `substep-${subStep.id}` ? '已复制' : '复制内容'"
                    @click.stop="handleCopy(`substep-${subStep.id}`, visibleTimelineText(subStep.details))"
                  >
                    <svg v-if="copiedKey === `substep-${subStep.id}`" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
                    </svg>
                    <svg v-else class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2z" />
                    </svg>
                  </button>
                  <pre class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(subStep.details) }}</pre>
                </div>
                <div v-if="subStep.children?.length && subStep.childrenExpanded !== false" class="ml-4 mt-0.5 space-y-0 border-l border-indigo-200/70 pl-2 dark:border-indigo-800/50">
                  <div
                    v-for="nestedStep in subStep.children"
                    :key="nestedStep.id"
                    class="rounded-md px-1 py-0.5 text-[11px] leading-5 transition-colors"
                    :class="{
                      'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': nestedStep.status === 'error',
                      'text-gray-600 dark:text-gray-300': nestedStep.status === 'pending',
                      'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': nestedStep.status !== 'pending' && nestedStep.status !== 'error',
                    }"
                  >
                    <button
                      type="button"
                      class="flex w-full items-center gap-2 text-left"
                      :aria-expanded="nestedStep.isExpanded === true"
                      @click="hasVisibleTimelineText(nestedStep.details) ? nestedStep.isExpanded = !nestedStep.isExpanded : undefined"
                    >
                      <span v-if="nestedStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                      <WrenchScrewdriverIcon
                        v-if="isToolTimelineItem(nestedStep)"
                        class="h-3.5 w-3.5 shrink-0"
                        aria-hidden="true"
                      />
                      <component v-else :is="timelineIconFor(nestedStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(nestedStep)">{{ displayTimelineTitle(nestedStep) }}</span>
                      <span v-if="nestedStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                      <span v-if="formatTimelineDuration(nestedStep)" class="shrink-0 font-mono text-[10px] text-gray-400" :title="timelineDurationTitle(nestedStep)">{{ formatTimelineDuration(nestedStep) }}</span>
                      <svg v-if="hasVisibleTimelineText(nestedStep.details)" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': nestedStep.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                      </svg>
                    </button>
                    <div v-if="nestedStep.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                      错误原因：{{ nestedStep.error_reason }}
                    </div>
                    <pre v-if="hasVisibleTimelineText(nestedStep.details) && nestedStep.isExpanded" class="mt-1 whitespace-pre-wrap break-words border-t border-gray-200/70 pt-1 pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:border-gray-700/70 dark:text-gray-400">{{ visibleTimelineText(nestedStep.details) }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="isThinking && !items.length" class="px-2 py-1 text-xs text-gray-400 animate-pulse">
          {{ thinkingText || '正在处理…' }}
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import ChatThinkingHeader from "@/components/chat/ChatThinkingHeader.vue";
import {
  BookOpenIcon,
  ChatBubbleLeftRightIcon,
  ClipboardDocumentListIcon,
  ClockIcon,
  Cog6ToothIcon,
  CpuChipIcon,
  ExclamationTriangleIcon,
  FolderOpenIcon,
  LockClosedIcon,
  MapIcon,
  PuzzlePieceIcon,
  ShieldCheckIcon,
  SparklesIcon,
  UserGroupIcon,
  WrenchScrewdriverIcon,
} from "@heroicons/vue/24/outline";
import { copyToClipboard } from "@/utils/clipboard";
import { stripInternalContextBlocks } from "@/utils/streamContentSanitize";
import {
  skillFlowNoticeLabel,
  summarizeSkillFlowBadges,
  type SkillFlowBadge,
} from "@/utils/skillFlowBadges";
import {
  buildLegacyProcessTimeline,
  countTimelineSteps,
  formatTimelineTitle,
  groupRouteTimelineItems,
  isReasoningContentExpanded,
  mergeTimelineLogs,
  resolveTimelineCurrentStep,
  timelineHasPending,
  PREPARATION_TIMELINE_PARENT_ID,
  type FileToolMetadata,
  type ProcessTimelineItem,
  type ProcessTimelineLogItem,
  type ProcessTimelineTextItem,
} from "@/utils/processTimeline";
import {
  formatSubagentTraceSummary,
} from "@/utils/subagentTrace";

const props = withDefaults(defineProps<{
  timeline?: ProcessTimelineItem[];
  logs?: Array<{
    id: string | number;
    parent_id?: string | number;
    title: string;
    details: string;
    status: "pending" | "success" | "error" | "warning";
    error_reason?: string;
    category?: string;
    execution_time_ms?: number | null;
    started_at?: number | null;
    file_metadata?: FileToolMetadata;
    subagent?: ProcessTimelineLogItem["subagent"];
  }>;
  reasoningContent?: string;
  processNarration?: string;
  processNarrationPending?: string;
  isThinking?: boolean;
  hasAnswer?: boolean;
  thinkingText?: string;
  duration?: string;
  skillSummary?: string;
  skillBadges?: SkillFlowBadge[];
  bordered?: boolean;
  darkMode?: boolean;
  /** 权限卡已承载确认详情时，隐藏其上方重复的权限时间线行。 */
  suppressPermissionLogs?: boolean;
}>(), {
  timeline: () => [],
  logs: () => [],
  reasoningContent: "",
  processNarration: "",
  processNarrationPending: "",
  isThinking: false,
  hasAnswer: false,
  thinkingText: "",
  duration: "",
  skillSummary: "",
  skillBadges: () => [],
  bordered: false,
  darkMode: false,
  suppressPermissionLogs: false,
});

const expanded = defineModel<boolean>("expanded", { default: false });
const routeGroupExpanded = ref(true);

function suppressPermissionLogs(items: ProcessTimelineLogItem[]): ProcessTimelineLogItem[];
function suppressPermissionLogs(items: ProcessTimelineItem[]): ProcessTimelineItem[];
function suppressPermissionLogs(items: ProcessTimelineItem[]): ProcessTimelineItem[] {
  return items.flatMap((item): ProcessTimelineItem[] => {
    if (item.kind === "log") {
      if (item.category === "permission") return [];
      return item.children?.length
        ? [{ ...item, children: suppressPermissionLogs(item.children) }]
        : [item];
    }
    if (item.kind === "text" && item.children?.length) {
      return [{ ...item, children: suppressPermissionLogs(item.children) }];
    }
    return [item];
  });
}

const timelineItems = computed(() => {
  const merged = props.timeline.length
    ? mergeTimelineLogs(props.timeline, props.logs)
    : buildLegacyProcessTimeline(props);
  const visibleItems = props.suppressPermissionLogs ? suppressPermissionLogs(merged) : merged;
  return visibleItems.filter((item) => item.kind !== "todo");
});
const items = computed(() => groupRouteTimelineItems(timelineItems.value, routeGroupExpanded.value));

const hasPending = computed(() => timelineHasPending(items.value));
const headerTitle = computed(() => props.hasAnswer ? "执行完成" : "执行过程");
const currentStep = computed(() => resolveTimelineCurrentStep(
  items.value,
  Boolean(!props.hasAnswer && (props.isThinking || hasPending.value)),
) || (!props.hasAnswer && props.isThinking ? visibleTimelineText(props.thinkingText) : ""));

const visible = computed(() => Boolean(props.isThinking || items.value.length || props.skillBadges.length));
const skillNoticeLabel = computed(() => skillFlowNoticeLabel(props.skillBadges));
const headerSkillSummary = computed(() =>
  props.skillSummary || summarizeSkillFlowBadges(props.skillBadges)
);

function visibleTimelineText(text?: string | null): string {
  return stripInternalContextBlocks(text ?? "");
}

function hasVisibleTimelineText(text?: string | null): boolean {
  return Boolean(visibleTimelineText(text).trim());
}

watch(hasPending, (pending) => {
  if (pending && !props.hasAnswer) expanded.value = true;
}, { immediate: true });

watch(() => props.hasAnswer, (answer) => {
  if (answer) expanded.value = false;
}, { immediate: true });

function isReasoningBodyOpen(item: ProcessTimelineTextItem): boolean {
  return isReasoningContentExpanded(item);
}

function isRouteGroup(item: ProcessTimelineLogItem): boolean {
  return String(item.id) === "route:target_config" && Boolean(item.children?.length);
}

function isPreparationParent(item: ProcessTimelineLogItem): boolean {
  return String(item.id) === PREPARATION_TIMELINE_PARENT_ID;
}

function preparationStatusLabel(item: ProcessTimelineLogItem): string {
  if (item.status === "pending") return "准备中";
  if (item.status === "error") return "准备失败";
  return "已完成";
}

function displayTimelineTitle(item: ProcessTimelineLogItem): string {
  const baseTitle = formatTimelineTitle(item.title || item.tool_name || "执行步骤").replace(/^✨\s*/, "");
  const metadata = item.file_metadata;
  if (!metadata) return baseTitle;
  if (metadata.document_type) {
    const documentLabel = metadata.document_type === "excel" ? "Excel" : "Word";
    const operationLabel = metadata.operation === "read" ? "读取" : "编辑";
    return `${baseTitle} · ${operationLabel} ${documentLabel}`;
  }
  const operationLabels: Record<FileToolMetadata["operation"], string> = {
    read: "读取文件",
    write: "写入文件",
    edit: "编辑文件",
    search: "搜索文件",
  };
  return `${baseTitle} · ${operationLabels[metadata.operation]}`;
}

function fileMetadataSummary(metadata?: FileToolMetadata): string {
  if (!metadata) return "";
  const parts: string[] = [`路径：${metadata.path}`];
  if (metadata.range) {
    const start = metadata.range.start !== undefined ? `第 ${metadata.range.start} 行` : "指定范围";
    parts.push(metadata.range.limit !== undefined ? `${start}起 ${metadata.range.limit} 行` : start);
  }
  if (metadata.paragraph_range) {
    parts.push(`第 ${metadata.paragraph_range.start ?? 0} 段起 ${metadata.paragraph_range.limit ?? 0} 段`);
  }
  if (metadata.sheet_name) parts.push(`工作表：${metadata.sheet_name}`);
  if (metadata.cell_range) parts.push(`范围：${metadata.cell_range}`);
  if (metadata.pattern) parts.push(`关键词：${metadata.pattern}`);
  if (metadata.glob) parts.push(`匹配：${metadata.glob}`);
  if (metadata.size_bytes !== undefined) parts.push(`${Math.round(metadata.size_bytes / 1024)}KB`);
  if (metadata.changes) {
    const changes = Object.entries(metadata.changes).map(([key, value]) => `${key}：${String(value)}`);
    if (changes.length) parts.push(changes.join(" · "));
  }
  return parts.join(" · ");
}

function isToolTimelineItem(item: ProcessTimelineLogItem): boolean {
  if (item.subagent || item.status === "error" || item.category === "tool_resolution") return false;
  return item.category === "tool" || item.category === "sql" || item.title.includes("工具");
}

function isTimelineItemExpanded(item: ProcessTimelineLogItem): boolean {
  if (isRouteGroup(item)) return routeGroupExpanded.value;
  return item.children?.length ? item.childrenExpanded !== false : item.isExpanded === true;
}

function toggleTimelineItem(item: ProcessTimelineLogItem): void {
  if (isRouteGroup(item)) {
    routeGroupExpanded.value = !routeGroupExpanded.value;
    return;
  }
  if (item.children?.length) {
    item.childrenExpanded = item.childrenExpanded === false;
    return;
  }
  if (item.details) item.isExpanded = !item.isExpanded;
}

function iconFor(item: ProcessTimelineLogItem): string {
  if (item.category === "tool_resolution") return item.status === "error" ? "⚠️" : "🧭";
  if (item.status === "error") return "⚠️";
  if (item.title.includes("鉴权及上下文与能力准备")) return "🛡️";
  if (item.title.includes("请求校验")) return "🛡️";
  if (item.title.includes("会话上下文")) return "🗂️";
  if (item.title.includes("知识库和专家清单加载")) return "📚";
  if (item.title.includes("Prompt 组装")) return "🧩";
  if (item.title.includes("获取可用专家")) return "📚";
  if (item.title.includes("准备知识资源范围")) return "📋";
  if (item.title.includes("加载入口专家配置") || item.title.includes("加载目标专家配置")) return "⚙️";
  if (item.title.includes("校验入口专家权限") || item.title.includes("校验目标专家权限")) return "🔒";
  if (item.title.includes("判断并匹配目标专家") || item.title.includes("匹配目标专家")) return "🧠";
  if (item.title.includes("等待上一次会话") || item.title.includes("排队")) return "⏳";
  if (item.category === "context_summarized" || item.title.includes("平台摘录")) return "📋";
  if (item.subagent || item.category === "agent") return "🤖";
  if (item.category === "tool" || item.category === "sql" || item.title.includes("工具")) return "🔧";
  if (item.category === "model" || item.title.includes("模型")) return "✦";
  if (
    item.category === "router"
    || item.category === "intent"
    || item.title.includes("路由")
    || item.title.includes("意图")
  ) return "🧠";
  if (item.category === "permission") return "🔒";
  return "•";
}

function timelineIconFor(item: ProcessTimelineLogItem): any {
  if (item.category === "tool_resolution") return item.status === "error" ? ExclamationTriangleIcon : MapIcon;
  if (item.status === "error") return ExclamationTriangleIcon;
  if (item.title.includes("鉴权及上下文与能力准备") || item.title.includes("请求校验")) return ShieldCheckIcon;
  if (item.title.includes("会话上下文")) return FolderOpenIcon;
  if (item.title.includes("知识库和专家清单加载") || item.title.includes("获取可用专家")) return BookOpenIcon;
  if (item.title.includes("Prompt 组装")) return PuzzlePieceIcon;
  if (item.title.includes("准备知识资源范围") || item.category === "context_summarized" || item.title.includes("平台摘录")) return ClipboardDocumentListIcon;
  if (item.title.includes("加载入口专家配置") || item.title.includes("加载目标专家配置")) return Cog6ToothIcon;
  if (item.title.includes("校验入口专家权限") || item.title.includes("校验目标专家权限")) return LockClosedIcon;
  if (item.title.includes("判断并匹配目标专家") || item.title.includes("匹配目标专家") || item.category === "router" || item.category === "intent") return CpuChipIcon;
  if (item.title.includes("等待上一次会话") || item.title.includes("排队")) return ClockIcon;
  if (item.subagent || item.category === "agent") return UserGroupIcon;
  if (item.category === "tool" || item.category === "sql" || item.title.includes("工具")) return WrenchScrewdriverIcon;
  if (item.category === "model" || item.title.includes("模型")) return SparklesIcon;
  if (item.category === "permission") return LockClosedIcon;
  return ChatBubbleLeftRightIcon;
}

function subagentStatusLabel(status: ProcessTimelineLogItem["status"]): string {
  if (status === "pending") return "进行中";
  if (status === "error") return "失败";
  return "已完成";
}

function formatDuration(duration?: number | null): string {
  if (duration === undefined || duration === null || Number.isNaN(duration) || duration <= 0) return "";
  return duration < 1000 ? `${Math.max(1, Math.round(duration))}ms` : `${(duration / 1000).toFixed(1)}s`;
}

function formatTimelineDuration(item: ProcessTimelineLogItem): string {
  const duration = formatDuration(item.execution_time_ms);
  return isRouteGroup(item) && duration ? `总计 ${duration}` : duration;
}

function timelineDurationTitle(item: ProcessTimelineLogItem): string | undefined {
  if (isPreparationParent(item)) {
    return "准备阶段总耗时，已包含鉴权、上下文、专家配置、模型和能力准备步骤";
  }
  return isRouteGroup(item)
    ? "父级总耗时，已包含下面的路由子步骤；子步骤耗时可能与父级重叠，不应直接相加"
    : undefined;
}

const copiedKey = ref<string | null>(null);
let copyTimer: ReturnType<typeof setTimeout> | null = null;

const fullTimelineText = computed(() => {
  const parts: string[] = [];
  for (const item of items.value) {
    if (item.kind === "text") {
      const content = visibleTimelineText(item.content).trim();
      if (content) {
        parts.push(content);
      }
    } else if (item.kind === "log") {
      const logParts: string[] = [];
      const title = displayTimelineTitle(item);
      const statusText = item.status === "error" ? " (失败)" : item.status === "pending" ? " (执行中)" : "";
      logParts.push(`[${iconFor(item)} ${title}${statusText}]`);
      const details = visibleTimelineText(item.details).trim();
      if (details) {
        logParts.push(`详情: ${details}`);
      }
      if (logParts.length) {
        parts.push(logParts.join("\n"));
      }
    }
  }
  const reasoningContent = visibleTimelineText(props.reasoningContent).trim();
  if (!parts.length && reasoningContent) {
    parts.push(reasoningContent);
  }
  const thinkingText = visibleTimelineText(props.thinkingText).trim();
  if (!parts.length && thinkingText) {
    parts.push(thinkingText);
  }
  return parts.join("\n\n").trim();
});

const handleCopyAll = () => {
  if (!fullTimelineText.value) return;
  void handleCopy("full-timeline", fullTimelineText.value);
};

async function handleCopy(key: string, text?: string | null) {
  const visibleText = visibleTimelineText(text);
  if (!visibleText) return;
  const ok = await copyToClipboard(visibleText);
  if (ok) {
    copiedKey.value = key;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      if (copiedKey.value === key) {
        copiedKey.value = null;
      }
    }, 1500);
  }
}
</script>

<style scoped>
.thought-status-dot {
  display: inline-block;
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 9999px;
  background: #0ea5e9;
  box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.35);
  animation: thought-status-breathe 1.6s ease-in-out infinite;
}

@keyframes thought-status-breathe {
  0%, 100% { opacity: 0.55; transform: scale(0.85); box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.28); }
  50% { opacity: 1; transform: scale(1.15); box-shadow: 0 0 0 0.28rem rgba(14, 165, 233, 0.08); }
}

@media (prefers-reduced-motion: reduce) {
  .thought-status-dot {
    animation: none;
  }
}
</style>
