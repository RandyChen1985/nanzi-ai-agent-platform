<template>
  <div
    v-if="visible"
    class="mb-1 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem]"
    style="contain: layout"
  >
    <ChatThinkingHeader
      v-model:expanded="expanded"
      :is-thinking="!hasAnswer && (isThinking || hasPending)"
      :title="headerTitle"
      :step-count="countTimelineSteps(items)"
      :skill-summary="headerSkillSummary"
      :action-summary="actionSummary"
      :current-step="currentStep"
      :duration="duration"
      :bordered="bordered"
      :dark-mode="darkMode"
      :show-copy="false"
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
        <!-- 一键全部展开 / 全部收起操作条 -->
        <div
          v-if="hasExpandableItems"
          class="flex items-center justify-between px-1 py-0.5 mb-0.5 text-[10px] text-gray-400 dark:text-gray-500 select-none"
        >
          <span>执行明细链路</span>
          <button
            type="button"
            class="inline-flex items-center gap-1 font-medium text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
            @click="toggleAllDetails"
          >
            <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-180': isAllDetailsExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
            </svg>
            <span>{{ isAllDetailsExpanded ? '全部收起明细' : '全部展开明细' }}</span>
          </button>
        </div>
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
          <div v-if="item.kind === 'todo'" class="py-0.5">
            <ChatTodoCard :timeline="[item]" archived />
          </div>
          <div v-else-if="item.kind === 'text' && (hasVisibleTimelineText(item.content) || item.children?.length)" class="rounded-md px-1 py-0.5 text-[12px] leading-5"
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
                  <span class="shrink-0">深度思考</span>
                  <span
                    v-if="reasoningPreviewOf(item).text"
                    class="hidden min-w-0 flex-1 truncate text-gray-400 dark:text-gray-500 sm:block"
                    :title="reasoningPreviewOf(item).truncated ? reasoningPreviewOf(item).text : ''"
                  >{{ reasoningPreviewOf(item).text }}<span v-if="item.pending" class="ml-0.5 animate-pulse">▌</span></span>
                  <span class="ml-auto flex shrink-0 items-center gap-2">
                    <span
                      v-if="reasoningCharCountOf(item) >= REASONING_CHAR_COUNT_MIN_VISIBLE"
                      class="rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400"
                      :title="`本次思考共 ${reasoningCharCountOf(item)} 字`"
                    >{{ reasoningCharCountOf(item) }} 字</span>
                    <span v-if="formatDuration(item.execution_time_ms)" class="shrink-0 font-mono text-[10px] text-gray-400">{{ formatDuration(item.execution_time_ms) }}</span>
                    <svg class="h-3 w-3 shrink-0 transition-transform" :class="{ 'rotate-180': isReasoningBodyOpen(item) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                    </svg>
                  </span>
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
              <div v-if="item.childrenExpanded !== false" class="space-y-0">
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
                    :aria-expanded="isChildDetailsOpen(child)"
                    @click="toggleChildItem(child)"
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
                        v-if="child.subagent && !child.children?.length && !displayTimelineTitle(child).includes('委派智能体') && !displayTimelineTitle(child).includes('子代理')"
                        :title="formatSubagentTraceSummary(child.subagent)"
                      >子代理 · </span>
                      <span>{{ displayTimelineTitle(child) }}</span>
                    </span>
                    <!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，等待用户操作的类别不显示 -->
                    <TimelinePendingHint
                      :item="child"
                      :now="nowMs"
                      :is-live-timer="String(child.id) === liveTimerLogId"
                    />
                    <span
                      v-if="child.subagent && subagentStatusLabel(child.status)"
                      class="shrink-0 text-[10px]"
                      :class="child.status === 'error' ? 'text-red-600' : child.status === 'pending' ? 'text-emerald-500 dark:text-emerald-400' : child.status === 'interrupted' ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'"
                    >
                      {{ subagentStatusLabel(child.status) }}
                    </span>
                    <span
                      v-if="!child.subagent && settledStatusLabel(child.status)"
                      class="shrink-0 text-[10px]"
                      :class="child.status === 'error' ? 'text-red-600' : 'text-amber-600 dark:text-amber-400'"
                    >{{ settledStatusLabel(child.status) }}</span>
                    <span v-if="timelineDurationText(child)" class="shrink-0 font-mono text-[10px]" :class="timelineDurationClass(child)" :title="timelineDurationTitle(child)">{{ timelineDurationText(child) }}</span>
                    <svg v-if="hasVisibleTimelineText(child.details) || hasTimelineArgs(child) || child.children?.length" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': isChildDetailsOpen(child) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                    </svg>
                  </button>
                  <div v-if="child.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    错误原因：{{ child.error_reason }}
                  </div>
                  <div v-if="fileMetadataSummary(child.file_metadata)" class="ml-5 truncate text-[10px] text-gray-400 dark:text-gray-500">
                    {{ fileMetadataSummary(child.file_metadata) }}
                  </div>

                  <!-- 自身入参与元信息：这是本次调用「输入了什么」（如委派工具的 agent_name / query），必须排在内部步骤之前，否则会读成最后一个子步骤的参数 -->
                  <div v-if="(hasTimelineArgs(child) || timelineMetaText(child)) && isChildDetailsOpen(child)" class="mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80">
                    <TimelineToolArgsBlock
                      v-if="timelineArgsText(child)"
                      :text="timelineArgsText(child)"
                      :tool-name="child.tool_name || child.title"
                      :copy-key="`child-args-${child.id}`"
                      :copied-key="copiedKey"
                      @copy="handleCopy"
                    />
                    <div v-if="timelineMetaText(child)" class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500">
                      {{ timelineMetaText(child) }}
                    </div>
                  </div>

                  <!-- 嵌套展示子代理/沙箱准备等内部前置步骤 -->
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
                        @click="subStep.details || hasTimelineArgs(subStep) ? subStep.isExpanded = !subStep.isExpanded : undefined"
                      >
                        <span v-if="subStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                        <WrenchScrewdriverIcon
                          v-if="isToolTimelineItem(subStep)"
                          class="h-3.5 w-3.5 shrink-0"
                          aria-hidden="true"
                        />
                        <component v-else :is="timelineIconFor(subStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(subStep)">{{ displayTimelineTitle(subStep) }}</span>
                        <!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，等待用户操作的类别不显示 -->
                        <TimelinePendingHint
                          :item="subStep"
                          :now="nowMs"
                          :is-live-timer="String(subStep.id) === liveTimerLogId"
                        />
                        <span v-if="subStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                        <span v-if="timelineDurationText(subStep)" class="shrink-0 font-mono text-[10px]" :class="timelineDurationClass(subStep)" :title="timelineDurationTitle(subStep)">{{ timelineDurationText(subStep) }}</span>
                        <svg v-if="hasVisibleTimelineText(subStep.details) || hasTimelineArgs(subStep)" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': subStep.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                        </svg>
                      </button>
                      <div v-if="subStep.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                        错误原因：{{ subStep.error_reason }}
                      </div>
                      <!-- 子步骤自身的入参与元信息：与其它层级一致，参数独立成盒 -->
                      <div v-if="(hasTimelineArgs(subStep) || timelineMetaText(subStep)) && subStep.isExpanded" class="mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80">
                        <TimelineToolArgsBlock
                          v-if="timelineArgsText(subStep)"
                          :text="timelineArgsText(subStep)"
                          :tool-name="subStep.tool_name || subStep.title"
                          :copy-key="`substep-args-${subStep.id}`"
                          :copied-key="copiedKey"
                          @copy="handleCopy"
                        />
                        <div v-if="timelineMetaText(subStep)" class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500">
                          {{ timelineMetaText(subStep) }}
                        </div>
                      </div>
                      <!-- 子步骤的工具输出：独立于参数盒（输入 → 过程 → 输出） -->
                      <div v-if="hasVisibleTimelineText(subStep.details) && subStep.isExpanded" class="mt-1">
                        <div v-if="hasVisibleTimelineText(subStep.details)" class="group/details relative border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
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

                  <!-- 工具输出：属于「这次调用产出了什么」，排在内部步骤之后，与「输入 → 过程 → 输出」的阅读顺序一致 -->
                  <div v-if="hasVisibleTimelineText(child.details) && isChildDetailsOpen(child)" class="mt-1">
                    <div v-if="hasVisibleTimelineText(child.details)" class="group/details relative border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
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
              :aria-expanded="isTimelineItemExpanded(item)"
              @click="toggleTimelineItem(item)"
            >
              <span v-if="item.status === 'pending' && !isQueueWaitStopped(item)" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
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
                v-if="item.auto_approved"
                class="shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
                title="本会话已授权该工具，这条调用没有再弹出确认"
              >本会话已授权</span>
              <!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，等待用户操作的类别不显示 -->
              <TimelinePendingHint
                :item="item"
                :now="nowMs"
                :is-live-timer="String(item.id) === liveTimerLogId"
              />
              <button
                v-if="isQueueWaitItem(item)"
                type="button"
                class="shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                :class="isQueueWaitStopped(item)
                  ? 'border-gray-200 bg-gray-100 text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400'
                  : 'border-red-200 bg-white/90 text-red-600 hover:bg-red-50 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-900/40'"
                :disabled="isQueueWaitStopped(item)"
                :title="isQueueWaitStopped(item) ? '已终止，正在等待会话锁释放' : '终止当前运行并释放会话锁，不再排队等待'"
                @click.stop="handleQueueWaitStop(item)"
              >
                {{ isQueueWaitStopped(item) ? '已终止' : '终止' }}
              </button>
              <span
                v-if="isPreparationParent(item)"
                class="shrink-0 text-[10px] font-medium"
                :class="item.status === 'error' ? 'text-red-600 dark:text-red-400' : item.status === 'pending' ? 'text-sky-600 dark:text-sky-400' : item.status === 'interrupted' ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400 dark:text-gray-500'"
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
                :class="item.status === 'error' ? 'text-red-600' : item.status === 'pending' ? 'text-emerald-500 dark:text-emerald-400' : item.status === 'interrupted' ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'"
              >
                {{ subagentStatusLabel(item.status) }}
              </span>
              <span
                v-if="!item.subagent && settledStatusLabel(item.status)"
                class="shrink-0 text-[10px]"
                :class="item.status === 'error' ? 'text-red-600' : 'text-amber-600 dark:text-amber-400'"
              >{{ settledStatusLabel(item.status) }}</span>
              <span v-if="timelineDurationText(item)" class="shrink-0 font-mono text-[10px]" :class="timelineDurationClass(item)" :title="timelineDurationTitle(item)">
                {{ timelineDurationText(item) }}
              </span>
              <svg
                v-if="hasVisibleTimelineText(item.details) || hasTimelineArgs(item) || item.children?.length"
                class="h-3 w-3 shrink-0 text-gray-400 transition-transform"
                :class="{ 'rotate-180': isTimelineItemExpanded(item) }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
              </svg>
            </button>
            <!-- 工具执行中的进度：远端上报了就是确定态百分比，没上报则是不确定态兜底，
                 结束后让位给耗时/结果。只有工具类条目才有这条，避免给「模型调用」等加噪音。 -->
            <div
              v-if="isToolTimelineItem(item) && item.status === 'pending'"
              class="timeline-progress-shell ml-5 mt-1 flex items-center gap-2"
            >
              <div
                class="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-gray-200/80 dark:bg-gray-700/70"
                :class="{ 'timeline-progress-indeterminate': toolProgressPercent(item) === null }"
                role="progressbar"
                :aria-label="item.title"
                :aria-valuemin="0"
                :aria-valuemax="100"
                :aria-valuenow="toolProgressPercent(item) ?? undefined"
              >
                <div
                  class="h-full rounded-full bg-indigo-500 transition-[width] duration-300 ease-out dark:bg-indigo-400"
                  :style="toolProgressWidth(item) ? { width: toolProgressWidth(item) } : undefined"
                />
              </div>
              <span class="shrink-0 font-mono text-[10px] tabular-nums text-gray-400">{{ toolProgressText(item) }}</span>
            </div>
            <div v-if="item.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
              错误原因：{{ item.error_reason }}
            </div>
            <div v-if="fileMetadataSummary(item.file_metadata)" class="ml-5 truncate text-[10px] text-gray-400 dark:text-gray-500">
              {{ fileMetadataSummary(item.file_metadata) }}
            </div>

            <!-- 顶级项自身入参与元信息：这次调用「输入了什么」，排在子步骤之前 -->
            <div v-if="(hasTimelineArgs(item) || timelineMetaText(item)) && isTimelineItemDetailsOpen(item)" class="mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80">
              <TimelineToolArgsBlock
                v-if="timelineArgsText(item)"
                :text="timelineArgsText(item)"
                :tool-name="item.tool_name || item.title"
                :copy-key="`item-args-${item.id}`"
                :copied-key="copiedKey"
                @copy="handleCopy"
              />
              <div v-if="timelineMetaText(item)" class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500">
                {{ timelineMetaText(item) }}
              </div>
            </div>

            <!-- 嵌套展示根级别子代理内部步骤 / 子步骤 -->
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
                  :aria-expanded="isSubStepDetailsOpen(subStep)"
                  @click="toggleSubStepItem(subStep)"
                >
                  <span v-if="subStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                  <WrenchScrewdriverIcon
                    v-if="isToolTimelineItem(subStep)"
                    class="h-3.5 w-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  <component v-else :is="timelineIconFor(subStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(subStep)">{{ displayTimelineTitle(subStep) }}</span>
                  <!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，等待用户操作的类别不显示 -->
                  <TimelinePendingHint
                    :item="subStep"
                    :now="nowMs"
                    :is-live-timer="String(subStep.id) === liveTimerLogId"
                  />
                  <span v-if="subStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                  <span v-if="timelineDurationText(subStep)" class="shrink-0 font-mono text-[10px]" :class="timelineDurationClass(subStep)" :title="timelineDurationTitle(subStep)">{{ timelineDurationText(subStep) }}</span>
                  <svg v-if="hasVisibleTimelineText(subStep.details) || hasTimelineArgs(subStep) || subStep.children?.length" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': isSubStepDetailsOpen(subStep) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                  </svg>
                </button>
                <!-- 子步骤自身的入参与元信息：有更深子步骤时也必须显示，且排在它们之前 -->
                <div v-if="(hasTimelineArgs(subStep) || timelineMetaText(subStep)) && isSubStepDetailsOpen(subStep)" class="mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80">
                  <TimelineToolArgsBlock
                    v-if="timelineArgsText(subStep)"
                    :text="timelineArgsText(subStep)"
                    :tool-name="subStep.tool_name || subStep.title"
                    :copy-key="`substep-args-${subStep.id}`"
                    :copied-key="copiedKey"
                    @copy="handleCopy"
                  />
                  <div v-if="timelineMetaText(subStep)" class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500">
                    {{ timelineMetaText(subStep) }}
                  </div>
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
                      @click="hasVisibleTimelineText(nestedStep.details) || hasTimelineArgs(nestedStep) ? nestedStep.isExpanded = !nestedStep.isExpanded : undefined"
                    >
                      <span v-if="nestedStep.status === 'pending'" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
                      <WrenchScrewdriverIcon
                        v-if="isToolTimelineItem(nestedStep)"
                        class="h-3.5 w-3.5 shrink-0"
                        aria-hidden="true"
                      />
                      <component v-else :is="timelineIconFor(nestedStep)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      <span class="min-w-0 flex-1 truncate" :title="displayTimelineTitle(nestedStep)">{{ displayTimelineTitle(nestedStep) }}</span>
                      <!-- 挂起步骤安抚文案与动效条：机器正在推进时按耗时递进，等待用户操作的类别不显示 -->
                      <TimelinePendingHint
                        :item="nestedStep"
                        :now="nowMs"
                        :is-live-timer="String(nestedStep.id) === liveTimerLogId"
                      />
                      <span v-if="nestedStep.status === 'error'" class="shrink-0 text-[10px] text-red-600">失败</span>
                      <span v-if="timelineDurationText(nestedStep)" class="shrink-0 font-mono text-[10px]" :class="timelineDurationClass(nestedStep)" :title="timelineDurationTitle(nestedStep)">{{ timelineDurationText(nestedStep) }}</span>
                      <svg v-if="hasVisibleTimelineText(nestedStep.details) || hasTimelineArgs(nestedStep)" class="h-3 w-3 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': nestedStep.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                      </svg>
                    </button>
                    <div v-if="nestedStep.error_reason" class="ml-5 mt-0.5 rounded bg-red-100/70 px-1.5 py-0.5 text-[10px] leading-4 text-red-700 dark:bg-red-950/30 dark:text-red-300">
                      错误原因：{{ nestedStep.error_reason }}
                    </div>
                    <!-- 第四层同样用独立参数盒，元信息留在盒内，与其它层级保持同一套结构 -->
                    <div v-if="(hasTimelineArgs(nestedStep) || timelineMetaText(nestedStep)) && nestedStep.isExpanded" class="mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80">
                      <TimelineToolArgsBlock
                        v-if="timelineArgsText(nestedStep)"
                        :text="timelineArgsText(nestedStep)"
                        :tool-name="nestedStep.tool_name || nestedStep.title"
                        :copy-key="`nested-args-${nestedStep.id}`"
                        :copied-key="copiedKey"
                        @copy="handleCopy"
                      />
                      <div v-if="timelineMetaText(nestedStep)" class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500">
                        {{ timelineMetaText(nestedStep) }}
                      </div>
                    </div>
                    <pre v-if="hasVisibleTimelineText(nestedStep.details) && nestedStep.isExpanded" class="mt-1 whitespace-pre-wrap break-words border-t border-gray-200/70 pt-1 pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:border-gray-700/70 dark:text-gray-400">{{ visibleTimelineText(nestedStep.details) }}</pre>
                  </div>
                </div>
                <!-- 子步骤的工具输出：排在更深子步骤之后（输入 → 过程 → 输出） -->
                <div v-if="hasVisibleTimelineText(subStep.details) && isSubStepDetailsOpen(subStep)" class="mt-1">
                  <div v-if="hasVisibleTimelineText(subStep.details)" class="group/details relative border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
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

            <!-- 顶级项的工具输出：属于「这次调用产出了什么」，排在子步骤之后 -->
            <div v-if="hasVisibleTimelineText(item.details) && isTimelineItemDetailsOpen(item)" class="mt-1">
              <div v-if="hasVisibleTimelineText(item.details)" class="group/details relative border-t border-gray-200/70 pt-1 dark:border-gray-700/70">
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
            </div>
          </div>
        </div>

        <!-- 模型调用折叠汇总：逐条不再占主时间线，只留时间构成（模型 / 工具 / 其他）+ 明细入口 -->
        <div
          v-if="modelCallSummary.count"
          class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border border-dashed border-gray-200/80 px-2 py-1 text-[11px] leading-4 text-gray-400 dark:border-gray-700/70 dark:text-gray-500"
        >
          <SparklesIcon class="h-3 w-3 shrink-0" aria-hidden="true" />
          <span :title="modelCallSummaryTitle">{{ modelCallSummaryLabel }}</span>
          <button
            type="button"
            class="ml-auto shrink-0 font-medium text-gray-500 transition-colors hover:text-gray-800 hover:underline dark:text-gray-400 dark:hover:text-gray-200"
            title="查看本次执行的大模型调用明细"
            @click="emit('open-model-stats')"
          >查看明细</button>
        </div>

        <div v-if="isThinking && !items.length" class="px-2 py-1 text-xs text-gray-400 animate-pulse">
          {{ thinkingText || '正在处理…' }}
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import ChatThinkingHeader from "@/components/chat/ChatThinkingHeader.vue";
import ChatTodoCard from "@/components/chat/ChatTodoCard.vue";
import TimelineToolArgsBlock from "@/components/chat/TimelineToolArgsBlock.vue";
import TimelinePendingHint from "@/components/chat/TimelinePendingHint.vue";
import {
  ArrowsPointingInIcon,
  BookOpenIcon,
  ChatBubbleLeftRightIcon,
  ClipboardDocumentListIcon,
  ClockIcon,
  Cog6ToothIcon,
  CpuChipIcon,
  CubeIcon,
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
  REASONING_CHAR_COUNT_MIN_VISIBLE,
  reasoningCharCount,
  resolveReasoningPreview,
  type ReasoningPreview,
} from "@/utils/reasoningPreview";
import {
  skillFlowNoticeLabel,
  summarizeSkillFlowBadges,
  type SkillFlowBadge,
} from "@/utils/skillFlowBadges";
import {
  appendToolSummary,
  appendToolTimeoutSuffix,
  buildLegacyProcessTimeline,
  countTimelineSteps,
  formatTimelineTitle,
  groupRouteTimelineItems,
  isReasoningContentExpanded,
  isToolTimeoutNearLimit,
  mergeTimelineLogs,
  resolveLiveTimerDurationMs,
  resolveLiveTimerLogId,
  resolveTimelineCurrentStep,
  selectTimelineRenderableItems,
  stripModelCallLogs,
  summarizeModelCalls,
  summarizeToolCalls,
  timelineHasPending,
  PREPARATION_TIMELINE_PARENT_ID,
  isWorkspacePrewarmPending,
  type FileToolMetadata,
  type ProcessTimelineItem,
  type ProcessTimelineLogItem,
  type ProcessTimelineTextItem,
} from "@/utils/processTimeline";
import {
  formatSubagentTraceSummary,
} from "@/utils/subagentTrace";
import { formatTokenFull, formatTokenUsageAmount } from "@/utils/tokenFormat";

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
  /** 当前轮的清单正由输入框上方的 banner 卡片展示时，时间线内不再重复渲染。 */
  hideTodo?: boolean;
  /**
   * 模型调用（category=model）默认折叠为底部一行汇总，避免「工具全在上、模型调用全在下」
   * 的时间倒挂；需要逐条展示观测信息的场景传 false 恢复旧行为。
   */
  hideModelCalls?: boolean;
  /**
   * 本轮工具调用的实际超时上限（秒，版本级优先、否则全局）。
   * 挂起中的工具行显示「已耗时 / 上限」，让用户知道还要等多久才算超时。
   */
  toolTimeoutSeconds?: number;
  /**
   * 本轮消息累计的输入 / 输出 token（后端 usage 口径）。
   * 时间只说明「模型算了多久」，token 才说明「模型干了多少活」——
   * 同一条回复里 10 次 × 0.8s 可能只是 10 段几十 token 的短判断。
   */
  promptTokens?: number;
  completionTokens?: number;
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
  hideModelCalls: true,
  toolTimeoutSeconds: 0,
  promptTokens: 0,
  completionTokens: 0,
});

