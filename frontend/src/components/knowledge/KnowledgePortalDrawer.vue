<template>
  <teleport to="body">
    <div
      v-show="modelValue"
      :class="[
        'z-[120]',
        pinned && isMobile
          ? 'fixed inset-x-0 bottom-0 max-w-full flex flex-col justify-end pointer-events-none'
          : pinned
            ? 'fixed inset-y-0 right-0 max-w-full flex pointer-events-none'
            : isMobile
              ? 'fixed inset-0 flex flex-col overflow-hidden'
              : 'fixed inset-0 overflow-hidden',
      ]"
    >
      <transition
        v-if="!pinned"
        enter-active-class="ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-show="modelValue"
          :class="[
            'bg-gray-500/30 backdrop-blur-xs transition-opacity',
            isMobile ? 'flex-1 min-h-0 w-full' : 'absolute inset-0',
          ]"
          @click="closeDrawer"
        />
      </transition>

      <div
        :class="[
          pinned
            ? isMobile
              ? 'w-full flex pointer-events-auto min-h-0 max-h-[58%]'
              : 'h-full flex pointer-events-auto'
            : isMobile
              ? 'w-full flex justify-center min-h-0 max-h-[92%] shrink-0'
              : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
        ]"
      >
        <transition
          enter-active-class="transform transition ease-in-out duration-300"
          :enter-from-class="sheetEnterFrom"
          enter-to-class="translate-x-0 translate-y-0"
          leave-active-class="transform transition ease-in-out duration-300"
          leave-from-class="translate-x-0 translate-y-0"
          :leave-to-class="sheetLeaveTo"
        >
          <div
            v-show="modelValue"
            :class="[
              'bg-white dark:bg-gray-900 shadow-2xl flex flex-col relative z-10 min-h-0 pb-[env(safe-area-inset-bottom,0px)]',
              isMobile
                ? 'w-full max-w-none rounded-t-2xl border-t border-gray-200 dark:border-gray-800 h-full max-h-full'
                : 'w-screen max-w-[min(100vw,28rem)] h-full border-l border-gray-200 dark:border-gray-800',
            ]"
          >
            <!-- Drawer pull handle for mobile -->
            <div
              v-if="isMobile"
              class="shrink-0 flex justify-center pt-2 pb-1"
              aria-hidden="true"
            >
              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
            </div>

            <!-- Header -->
            <div
              class="shrink-0 px-4 py-3 sm:py-4 border-b border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 flex items-center justify-between gap-2"
            >
              <span class="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5 select-none min-w-0">
                <svg class="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
                <span class="truncate">知识库中心</span>
                <span
                  v-if="pinned"
                  class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide text-green-600 bg-green-50 border border-green-100 dark:text-green-300 dark:bg-green-500/10 dark:border-green-500/20"
                >
                  已钉住
                </span>
              </span>
              <div class="flex items-center gap-2 flex-shrink-0">
                <label
                  class="hidden sm:flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400 cursor-pointer select-none whitespace-nowrap"
                  title="开启后点击推荐问题不会关闭抽屉，可连续提问"
                >
                  <input
                    v-model="keepOpenOnQuestion"
                    type="checkbox"
                    class="rounded border-gray-300 text-primary focus:ring-primary/30"
                  />
                  提问后保持
                </label>
                <button
                  type="button"
                  class="hidden sm:inline-flex text-gray-400 hover:text-green-600 dark:hover:text-green-400 p-1 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  :class="{ 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-500/10': pinned }"
                  :title="pinned ? '取消钉住' : '钉住侧栏'"
                  @click="pinned = !pinned"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 17v5M9 10.76a2 2 0 01-1.11 1.79l-1.78.9A2 2 0 005 15.24V16a1 1 0 001 1h12a1 1 0 001-1v-.76a2 2 0 00-1.11-1.79l-1.78-.9A2 2 0 0115 10.76V7a1 1 0 00-1-1h-4a1 1 0 00-1 1v3.76" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 p-1.5 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  title="关闭 (Esc)"
                  @click="closeDrawer"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

             <!-- Content List -->
            <div class="flex-1 overflow-y-auto p-4 bg-gray-50/50 dark:bg-gray-900/40 min-h-0 space-y-3 scrollbar-thin">
              <!-- Overview Card -->
              <div class="bg-white dark:bg-gray-800/80 backdrop-blur-xs border border-gray-150 dark:border-gray-800 rounded-xl p-3 flex flex-row items-center justify-between gap-2 shadow-xs select-none">
                <div class="flex items-center gap-2.5 min-w-0">
                  <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-green-600 dark:bg-green-500 text-white shadow-sm flex-shrink-0 text-xs font-bold">
                    📚
                  </div>
                  <div class="min-w-0">
                    <h3 class="text-xs font-bold text-gray-900 dark:text-gray-100 tracking-wide">我的知识库中心</h3>
                    <div class="flex flex-wrap items-center gap-1.5 mt-0.5 text-[9px]">
                      <span class="font-semibold text-green-600 dark:text-green-400">
                        {{ datasets.length }} 个知识库
                      </span>
                      <template v-if="generatedAt">
                        <span class="text-gray-300 dark:text-gray-700">|</span>
                        <span class="text-gray-500 dark:text-gray-400">更新 {{ generatedAt }}</span>
                      </template>
                    </div>
                  </div>
                </div>
                
                <div class="flex items-center gap-1.5 flex-shrink-0">
                  <!-- 🔍 搜索折叠切换按钮 -->
                  <button
                    type="button"
                    class="w-7 h-7 flex items-center justify-center rounded-lg border border-transparent transition-all cursor-pointer active:scale-90"
                    :class="showSearchBar
                      ? 'bg-green-50 text-green-600 dark:bg-green-955/40 dark:text-green-400 font-bold'
                      : 'bg-gray-50 text-gray-500 dark:bg-gray-800 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-750'"
                    title="展开/折叠搜索与标签过滤"
                    @click="showSearchBar = !showSearchBar"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                  </button>

                  <button
                    type="button"
                    class="w-7 h-7 flex items-center justify-center rounded-lg border border-transparent bg-gray-50 text-gray-500 hover:bg-gray-100 hover:text-gray-750 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-750 dark:hover:text-gray-200 transition-all cursor-pointer active:scale-90"
                    title="刷新知识库列表"
                    :disabled="loading"
                    @click="emit('refresh')"
                  >
                    <svg 
                      class="w-3.5 h-3.5"
                      :class="{ 'animate-spin': loading }"
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                    </svg>
                  </button>
                </div>
              </div>

              <!-- Search and Filter Bar -->
              <transition
                enter-active-class="transition-all duration-300 ease-out"
                enter-from-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
                enter-to-class="transform opacity-100 translate-y-0 max-h-[120px] overflow-hidden"
                leave-active-class="transition-all duration-200 ease-in"
                leave-from-class="transform opacity-100 translate-y-0 max-h-[120px] overflow-hidden"
                leave-to-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
              >
                <div v-show="showSearchBar" class="space-y-2">
                  <div class="relative">
                    <span class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none text-gray-400 dark:text-gray-500">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                    </span>
                    <input
                      v-model="searchQuery"
                      type="text"
                      placeholder="搜索分类标签、名称或描述..."
                      class="w-full pl-9 pr-8 py-1.5 text-[11px] rounded-xl border border-gray-150 dark:border-gray-800 bg-white dark:bg-gray-900/30 text-gray-850 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-green-500 focus:ring-1 focus:ring-green-500 transition-all shadow-xs"
                    />
                    <button
                      v-if="searchQuery"
                      type="button"
                      class="absolute inset-y-0 right-0 flex items-center pr-2.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
                      @click="searchQuery = ''"
                    >
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>

                  <div v-if="allTags.length" class="relative">
                    <div class="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1 -mx-0.5 scroll-smooth">
                      <button
                        type="button"
                        class="px-2.5 py-1 text-[9px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95 select-none"
                        :class="selectedTag === 'All'
                          ? 'bg-green-600 border-transparent text-white shadow-xs'
                          : 'bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                        @click="selectedTag = 'All'"
                      >
                        全部
                      </button>
                      <button
                        v-for="tag in allTags"
                        :key="tag"
                        type="button"
                        class="px-2.5 py-1 text-[9px] font-bold rounded-lg border transition-all whitespace-nowrap cursor-pointer active:scale-95 select-none"
                        :class="selectedTag === tag
                          ? 'bg-green-600 border-transparent text-white shadow-xs'
                          : 'bg-gray-50 dark:bg-gray-800/40 border-gray-150 dark:border-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                        @click="selectedTag = tag"
                      >
                        {{ tag }}
                      </button>
                    </div>
                  </div>
                </div>
              </transition>

              <!-- ⚙️ 高级选项 (Collapsible) -->
              <div class="rounded-xl border border-gray-150 dark:border-gray-800 bg-gray-50/20 dark:bg-gray-900/10 p-3 space-y-2.5 transition-all duration-300">
                <div
                  class="flex items-center justify-between w-full text-[10px] font-bold text-gray-500 hover:text-green-600 dark:text-gray-400 dark:hover:text-green-400 uppercase tracking-wider transition-colors select-none cursor-pointer"
                  @click="showAdvancedConfig = !showAdvancedConfig"
                >
                  <span class="flex items-center gap-1.5">
                    <span class="text-xs">⚙️</span> 高级配置
                  </span>
                  <svg
                    class="w-3.5 h-3.5 transform transition-transform duration-300 pointer-events-none"
                    :class="{ 'rotate-180': showAdvancedConfig }"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>

                <transition
                  enter-active-class="transition-all duration-300 ease-out"
                  enter-from-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
                  enter-to-class="transform opacity-100 translate-y-0 max-h-[380px] overflow-hidden"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="transform opacity-100 translate-y-0 max-h-[380px] overflow-hidden"
                  leave-to-class="transform opacity-0 -translate-y-2 max-h-0 overflow-hidden"
                >
                  <div v-show="showAdvancedConfig" class="space-y-3 pt-1 select-none">
                    <!-- 反幻觉检测胶囊开关 -->
                    <div class="flex items-center justify-between bg-white dark:bg-gray-800 p-2.5 rounded-lg border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex flex-col min-w-0">
                        <span class="text-[11px] font-bold text-gray-850 dark:text-gray-200 flex items-center gap-1.5">
                          启用反幻觉检测
                          <button type="button" @click.stop="toggleTooltip('hallucination')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="text-[9px] text-gray-400 dark:text-gray-500 mt-0.5">
                          二次核查回答与文献一致性
                        </span>
                      </div>
                      
                      <button
                        type="button"
                        class="relative inline-flex h-4.5 w-8 shrink-0 cursor-pointer rounded-full border border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
                        :class="[hallucinationCheck ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700']"
                        @click="hallucinationCheck = !hallucinationCheck"
                      >
                        <span
                          class="pointer-events-none inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                          :class="[hallucinationCheck ? 'translate-x-3.5' : 'translate-x-0']"
                        />
                      </button>
                    </div>

                    <transition
                      enter-active-class="transition-all duration-200 ease-out"
                      enter-from-class="opacity-0 max-h-0"
                      enter-to-class="opacity-100 max-h-[80px]"
                      leave-active-class="transition-all duration-150 ease-in"
                      leave-from-class="opacity-100 max-h-[80px]"
                      leave-to-class="opacity-0 max-h-0"
                    >
                      <div v-if="activeTooltip === 'hallucination'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                        开启后，系统将使用反幻觉大模型二次审视生成的回答是否完全忠实于事实文献，如存在偏差将自动重写。关闭可显著提升问答的响应速度。
                      </div>
                    </transition>

                    <!-- Similarity Threshold -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          相似度阈值
                          <button type="button" @click.stop="toggleTooltip('threshold')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ similarityThreshold }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'threshold'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          常规知识库检索时的相似度阈值（0.0 至 1.0）。低于此设定值的检索结果将被过滤，以防混入无关文档，推荐配置为 0.20。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="range"
                          v-model.number="similarityThreshold"
                          min="0.0"
                          max="1.0"
                          step="0.05"
                          class="flex-1 accent-green-600 h-1 bg-gray-200 dark:bg-gray-750 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div class="flex items-center justify-between text-[8px] text-gray-450 dark:text-gray-500 font-mono select-none">
                        <span>0.0 (无门槛)</span>
                        <span>0.5</span>
                        <span>1.0 (极严格)</span>
                      </div>
                    </div>

                    <!-- Vector Weight -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          语义权重占比
                          <button type="button" @click.stop="toggleTooltip('weight')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ vectorWeight }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'weight'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          常规知识库检索时向量相似度权重的占比（0.0 至 1.0），其余权重为全文关键词匹配。推荐配置为 0.30。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="range"
                          v-model.number="vectorWeight"
                          min="0.0"
                          max="1.0"
                          step="0.05"
                          class="flex-1 accent-green-600 h-1 bg-gray-200 dark:bg-gray-750 rounded-lg appearance-none cursor-pointer"
                        />
                      </div>
                      <div class="flex items-center justify-between text-[8px] text-gray-450 dark:text-gray-500 font-mono select-none">
                        <span>0.0 (纯关键词)</span>
                        <span>0.5</span>
                        <span>1.0 (纯向量)</span>
                      </div>
                    </div>

                    <!-- Metadata Top_K -->
                    <div class="space-y-1.5 p-2.5 rounded-lg bg-white dark:bg-gray-800 border border-gray-150 dark:border-gray-700/60 shadow-xxs">
                      <div class="flex items-center justify-between text-[11px] font-bold text-gray-850 dark:text-gray-200">
                        <span class="flex items-center gap-1.5">
                          最大召回分块数
                          <button type="button" @click.stop="toggleTooltip('top_k')" class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-green-500 hover:text-green-500 text-[9px] font-bold transition-all focus:outline-none select-none">?</button>
                        </span>
                        <span class="font-mono text-green-600 dark:text-green-400">{{ metadataTopK }}</span>
                      </div>

                      <transition
                        enter-active-class="transition-all duration-200 ease-out"
                        enter-from-class="opacity-0 max-h-0"
                        enter-to-class="opacity-100 max-h-[80px]"
                        leave-active-class="transition-all duration-150 ease-in"
                        leave-from-class="opacity-100 max-h-[80px]"
                        leave-to-class="opacity-0 max-h-0"
                      >
                        <div v-if="activeTooltip === 'top_k'" class="p-2 bg-gray-150 dark:bg-gray-800/80 rounded text-[9px] text-gray-500 dark:text-gray-400 leading-relaxed border border-gray-200/50 dark:border-gray-750">
                          最大检索召回的文档切片数量。数量越多，AI 可参考的事实越丰富，但也会增加生成时的上下文 Token 消耗。推荐范围：5 ~ 10。
                        </div>
                      </transition>

                      <div class="flex items-center gap-3">
                        <input
                          type="number"
                          v-model.number="metadataTopK"
                          min="1"
                          max="50"
                          class="w-full px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-850 dark:text-gray-100 focus:outline-none focus:border-green-500 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                </transition>
              </div>

              <!-- Loading State -->
              <div v-if="loading" class="space-y-3 py-6">
                <div v-for="i in 3" :key="i" class="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-100 dark:border-gray-800 animate-pulse space-y-3">
                  <div class="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/3"></div>
                  <div class="h-3 bg-gray-200 dark:bg-gray-700 rounded w-2/3"></div>
                </div>
              </div>

              <!-- Error State -->
              <div v-else-if="loadError" class="flex flex-col items-center justify-center py-20 text-gray-400 dark:text-gray-500 text-center px-6">
                <svg class="w-12 h-12 text-red-500 dark:text-red-600 mb-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
                <h4 class="text-xs font-bold text-gray-700 dark:text-gray-300">知识库服务不可用</h4>
                <p class="text-[11px] text-gray-400 dark:text-gray-500 mt-2 max-w-[18rem] leading-relaxed">
                  连接知识库引擎失败，服务可能正在维护或已离线。请稍后重试或联系管理员排查。
                </p>
                <button
                  type="button"
                  @click="emit('refresh')"
                  class="mt-4 px-3 py-1.5 text-[10px] font-medium bg-green-500 hover:bg-green-600 text-white rounded transition-colors active:scale-95 cursor-pointer"
                >
                  重新加载
                </button>
              </div>

              <!-- Empty State -->
              <div v-else-if="datasets.length === 0" class="flex flex-col items-center justify-center py-20 text-gray-400 dark:text-gray-500 text-center px-6">
                <svg class="w-12 h-12 text-gray-300 dark:text-gray-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 15v2m0 0v2m0-2h2m-2 0H8m13 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <h4 class="text-xs font-bold text-gray-700 dark:text-gray-300">暂无可用的知识库</h4>
                <p class="text-[11px] text-gray-400 dark:text-gray-500 mt-2 max-w-[18rem] leading-relaxed">
                  您当前可能没有已被分配的知识库权限。请联系管理员配置。
                </p>
              </div>

              <!-- Datasets Cards -->
              <div
                v-else
                v-for="ds in sortedDatasets"
                :key="ds.id"
                class="group/card relative overflow-hidden rounded-xl border p-3.5 sm:p-4 shadow-sm hover:shadow-lg hover:-translate-y-0.5 transition-all duration-300"
                :class="[
                  activeDatasetIds.includes(ds.id)
                    ? 'border-green-200 dark:border-green-950 bg-green-50/5'
                    : 'border-gray-150 dark:border-gray-800 bg-white dark:bg-gray-850'
                ]"
              >
                <!-- Background decor bubbles matching data portal -->
                <div
                  class="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-[0.12] blur-2xl transition-opacity duration-300 group-hover/card:opacity-[0.20] bg-green-500"
                />
                <div
                  class="pointer-events-none absolute -left-6 bottom-0 h-20 w-20 rounded-full opacity-[0.05] blur-xl bg-green-500"
                />

                <div class="relative space-y-2.5">
                  <!-- Header Row -->
                  <div class="flex items-start gap-2.5 min-w-0">
                    <!-- Collapse Toggle Chevron -->
                    <button
                      type="button"
                      class="flex-shrink-0 flex items-center justify-center w-5 h-9 rounded text-gray-400 hover:text-green-500 hover:bg-green-50/30 dark:hover:bg-green-950/20 transition-all duration-200 cursor-pointer"
                      @click.stop="toggleCollapse(ds.id)"
                      :title="isCollapsed(ds.id) ? '展开卡片' : '收起卡片'"
                    >
                      <svg
                        class="w-3.5 h-3.5 transform transition-transform duration-200"
                        :class="{ '-rotate-90': isCollapsed(ds.id) }"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2.5"
                        viewBox="0 0 24 24"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                      </svg>
                    </button>

                    <div
                      @click.stop="toggleCollapse(ds.id)"
                      class="flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-xl shadow-xs border text-[18px] bg-green-50/10 border-green-500/20 text-green-600 dark:bg-green-500/20 dark:border-green-500/30 dark:text-green-400 cursor-pointer hover:opacity-80 transition-opacity select-none"
                    >
                      📚
                    </div>
                    
                    <h4
                      @click.stop="toggleCollapse(ds.id)"
                      class="flex-1 min-w-0 text-sm font-bold leading-snug break-words pt-0.5 text-gray-800 dark:text-gray-100 flex items-center flex-wrap gap-1.5 cursor-pointer hover:text-green-600 dark:hover:text-green-400 transition-colors select-none"
                      :title="ds.platform_name || ds.name"
                    >
                      <span>{{ ds.platform_name || ds.name }}</span>
                      <span
                        v-if="isMyCreated(ds)"
                        class="inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] font-bold tracking-wider uppercase bg-green-50/50 text-green-600 border border-green-200/30 dark:bg-green-950/20 dark:border-green-800/30 dark:text-green-400 select-none"
                      >
                        自建
                      </span>
                    </h4>
                    
                    <!-- Actions (Pin & Toggle switch) -->
                    <div class="flex items-center gap-2 flex-shrink-0 mt-0.5">
                      <!-- 置顶支持 -->
                      <button
                        type="button"
                        class="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-lg transition-all duration-200 active:scale-90"
                        :class="pinnedDatasetIds.includes(ds.id)
                          ? 'text-amber-500 bg-amber-50 dark:bg-amber-950/20 opacity-100'
                          : 'text-gray-300 dark:text-gray-600 opacity-0 group-hover/card:opacity-100 hover:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-900/20'"
                        :title="pinnedDatasetIds.includes(ds.id) ? '取消置顶' : '置顶知识库'"
                        @click.stop="emit('toggle-pin', ds.id)"
                      >
                        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                          <line x1="12" y1="17" x2="12" y2="22"/>
                          <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/>
                        </svg>
                      </button>

                      <!-- Toggle switch -->
                      <button
                        type="button"
                        class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-1 focus:ring-green-500/20"
                        :class="[activeDatasetIds.includes(ds.id) ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700']"
                        @click="emit('toggle-active', ds.id)"
                      >
                        <span
                          class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                          :class="[activeDatasetIds.includes(ds.id) ? 'translate-x-4' : 'translate-x-0']"
                        />
                      </button>
                    </div>
                  </div>
                  
                  <!-- Collapsible Content Wrapper -->
                  <div v-show="!isCollapsed(ds.id)" class="space-y-2.5 pt-0.5">

                  <!-- Badges list matching pl-11.5 indentation -->
                  <div class="flex flex-wrap gap-1.5 pl-11.5 min-w-0">
                    <template v-if="ds.tags && ds.tags.length">
                      <span
                        v-for="tag in ds.tags.slice(0, 3)"
                        :key="tag"
                        class="inline-block max-w-full rounded-full border px-2 py-0.5 text-[9px] font-bold leading-tight bg-blue-50 border-blue-200 text-blue-600 dark:bg-blue-950/20 dark:border-blue-800 dark:text-blue-400"
                      >
                        🏷️ {{ tag }}
                      </span>
                      <button
                        v-if="ds.tags.length > 3"
                        type="button"
                        class="inline-flex items-center justify-center rounded-full border px-2 py-0.5 text-[9px] font-bold leading-tight bg-gray-100 hover:bg-gray-200 border-gray-250 text-gray-600 dark:bg-gray-850 dark:hover:bg-gray-750 dark:border-gray-700 dark:text-gray-300 transition-all cursor-pointer active:scale-90"
                        @click.stop="openAllTagsModal(ds.platform_name || ds.name, ds.tags)"
                        title="查看全部标签"
                      >
                        +{{ ds.tags.length - 3 }}
                      </button>
                    </template>
                    <span
                      v-else
                      class="inline-block max-w-full rounded-full border px-2 py-0.5 text-[9px] font-bold leading-tight bg-gray-50 border-gray-200 text-gray-500 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-400"
                    >
                      🏷️ 未分类
                    </span>
                    <span
                      class="inline-block max-w-full rounded-full border px-2 py-0.5 text-[9px] font-bold leading-tight"
                      :class="activeDatasetIds.includes(ds.id)
                        ? 'bg-green-50 border-green-200 text-green-600 dark:bg-green-950/20 dark:border-green-800 dark:text-green-400'
                        : 'bg-gray-50 border-gray-150 text-gray-400 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-500'"
                    >
                      {{ activeDatasetIds.includes(ds.id) ? '● 已启用' : '未启用' }}
                    </span>
                  </div>

                  <!-- Description Summary Box (blockquote) -->
                  <blockquote
                    v-if="ds.platform_description || ds.description"
                    class="relative w-full m-0 px-3.5 py-2.5 text-[11px] leading-relaxed rounded-r-lg border-l-[3px] bg-green-50/40 dark:bg-green-950/10 border-green-500 text-gray-600 dark:text-gray-400 font-medium"
                  >
                    {{ ds.platform_description || ds.description }}
                  </blockquote>

                  <!-- Collapsible Relevant Documents Section -->
                  <div class="relative mt-2.5">
                    <div 
                      class="mb-1.5 text-[10px] font-bold uppercase tracking-wider flex items-center justify-between select-none text-gray-400 dark:text-gray-500 cursor-pointer hover:text-green-500 transition-colors"
                      @click.stop="toggleDocsExpand(ds.id)"
                    >
                      <span class="flex items-center gap-1.5">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                        </svg>
                        相关文档
                        <!-- 文档数量徽章 -->
                        <span
                          v-if="(datasetDocuments[ds.id]?.docs?.length || 0) > 0 || (ds.doc_count ?? 0) > 0 || (ds.document_count ?? 0) > 0"
                          class="ml-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 leading-none"
                        >
                          {{ datasetDocuments[ds.id]?.docs?.length ?? ds.doc_count ?? ds.document_count }}
                        </span>
                      </span>
                      <svg
                        class="w-3.5 h-3.5 transform transition-transform duration-200"
                        :class="{ 'rotate-180': expandedDocId === ds.id }"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                      </svg>
                    </div>

                    <!-- Collapsible Documents List -->
                    <transition
                      enter-active-class="transition-all duration-200 ease-out"
                      enter-from-class="max-h-0 opacity-0"
                      enter-to-class="max-h-[350px] opacity-100"
                      leave-active-class="transition-all duration-200 ease-in"
                      leave-from-class="max-h-[350px] opacity-100"
                      leave-to-class="max-h-0 opacity-0"
                    >
                      <div 
                        v-show="expandedDocId === ds.id" 
                        class="mt-1.5 border border-gray-150 dark:border-gray-800/80 bg-white dark:bg-gray-900 rounded-lg overflow-y-auto max-h-[250px] p-2 space-y-1.5 scrollbar-thin"
                        @click.stop
                      >
                        <div v-if="datasetDocuments[ds.id]?.loading" class="space-y-2 py-2">
                          <div
                            v-for="i in 3"
                            :key="i"
                            class="flex items-center gap-2 px-2 py-1.5 rounded-lg border border-gray-100 dark:border-gray-800"
                          >
                            <div class="kp-skeleton-shimmer w-3.5 h-3.5 rounded flex-shrink-0" />
                            <div class="kp-skeleton-shimmer h-2.5 rounded flex-1" :style="{ maxWidth: i === 1 ? '70%' : '85%' }" />
                          </div>
                        </div>
                        <template v-else-if="(datasetDocuments[ds.id]?.docs?.length || 0) > 0">
                          <div
                            v-for="doc in (datasetDocuments[ds.id]?.docs || [])"
                            :key="doc.id"
                            class="flex flex-col border border-gray-100/50 dark:border-gray-800/30 bg-gray-50/20 dark:bg-gray-800/10 p-2 rounded-md transition-colors"
                          >
                            <!-- Doc Header/Row (Click to expand doc recommendations) -->
                            <div 
                              class="flex items-center justify-between gap-3 text-[10px] text-gray-600 dark:text-gray-400 cursor-pointer hover:text-gray-900 dark:hover:text-gray-200 select-none"
                              @click.stop="toggleDocRecsExpand(ds.id, doc.id)"
                            >
                              <div class="flex items-center gap-1.5 min-w-0 flex-1">
                                <span class="text-[11px] shrink-0">📄</span>
                                <span class="truncate text-[10px] text-gray-700 dark:text-gray-300 font-medium" :title="doc.name">{{ doc.name }}</span>
                              </div>
                              <div class="flex items-center gap-1.5 shrink-0">
                                <span class="text-[8px] font-mono text-gray-400">
                                  {{ formatFileSize(doc.size) }}
                                </span>
                                <svg
                                  class="w-2.5 h-2.5 transform transition-transform duration-200 text-gray-400"
                                  :class="{ 'rotate-180 text-green-500': expandedDocRecsId === doc.id }"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                                </svg>
                              </div>
                            </div>

                            <!-- Single Doc Recommendations List -->
                            <transition
                              enter-active-class="transition-all duration-200 ease-out"
                              enter-from-class="max-h-0 opacity-0"
                              enter-to-class="max-h-[160px] opacity-100"
                              leave-active-class="transition-all duration-150 ease-in"
                              leave-from-class="max-h-[160px] opacity-100"
                              leave-to-class="max-h-0 opacity-0"
                            >
                              <div
                                v-if="expandedDocRecsId === doc.id"
                                class="mt-2 pl-4 pr-1 border-t border-dashed border-gray-150 dark:border-gray-800 pt-2 flex flex-col gap-1.5 overflow-hidden"
                              >
                                <div class="text-[9px] text-gray-400 dark:text-gray-500 flex items-center gap-1 select-none">
                                  <span class="text-green-500 font-bold">💡</span> 针对该文件的专属提问：
                                </div>
                                
                                <div v-if="documentRecommendations[doc.id]?.loading" class="space-y-2 py-1">
                                  <div class="flex flex-wrap gap-1.5 kp-question-skeleton-wrap">
                                    <div
                                      v-for="(width, i) in ['6.5rem', '5.75rem']"
                                      :key="i"
                                      class="inline-flex items-center h-7 rounded-md border border-green-500/10 dark:border-green-500/15 bg-green-50/25 dark:bg-green-950/10 overflow-hidden"
                                      :style="{ width }"
                                    >
                                      <div class="kp-skeleton-shimmer h-2 rounded mx-2 w-[calc(100%-1rem)]" />
                                    </div>
                                  </div>
                                  <p class="text-[9px] text-center text-gray-400 dark:text-gray-500 select-none">
                                    生成专属提问中<span class="kp-loading-dots" aria-hidden="true">...</span>
                                  </p>
                                </div>
                                
                                <div v-else-if="(documentRecommendations[doc.id]?.questions?.length || 0) > 0" class="space-y-1">
                                  <div
                                    v-for="(q, idx) in (documentRecommendations[doc.id]?.questions || [])"
                                    :key="idx"
                                    class="flex items-stretch rounded border border-gray-150 dark:border-gray-800 bg-white dark:bg-gray-900 overflow-hidden hover:border-green-500/50 dark:hover:border-green-500/30 transition-colors"
                                  >
                                    <button
                                      class="flex-1 text-left px-2 py-1.5 text-[10px] leading-snug text-gray-700 dark:text-gray-300 line-clamp-2 hover:text-green-600 dark:hover:text-green-400 hover:bg-green-50/30 dark:hover:bg-green-950/10 transition-colors"
                                      :title="q.query"
                                      @click.stop="handleQuestionClick(q.query, ds.id, 'send')"
                                    >
                                      {{ q.label }}
                                    </button>
                                    <div class="w-[1px] bg-gray-150 dark:bg-gray-800" />
                                    <button
                                      class="px-2 flex items-center justify-center hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-green-500 transition-colors"
                                      title="填入输入框进行修改"
                                      @click.stop="handleQuestionClick(q.query, ds.id, 'fill')"
                                    >
                                      <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                      </svg>
                                    </button>
                                  </div>
                                </div>
                                
                                <div v-else class="text-[9px] text-gray-400 dark:text-gray-500 text-center py-1 select-none">
                                  该文件暂无可用专属提问
                                </div>
                              </div>
                            </transition>
                          </div>
                        </template>
                        <div v-else class="text-[10px] text-gray-400 text-center py-3 select-none">
                          暂无文档记录
                        </div>
                      </div>
                    </transition>
                  </div>

                  <!-- Recommended Questions Section (Always visible) -->
                  <div
                    class="relative pt-2.5 border-t border-gray-100 dark:border-gray-700/60"
                  >
                    <div class="mb-2 flex items-center justify-between select-none">
                      <span class="text-[10px] font-bold uppercase tracking-wider flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
                        <svg class="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        你可以这样问
                      </span>
                      
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-md border border-gray-200 hover:border-green-500/30 hover:bg-green-500/5 dark:border-gray-700 dark:hover:border-green-500/20 text-gray-500 hover:text-green-500 dark:text-gray-400 dark:hover:text-green-400 transition-all duration-200 cursor-pointer active:scale-95 disabled:opacity-50"
                        :disabled="recommendations[ds.id]?.loading"
                        @click.stop="handleRefreshRecommendations(ds.id)"
                      >
                        <svg
                          class="w-3.5 h-3.5 opacity-70"
                          :class="{ 'animate-spin': recommendations[ds.id]?.loading }"
                          fill="none" stroke="currentColor" viewBox="0 0 24 24"
                        >
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                        </svg>
                        <span>换一批</span>
                      </button>
                    </div>

                    <!-- loading skeleton -->
                    <div v-if="recommendations[ds.id]?.loading" class="space-y-2.5">
                      <div class="flex flex-wrap gap-2 kp-question-skeleton-wrap">
                        <div
                          v-for="(width, i) in ['8.75rem', '10.25rem', '7.5rem']"
                          :key="i"
                          class="inline-flex items-center h-[34px] rounded-lg border border-green-500/15 dark:border-green-500/20 bg-green-50/30 dark:bg-green-950/15 overflow-hidden shadow-sm"
                          :style="{ width }"
                        >
                          <div class="flex items-center gap-2 px-3 w-full">
                            <div class="kp-skeleton-shimmer w-3.5 h-3.5 rounded-full flex-shrink-0" />
                            <div
                              class="kp-skeleton-shimmer h-2.5 rounded flex-1"
                              :style="{ maxWidth: i === 0 ? '72%' : i === 1 ? '88%' : '64%' }"
                            />
                          </div>
                        </div>
                      </div>
                      <div class="flex items-center justify-center gap-2 py-0.5">
                        <span class="relative flex h-1.5 w-1.5">
                          <span class="kp-loading-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-50" />
                          <span class="relative inline-flex h-1.5 w-1.5 rounded-full bg-green-500" />
                        </span>
                        <span class="text-[10px] font-medium text-gray-400 dark:text-gray-500 select-none">
                          正在生成推荐提问<span class="kp-loading-dots" aria-hidden="true">...</span>
                        </span>
                      </div>
                    </div>

                    <!-- horizontal flex-wrap layout matching data portal -->
                    <div v-else-if="(recommendations[ds.id]?.questions?.length || 0) > 0 || (recommendations[ds.id]?.custom_questions?.length || 0) > 0" class="space-y-3.5 w-full">
                      <!-- 置顶快捷推荐 -->
                      <div v-if="(recommendations[ds.id]?.custom_questions?.length || 0) > 0" class="space-y-1.5 w-full">
                        <div class="flex items-center gap-1 text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase select-none">
                          <svg class="w-3 h-3 text-amber-500 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                            <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                          </svg>
                          <span>📌 置顶快捷提问 / 业务指南</span>
                        </div>
                        <div class="flex flex-wrap gap-2">
                          <div
                            v-for="(q, idx) in (recommendations[ds.id]?.custom_questions || [])"
                            :key="'c_'+idx"
                            class="inline-flex items-stretch rounded-lg border border-amber-250 dark:border-amber-900/40 bg-amber-50/20 dark:bg-amber-950/10 shadow-sm hover:shadow hover:-translate-y-0.5 active:translate-y-0 transition-all duration-200 overflow-hidden"
                          >
                            <!-- Left Question Body (Direct Send) -->
                            <button
                              type="button"
                              @click="handleQuestionClick(q.query, ds.id, 'send')"
                              class="flex items-center gap-1.5 px-3 py-2 text-left text-xs font-semibold cursor-pointer select-none text-gray-700 dark:text-gray-300 hover:bg-amber-100/10 dark:hover:bg-amber-900/10 transition-colors leading-normal"
                            >
                              <svg class="w-3.5 h-3.5 flex-shrink-0 text-amber-500" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" />
                              </svg>
                              <span>{{ q.label }}</span>
                            </button>
                            <!-- Right Edit Pencil -->
                            <button
                              type="button"
                              @click="handleQuestionClick(q.query, ds.id, 'fill')"
                              class="flex items-center justify-center px-2 hover:bg-amber-100/15 dark:hover:bg-amber-900/20 text-gray-400 hover:text-amber-600 dark:hover:text-amber-400 border-l border-amber-100 dark:border-amber-900/30 transition-colors cursor-pointer"
                              title="编辑此问题"
                            >
                              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      </div>

                      <!-- 智能生成提问 -->
                      <div v-if="(recommendations[ds.id]?.questions?.length || 0) > 0" class="space-y-1.5 w-full">
                        <div class="flex items-center gap-1 text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase select-none">
                          <svg class="w-3 h-3 text-green-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                          </svg>
                          <span>智能提问推荐（LLM 动态推断）</span>
                        </div>
                        <div class="flex flex-wrap gap-2">
                          <div
                            v-for="(q, idx) in (recommendations[ds.id]?.questions || [])"
                            :key="'d_'+idx"
                            class="inline-flex items-stretch rounded-lg border border-gray-150 dark:border-gray-750 bg-white dark:bg-gray-800 shadow-sm hover:shadow hover:-translate-y-0.5 active:translate-y-0 transition-all duration-200 overflow-hidden"
                          >
                            <!-- Left Question Body (Direct Send) -->
                            <button
                              type="button"
                              @click="handleQuestionClick(q.query, ds.id, 'send')"
                              class="flex items-center gap-1.5 px-3 py-2 text-left text-xs font-semibold cursor-pointer select-none text-gray-700 dark:text-gray-300 hover:bg-green-50/10 dark:hover:bg-green-950/10 transition-colors leading-normal"
                            >
                              <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-80 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                              </svg>
                              <span>{{ q.label }}</span>
                            </button>
                            <!-- Right Edit Pencil -->
                            <button
                              type="button"
                              @click="handleQuestionClick(q.query, ds.id, 'fill')"
                              class="flex items-center justify-center px-2 hover:bg-green-50/15 dark:hover:bg-green-950/20 text-gray-400 hover:text-green-600 dark:hover:text-green-400 border-l border-gray-100 dark:border-gray-700/80 transition-colors cursor-pointer"
                              title="编辑此问题"
                            >
                              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div v-else class="w-full py-1">
                      <button
                        type="button"
                        class="w-full flex items-center justify-center gap-2 rounded-xl border border-dashed border-gray-200 dark:border-gray-800 bg-gray-50/20 dark:bg-gray-900/10 hover:bg-green-50/10 dark:hover:bg-green-950/10 hover:border-green-500/35 hover:text-green-600 dark:hover:text-green-400 px-4 py-3.5 text-xs font-bold text-gray-500 dark:text-gray-400 transition-all active:scale-[0.99] cursor-pointer"
                        @click.stop="emit('load-recommendations', ds.id, false)"
                      >
                        <svg class="w-4 h-4 text-green-500/95" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 21l-1.81-5.096L2.096 14.1 7.19 13.287 8 8.1l1.81 5.187 5.096.91-5.093.907zM19.5 4.5l-.81 2.274L16.417 7.58l2.273.81.81 2.274.81-2.274 2.274-.81-2.274-.806L19.5 4.5z" />
                        </svg>
                        <span>智能生成提问推荐</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <!-- 标签全部展示弹窗 -->
    <transition
      enter-active-class="ease-out duration-300"
      enter-from-class="opacity-0 scale-95"
      enter-to-class="opacity-100 scale-100"
      leave-active-class="ease-in duration-200"
      leave-from-class="opacity-100 scale-100"
      leave-to-class="opacity-0 scale-95"
    >
      <div 
        v-if="showAllTagsModal" 
        class="fixed inset-0 z-[150] flex items-center justify-center p-4 bg-gray-500/40 backdrop-blur-xs"
        @click="showAllTagsModal = false"
      >
        <div 
          class="bg-white dark:bg-gray-800 rounded-xl border border-gray-150 dark:border-gray-700 max-w-sm w-full p-4 shadow-xl transform transition-all space-y-4"
          @click.stop
        >
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-gray-800 dark:text-gray-100 select-none">
              🏷️ {{ activeModalDatasetName }} 的所有标签
            </h4>
            <button
              type="button"
              class="text-gray-400 hover:text-gray-550 dark:hover:text-gray-250 cursor-pointer p-0.5 rounded-lg transition-colors"
              @click="showAllTagsModal = false"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div class="flex flex-wrap gap-2 pt-1 max-h-[200px] overflow-y-auto pr-1 scrollbar-thin">
            <span
              v-for="tag in activeModalTags"
              :key="tag"
              class="inline-block rounded-full border px-2.5 py-0.5 text-[10px] font-bold bg-blue-50 border-blue-200 text-blue-600 dark:bg-blue-950/20 dark:border-blue-800 dark:text-blue-400 select-none"
            >
              {{ tag }}
            </span>
          </div>
          <div class="flex justify-end pt-1">
            <button
              type="button"
              class="px-3.5 py-1.5 text-[10px] font-bold rounded-lg border border-gray-200 dark:border-gray-750 bg-gray-50 dark:bg-gray-800 text-gray-750 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors cursor-pointer select-none"
              @click="showAllTagsModal = false"
            >
              关闭
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";

