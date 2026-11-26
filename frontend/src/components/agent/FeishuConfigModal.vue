<script setup lang="ts">
import { MegaphoneIcon } from "@heroicons/vue/24/outline";
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
    title="飞书通知工具配置"
    :z-index="10050"
    @close="emit('update:model', false)"
    size="max-w-md"
  >
    <div class="space-y-5 py-2">
      <div class="bg-cyan-50 p-5 rounded-xl border border-cyan-100 flex items-start space-x-3">
        <span class="text-xl"><MegaphoneIcon class="h-5 w-5" aria-hidden="true" /></span>
        <div>
          <p class="text-sm font-bold text-primary-dark">配置说明</p>
          <p class="text-xs text-primary-dark mt-2 leading-relaxed">
            飞书群机器人通知配置已改为读取各用户的个人配置。请前往页面右上角的<strong>「个人中心 &rarr; 消息通知」</strong>进行设置，智能体在运行飞书发送消息工具时将自动采用您个人的飞书 Webhook 及签名密钥参数。
          </p>
        </div>
      </div>

      <div class="flex justify-end pt-4 border-t border-gray-100">
        <button
          @click="emit('update:model', false)"
          class="px-6 py-2 bg-primary text-white text-sm rounded-xl hover:bg-primary-dark active:scale-95 transition-all font-bold shadow-md shadow-primary/10 cursor-pointer"
        >
          我知道了
        </button>
      </div>
    </div>
  </Modal>
</template>
