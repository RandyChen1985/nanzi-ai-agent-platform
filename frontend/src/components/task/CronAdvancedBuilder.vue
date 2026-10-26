<template>
  <div class="space-y-4">
    <!-- 场景分类 -->
    <div class="flex gap-1 overflow-x-auto border-b border-gray-100">
      <button
        v-for="tab in CRON_SCENE_TABS"
        :key="tab.id"
        type="button"
        class="shrink-0 border-b-2 px-3 py-2 text-xs font-bold transition"
        :class="activeTab === tab.id ? 'border-primary text-primary' : 'border-transparent text-gray-400 hover:text-gray-600'"
        @click="selectTab(tab.id)"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 场景卡片 -->
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
      <button
        v-for="scene in scenesOfTab"
        :key="scene.id"
        type="button"
        class="rounded-xl border p-2.5 text-left transition"
        :class="activeScene === scene.id
          ? 'border-primary bg-primary/5 ring-1 ring-primary/20'
          : 'border-gray-100 bg-white hover:border-primary/30'"
        @click="selectScene(scene.id)"
      >
        <span class="text-base leading-none">{{ scene.icon }}</span>
        <p class="mt-1.5 text-xs font-bold text-gray-800">{{ scene.title }}</p>
        <p class="mt-0.5 text-[10px] leading-snug text-gray-400">{{ scene.desc }}</p>
      </button>
    </div>

    <!-- 场景选项 -->
    <div v-if="activeScene !== 'custom'" class="space-y-3 rounded-xl border border-gray-100 bg-white p-3">
      <div v-if="activeScene === 'weekly'" class="space-y-1.5">
        <p class="text-[11px] font-bold text-gray-500">每周哪几天？</p>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="day in 7"
            :key="day"
            type="button"
            class="rounded-lg border px-2 py-1 text-[11px] font-bold transition"
            :class="state.weekdays.includes(day - 1)
              ? 'border-primary bg-primary text-white'
              : 'border-gray-200 bg-white text-gray-500 hover:border-primary/40'"
            @click="toggleWeekday(day - 1)"
          >
            {{ WEEKDAY_LABELS[day - 1] }}
          </button>
        </div>
        <p class="text-[10px] text-gray-400">可多选，至少保留一天。</p>
      </div>

      <div v-if="needsMonthday" class="flex flex-wrap items-center gap-2">
        <p class="text-[11px] font-bold text-gray-500">
          {{ activeScene === 'quarter' ? '季度中的哪一天' : '每月几号' }}
        </p>
        <select
          v-model.number="state.monthday"
          class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
        >
          <option v-for="day in 31" :key="day" :value="day">{{ day }} 号</option>
        </select>
      </div>

      <div v-if="needsMonth" class="flex flex-wrap items-center gap-2">
        <p class="text-[11px] font-bold text-gray-500">选择月份</p>
        <select
          v-model.number="state.month"
          class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
        >
          <option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option>
        </select>
      </div>

      <div v-if="activeScene === 'multiMonthday'" class="space-y-2">
        <p class="text-[11px] font-bold text-gray-500">每月哪几天？</p>
        <div class="grid grid-cols-8 gap-1">
          <button
            v-for="day in 31"
            :key="day"
            type="button"
            class="rounded-md border py-1 text-[10px] font-bold transition"
            :class="state.days.includes(day)
              ? 'border-primary bg-primary text-white'
              : 'border-gray-200 bg-white text-gray-500 hover:border-primary/40'"
            @click="toggleDay(day)"
          >{{ day }}</button>
        </div>
        <p class="text-[10px] text-gray-400">已选 {{ state.days.length }} 个，最多 5 个。</p>
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">几点运行</p>
          <select
            v-model.number="state.hour"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="hour in 24" :key="hour" :value="hour - 1">{{ hour - 1 }} 点</option>
          </select>
          <select
            v-model.number="state.minute"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
          </select>
        </div>
      </div>

      <div v-if="activeScene === 'multiHourly'" class="space-y-2">
        <p class="text-[11px] font-bold text-gray-500">每天哪几个整点？</p>
        <div class="grid grid-cols-8 gap-1">
          <button
            v-for="hour in 24"
            :key="hour"
            type="button"
            class="rounded-md border py-1 text-[10px] font-bold transition"
            :class="state.hours.includes(hour - 1)
              ? 'border-primary bg-primary text-white'
              : 'border-gray-200 bg-white text-gray-500 hover:border-primary/40'"
            @click="toggleHour(hour - 1)"
          >{{ hour - 1 }}</button>
        </div>
        <p class="text-[10px] text-gray-400">已选 {{ state.hours.length }} 个，最多 5 个。</p>
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">每小时第几分钟</p>
          <select
            v-model.number="state.minute"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
          </select>
        </div>
      </div>

      <div v-if="activeScene === 'hourly'" class="flex flex-wrap items-center gap-2">
        <p class="text-[11px] font-bold text-gray-500">每小时第几分钟</p>
        <select
          v-model.number="state.minute"
          class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
        >
          <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
        </select>
      </div>

      <div v-if="activeScene === 'everyDay'" class="space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">间隔多久</p>
          <span class="text-[11px] text-gray-400">每隔</span>
          <input
            v-model.number="state.dayInterval"
            type="number"
            :min="1"
            :max="30"
            class="w-16 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] outline-none focus:border-primary"
          />
          <span class="text-[11px] text-gray-400">天</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">几点运行</p>
          <select
            v-model.number="state.hour"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="hour in 24" :key="hour" :value="hour - 1">{{ hour - 1 }} 点</option>
          </select>
          <select
            v-model.number="state.minute"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
          </select>
        </div>
        <p class="text-[10px] text-gray-400">按每月 1 号起算，不是严格连续 N 天。</p>
      </div>

      <div v-if="activeScene === 'hourRange'" class="space-y-2">
        <p class="text-[11px] font-bold text-gray-500">每天哪个时段？</p>
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-[11px] text-gray-400">从</span>
          <select
            v-model.number="state.startHour"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="hour in 24" :key="hour" :value="hour - 1">{{ hour - 1 }} 点</option>
          </select>
          <span class="text-[11px] text-gray-400">到</span>
          <select
            v-model.number="state.endHour"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="hour in 24" :key="hour" :value="hour - 1">{{ hour - 1 }} 点</option>
          </select>
          <span class="text-[11px] text-gray-400">每小时第</span>
          <select
            v-model.number="state.minute"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
          </select>
        </div>
        <p class="text-[10px] text-gray-400">表示在这个时段内，每小时运行一次。</p>
      </div>

      <div v-if="activeScene === 'everyMinute' || activeScene === 'everyHour'" class="space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">间隔多久</p>
          <span class="text-[11px] text-gray-400">每隔</span>
          <input
            v-model.number="state.n"
            type="number"
            :min="1"
            :max="activeScene === 'everyMinute' ? 59 : 23"
            class="w-16 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] outline-none focus:border-primary"
          />
          <span class="text-[11px] text-gray-400">{{ activeScene === 'everyMinute' ? '分钟' : '小时' }}</span>
        </div>
        <div v-if="activeScene === 'everyHour'" class="flex flex-wrap items-center gap-2">
          <p class="text-[11px] font-bold text-gray-500">每小时第几分钟</p>
          <select
            v-model.number="state.minute"
            class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
          >
            <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
          </select>
        </div>
      </div>

      <div v-if="needsFixedTime" class="flex flex-wrap items-center gap-2">
        <p class="text-[11px] font-bold text-gray-500">几点运行</p>
        <select
          v-model.number="state.hour"
          class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
        >
          <option v-for="hour in 24" :key="hour" :value="hour - 1">{{ hour - 1 }} 点</option>
        </select>
        <select
          v-model.number="state.minute"
          class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary"
        >
          <option v-for="minute in 60" :key="minute" :value="minute - 1">{{ minute - 1 }} 分</option>
        </select>
      </div>
    </div>

    <!-- 自定义表达式 -->
    <div v-else class="space-y-2 rounded-xl border border-gray-100 bg-white p-3">
      <p class="text-[11px] font-bold text-gray-500">输入 Cron 表达式</p>
      <input
        v-model="state.customText"
        type="text"
        spellcheck="false"
        placeholder="0 9 * * 1-5"
        class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 font-mono text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
      />
      <p v-if="!validation.valid" class="text-[11px] leading-relaxed text-amber-600">{{ validation.message }}</p>
      <p v-else class="text-[11px] text-emerald-600">格式检查通过。</p>
    </div>

    <!-- 实时预览 -->
    <div class="rounded-xl border border-gray-100 bg-gray-50/70 p-3">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div class="min-w-0">
          <p class="text-sm font-bold text-gray-800">{{ description.summary }}</p>
          <p class="mt-0.5 text-[11px] leading-relaxed text-gray-400">{{ description.detail }}</p>
        </div>
        <code class="shrink-0 rounded-md bg-white px-2 py-1 font-mono text-[11px] text-blue-600 ring-1 ring-gray-100">{{ expression || '—' }}</code>
      </div>
      <div class="mt-2 flex flex-wrap items-center gap-1">
        <span class="text-[10px] font-bold text-gray-400">接下来（{{ timezone }}）：</span>
        <span
          v-for="run in nextRuns"
          :key="run.getTime()"
          class="rounded-md bg-white px-1.5 py-0.5 text-[10px] text-gray-500 ring-1 ring-gray-100"
        >{{ formatCronRunTime(run, timezone) }}</span>
        <span v-if="!nextRuns.length && validation.valid" class="text-[10px] text-gray-400">未来 5 年无匹配</span>
        <span v-if="!validation.valid" class="text-[10px] text-amber-600">表达式待修正</span>
      </div>
    </div>

    <p class="text-[10px] leading-relaxed text-gray-400">
      周字段按标准 crontab：0/7=周日；「日」与「周」不同时限定，避免同时生效时结果不符合预期。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import {
  CRON_SCENES,
  CRON_SCENE_TABS,
  buildSceneExpression,
  describeScene,
  inferScene,
  type CronSceneId,
  type CronSceneState,
  type CronSceneTabId,
} from '@/utils/cronScenes';
import {
  WEEKDAY_LABELS,
  formatCronRunTime,
  nextCronRunTimes,
  validateCronExpression,
} from '@/utils/cronSchedule';
import { getPlatformTimezone } from '@/utils/platformTimezone';

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>();