const modelValue = defineModel<boolean>({ default: false });
const keepOpenOnQuestion = defineModel<boolean>("keepOpenOnQuestion", { default: false });
const pinned = defineModel<boolean>("pinned", { default: false });
const hallucinationCheck = defineModel<boolean>("hallucinationCheck", { default: false });

const similarityThreshold = defineModel<number>("similarityThreshold", { default: 0.20 });
const vectorWeight = defineModel<number>("vectorWeight", { default: 0.30 });
const metadataTopK = defineModel<number>("metadataTopK", { default: 5 });

const props = defineProps<{
  datasets: Array<{
    id: string;
    name: string;
    description: string;
    doc_count?: number;
    word_count?: number;
    [key: string]: any;
  }>;
  activeDatasetIds: string[];
  pinnedDatasetIds: string[];
  recommendations: Record<string, { questions: any[]; custom_questions?: any[]; loading?: boolean }>;
  datasetDocuments: Record<string, { docs: any[]; loading?: boolean }>;
  documentRecommendations: Record<string, { questions: any[]; loading?: boolean }>;
  loading?: boolean;
  loadError?: boolean;
  generatedAt?: string;
}>();

const emit = defineEmits<{
  (e: "toggle-active", id: string): void;
  (e: "quick-question", query: string, action?: "send" | "fill"): void;
  (e: "load-recommendations", id: string, refresh?: boolean): void;
  (e: "refresh"): void;
  (e: "toggle-pin", id: string): void;
  (e: "load-documents", id: string): void;
  (e: "load-document-recommendations", datasetId: string, documentId: string): void;
}>();

