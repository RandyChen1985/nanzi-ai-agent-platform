<template>
  <div
    v-if="visible"
    class="mb-1 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem]"
    style="contain: layout"
  >
    <ChatThinkingHeader
      v-model:expanded="expanded"
      @user-toggle="userToggledExpanded = true"
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
          class="flex flex-wrap items-center gap-1 rounded-md border border-gray-200/70 bg-gray-50/70 px-1.5 py-1 text-[11px] font-semibold text-gray-600 dark:border-gray-700/40 dark:bg-gray-800/40 dark:text-gray-300"
        >
          <SparklesIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{{ skillNoticeLabel }}</span>
          <span
            v-for="skill in skillBadges"
            :key="skill.key"
            class="rounded-full border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
            :title="skill.description"
          >
            {{ skill.label }}
          </span>
        </div>
        <div
          class="timeline-rail space-y-0.5"
        >
        <div
          v-for="item in items"
          :key="item.id"
          class="relative"
          :class="timelineNodeClass(item)"
        >
          <div v-if="item.kind === 'todo'" class="py-0.5">
            <ChatTodoCard :timeline="[item]" archived />
          </div>
          <!-- 精简密度：多轮思考压成一行。工具调用仍是独立条目，不做合并。 -->
          <div
            v-else-if="item.kind === 'text' && item.mergedReasoning"
            class="rounded-md px-1 py-0.5 text-[12px] leading-5 text-gray-400 dark:text-gray-500"
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 text-left text-[11.5px] hover:text-gray-600 dark:hover:text-gray-300"
              :aria-expanded="compactRoundsOpen"
              :aria-label="compactRoundsOpen ? '收起各组思考' : '展开各组思考'"
              @click="toggleCompactRounds"
            >
              <span v-if="item.pending" class="thought-status-dot shrink-0" aria-label="进行中" title="进行中" />
              <CpuChipIcon v-else class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span class="shrink-0">深度思考</span>
              <span
                v-if="item.mergedReasoning.previews.length"
                class="hidden min-w-0 flex-1 truncate text-gray-400 dark:text-gray-500 sm:block"
              >{{ item.mergedReasoning.previews[item.mergedReasoning.previews.length - 1] }}</span>
              <span class="ml-auto flex shrink-0 items-center gap-2">
                <span
                  class="rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400"
                  :title="mergedReasoningLabel(item.mergedReasoning)"
                >{{ mergedReasoningLabel(item.mergedReasoning) }}</span>
                <svg class="h-3 w-3 shrink-0 transition-transform" :class="{ 'rotate-180': compactRoundsOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                </svg>
              </span>
            </button>
            <!-- 展开态：逐轮摊开正文，合并行留在原位，再点一次即可收回 -->
            <div v-if="compactRoundsOpen && item.mergedReasoning.sources?.length" class="mt-1 space-y-1.5">
              <div v-for="(round, roundIndex) in item.mergedReasoning.sources" :key="round.id">
                <div class="flex items-center gap-2 text-[10px] text-gray-400 dark:text-gray-500">
                  <span class="shrink-0">第 {{ roundIndex + 1 }} 轮</span>
                  <span v-if="round.execution_time_ms" class="shrink-0 font-mono">{{ formatDuration(round.execution_time_ms) }}</span>
                </div>
                <div class="reasoning-md border-l-2 border-gray-200 pl-2.5 pr-6 text-gray-600 dark:border-gray-700 dark:text-gray-400">
                  <div class="reasoning-md-body" v-html="reasoningDetailsHtml(round.content)" />
                </div>
              </div>
            </div>
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
                  <div
                    class="reasoning-md pr-6 text-gray-600 dark:text-gray-400"
                    :class="{ 'reasoning-md-clamped': isReasoningClamped(item) }"
                  >
                    <!-- 走与正文同一条 markdown 管线（内部已 sanitize），不再是裸 <pre> -->
                    <div class="reasoning-md-body" v-html="reasoningHtml(item)" />
                  </div>
                  <button
                    v-if="isReasoningClamped(item)"
                    type="button"
                    class="mt-0.5 inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 transition-colors hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                    @click="expandReasoningFull(item)"
                  >
                    <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                    </svg>
                    展开全部（共 {{ reasoningCharCountOf(item) }} 字）
                  </button>
                </blockquote>
                <template v-else>
                  <button
                    v-if="narrationNeedsCollapse(item)"
                    type="button"
                    class="mb-0.5 flex w-full items-center gap-2 text-left text-[10px] text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                    :aria-expanded="isNarrationBodyOpen(item)"
                    @click="item.contentExpanded = !isNarrationBodyOpen(item)"
                  >
                    <span class="narration-preview min-w-0 flex-1 truncate">{{ narrationPreviewOf(item) }}</span>
                    <span class="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400">{{ narrationCharCountOf(item) }} 字</span>
                    <svg class="h-3 w-3 shrink-0 transition-transform" :class="{ 'rotate-180': isNarrationBodyOpen(item) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                    </svg>
                  </button>
                  <pre
                    v-show="isNarrationBodyOpen(item)"
                    class="w-fit max-w-full whitespace-pre-wrap break-words font-sans"
                  >{{ visibleTimelineText(item.content) }}<span v-if="item.pending" class="ml-0.5 animate-pulse">▌</span></pre>
                </template>
              </div>
            </div>
            <div v-if="item.children?.length" class="ml-5 mt-0.5 border-l border-gray-200/80 pl-1.5 dark:border-gray-700/80">
              <button
                type="button"
                class="mb-0 flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                :aria-expanded="isChildrenOpen(item)"
                @click="item.childrenExpanded = item.childrenExpanded === false"
              >
                <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-180': isChildrenOpen(item) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 9-7 7-7-7" />
                </svg>
                <span>{{ item.children.length }} 个工具调用</span>
              </button>
              <div v-if="isChildrenOpen(item)" class="space-y-0">
                <div
                  v-for="child in item.children"
                  :key="child.id"
                  class="rounded-md px-1 py-0.5 text-[11px] leading-5 transition-colors"
                  :class="{
                    'subagent-branch': isSubagentLog(child),
                    'bg-red-50/60 text-red-700 dark:bg-red-950/20 dark:text-red-300': child.status === 'error',
                    'bg-amber-50/60 text-amber-700 dark:bg-amber-950/20 dark:text-amber-300': child.status === 'warning',
                    'text-gray-600 dark:text-gray-300': child.status === 'pending',
                    'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': child.status !== 'pending' && child.status !== 'error' && child.status !== 'warning',
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
                      v-if="parallelBatchLabel(child)"
                      class="shrink-0 rounded bg-gray-100 px-1 font-mono text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400"
                      title="同一批并行执行的委派"
                    >{{ parallelBatchLabel(child) }}</span>
                    <span
                      v-if="subagentScaleLabel(child)"
                      class="shrink-0 font-mono text-[10px] text-gray-400 dark:text-gray-500"
                      :title="`子代理内部共 ${child.children?.length || 0} 个步骤`"
                    >{{ subagentScaleLabel(child) }}</span>
                    <span
                      v-if="child.subagent && subagentStatusLabel(child.status)"
                      class="shrink-0 text-[10px]"
                      :class="child.status === 'error' ? 'text-red-600' : 'text-gray-400 dark:text-gray-500'"
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
                  <div v-if="child.children?.length && child.childrenExpanded !== false" class="tl-branch ml-4 mt-0.5 space-y-0 pl-2">
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
                        @click="subStep.details || hasTimelineArgs(subStep) ? toggleChildItem(subStep) : undefined"
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
                          <div
                            v-if="subStep.reasoning"
                            class="reasoning-md border-l-2 border-gray-200 pl-2.5 pr-6 text-gray-600 dark:border-gray-700 dark:text-gray-400"
                          >
                            <div class="reasoning-md-body" v-html="reasoningDetailsHtml(subStep.details)" />
                          </div>
                          <pre v-else class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(subStep.details) }}</pre>
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
                      <div
                        v-if="child.reasoning"
                        class="reasoning-md mt-1 border-l-2 border-gray-200 pl-2.5 pr-6 text-gray-600 dark:border-gray-700 dark:text-gray-400"
                      >
                        <div class="reasoning-md-body" v-html="reasoningDetailsHtml(child.details)" />
                      </div>
                      <pre v-else class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(child.details) }}</pre>
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
              'bg-amber-50/60 text-amber-700 dark:bg-amber-950/20 dark:text-amber-300': item.status === 'warning',
              'text-gray-600 dark:text-gray-300': item.status === 'pending',
              'text-gray-400 hover:bg-gray-50 dark:text-gray-500 dark:hover:bg-gray-800/40': item.status !== 'pending' && item.status !== 'error' && item.status !== 'warning',
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
              <!-- 根级别的委派容器同样要标并行位置与规模：主代理没有旁白时容器就落在这一层 -->
              <span
                v-if="parallelBatchLabel(item)"
                class="shrink-0 rounded bg-gray-100 px-1 font-mono text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400"
                title="同一批并行执行的委派"
              >{{ parallelBatchLabel(item) }}</span>
              <span
                v-if="subagentScaleLabel(item)"
                class="shrink-0 font-mono text-[10px] text-gray-400 dark:text-gray-500"
                :title="`子代理内部共 ${item.children?.length || 0} 个步骤`"
              >{{ subagentScaleLabel(item) }}</span>
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
                :class="item.status === 'error' ? 'text-red-600 dark:text-red-400' : 'text-gray-400 dark:text-gray-500'"
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
                :class="item.status === 'error' ? 'text-red-600' : 'text-gray-400 dark:text-gray-500'"
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
                  class="h-full rounded-full bg-gray-400 transition-[width] duration-300 ease-out dark:bg-gray-500"
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
            <div v-if="item.children?.length && isChildrenOpen(item)" class="tl-branch ml-4 mt-0.5 space-y-0 pl-2">
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
                <div v-if="subStep.children?.length && subStep.childrenExpanded !== false" class="tl-branch ml-4 mt-0.5 space-y-0 pl-2">
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
                      @click="hasVisibleTimelineText(nestedStep.details) || hasTimelineArgs(nestedStep) ? toggleChildItem(nestedStep) : undefined"
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
                    <div
                      v-if="subStep.reasoning"
                      class="reasoning-md border-l-2 border-gray-200 pl-2.5 pr-6 text-gray-600 dark:border-gray-700 dark:text-gray-400"
                    >
                      <div class="reasoning-md-body" v-html="reasoningDetailsHtml(subStep.details)" />
                    </div>
                    <pre v-else class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(subStep.details) }}</pre>
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
                <div
                  v-if="item.reasoning"
                  class="reasoning-md mt-1 border-l-2 border-gray-200 pl-2.5 pr-6 text-gray-600 dark:border-gray-700 dark:text-gray-400"
                >
                  <div class="reasoning-md-body" v-html="reasoningDetailsHtml(item.details)" />
                </div>
                <pre v-else class="whitespace-pre-wrap break-words pr-6 font-mono text-[10px] leading-relaxed text-gray-500 dark:text-gray-400">{{ visibleTimelineText(item.details) }}</pre>
              </div>
            </div>
          </div>
        </div>

        <div v-if="isThinking && !items.length" class="px-2 py-1 text-xs text-gray-400 animate-pulse">
          {{ thinkingText || '正在处理…' }}
        </div>
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
import { renderMarkdown } from "@/utils/markdown";
import {
  mergeReasoningRounds,
  mergedReasoningLabel,
  normalizeReasoningDensity,
  type ReasoningDensity,
} from "@/utils/reasoningDensity";
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
  timelineHasPending,
  PREPARATION_TIMELINE_PARENT_ID,
  isWorkspacePrewarmPending,
  type FileToolMetadata,
  type ProcessTimelineItem,
  type ProcessTimelineLogItem,
  type ProcessTimelineTextItem,
  resolveTimelineExpanded,
  shouldCollapsePreparationChildren,
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
    tool_args?: string;
    /** 模型为本次调用写的意图摘要（沙箱 bash 的 description）。 */
    tool_summary?: string;
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
  /**
   * 「思考过程默认展开」用户设置（界面设置 → 思考过程）。
   *
   * 传了值就**完全跟随**：开启则整张卡片始终展开（生成中也展开，能实时看到执行
   * 明细），关闭则始终折叠，只留标题行。不传（undefined）退回旧的自动行为——
   * 生成中展开、有正文即折叠，供 AgentDebug 这类不接该设置的调用方使用。
   * 无论哪种情况，用户手动点过标题之后都只认用户操作。
   */
  expandThoughts?: boolean;
  /** 思考密度：详细 / 标准 / 精简。未传按标准（与改造前行为一致）。 */
  reasoningDensity?: ReasoningDensity;
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
  toolTimeoutSeconds: 0,
  promptTokens: 0,
  completionTokens: 0,
});