const expanded = defineModel<boolean>("expanded", { default: false });
const routeGroupExpanded = ref(true);

const emit = defineEmits<{
  (e: "stop"): void;
  (e: "open-model-stats"): void;
}>();

// 会话运行锁的排队等待条目（id 由后端 preflight 下发）：这里提供直接终止入口，
// 否则用户只能干等上一轮任务结束或等待窗口超时。
const QUEUE_WAIT_STEP_ID = "session:queue_wait";
const isQueueWaitItem = (item: ProcessTimelineItem): boolean =>
  item.kind === "log" && String(item.id) === QUEUE_WAIT_STEP_ID && item.status === "pending";

// 用户点击终止后本地立即置为「已终止」：后端不会再为该条目推送状态，
// 不做本地标记的话按钮仍可点、进行中动效也继续转，看起来像没生效。
const stoppedQueueWaitKeys = ref<Set<string>>(new Set());
const isQueueWaitStopped = (item: ProcessTimelineItem): boolean =>
  item.kind === "log" && stoppedQueueWaitKeys.value.has(String(item.id));
const handleQueueWaitStop = (item: ProcessTimelineItem) => {
  if (isQueueWaitStopped(item)) return;
  stoppedQueueWaitKeys.value = new Set(stoppedQueueWaitKeys.value).add(String(item.id));
  emit("stop");
};

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
  // 当前轮由 banner 负责时隐藏清单，其余情况（含没有 settled 标记的旧快照）
  // 都归档到执行过程里，保证历史消息仍然查得到。
  return selectTimelineRenderableItems(visibleItems, { hideTodo: props.hideTodo });
});