const expandedDocId = ref<string | null>(null);
const expandedDocRecsId = ref<string | null>(null);
const showAdvancedConfig = ref(false);

// 标签全部展示弹窗状态
const showAllTagsModal = ref(false);
const activeModalTags = ref<string[]>([]);
const activeModalDatasetName = ref("");
const openAllTagsModal = (datasetName: string, tags: string[]) => {
  activeModalDatasetName.value = datasetName;
  activeModalTags.value = tags;
  showAllTagsModal.value = true;
};

const isMyCreated = (ds: any) => {
  try {
    const userInfoStr = localStorage.getItem('user_info');
    if (userInfoStr) {
      const userInfo = JSON.parse(userInfoStr);
      const currentUserName = userInfo.username || userInfo.user_name;
      return currentUserName && ds.created_by === currentUserName;
    }
  } catch (e) {}
  return false;
};

// 卡片折叠状态管理
const collapsedDatasetIds = ref<string[]>([]);
const isCollapsed = (datasetId: string) => collapsedDatasetIds.value.includes(datasetId);
const toggleCollapse = (datasetId: string) => {
  const index = collapsedDatasetIds.value.indexOf(datasetId);
  if (index > -1) {
    collapsedDatasetIds.value.splice(index, 1);
  } else {
    collapsedDatasetIds.value.push(datasetId);
  }
};

