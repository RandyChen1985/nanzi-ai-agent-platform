<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';

interface Props {
  visible: boolean;
  imageSrc: string;
  title?: string;
  loading?: boolean;
  outputSize?: number;
}

const props = withDefaults(defineProps<Props>(), {
  title: '裁剪 AI 助手头像',
  loading: false,
  outputSize: 256,
});

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'confirm', blob: Blob, previewUrl: string): void;
}>();

const cropSize = 240; // 视窗尺寸 px
const cropperZoom = ref(1);
const cropperOffset = ref({ x: 0, y: 0 });
const imgNaturalWidth = ref(0);
const imgNaturalHeight = ref(0);
const cropperInitWidth = ref(0);
const cropperInitHeight = ref(0);
const isImageReady = ref(false);

const isDragging = ref(false);
const dragStart = ref({ x: 0, y: 0 });

const cropperImageStyle = computed(() => {
  return {
    width: `${cropperInitWidth.value}px`,
    height: `${cropperInitHeight.value}px`,
    transform: `translate(${cropperOffset.value.x}px, ${cropperOffset.value.y}px) scale(${cropperZoom.value})`,
    transformOrigin: 'center center',
  };
});

// 计算小预览图样式（用于右侧头像预览卡片）
const previewScale = computed(() => 64 / cropSize); // 64px 预览框
const previewImageStyle = computed(() => {
  const scale = previewScale.value;
  return {
    width: `${cropperInitWidth.value * scale}px`,
    height: `${cropperInitHeight.value * scale}px`,
    transform: `translate(${cropperOffset.value.x * scale}px, ${cropperOffset.value.y * scale}px) scale(${cropperZoom.value})`,
    transformOrigin: 'center center',
  };
});

const initImage = () => {
  if (!props.imageSrc) return;
  isImageReady.value = false;
  const img = new Image();
  img.src = props.imageSrc;
  img.onload = () => {
    imgNaturalWidth.value = img.naturalWidth;
    imgNaturalHeight.value = img.naturalHeight;
    const ratio = Math.max(cropSize / img.naturalWidth, cropSize / img.naturalHeight);
    cropperInitWidth.value = img.naturalWidth * ratio;
    cropperInitHeight.value = img.naturalHeight * ratio;
    cropperZoom.value = 1;
    cropperOffset.value = { x: 0, y: 0 };
    isImageReady.value = true;
  };
};

watch(
  () => [props.visible, props.imageSrc],
  ([newVisible, newSrc]) => {
    if (newVisible && newSrc) {
      nextTick(() => {
        initImage();
      });
    }
  },
  { immediate: true },
);

// 拖拽控制
const onMouseDown = (e: MouseEvent) => {
  if (!isImageReady.value || props.loading) return;
  isDragging.value = true;
  dragStart.value = {
    x: e.clientX - cropperOffset.value.x,
    y: e.clientY - cropperOffset.value.y,
  };
};

const onMouseMove = (e: MouseEvent) => {
  if (!isDragging.value) return;
  cropperOffset.value = {
    x: e.clientX - dragStart.value.x,
    y: e.clientY - dragStart.value.y,
  };
};

const onMouseUp = () => {
  isDragging.value = false;
};

// 滚轮缩放控制
const onWheel = (e: WheelEvent) => {
  if (!isImageReady.value || props.loading) return;
  const delta = e.deltaY < 0 ? 0.08 : -0.08;
  const nextZoom = Math.min(Math.max(cropperZoom.value + delta, 0.3), 3.5);
  cropperZoom.value = parseFloat(nextZoom.toFixed(2));
};

// 移动端触摸
const onTouchStart = (e: TouchEvent) => {
  if (!isImageReady.value || props.loading || e.touches.length !== 1) return;
  const touch = e.touches[0];
  if (!touch) return;
  isDragging.value = true;
  dragStart.value = {
    x: touch.clientX - cropperOffset.value.x,
    y: touch.clientY - cropperOffset.value.y,
  };
};

const onTouchMove = (e: TouchEvent) => {
  if (!isDragging.value || e.touches.length !== 1) return;
  const touch = e.touches[0];
  if (!touch) return;
  cropperOffset.value = {
    x: touch.clientX - dragStart.value.x,
    y: touch.clientY - dragStart.value.y,
  };
};

const resetTransform = () => {
  cropperZoom.value = 1;
  cropperOffset.value = { x: 0, y: 0 };
};