// 模型调用（category=model）不占主时间线：它们每次请求一条、又固定堆在数组末尾，
// 留在列表里会造成「工具在上、模型调用在下」的时间倒挂。默认折叠为底部一行汇总，
// 逐条明细由「查看明细」承载；进行中的那条仍保留，避免模型思考期间列表完全静止。
const hideModelCalls = computed(() => props.hideModelCalls !== false);
const items = computed(() => {
  const grouped = groupRouteTimelineItems(timelineItems.value, routeGroupExpanded.value);
  return hideModelCalls.value ? stripModelCallLogs(grouped) : grouped;
});

const modelCallSummary = computed(() =>
  hideModelCalls.value
    ? summarizeModelCalls(timelineItems.value)
    : { count: 0, totalMs: 0, pending: false }
);
// 工具耗时合计：用来把「模型 5.4s」放回总耗时里解释，而不是让它孤零零地与
// 顶部「耗时 36.2s」并排——那会让人以为统计漏掉了三十秒。
const toolCallSummary = computed(() =>
  hideModelCalls.value
    ? summarizeToolCalls(timelineItems.value)
    : { count: 0, totalMs: 0, pending: false }
);
// header 的 duration 是字符串（如 "36.2"），缺省 / 流式中为空，此时退回旧的「合计」口径。
const totalDurationMs = computed(() => {
  const value = Number(props.duration);
  return Number.isFinite(value) && value > 0 ? value * 1000 : 0;
});
/**
 * 模型 token 用量（紧凑格式，如「入 299.5K / 出 284 tok」）。
 *
 * 时间只能说明「算了多久」，token 才能说明「干了多少活」：同一条回复里 10 次
 * 0.8s 往往只是 10 段几十 token 的短判断，只看时间会误以为是统计漏了。
 * 输入 token 是各次调用累加（每次都会重发上下文），输出才是实际生成量。
 */