const activeTooltip = ref<string | null>(null);

const showSearchBar = ref(false);
const searchQuery = ref("");
const selectedTag = ref("All");

const isMobile = computed(() => {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 639px)").matches;
});

const allTags = computed(() => {
  const tagsSet = new Set<string>();
  props.datasets.forEach(ds => {
    if (ds.tags && Array.isArray(ds.tags)) {
      ds.tags.forEach(tag => {
        if (tag && tag.trim()) {
          tagsSet.add(tag.trim());
        }
      });
    }
  });
  return Array.from(tagsSet).sort();
});

const sortedDatasets = computed(() => {
  let filtered = [...props.datasets];
  
  if (selectedTag.value !== "All") {
    filtered = filtered.filter(ds => 
      ds.tags && Array.isArray(ds.tags) && ds.tags.map(t => t.trim()).includes(selectedTag.value)
    );
  }
  
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase();
    filtered = filtered.filter(ds => {
      const name = (ds.platform_name || ds.name || "").toLowerCase();
      const desc = (ds.platform_description || ds.description || "").toLowerCase();
      const tags = (ds.tags || []).join(",").toLowerCase();
      return name.includes(q) || desc.includes(q) || tags.includes(q);
    });
  }

  return filtered.sort((a, b) => {
    const aPinned = props.pinnedDatasetIds.includes(a.id);
    const bPinned = props.pinnedDatasetIds.includes(b.id);
    if (aPinned && !bPinned) return -1;
    if (!aPinned && bPinned) return 1;
    return 0;
  });
});