const expanded = defineModel<boolean>("expanded", { default: false });
/**
 * 用户是否手动切换过本条消息的展开状态。
 *
 * 一旦置位，下面两个自动 watch 全部让位：生成中不再强制展开，有正文/完成也不再
 * 强制折叠。唯一置位入口是 `ChatThinkingHeader` 的 userToggle 事件，watch 内部
 * 对 `expanded.value` 的自动写入不会走到这里，所以「自动决策」与「用户选择」
 * 能真正区分开。
 */
const userToggledExpanded = ref(false);
const routeGroupExpanded = ref(true);

const emit = defineEmits<{
  (e: "stop"): void;
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
  // 用原型投影替换 `{ ...item, children }`：展开运算符会把 progress 这类字段也登记成
  // timelineItems 的依赖，导致每条工具进度都触发整棵时间线重算。
  return items.flatMap((item): ProcessTimelineItem[] => {
    if (item.kind === "log" && item.category === "permission") return [];
    const children = item.children;
    if (children?.length) {
      const suppressed = suppressPermissionLogs(children as ProcessTimelineItem[]);
      if (suppressed !== children) {
        const projection = Object.create(item) as ProcessTimelineItem;
        projection.children = suppressed;
        return [projection];
      }
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

/**
 * 模型调用（`category=model`）是观测信息，不是执行步骤：每次 LLM 请求一条，
 * 又固定堆在顶层数组末尾，留在列表里就是「工具全在上、几十条模型调用全在下」。
 *
 * 这里直接把它挡在渲染列表之外——注意不是「先显示、完成后再移除」，
 * 而是从来就不进 `items`，所以 v-for 的列表长度只随工具/旁白变化，
 * 不会出现「入队 → 出队」的增删抖动（那才是之前拖死页面的原因）。
 *
 * fallback 换模提示同属 model 类别，但它是用户必须看见的事件，保留。
 */
const isHiddenModelCall = (item: ProcessTimelineItem): boolean =>
  item.kind === "log"
  && item.category === "model"
  && item.status !== "warning"
  && !/fallback/i.test(String(item.title || ""));

/** 精简档下用户点了汇总行：临时取消合并，把各轮还原成一行行摘要。 */
const compactRoundsOpen = ref(false);

const densityMode = computed(() => normalizeReasoningDensity(props.reasoningDensity));

const items = computed(() => {
  const base = groupRouteTimelineItems(timelineItems.value, routeGroupExpanded.value)
    .filter((item) => !isHiddenModelCall(item));
  // 只在精简档合并。合并行本身不随展开消失——展开了还留着它，用户才能再收回去。
  if (densityMode.value !== "compact") return base;
  return mergeReasoningRounds(base);
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
  || timelineItems.value.length
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
    if (it.kind === "text") {
      if (it.textKind === "reasoning" || it.children?.length) return true;
      return narrationNeedsCollapse(it);
    }
    if (it.kind === "log") return hasVisibleTimelineText(it.details) || hasTimelineArgs(it) || it.children?.length;
    return false;
  });
});

function toggleAllDetails() {
  isAllDetailsExpanded.value = !isAllDetailsExpanded.value;
  const target = isAllDetailsExpanded.value;
  // 精简档的合并行不受 contentExpanded 支配（正文挂在汇总行下面），
  // 必须单独跟随，否则点了「全部展开明细」思考纹丝不动。
  compactRoundsOpen.value = target;
  function setExpand(it: any) {
    if (it.kind === "text") {
      if (it.textKind === "reasoning" || narrationNeedsCollapse(it)) it.contentExpanded = target;
      if (it.children?.length) it.childrenExpanded = target;
    } else if (it.kind === "log") {
      it.isExpanded = target;
      if (it.children?.length) it.childrenExpanded = target;
      // 「全部展开/收起」是明确的用户意图，一并保护起来：否则随后跑完的工具
      // 会被自动折叠掉，和刚点下的「全部展开」自相矛盾。
      it.userToggled = true;
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

/**
 * 折叠决策只有一处，避免多条 watch 互相覆盖（历史上正是三个 watch 各自写
 * `expanded`，把用户的「思考过程」设置彻底盖掉了）。
 *
 * 两条规则，顺序不能换：
 * 1. **生成完就折叠**——有正文且没有待办步骤时一律收成标题行。「思考过程」开关
 *    不参与这条判断，它只管生成过程中要不要摊开看进度。
 * 2. **生成中听设置**——开关有值就按开关（开=展开看推理链、关=只留标题行），
 *    没设置则默认展开看进度。
 *
 * 用户手动点过标题（userToggle）后一律让位，直到本条消息被重新创建。
 */
watch(
  [hasPending, () => props.hasAnswer, () => props.expandThoughts],
  () => {
    // 决策本身在 `resolveTimelineExpanded` 里，返回 null 表示用户手动点过、不要改。
    const next = resolveTimelineExpanded({
      hasAnswer: Boolean(props.hasAnswer),
      hasPending: hasPending.value,
      expandThoughts: props.expandThoughts,
      userToggled: userToggledExpanded.value,
    });
    if (next !== null) expanded.value = next;
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
  return isReasoningContentExpanded(item, { density: densityMode.value });
}

/**
 * 子代理思考的详情渲染成 Markdown。
 *
 * 后端把它作为 `category="reasoning"` 的日志下发，若仍用工具输出那套 `<pre>` + mono 字体，
 * 星号、列表、围栏一样会裸露——和主代理思考正文是同一个问题。
 * 结果按原文缓存：时间线的秒表 tick 每 500ms 重渲整棵树，同一段思考不该被反复转 markdown。
 */
const reasoningDetailsCache = new Map<string, string>();
function reasoningDetailsHtml(text?: string | null): string {
  const source = visibleTimelineText(text);
  if (!source) return "";
  const cached = reasoningDetailsCache.get(source);
  if (cached !== undefined) return cached;
  const html = renderMarkdown(source);
  if (reasoningDetailsCache.size > 200) reasoningDetailsCache.clear();
  reasoningDetailsCache.set(source, html);
  return html;
}

function toggleCompactRounds(): void {
  compactRoundsOpen.value = !compactRoundsOpen.value;
}

/** 折叠态摘要；pending 时跟随最新内容。内部上下文块剥离沿用 visibleTimelineText。 */
function reasoningPreviewOf(item: ProcessTimelineTextItem): ReasoningPreview {
  return resolveReasoningPreview(visibleTimelineText(item.content), { pending: Boolean(item.pending) });
}

/** 折叠态字数胶囊口径，与摘要同源。 */
function reasoningCharCountOf(item: ProcessTimelineTextItem): number {
  return reasoningCharCount(visibleTimelineText(item.content));
}

/**
 * 超过该字数的思考正文默认限高。
 *
 * 阈值取 2000 而不是 1200：实测单轮「深度思考」常见就在 1000~1300 字，按 1200 拦等于
 * 每一轮都要点两次才能读完（点开思考 → 再点展开全部），把一个偶发保护变成固定负担。
 * 真正要拦的是几千字的超长思考——那种才是把链路撑成好几屏的原因。
 */
const REASONING_CLAMP_CHAR_THRESHOLD = 2000;

/** 已手动「展开全部」的思考项，按 id 记在组件内。 */
const reasoningFullExpanded = ref<Set<string>>(new Set());

/**
 * 思考正文是否限高。
 *
 * 三条约束：
 * 1. **进行中不限高**——流式最后一段还在逐字增长，套固定高度会让新打出来的字落在可视区外，
 *    与旁白「不做限高内滚」同源（见 isNarrationBodyOpen 注释）；
 * 2. 只在超长时拦一道，短思考点开即读全，不做「点开一行 → 再点展开」两次点击；
 * 3. 展开状态记在组件里，不写回时间线对象——时间线在流式合并与历史恢复时会被重建，
 *    写回的字段会被冲掉，表现成「点了展开又收回去」。
 */
function isReasoningClamped(item: ProcessTimelineTextItem): boolean {
  if (item.textKind !== "reasoning") return false;
  if (item.pending) return false;
  if (reasoningFullExpanded.value.has(String(item.id))) return false;
  return reasoningCharCountOf(item) > REASONING_CLAMP_CHAR_THRESHOLD;
}

function expandReasoningFull(item: ProcessTimelineTextItem): void {
  const next = new Set(reasoningFullExpanded.value);
  next.add(String(item.id));
  reasoningFullExpanded.value = next;
}

/**
 * 思考正文的 HTML。
 *
 * 已落定的内容做结果缓存：折叠别的 item、工具状态变化都会触发整条时间线重渲染，
 * 不缓存就每次都重跑一遍 markdown-it。进行中不缓存——内容还在长，缓存会锁在旧版本。
 */
const reasoningHtmlCache = new WeakMap<ProcessTimelineTextItem, { source: string; html: string }>();

/**
 * 把流式光标塞进最后一个块级元素的**内部**末尾。
 *
 * 直接拼在 `</p>` 后面时，光标是行内元素却被放在块级元素之外，浏览器会另起一行渲染，
 * 看起来像正文下面多了一条不断闪动的竖线。
 */
function withReasoningCaret(html: string): string {
  const caret = '<span class="reasoning-caret">▌</span>';
  const closeIndex = html.lastIndexOf("</p>");
  if (closeIndex < 0) return `${html}${caret}`;
  return `${html.slice(0, closeIndex)}${caret}${html.slice(closeIndex)}`;
}

function reasoningHtml(item: ProcessTimelineTextItem): string {
  const source = visibleTimelineText(item.content);
  if (item.pending) {
    return withReasoningCaret(renderMarkdown(source));
  }
  const cached = reasoningHtmlCache.get(item);
  if (cached && cached.source === source) return cached.html;
  const html = renderMarkdown(source);
  reasoningHtmlCache.set(item, { source, html });
  return html;
}

/**
 * 旁白（process_narration）的折叠阈值。
 *
 * 旁白是模型的「我准备做什么」，一次任务可能产出七八段；平铺会把时间线撑得很长。
 * 短旁白保持原样直接展示（不需要点开就能读完），超过任一阈值才默认收起，
 * 收起态给一行摘要 + 字数，点标题行展开——与「深度思考」的交互保持一致。
 */
const NARRATION_COLLAPSE_CHAR_THRESHOLD = 120;
const NARRATION_COLLAPSE_LINE_THRESHOLD = 3;

function narrationTextOf(item: ProcessTimelineTextItem): string {
  return visibleTimelineText(item.content);
}

function narrationNeedsCollapse(item: ProcessTimelineTextItem): boolean {
  const text = narrationTextOf(item);
  if (!text) return false;
  if (text.length > NARRATION_COLLAPSE_CHAR_THRESHOLD) return true;
  const lineCount = text.split(/\r?\n/).filter((line) => line.trim()).length;
  return lineCount > NARRATION_COLLAPSE_LINE_THRESHOLD;
}

function narrationCharCountOf(item: ProcessTimelineTextItem): number {
  return narrationTextOf(item).length;
}

/** 收起态的一行摘要：压平换行后截断，避免摘要本身又占两行。 */
function narrationPreviewOf(item: ProcessTimelineTextItem): string {
  const text = narrationTextOf(item).replace(/\s+/g, " ").trim();
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/**
 * 未手动切换过时一律展开。
 *
 * 原来「超过 120 字默认收起」，理由是防止旁白占屏。但被撤回的长旁白往往是
 * **答案级**内容（模型先写了一段，随后又调了工具，这段就被降级成过程旁白），
 * 收起来等于让用户在答案区找不到它。现在默认展开、由用户主动收起。
 *
 * 这里**不做限高内滚**：流式时最后一条旁白还在逐字增长，套一个固定高度的滚动盒
 * 会让新打出来的字落在可视区外，用户看不到打字过程；而且这段内容在模型
 * 不继续调工具时会直接被提升为正文，本就该按正文的完整形态展示。
 */
function isNarrationBodyOpen(item: ProcessTimelineTextItem): boolean {
  return item.contentExpanded !== false;
}

/**
 * 委派容器判定：与 processTimeline 里 reorganizeSubagentItems 的口径保持一致。
 *
 * 五个条件缺一不可地覆盖了历史数据：老记录只有标题、新记录才带 subagent 元数据，
 * 批量委派又是另一个 tool_name。任何一条漏掉，那条委派就会退化成普通工具行。
 */
function isSubagentLog(log: ProcessTimelineLogItem): boolean {
  const id = String(log.id || "");
  const title = log.title || "";
  return id.startsWith("subagent_")
    || log.tool_name === "sub_agent_call"
    || log.tool_name === "sub_agent_batch_call"
    || title.includes("调用子代理")
    || title.includes("委派智能体")
    || Boolean(log.subagent);
}

function isSubagentContainer(item: ProcessTimelineItem): boolean {
  return item.kind === "log" && isSubagentLog(item);
}

/**
 * 委派头行的规模摘要：「4 步 · 2 次工具」。
 *
 * 规模原先只在展开后看得到（内部那张明细），头行只剩「谁 + 多久」——
 * 判断「这次委派是不是干了件大事」得先展开再数，多一次交互。
 * 步数等于工具数时只写「N 步」，避免「4 步 · 4 次工具」这种同义重复。
 */
/**
 * 并行委派标记：「并行 2/3」。
 *
 * `sub_agent_batch_call` 派出的几个子代理是**同时**跑的，但界面上原本一条条平铺，
 * 读起来像依次执行——一个序号就能纠正这个误读，且不用为它套一层分组节点
 * （分组会把这几张容器降级成普通子步骤，丢掉各自的展开态与耗时）。
 */
function parallelBatchLabel(log: ProcessTimelineLogItem): string {
  const meta = log.subagent;
  if (!meta?.batch_id) return "";
  const total = Number(meta.batch_total || 0);
  if (!Number.isFinite(total) || total < 2) return "";
  const index = Number(meta.batch_index || 0);
  return `并行 ${index + 1}/${total}`;
}

function subagentScaleLabel(log: ProcessTimelineLogItem): string {
  if (!isSubagentLog(log)) return "";
  const children = log.children || [];
  if (!children.length) return "";
  const parts = [`${children.length} 步`];
  const toolCount = children.filter((child) => isToolTimelineItem(child)).length;
  if (toolCount > 0 && toolCount < children.length) parts.push(`${toolCount} 次工具`);
  return parts.join(" · ");
}

/**
 * 轨道节点的形状类。
 *
 * 只区分形状、不区分色相：思考圆点、工具方块、旁白小点、委派菱形。
 * 颜色在浅色与深色两套主题下都要成立，饱和色总会在其中一套里失衡，所以整条链只走灰阶，
 * 由形状 + 缩进承担分层（见 style 里的 .timeline-rail 注释）。
 */
function timelineNodeClass(item: ProcessTimelineItem): string {
  if (item.kind === "todo") return "tl-node tl-node-todo";
  if (item.kind === "text") {
    return item.textKind === "reasoning" ? "tl-node tl-node-think" : "tl-node tl-node-say";
  }
  if (item.status === "error") return "tl-node tl-node-fail";
  if (isSubagentContainer(item)) return "tl-node tl-node-agent";
  return "tl-node tl-node-tool";
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

/**
 * 子项此刻是否展开。
 *
 * 准备父级**默认收起**、只在出错/中断或用户手动点开时展开。收起判定必须从第一帧
 * 就成立，否则树先摊开、跑完再收起，视觉上会闪一下。
 *
 * 判定是**派生值**（见 `shouldCollapsePreparationChildren`），不是把
 * `childrenExpanded` 改成 false 的一次性副作用——时间线对象在流式合并与历史恢复时
 * 会被重建，一次性标志会被冲掉，表现为「跑完了还摊着」。
 */
function isChildrenOpen(item: ProcessTimelineLogItem): boolean {
  if (
    shouldCollapsePreparationChildren({
      isPreparation: isPreparationParent(item),
      userToggled: item.userToggled,
      status: item.status,
    })
  ) {
    return false;
  }
  return item.childrenExpanded !== false;
}

function isTimelineItemExpanded(item: ProcessTimelineLogItem): boolean {
  if (isRouteGroup(item)) return routeGroupExpanded.value;
  return item.children?.length ? isChildrenOpen(item) : item.isExpanded === true;
}

function isTimelineItemDetailsOpen(item: ProcessTimelineLogItem): boolean {
  if (item.children?.length) {
    return item.isExpanded === true || isChildrenOpen(item);
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
  // 手动点过就标记：执行完的自动折叠会跳过它，避免正看着结果时被收走。
  item.userToggled = true;
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
  // 同上：子层也要标记，否则只有顶层被保护、子步骤照样在跑完后被收起。
  child.userToggled = true;
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
  // 子代理思考用 CpuChip（与主代理思考同图标），避免被当成一次工具调用读过去。
  if (item.reasoning) return CpuChipIcon;
  if (item.category === "tool_resolution") return item.status === "error" ? ExclamationTriangleIcon : MapIcon;
  if (item.status === "error") return ExclamationTriangleIcon;
  // 降级/重置这类提醒既不是错误也不是普通步骤：默认气泡图标会让人以为只是又一步。
  if (item.status === "warning") return ExclamationTriangleIcon;
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
  // 优先这条调用自己的生效超时：工具级可能把 180s 收紧成 10s，
  // 只显示本轮快照会让人以为工具级没生效。字段缺失才回退快照。
  const own = Number(item.timeout_seconds);
  if (Number.isFinite(own) && own > 0) return own;
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
/* ===== 执行链路轨道 =====
   一条竖线 + 四种形状的节点。要解决的是「这一段是思考还是工具、层级在哪」——
   原先所有条目同字号同灰度平铺，只能靠逐行读文字判断。
   刻意**只用灰阶**、不用色相：彩色需要在浅色/深色两套主题里各调一次且总有一套失衡，
   而形状 + 缩进天然与主题无关。深浅两套灰由模板上的 --tl-* 变量注入（scoped 里不能写
   `:global(.dark)`，契约 test_embed_theme_sync_contract 有反向锁）。 */
.timeline-rail {
  position: relative;
  padding-left: 1.1rem;
}
.timeline-rail::before {
  content: "";
  position: absolute;
  left: 0.24rem;
  top: 0.45rem;
  bottom: 0.55rem;
  width: 1px;
  /* 半透明中性灰：叠在白卡片上是浅灰，叠在深色卡片上是深灰，一套值覆盖两套主题。
     用主题变量要各调一次，用任意属性类又要 Tailwind 生成规则（本项目没有先例），
     半透明是这里最省事也最不会在某个主题下失衡的写法。 */
  background: rgba(127, 127, 127, 0.24);
}
/* 实心点直接压在竖线上：不需要描边，也就不用猜底层是白卡片还是深色卡片。 */
.timeline-rail > .tl-node::before {
  content: "";
  position: absolute;
  z-index: 1;
  left: -1.04rem;
  top: 0.44rem;
  width: 0.36rem;
  height: 0.36rem;
  border-radius: 9999px;
  background: rgba(127, 127, 127, 0.52);
}
/* 思考节点：默认形状，其余类型在此基础上改。 */
.timeline-rail > .tl-node-think::before {
  background: rgba(127, 127, 127, 0.52);
}
.timeline-rail > .tl-node-tool::before {
  left: -1.02rem;
  top: 0.46rem;
  width: 0.32rem;
  height: 0.32rem;
  border-radius: 1px;
  background: rgba(127, 127, 127, 0.4);
}
.timeline-rail > .tl-node-say::before {
  left: -0.97rem;
  top: 0.52rem;
  width: 0.22rem;
  height: 0.22rem;
  background: rgba(127, 127, 127, 0.36);
}
.timeline-rail > .tl-node-agent::before {
  left: -1.01rem;
  top: 0.45rem;
  width: 0.3rem;
  height: 0.3rem;
  border-radius: 1px;
  transform: rotate(45deg);
  background: rgba(127, 127, 127, 0.4);
}
.timeline-rail > .tl-node-todo::before {
  background: rgba(127, 127, 127, 0.32);
}
/* 失败状态是全链路唯一的彩色：它需要被立刻看见，灰度做不到。 */
.timeline-rail > .tl-node-fail::before {
  background: rgb(239 68 68);
}

/* 嵌套层（工具的内部步骤、子步骤）沿用轨道同一套灰，不再另起一套色。 */
.tl-branch {
  border-left: 1px solid rgba(127, 127, 127, 0.2);
}

/* 委派分支：一条左侧竖线把子代理的执行范围框出来，头行仍是一行概要。
   与普通工具行的区别只靠「分支线 + 缩进 + 委派图标」，不靠颜色——
   颜色在深浅两套主题里各调一次，形状和缩进一次就够。 */
.subagent-branch {
  border-left: 2px solid rgba(127, 127, 127, 0.24);
  border-radius: 0 0.375rem 0.375rem 0;
  padding-left: 0.4rem;
  margin-left: -0.35rem;
}

/* ===== 深度思考正文：真实渲染 Markdown 后的排版 =====
   思考正文原先是一块 <pre>，模型吐出的 `**加粗**`、`- 列表`、```sql 围栏全部裸露。
   这里不再另起一套 markdown 管线，直接复用正文的 renderMarkdown（内部已 sanitize），
   只补排版样式。字号与正文对齐（0.75rem），避免「想起了才像正文、想完了就变脚注」。 */
.reasoning-md {
  font-size: 0.75rem;
  line-height: 1.75;
  /* 正文颜色由模板上的 text-gray-600 / dark:text-gray-400 给，这里只定排版。 */
  overflow-wrap: anywhere;
}

.reasoning-md-body :deep(p) { margin: 0 0 0.5rem; }
.reasoning-md-body :deep(p:first-child) { margin-top: 0; }
.reasoning-md-body :deep(p:last-child) { margin-bottom: 0; }
.reasoning-md-body :deep(ul),
.reasoning-md-body :deep(ol) { margin: 0 0 0.5rem; padding-left: 1.1rem; }
.reasoning-md-body :deep(ul) { list-style: disc; }
.reasoning-md-body :deep(ol) { list-style: decimal; }
.reasoning-md-body :deep(li) { margin: 0.1rem 0; }
.reasoning-md-body :deep(strong) { font-weight: 600; color: inherit; }
.reasoning-md-body :deep(em) { font-style: italic; }
.reasoning-md-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.92em;
  padding: 0.05rem 0.22rem;
  border-radius: 0.25rem;
  background: rgba(127, 127, 127, 0.13);
  color: inherit;
  overflow-wrap: anywhere;
}
.reasoning-md-body :deep(pre) {
  margin: 0 0 0.5rem;
  padding: 0.5rem 0.65rem;
  border-radius: 0.4rem;
  background: rgba(127, 127, 127, 0.07);
  border: 1px solid rgba(127, 127, 127, 0.16);
  overflow-x: auto;
  line-height: 1.6;
}
.reasoning-md-body :deep(pre code) {
  background: none;
  border: 0;
  padding: 0;
  font-size: 0.95em;
}
.reasoning-md-body :deep(a) {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.reasoning-md-body :deep(blockquote) {
  margin: 0 0 0.5rem;
  padding-left: 0.6rem;
  border-left: 2px solid rgba(127, 127, 127, 0.2);
}
.reasoning-md-body :deep(h1),
.reasoning-md-body :deep(h2),
.reasoning-md-body :deep(h3),
.reasoning-md-body :deep(h4) {
  font-size: 1em;
  font-weight: 600;
  color: inherit;
  margin: 0.6rem 0 0.3rem;
}
.reasoning-md-body :deep(table) {
  border-collapse: collapse;
  margin: 0 0 0.5rem;
  font-size: 0.95em;
}
.reasoning-md-body :deep(th),
.reasoning-md-body :deep(td) {
  border: 1px solid rgba(127, 127, 127, 0.16);
  padding: 0.15rem 0.4rem;
}
.reasoning-md-body :deep(hr) {
  border: 0;
  border-top: 1px solid rgba(127, 127, 127, 0.16);
  margin: 0.6rem 0;
}

/* 流式光标：跟随最后一段仍在增长的思考。 */
.reasoning-caret {
  margin-left: 0.15rem;
  animation: reasoning-caret-pulse 1s ease-in-out infinite;
}
@keyframes reasoning-caret-pulse {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}

/* 超长思考限高。
   用 mask 而不是白色渐变遮罩：遮罩要猜底层背景色（卡片在浅色/深色、是否嵌套在子代理块里
   背景都不同），猜错就是一条突兀的色带；mask 直接让文字自身淡出，与任何背景都不冲突。 */
.reasoning-md-clamped {
  position: relative;
  max-height: 14rem;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
  mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
}

/* 深色不再单独写一套：正文颜色走模板上的 dark:text-gray-400，其余结构色（代码底、
   引用线、表格线）一律半透明中性灰——同一套值在深浅两套主题下都成立。
   这样也顺带避开了契约禁止的 `:global(.dark)`（会被编译成命中所有 .dark 元素的全局规则，
   是「深色白雾」的成因）。 */

@media (prefers-reduced-motion: reduce) {
  .reasoning-caret { animation: none; opacity: 1; }
}

/* 远端没给 total 时的「不确定态」：一条两端淡出的光带单向滑过整条轨道。
   两端都是透明的，循环接缝处看不出跳变；旧写法用 margin-left 在 0↔86% 来回跑，
   在窄轨道里就是一条横跳的实心色块，观感像闪烁。 */
.timeline-progress-indeterminate > div {
  width: 38%;
  background-color: transparent;
  background-image: linear-gradient(
    90deg,
    rgba(127, 127, 127, 0) 0%,
    rgba(127, 127, 127, 0.45) 45%,
    rgba(127, 127, 127, 0.62) 55%,
    rgba(127, 127, 127, 0) 100%
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