const modelTokenUsageLabel = computed(() => {
  const input = Math.max(0, Math.round(Number(props.promptTokens) || 0));
  const output = Math.max(0, Math.round(Number(props.completionTokens) || 0));
  if (input <= 0 && output <= 0) return "";
  // formatTokenUsageAmount 自带 "tok" 后缀，这里只取数量部分，末尾统一加一次。
  const compact = (value: number) => formatTokenUsageAmount(value).replace(/\s*tok$/, "");
  const parts: string[] = [];
  if (input > 0) parts.push(`入 ${compact(input)}`);
  if (output > 0) parts.push(`出 ${compact(output)}`);
  return `${parts.join(" / ")} tok`;
});
const modelCallSummaryLabel = computed(() => {
  const { count, totalMs, pending } = modelCallSummary.value;
  const total = totalDurationMs.value;
  // 有本轮墙钟耗时时直接给时间构成：模型 / 工具 / 其他，三者相加约等于总耗时。
  if (total > 0) {
    const toolMs = toolCallSummary.value.totalMs;
    const parts = [`模型 ${count} 次 ${formatDuration(totalMs) || "0s"}`];
    if (modelTokenUsageLabel.value) parts.push(modelTokenUsageLabel.value);
    if (toolMs > 0) parts.push(`工具 ${formatDuration(toolMs)}`);
    const restMs = total - totalMs - toolMs;
    // 差值低于 100ms 属于准备 / 收尾的正常抖动，不必单独列一项制造疑问。
    if (restMs >= 100) parts.push(`其他 ${formatDuration(restMs)}`);
    if (pending) parts.push("进行中");
    return parts.join(" · ");
  }
  const parts = [`${count} 次模型调用`];
  if (totalMs > 0) parts.push(`合计 ${formatDuration(totalMs)}`);
  if (modelTokenUsageLabel.value) parts.push(modelTokenUsageLabel.value);
  if (pending) parts.push("进行中");
  return parts.join(" · ");
});
const modelCallSummaryTitle = computed(() => {
  const { count, totalMs } = modelCallSummary.value;
  const total = totalDurationMs.value;
  const toolMs = toolCallSummary.value.totalMs;
  const input = Math.max(0, Math.round(Number(props.promptTokens) || 0));
  const output = Math.max(0, Math.round(Number(props.completionTokens) || 0));
  const tokenDetail =
    input > 0 || output > 0
      ? `Token 输入 ${formatTokenFull(input)} / 输出 ${formatTokenFull(output)}（输入为各次调用累加，输出为实际生成量）。`
      : "";
  if (total <= 0) return `模型生成耗时合计，不含工具执行与等待。${tokenDetail}`;
  const percent = (value: number) => `${Math.min(100, Math.round((value / total) * 100))}%`;
  const parts = [
    `本轮总耗时 ${formatDuration(total)}`,
    `模型生成 ${count} 次 ${formatDuration(totalMs) || "0s"}（${percent(totalMs)}）`,
  ];
  if (toolMs > 0) parts.push(`工具执行 ${formatDuration(toolMs)}（${percent(toolMs)}）`);
  const restMs = total - totalMs - toolMs;
  if (restMs >= 100) parts.push(`准备与收尾 ${formatDuration(restMs)}`);
  return `${parts.join("；")}。工具执行与等待不计入模型耗时；工具耗时为各次调用累加，并行执行时可能高于总耗时。${tokenDetail}`;
});