const sheetEnterFrom = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));
const sheetLeaveTo = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));

// toggleExpand removed

const handleRefreshRecommendations = (id: string) => {
  emit("load-recommendations", id, true);
};

const toggleDocsExpand = (id: string) => {
  if (expandedDocId.value === id) {
    expandedDocId.value = null;
  } else {
    expandedDocId.value = id;
    emit("load-documents", id);
  }
};

const toggleDocRecsExpand = (datasetId: string, documentId: string) => {
  if (expandedDocRecsId.value === documentId) {
    expandedDocRecsId.value = null;
  } else {
    expandedDocRecsId.value = documentId;
    emit("load-document-recommendations", datasetId, documentId);
  }
};

const toggleTooltip = (paramName: string) => {
  if (activeTooltip.value === paramName) {
    activeTooltip.value = null;
  } else {
    activeTooltip.value = paramName;
  }
};

const formatFileSize = (bytes?: number): string => {
  if (!bytes) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
};

const closeDrawer = () => {
  modelValue.value = false;
};

const handleQuestionClick = (query: string, dsId: string, action: "send" | "fill" = "send") => {
  // 如果当前知识库还未启用，点击其下属建议问题（发送或编辑）时会自动将其勾选启用绑定到当前会话中
  if (!props.activeDatasetIds.includes(dsId)) {
    emit("toggle-active", dsId);
  }
  emit("quick-question", query, action);
  if (action === "send" && !keepOpenOnQuestion.value) {
    closeDrawer();
  }
};
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar {
  width: 4px;
}
.scrollbar-thin::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 9999px;
}
.scrollbar-thin::-webkit-scrollbar-track {
  background-color: transparent;
}

@keyframes kp-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.kp-skeleton-shimmer {
  background: linear-gradient(90deg, rgb(229 231 235) 20%, rgb(209 250 229) 45%, rgb(229 231 235) 80%);
  background-size: 200% 100%;
  animation: kp-shimmer 1.5s ease-in-out infinite;
}

:global(.dark) .kp-skeleton-shimmer {
  background: linear-gradient(90deg, rgb(31 41 55) 20%, rgb(6 78 59 / 0.45) 45%, rgb(31 41 55) 80%);
  background-size: 200% 100%;
}

.kp-question-skeleton-wrap {
  animation: kp-skeleton-fade 0.35s ease-out;
}

@keyframes kp-skeleton-fade {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.kp-loading-ping {
  animation: kp-ping 1.4s cubic-bezier(0, 0, 0.2, 1) infinite;
}

@keyframes kp-ping {
  0% { transform: scale(1); opacity: 0.55; }
  75%, 100% { transform: scale(2.2); opacity: 0; }
}

.kp-loading-dots {
  display: inline-block;
  animation: kp-dots 1.2s ease-in-out infinite;
}

@keyframes kp-dots {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}
</style>
