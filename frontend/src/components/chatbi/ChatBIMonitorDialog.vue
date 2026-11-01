<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[300] flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm"
      @click.self="closeDialog"
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="chatbi-monitor-dialog-title"
        class="w-full max-w-md overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900"
      >
        <header class="flex items-start justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div>
            <h2 id="chatbi-monitor-dialog-title" class="text-base font-bold text-gray-900 dark:text-gray-100">订阅查询结果</h2>
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">系统会按设定频率重新执行本次查询。</p>
          </div>
          <button type="button" :disabled="submitting" class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-gray-800" aria-label="关闭" @click="closeDialog">×</button>
        </header>

        <form class="space-y-4 px-5 py-5" @submit.prevent="submitMonitor">
          <fieldset>
            <legend class="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">执行频率</legend>
            <div class="grid grid-cols-3 gap-2">
              <label v-for="option in scheduleOptions" :key="option.value" class="cursor-pointer">
                <input v-model="schedule_type" class="peer sr-only" type="radio" name="schedule_type" :value="option.value" />
                <span class="block rounded-lg border border-gray-200 px-3 py-2 text-center text-sm text-gray-600 transition peer-checked:border-indigo-500 peer-checked:bg-indigo-50 peer-checked:text-indigo-700 dark:border-gray-700 dark:text-gray-300 dark:peer-checked:bg-indigo-950/40 dark:peer-checked:text-indigo-300">{{ option.label }}</span>
              </label>
            </div>
          </fieldset>

          <div v-if="schedule_type === 'weekly'">
            <label class="mb-1.5 block text-sm font-semibold text-gray-700 dark:text-gray-200">星期</label>
            <select v-model.number="weekday" class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800">
              <option v-for="day in weekdays" :key="day.value" :value="day.value">{{ day.label }}</option>
            </select>
          </div>

          <div v-if="schedule_type === 'monthly'">
            <label class="mb-1.5 block text-sm font-semibold text-gray-700 dark:text-gray-200">每月日期</label>
            <input v-model.number="monthday" type="number" min="1" max="28" class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800" />
            <p class="mt-1 text-[11px] text-gray-400">为保证每月都能执行，可选择 1–28 日。</p>
          </div>

          <div>
            <label class="mb-1.5 block text-sm font-semibold text-gray-700 dark:text-gray-200">执行时间</label>
            <input v-model="time_value" required type="time" class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800" />
          </div>

          <label class="flex items-center justify-between rounded-xl bg-gray-50 px-3 py-2.5 dark:bg-gray-800/70">
            <span>
              <span class="block text-sm font-medium text-gray-700 dark:text-gray-200">成功后通知</span>
              <span class="block text-[11px] text-gray-400">每次订阅任务执行成功后发送站内通知</span>
            </span>
            <input v-model="notify_on_success" type="checkbox" class="h-4 w-4 rounded border-gray-300 text-indigo-600" />
          </label>

          <p v-if="errorMessage" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">{{ errorMessage }}</p>

          <footer class="flex justify-end gap-2 pt-1">
            <button type="button" :disabled="submitting" class="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800" @click="closeDialog">取消</button>
            <button type="submit" :disabled="submitting" class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60">{{ submitting ? "订阅中…" : "确认订阅" }}</button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import axios from "@/utils/axios";

const props = defineProps<{ open: boolean; conversationId: string; resultId?: string }>();
const emit = defineEmits<{
  (event: "close"): void;
  (event: "created", payload: { created: boolean; report_id?: string }): void;
}>();

const scheduleOptions = [
  { label: "每天", value: "daily" },
  { label: "每周", value: "weekly" },
  { label: "每月", value: "monthly" },
];
// 星期取值遵循标准 crontab 语义：周日 = 0，周一 = 1 … 周六 = 6
const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((label, index) => ({ label, value: (index + 1) % 7 }));
const schedule_type = ref("daily");
const time_value = ref("09:00");
const weekday = ref(1);
const monthday = ref(1);
const notify_on_success = ref(true);
const submitting = ref(false);
const errorMessage = ref("");

const resetForm = () => {
  schedule_type.value = "daily";
  time_value.value = "09:00";
  weekday.value = 1;
  monthday.value = 1;
  notify_on_success.value = true;
  errorMessage.value = "";
};

watch(() => props.open, (open) => {
  if (open) resetForm();
});

const closeDialog = () => {
  if (!submitting.value) emit("close");
};

const submitMonitor = async () => {
  errorMessage.value = "";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time_value.value)) {
    errorMessage.value = "请选择有效的执行时间";
    return;
  }
  if (schedule_type.value === "monthly" && (monthday.value < 1 || monthday.value > 28)) {
    errorMessage.value = "每月日期请选择 1–28 日";
    return;
  }
  submitting.value = true;
  try {
    const response = await axios.post("/api/portal/chatbi-monitors", {
      conversation_id: props.conversationId,
      result_id: props.resultId,
      schedule_type: schedule_type.value,
      time_value: time_value.value,
      weekday: schedule_type.value === "weekly" ? weekday.value : null,
      monthday: schedule_type.value === "monthly" ? monthday.value : null,
      notify_on_success: notify_on_success.value,
    });
    emit("created", response.data?.data || { created: true });
  } catch (error: any) {
    errorMessage.value = error.response?.data?.detail || "查询订阅创建失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
};
</script>