const hasPending = computed(() => timelineHasPending(items.value));
const headerTitle = computed(() => props.hasAnswer ? "执行完成" : "执行过程");
const currentStep = computed(() => resolveTimelineCurrentStep(
  items.value,
  Boolean(!props.hasAnswer && (props.isThinking || hasPending.value)),
) || (!props.hasAnswer && props.isThinking ? visibleTimelineText(props.thinkingText) : ""));

const visible = computed(() => Boolean(
  props.isThinking
  || items.value.length
  || modelCallSummary.value.count
  || props.skillBadges.length,
));
const skillNoticeLabel = computed(() => skillFlowNoticeLabel(props.skillBadges));
const headerSkillSummary = computed(() =>
  props.skillSummary || summarizeSkillFlowBadges(props.skillBadges)
);

/** 关键动作摘要：提炼出工具调用与深度思考动作，供 Header 直观展示 */
const actionSummary = computed(() => {
  if (props.isThinking && !props.hasAnswer) return "";
  const toolCounts: Record<string, number> = {};
  let hasReasoning = false;

  function collectSummary(it: any) {
    if (it.kind === "text" && it.textKind === "reasoning") {
      hasReasoning = true;
    }
    if (it.kind === "log") {
      if (it.tool_name) {
        const name = String(it.tool_name).trim();
        toolCounts[name] = (toolCounts[name] || 0) + 1;
      } else if (it.title?.includes("Bash")) {
        toolCounts["Bash"] = (toolCounts["Bash"] || 0) + 1;
      }
    }
    if (it.children?.length) {
      it.children.forEach(collectSummary);
    }
  }
  items.value.forEach(collectSummary);

  const parts: string[] = [];
  if (hasReasoning) parts.push("深度思考");
  const toolEntries = Object.entries(toolCounts);
  if (toolEntries.length > 0) {
    const toolDesc = toolEntries.map(([name, count]) => `${count} 项 ${name}`).join("、");
    parts.push(toolDesc);
  }
  return parts.join(" · ");
});