const handleConfirm = () => {
  if (!props.imageSrc || !isImageReady.value || props.loading) return;

  const img = new Image();
  img.src = props.imageSrc;
  img.onload = () => {
    const canvas = document.createElement('canvas');
    const outSize = props.outputSize;
    canvas.width = outSize;
    canvas.height = outSize;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const scaleFactor = outSize / cropSize;

    const initW = cropperInitWidth.value;
    const initH = cropperInitHeight.value;
    const drawW = initW * cropperZoom.value;
    const drawH = initH * cropperZoom.value;

    const x = (cropSize - initW) / 2 + cropperOffset.value.x;
    const y = (cropSize - initH) / 2 + cropperOffset.value.y;

    const drawX = x - (drawW - initW) / 2;
    const drawY = y - (drawH - initH) / 2;

    ctx.drawImage(
      img,
      drawX * scaleFactor,
      drawY * scaleFactor,
      drawW * scaleFactor,
      drawH * scaleFactor,
    );

    canvas.toBlob(
      (blob) => {
        if (blob) {
          const previewUrl = canvas.toDataURL('image/png');
          emit('confirm', blob, previewUrl);
        }
      },
      'image/png',
      0.95,
    );
  };
};
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[12000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm transition-opacity"
      @click.self="!loading && emit('close')"
    >
      <div
        class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-lg w-full overflow-hidden border border-gray-100 dark:border-gray-700 flex flex-col transform animate-fade-in-up"
        @mouseup="onMouseUp"
      >
        <!-- Header -->
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/70 dark:bg-gray-900/40">
          <div class="flex items-center space-x-2">
            <div class="p-2 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-xl">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100">{{ title }}</h3>
              <p class="text-[11px] text-gray-400">支持缩放与拖拽平移，生成标准清晰的圆形头像</p>
            </div>
          </div>
          <button
            :disabled="loading"
            @click="emit('close')"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Body -->
        <div class="p-5 flex flex-col items-center select-none space-y-5">
          <div class="flex flex-col sm:flex-row items-center justify-center gap-6 w-full">
            <!-- 裁剪交互主视窗 -->
            <div
              class="relative w-[240px] h-[240px] rounded-2xl bg-gray-900 overflow-hidden cursor-move shadow-inner border border-gray-200 dark:border-gray-700 shrink-0"
              @mousedown="onMouseDown"
              @mousemove="onMouseMove"
              @mouseup="onMouseUp"
              @mouseleave="onMouseUp"
              @wheel.prevent="onWheel"
              @touchstart="onTouchStart"
              @touchmove="onTouchMove"
              @touchend="onMouseUp"
            >
              <img
                v-if="imageSrc && isImageReady"
                :src="imageSrc"
                alt="头像裁剪源"
                class="absolute pointer-events-none max-w-none select-none transition-none"
                :style="cropperImageStyle"
              />
              <!-- 遮罩与圆形虚线取景区 -->
              <div class="absolute inset-0 pointer-events-none flex items-center justify-center">
                <!-- 圆形头像聚焦虚线与外层半透明遮罩 -->
                <div class="w-[200px] h-[200px] rounded-full border-2 border-dashed border-blue-500 shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] z-10 flex items-center justify-center">
                  <div class="w-2.5 h-2.5 rounded-full bg-blue-500/60"></div>
                </div>
              </div>
            </div>

            <!-- 右侧/下方：实时对话头像效果预览与复位 -->
            <div class="flex flex-col items-center justify-center space-y-3 bg-gray-50 dark:bg-gray-900/30 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700/50 w-full sm:w-44">
              <span class="text-xs font-bold text-gray-500 dark:text-gray-400">实时气泡效果预览</span>
              
              <!-- 模拟聊天气泡头像样式 -->
              <div class="relative w-16 h-16 rounded-full ring-2 ring-blue-500/30 ring-offset-2 ring-offset-white dark:ring-offset-gray-800 overflow-hidden bg-gray-100 dark:bg-gray-800 shadow-md flex items-center justify-center">
                <div
                  v-if="imageSrc && isImageReady"
                  class="absolute w-[64px] h-[64px] flex items-center justify-center overflow-hidden"
                >
                  <img
                    :src="imageSrc"
                    alt="Preview"
                    class="pointer-events-none max-w-none select-none"
                    :style="previewImageStyle"
                  />
                </div>
              </div>

              <div class="text-[11px] text-gray-400 text-center font-mono">
                标准 256×256 清晰
              </div>

              <button
                type="button"
                @click="resetTransform"
                class="px-2.5 py-1 text-[11px] font-medium text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors shadow-xs"
              >
                居中复位
              </button>
            </div>
          </div>

          <!-- 缩放滑块控制条 -->
          <div class="w-full max-w-sm flex flex-col space-y-2">
            <div class="flex justify-between items-center px-1">
              <span class="text-xs font-bold text-gray-500 dark:text-gray-400">缩放大小</span>
              <span class="text-xs font-mono font-bold text-blue-600 dark:text-blue-400">{{ Math.round(cropperZoom * 100) }}%</span>
            </div>
            <div class="flex items-center space-x-3">
              <button
                type="button"
                @click="cropperZoom = Math.max(0.3, parseFloat((cropperZoom - 0.1).toFixed(2)))"
                class="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                title="缩小"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4" /></svg>
              </button>
              <input
                v-model.number="cropperZoom"
                type="range"
                min="0.3"
                max="3.5"
                step="0.05"
                class="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-600 focus:outline-none"
              />
              <button
                type="button"
                @click="cropperZoom = Math.min(3.5, parseFloat((cropperZoom + 0.1).toFixed(2)))"
                class="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                title="放大"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" /></svg>
              </button>
            </div>
          </div>

          <p class="text-[11px] text-gray-400 dark:text-gray-500 text-center leading-relaxed">
            💡 提示：鼠标左键拖拽可微调位置，滚动鼠标滚轮可平滑缩放。<br/>
            即使上传超大高分辨率原图，系统也会自动无损裁剪压缩，极速呈现。
          </p>
        </div>

        <!-- Footer -->
        <div class="px-5 py-3.5 bg-gray-50/70 dark:bg-gray-900/40 border-t border-gray-100 dark:border-gray-700 flex justify-end items-center space-x-2.5">
          <button
            type="button"
            :disabled="loading"
            @click="emit('close')"
            class="px-4 py-2 rounded-xl text-xs font-bold text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="loading || !isImageReady"
            @click="handleConfirm"
            class="px-5 py-2 rounded-xl text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-95 transition-all shadow-sm focus:outline-none flex items-center space-x-1.5 disabled:opacity-50"
          >
            <svg
              v-if="loading"
              class="animate-spin h-3.5 w-3.5 text-white"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
            </svg>
            <span>{{ loading ? '处理上传中...' : '确认裁剪并应用' }}</span>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