const initial = inferScene(props.modelValue);
const activeScene = ref<CronSceneId>(initial.scene);
const state = reactive<CronSceneState>(initial.state);
const activeTab = ref<CronSceneTabId>(tabOf(initial.scene));

const expression = computed(() => buildSceneExpression(activeScene.value, state));
const validation = computed(() => validateCronExpression(expression.value));
const description = computed(() => describeScene(activeScene.value, state));
const nextRuns = computed(() => (validation.value.valid ? nextCronRunTimes(expression.value, 3) : []));
const timezone = computed(() => getPlatformTimezone());
const scenesOfTab = computed(() => CRON_SCENES.filter((item) => item.tab === activeTab.value));
const needsFixedTime = computed(
  () => !['hourly', 'everyMinute', 'everyHour', 'everyDay', 'hourRange', 'multiHourly', 'multiMonthday', 'custom'].includes(activeScene.value),
);
const needsMonthday = computed(() => ['monthly', 'quarter', 'yearly'].includes(activeScene.value));
const needsMonth = computed(() => ['yearly', 'monthOnly'].includes(activeScene.value));

watch(expression, (value) => {
  if (value && value !== props.modelValue) emit('update:modelValue', value);
});

function tabOf(scene: CronSceneId): CronSceneTabId {
  return CRON_SCENES.find((item) => item.id === scene)?.tab ?? 'common';
}

function selectTab(id: CronSceneTabId): void {
  activeTab.value = id;
  const first = CRON_SCENES.find((item) => item.tab === id);
  if (first && first.id !== activeScene.value) selectScene(first.id);
}

function selectScene(id: CronSceneId): void {
  if (id === 'custom') {
    // 以当前场景生成的结果作为自定义编辑的起点
    state.customText = buildSceneExpression(activeScene.value, state);
  }
  activeScene.value = id;
}

function toggleNumber(list: number[], value: number): void {
  const index = list.indexOf(value);
  if (index >= 0) {
    if (list.length > 1) list.splice(index, 1);
    return;
  }
  if (list.length < 5) list.push(value);
}

function toggleWeekday(day: number): void {
  toggleNumber(state.weekdays, day);
}

function toggleDay(day: number): void {
  toggleNumber(state.days, day);
}

function toggleHour(hour: number): void {
  toggleNumber(state.hours, hour);
}
</script>