/** 一键全部展开 / 全部收起明细 */
const isAllDetailsExpanded = ref(false);

const hasExpandableItems = computed(() => {
  return items.value.some((it: any) => {
    if (it.kind === "text") return it.textKind === "reasoning" || it.children?.length;
    if (it.kind === "log") return hasVisibleTimelineText(it.details) || hasTimelineArgs(it) || it.children?.length;
    return false;
  });
});

function toggleAllDetails() {
  isAllDetailsExpanded.value = !isAllDetailsExpanded.value;
  const target = isAllDetailsExpanded.value;
  function setExpand(it: any) {
    if (it.kind === "text") {
      if (it.textKind === "reasoning") it.contentExpanded = target;
      if (it.children?.length) it.childrenExpanded = target;
    } else if (it.kind === "log") {
      it.isExpanded = target;
      if (it.children?.length) it.childrenExpanded = target;
    }
    if (it.children?.length) {
      it.children.forEach(setExpand);
    }
  }
  items.value.forEach(setExpand);
}

/**
 * 时间线文本清洗带一层小缓存。
 *
 * `stripInternalContextBlocks` 要跑十来条正则（其中包含 `[\s\S]*?` 的跨行匹配），
 * 而它在每次渲染里对每个节点的 details 至少调一次（`hasVisibleTimelineText` 决定
 * 是否显示折叠箭头），时间线每 500ms 的秒表 tick 又会把整棵树重渲一遍。工具输出
 * 动辄几十 KB，同一段文本被反复清洗是纯浪费。键就是原文本身，内容一变键就变，
 * 不会读到脏值；上限 200 条避免长会话无限增长。
 */
const timelineTextCache = new Map<string, string>();
let timelineTextCacheBytes = 0;
/** 按总字节收敛：工具结果动辄几十 KB，按条数限制会吃掉上百 MB。 */
const TIMELINE_TEXT_CACHE_MAX_BYTES = 8 * 1024 * 1024;
function visibleTimelineText(text?: string | null): string {
  const raw = text ?? "";
  if (!raw) return "";
  const cached = timelineTextCache.get(raw);
  if (cached !== undefined) return cached;
  const cleaned = stripInternalContextBlocks(raw);
  if (timelineTextCacheBytes + raw.length > TIMELINE_TEXT_CACHE_MAX_BYTES) {
    timelineTextCache.clear();
    timelineTextCacheBytes = 0;
  }
  timelineTextCache.set(raw, cleaned);
  timelineTextCacheBytes += raw.length + cleaned.length;
  return cleaned;
}

function hasVisibleTimelineText(text?: string | null): boolean {
  return Boolean(visibleTimelineText(text).trim());
}

/** 工具入参展示文本；命令类工具（Bash）即命令原文。与 details（输出）独立。 */
function timelineArgsText(item: { tool_args?: string }): string {
  return String(item.tool_args || "").trim();
}

function hasTimelineArgs(item: { tool_args?: string }): boolean {
  return Boolean(timelineArgsText(item));
}

/** AgentScope 框架侧结果状态的展示文案，未知状态原样回退。 */
const TOOL_RESULT_STATE_LABELS: Record<string, string> = {
  success: "成功",
  succeeded: "成功",
  finished: "成功",
  completed: "成功",
  error: "失败",
  failed: "失败",
  failure: "失败",
  denied: "被拒绝",
  interrupted: "已中断",
  timeout: "超时",
  timed_out: "超时",
};

/** 工具调用的技术元信息：实际使用的模型、温度与框架侧结果状态，用于排查配置与超时/拒绝。 */
function timelineMetaText(item: {
  model?: string;
  temperature?: number;
  tool_result_state?: string;
}): string {
  const parts: string[] = [];
  const model = String(item.model || "").trim();
  if (model) parts.push(`模型 ${model}`);
  if (typeof item.temperature === "number" && !Number.isNaN(item.temperature)) {
    parts.push(`温度 ${item.temperature}`);
  }
  const state = String(item.tool_result_state || "").trim();
  if (state) parts.push(`工具状态 ${TOOL_RESULT_STATE_LABELS[state] || state}`);
  return parts.join(" · ");
}

watch(hasPending, (pending) => {
  if (pending) {
    expanded.value = true;
  } else if (props.hasAnswer) {
    expanded.value = false;
  }
}, { immediate: true });

watch(() => props.hasAnswer, (answer) => {
  if (answer && !hasPending.value) expanded.value = false;
}, { immediate: true });

// 执行完成后自动折叠「鉴权及上下文与能力准备」子树，仅保留一行标题，让界面干净。
// 进行中（存在 pending 子步骤）仍保持展开以观察每步推进；一旦达成有答案且无 pending，
// 即把该准备父级子树收起。仅针对此父级，其它父级（路由等）不受影响。
watch(
  () => !hasPending.value && props.hasAnswer,
  (done) => {
    if (!done) return;
    for (const item of timelineItems.value) {
      if (item.kind === "log" && isPreparationParent(item) && item.children?.length) {
        item.childrenExpanded = false;
      }
    }
  },
  { immediate: true },
);

// —— 沙箱工作区预热的"推进中"感知 ——
// 占位日志是静态"创建中"文案，沙箱初始化（拉镜像/k8s bootstrap/wait_for 锁）可能
// 持续数秒~数十秒。这里用一个 500ms 的 tick 驱动"已等待 Ns + 阶段文案 + 不确定进度条"，
// 让用户看到数值在走、阶段在变，避免"傻等"感。
function findWorkspacePrewarmPending(items: ProcessTimelineItem[]): boolean {
  // 统一递归遍历整棵时间线树：沙箱 prewarm 占位日志可能被挂在任意层级下
  // （prep 占位符顶层、Bash 卡片挂在 narration(text) 下→其三层的 prewarm 子项等），
  // 因此不能只检查前两层，需递归检查每个 text/log 节点及其所有后代。
  const walk = (node: ProcessTimelineItem): boolean => {
    if (isWorkspacePrewarmPending(node)) return true;
    for (const child of node.children || []) {
      if (walk(child)) return true;
    }
    return false;
  };
  return items.some(walk);
}

