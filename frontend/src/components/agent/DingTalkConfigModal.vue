<script setup lang="ts">
import Modal from "../Modal.vue";

const props = defineProps<{
  model: boolean;
  config?: any;
  readonly?: boolean;
}>();

const emit = defineEmits(["update:model", "save"]);
</script>

<template>
  <Modal
    v-if="model"
    title="钉钉通知工具配置"
    :z-index="10050"
    @close="emit('update:model', false)"
    size="max-w-md"
  >
    <div class="space-y-5 py-2">
      <div class="bg-blue-50 p-5 rounded-xl border border-blue-100 flex items-start space-x-3">
        <span class="text-xl">📢</span>
        <div>
          <p class="text-sm font-bold text-blue-800">配置说明</p>
          <p class="text-xs text-blue-600 mt-2 leading-relaxed">
            钉钉消息通知配置已改为读取各用户的个人配置。请前往页面右上角的<strong>「个人中心 &rarr; 消息通知」</strong>进行设置，智能体在运行发送消息工具时将自动采用您个人的钉钉机器人参数。
          </p>
        </div>
      </div>

      <div class="flex justify-end pt-4 border-t border-gray-100">
        <button
          @click="emit('update:model', false)"
          class="px-6 py-2 bg-blue-600 text-white text-sm rounded-xl hover:bg-blue-700 active:scale-95 transition-all font-bold shadow-md shadow-blue-500/10"
        >
          我知道了
        </button>
      </div>
    </div>
  </Modal>
</template>
