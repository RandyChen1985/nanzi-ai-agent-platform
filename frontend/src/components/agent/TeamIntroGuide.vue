<script setup lang="ts">
/**
 * 「团队介绍」写法指引：这段文字会作为系统提示词的「你的团队」段，
 * 模型会照着执行，所以它既是说明也是约束——写得好能约束行为，写得空等于没写。
 *
 * 做成可展开的内联说明而不是 popover：弹窗 body 是滚动容器，绝对定位浮层会被
 * 裁掉；内容也不短，浮层放不下。
 */
const EXAMPLE = [
  "涉及指标口径、建表语句的问题，先转元数据专家确认口径，再让数据助手取数。",
  "需要制度依据时转规章问答，不要自行推断。",
  "取数一律只读，禁止生成写库语句。",
  "超出以上范围的问题，直接说明该找哪个智能体，不要勉强作答。",
].join("\n");

const emit = defineEmits<{ apply: [value: string] }>();
</script>

<template>
  <div
    class="mt-2 rounded-xl border border-primary/30 bg-primary/5 p-3 text-[11px] leading-relaxed text-gray-700"
  >
    <p class="font-semibold text-gray-800">这段文字会进模型提示词，写法有讲究</p>
    <p class="mt-1 text-gray-600">
      它会作为「你的团队」段写进<b>团主专家自己</b>的系统提示词，模型会照着执行。
      写得好能约束行为，写空话则等于没写。
    </p>

    <div class="mt-2 grid gap-1.5">
      <p class="text-emerald-700">
        ✓ 写「什么情况找谁」和先后顺序，别写成员名单——成员清单模型本来就有
      </p>
      <p class="text-emerald-700">
        ✓ 写禁止项（「只读不写库」「不处理报销」），比写期望更能约束行为
      </p>
      <p class="text-emerald-700">
        ✓ 短句祈使句，一条一行；1~5 句最有效，超过会稀释其他指令
      </p>
      <p class="text-rose-600">✗ 不写营销话术（「专业高效的一站式团队」）——模型无法执行</p>
      <p class="text-rose-600">✗ 不写「本团专办 XX」除非描述信息也这么写，否则用户会困惑</p>
    </div>

    <div class="mt-3 rounded-lg border border-gray-200 bg-white p-2.5">
      <p class="mb-1 font-semibold text-gray-700">示例（可直接套用）</p>
      <pre class="whitespace-pre-wrap break-words font-sans text-[11px] leading-relaxed text-gray-600">{{ EXAMPLE }}</pre>
      <button
        type="button"
        class="mt-2 rounded-lg border border-primary/40 bg-white px-2 py-1 text-[11px] font-medium text-primary transition hover:bg-primary/10"
        @click="emit('apply', EXAMPLE)"
      >
        填入这段示例
      </button>
    </div>
  </div>
</template>