const tickNow = ref(0);
/**
 * 挂起文案使用的真实时间戳。
 *
 * tickNow 只是每 500ms 加 1 的渲染触发器（计数器），不能当成时间用；
 * 这里仅借它建立重算依赖，取值的仍是 Date.now()，与 liveTimerDuration 同源。
 */
const nowMs = computed(() => {
  void tickNow.value;
  return Date.now();
});
let tickTimer: ReturnType<typeof setInterval> | null = null;
const isWorkspacePrewarming = computed(() => findWorkspacePrewarmPending(items.value));

/** 当前该走实时秒表的那条步骤：只有最后一条挂起项、且不是在等用户操作。 */
const liveTimerLogId = computed(() => {
  // 依赖 tickNow 让秒表每 500ms 推进一次（同时承担 render 触发）
  void tickNow.value;
  return resolveLiveTimerLogId(items.value);
});

/** 工作区预热文案与单卡实时秒表共用同一个 500ms ticker；都不需要时自动停表。 */
const needsLiveTick = computed(
  () => isWorkspacePrewarming.value || liveTimerLogId.value !== null,
);

watch(
  needsLiveTick,
  (needs) => {
    if (needs) {
      if (!tickTimer) {
        tickTimer = setInterval(() => { tickNow.value += 1; }, 500);
      }
    } else if (tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
});

function isReasoningBodyOpen(item: ProcessTimelineTextItem): boolean {
  return isReasoningContentExpanded(item);
}

/** 折叠态摘要；pending 时跟随最新内容。内部上下文块剥离沿用 visibleTimelineText。 */
function reasoningPreviewOf(item: ProcessTimelineTextItem): ReasoningPreview {
  return resolveReasoningPreview(visibleTimelineText(item.content), { pending: Boolean(item.pending) });
}

/** 折叠态字数胶囊口径，与摘要同源。 */
function reasoningCharCountOf(item: ProcessTimelineTextItem): number {
  return reasoningCharCount(visibleTimelineText(item.content));
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
  if (item.status === "interrupted") return "未完成";
  return "已完成";
}

function displayTimelineTitle(item: ProcessTimelineLogItem): string {
  const baseTitle = appendToolSummary(
    formatTimelineTitle(item.title || item.tool_name || "执行步骤")
      .replace(/^[\p{Extended_Pictographic}✨📝]\s*/u, ""),
    item.tool_summary,
  );
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

function isTimelineItemDetailsOpen(item: ProcessTimelineLogItem): boolean {
  if (item.children?.length) {
    return item.isExpanded === true || item.childrenExpanded !== false;
  }
  return item.isExpanded === true;
}

function toggleTimelineItem(item: ProcessTimelineLogItem): void {
  if (isRouteGroup(item)) {
    routeGroupExpanded.value = !routeGroupExpanded.value;
    return;
  }
  const hasKids = Boolean(item.children?.length);
  const hasSelf = Boolean(hasVisibleTimelineText(item.details) || hasTimelineArgs(item));
  if (!hasKids && !hasSelf) return;

  const currentOpen = hasKids
    ? item.childrenExpanded !== false
    : item.isExpanded === true;
  const nextOpen = !currentOpen;

  if (hasKids) {
    item.childrenExpanded = nextOpen;
  }
  if (hasSelf) {
    item.isExpanded = nextOpen;
  }
}

function isChildDetailsOpen(child: ProcessTimelineLogItem): boolean {
  if (child.children?.length) {
    return child.isExpanded === true || child.childrenExpanded !== false;
  }
  return child.isExpanded === true;
}

function toggleChildItem(child: ProcessTimelineLogItem): void {
  const hasKids = Boolean(child.children?.length);
  const hasSelf = Boolean(hasVisibleTimelineText(child.details) || hasTimelineArgs(child));
  if (!hasKids && !hasSelf) return;

  const currentOpen = hasKids
    ? child.childrenExpanded !== false
    : child.isExpanded === true;
  const nextOpen = !currentOpen;

  if (hasKids) {
    child.childrenExpanded = nextOpen;
  }
  if (hasSelf) {
    child.isExpanded = nextOpen;
  }
}

// 子步骤（第三层）与 child（第二层）的展开语义必须完全一致：subStep 下有更深子步骤时，
// 自身入参与输出要跟着子列表一起开关。此前 subStep 的点击只切 childrenExpanded，
// isExpanded 永远是 false —— 详情区即便去掉 !children?.length 也照样打不开。
const isSubStepDetailsOpen = isChildDetailsOpen;
const toggleSubStepItem = toggleChildItem;

function iconFor(item: ProcessTimelineLogItem): string {
  if (item.category === "tool_resolution") return item.status === "error" ? "⚠️" : "🧭";
  if (item.status === "error") return "⚠️";
  if (item.title.includes("鉴权及上下文与能力准备")) return "🛡️";
  if (item.title.includes("请求校验")) return "🛡️";
  if (item.title.includes("会话上下文")) return "🗂️";
  if (item.title.includes("知识库和专家清单加载")) return "📚";
  if (item.title.includes("沙箱") || item.title.includes("工作区")) return "📦";
  if (item.title.includes("Prompt 组装")) return "🧩";
  if (item.title.includes("获取可用专家")) return "📚";
  if (item.title.includes("准备知识资源范围")) return "📋";
  if (item.title.includes("加载入口专家配置") || item.title.includes("加载目标专家配置")) return "⚙️";
  if (item.title.includes("校验入口专家权限") || item.title.includes("校验目标专家权限")) return "🔒";
  if (item.title.includes("判断并匹配目标专家") || item.title.includes("匹配目标专家")) return "🧠";
  if (item.title.includes("等待上一次会话") || item.title.includes("排队")) return "⏳";
  if (item.category === "context_summarized" || item.title.includes("平台摘录") || item.title.includes("上下文已压缩")) return "📦";
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
  if (item.title.includes("沙箱") || item.title.includes("工作区")) return CubeIcon;
  if (item.title.includes("Prompt 组装")) return PuzzlePieceIcon;
  if (item.category === "context_summarized" || item.title.includes("平台摘录") || item.title.includes("上下文已压缩")) return ArrowsPointingInIcon;
  if (item.title.includes("准备知识资源范围")) return ClipboardDocumentListIcon;
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
  if (status === "interrupted") return "未完成";
  return "已完成";
}

/** 落定但未成功的状态文案；pending / success / warning 不显示文字标签。 */
function settledStatusLabel(status: ProcessTimelineLogItem["status"]): string {
  if (status === "error") return "失败";
  if (status === "interrupted") return "未完成";
  return "";
}

function formatDuration(duration?: number | null): string {
  if (duration === undefined || duration === null || Number.isNaN(duration) || duration <= 0) return "";
  return duration < 1000 ? `${Math.max(1, Math.round(duration))}ms` : `${(duration / 1000).toFixed(1)}s`;
}

/** 命中实时秒表的那一条返回已执行时长；其余维持冻结耗时。 */
function liveTimerDuration(item: ProcessTimelineLogItem): string {
  const liveMs = resolveLiveTimerDurationMs(item, liveTimerLogId.value, Date.now());
  return liveMs === null ? "" : formatDuration(liveMs);
}

/** 进度百分比；`total` 缺失或非法时返回 null，交给不确定态渲染。 */
function toolProgressPercent(item: ProcessTimelineLogItem): number | null {
  const progress = item.progress;
  const total = item.total;
  if (typeof progress !== "number" || typeof total !== "number" || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((progress / total) * 100)));
}

/** 不确定态返回空串：宽度和位移都交给 CSS 扫光动画，内联 width 会把它钉死。 */
function toolProgressWidth(item: ProcessTimelineLogItem): string {
  const percent = toolProgressPercent(item);
  return percent === null ? "" : `${percent}%`;
}

function formatProgressNumber(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "?";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function toolProgressText(item: ProcessTimelineLogItem): string {
  const detail = item.progressMessage || "";
  const percent = toolProgressPercent(item);
  if (percent === null) {
    // 没有 total 时不能编造 a/b：只显示远端文案，没有文案就退回「进行中」。
    return detail || "进行中";
  }
  const ratio = `${formatProgressNumber(item.progress)}/${formatProgressNumber(item.total)}`;
  return detail ? `${ratio} ${detail}` : ratio;
}

function formatTimelineDuration(item: ProcessTimelineLogItem): string {
  const live = liveTimerDuration(item);
  if (live) return live;
  const duration = formatDuration(item.execution_time_ms);
  return isRouteGroup(item) && duration ? `总计 ${duration}` : duration;
}

/** 只有挂起中的工具行需要分母：完成/失败态已有实际耗时，再挂上限反而干扰阅读。 */
function pendingToolTimeoutSeconds(item: ProcessTimelineLogItem): number | null {
  if (item.status !== "pending") return null;
  if (String(item.category || "") !== "tool") return null;
  const seconds = Number(props.toolTimeoutSeconds);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/** 挂起工具显示「已耗时 / 上限」，其余情况沿用原有耗时文本。 */
function timelineDurationText(item: ProcessTimelineLogItem): string {
  return appendToolTimeoutSuffix(
    formatTimelineDuration(item),
    pendingToolTimeoutSeconds(item) ?? undefined,
  );
}

/** 接近上限（≥90%）时把数字染成琥珀色，超时后由既有逻辑标成 error 变红。 */
function timelineDurationClass(item: ProcessTimelineLogItem): string {
  const timeout = pendingToolTimeoutSeconds(item);
  if (timeout === null) return "text-gray-400";
  const liveMs = resolveLiveTimerDurationMs(item, liveTimerLogId.value, Date.now());
  return isToolTimeoutNearLimit(liveMs, timeout) ? "text-amber-500" : "text-gray-400";
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
      const args = timelineArgsText(item);
      if (args) {
        logParts.push(`参数: ${args}`);
      }
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
/* 远端没给 total 时的「不确定态」：一条两端淡出的光带单向滑过整条轨道。
   两端都是透明的，循环接缝处看不出跳变；旧写法用 margin-left 在 0↔86% 来回跑，
   在窄轨道里就是一条横跳的实心色块，观感像闪烁。 */
.timeline-progress-indeterminate > div {
  width: 38%;
  background-color: transparent;
  background-image: linear-gradient(
    90deg,
    rgba(129, 140, 248, 0) 0%,
    rgba(129, 140, 248, 0.55) 45%,
    #a5b4fc 55%,
    rgba(129, 140, 248, 0) 100%
  );
  /* 扫光保持单向循环。曾试过「只播 3 次（约 7 秒）」：委派时 sub_agent_call 会 pending
     近 30 秒，扫光 7 秒后停住，看起来像「已完成」，反而误导。这里持续循环的代价很低——
     关键帧只改 transform（不改 background-position / left），配合下面的合成层提示由
     合成器完成位移，不会每帧重绘整条 linear-gradient；真正贵的 box-shadow 动画已在
     状态点上移除。契约也要求「只动 transform、不做亮度闪烁」，所以不加 opacity 呼吸。 */
  animation: timeline-progress-sweep 2.4s cubic-bezier(0.45, 0, 0.55, 1) infinite;
  /* 显式合成层提示让位移交给合成器，避免重绘扩散到轨道容器。 */
  will-change: transform;
  transform: translateZ(0);
  contain: paint;
  backface-visibility: hidden;
}

/* 兜底进度条：几十毫秒就结束的工具不该闪一下，延后出现；确定态进度同样受益于此。 */
.timeline-progress-shell {
  opacity: 0;
  animation: timeline-progress-reveal 120ms ease-out 320ms forwards;
}

@keyframes timeline-progress-reveal {
  to {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .timeline-progress-shell {
    opacity: 1;
    animation: none;
  }
}

@keyframes timeline-progress-sweep {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(264%);
  }
}

.thought-status-dot {
  display: inline-block;
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 9999px;
  background: #0ea5e9;
  box-shadow: 0 0 0 0.12rem rgba(14, 165, 233, 0.18);
  animation: thought-status-breathe 1.6s ease-in-out infinite;
  will-change: opacity, transform;
}

/* 只动画 opacity / transform：这两者可以交给合成器。
   原先还动画 box-shadow，而 box-shadow 无法合成，必须每帧重绘整个元素——
   委派时同时挂起多个 pending 步骤，多个阴影一起重绘会把绘制线程长期占住，
   表现为主线程几乎空闲（Scripting 仅 20~30ms）但画面持续发卡。 */
@keyframes thought-status-breathe {
  0%, 100% { opacity: 0.55; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
}

@media (prefers-reduced-motion: reduce) {
  .thought-status-dot {
    animation: none;
  }
}
</style>
