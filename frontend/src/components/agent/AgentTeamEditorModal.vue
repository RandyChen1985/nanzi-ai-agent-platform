<script setup lang="ts">
/**
 * 专家团设置：只改「这个智能体是不是团主专家」和团的展示文案。
 *
 * 刻意不动版本、不动工具、不动提示词——团身份是智能体级元数据，改完立刻生效；
 * 而成员来自**已发布版本**的调度范围，属于版本级配置，所以这里只读展示成员，
 * 真正的成员增删要走「配置调度范围」（打开「配置与发布」抽屉）。
 *
 * 层级：与 DelegationScopeModal 一致用 10010，避免被版本抽屉（9990）盖住。
 */
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";
import TeamIntroGuide from "./TeamIntroGuide.vue";

const TEAM_EDITOR_Z_INDEX = 10010;

export interface TeamMemberRow {
  id: string;
  display_name: string;
  name: string;
  description: string;
  avatar_url?: string | null;
  is_lead: boolean;
  issue: { label: string; cls: string } | null;
}

const props = defineProps<{
  model: boolean;
  agent: any | null;
  members: TeamMemberRow[];
  saving?: boolean;
}>();

const emit = defineEmits<{
  "update:model": [value: boolean];
  save: [{ is_expert_team_lead: boolean; team_profile: { name: string; intro: string } | null }];
  "configure-scope": [];
}>();

const isLead = ref(false);
/** 写法指引走弹层（与智能体编辑弹窗一致）：内联展开会把设置页撑长 */
const showIntroHelp = ref(false);
const teamName = ref("");
const teamIntro = ref("");

// 每次换团都重置：残留上一个团的文案会被误存进当前团
watch(
  () => props.agent,
  (agent) => {
    if (!agent) {
      isLead.value = false;
      teamName.value = "";
      teamIntro.value = "";
      return;
    }
    isLead.value = agent.is_expert_team_lead === true;
    teamName.value = agent.team_profile?.name || "";
    teamIntro.value = agent.team_profile?.intro || "";
  },
  { immediate: true },
);

const close = () => emit("update:model", false);

const childCount = computed(() => props.members.filter((row) => !row.is_lead).length);

/** 从写法指引里「填入示例」：填完收起，用户接着改 */
const applyIntroExample = (value: string) => {
  teamIntro.value = value;
  showIntroHelp.value = false;
};

const submit = () => {
  if (props.saving) return;
  const name = teamName.value.trim();
  const intro = teamIntro.value.trim();
  emit("save", {
    is_expert_team_lead: isLead.value,
    // 两项都空时传 null：后端也会归一化，这里先传干净值避免存一个空壳对象
    team_profile: name || intro ? { name, intro } : null,
  });
};
</script>

<template>
  <Modal
    :show="model"
    title="专家团设置"
    size="max-w-xl"
    :z-index="TEAM_EDITOR_Z_INDEX"
    @close="close"
    @update:model-value="close"
  >
    <div v-if="agent" class="-mt-2 space-y-4">
      <!-- 身份开关 -->
      <div class="rounded-xl border border-gray-200 bg-gray-50/70 p-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 text-sm font-medium text-gray-700">
              <span>👥</span><span>专家团主专家</span>
            </div>
            <p class="mt-0.5 text-[11px] leading-relaxed text-gray-500">
              打开后会在智能体中心以「专家团」形式展示；关闭不影响它作为普通智能体使用，已配好的调度范围会保留。
            </p>
          </div>
          <button
            type="button"
            class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
            :class="isLead ? 'bg-green-500' : 'bg-gray-200'"
            role="switch"
            :aria-checked="isLead"
            @click="isLead = !isLead"
          >
            <span
              aria-hidden="true"
              class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
              :class="isLead ? 'translate-x-4' : 'translate-x-0'"
            ></span>
          </button>
        </div>
      </div>

      <template v-if="isLead">
        <div>
          <label class="mb-1 block text-xs font-medium text-gray-600">专家团名称</label>
          <input
            v-model="teamName"
            type="text"
            maxlength="100"
            placeholder="例如：数据分析专家团（留空用展示名）"
            class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
        </div>
        <div>
          <label class="mb-1 flex items-center gap-1 text-xs font-medium text-gray-600">
            <span>团队介绍&amp;提示词</span>
            <button
              type="button"
              class="grid h-4 w-4 shrink-0 place-items-center rounded-full border border-gray-300 text-[10px] font-bold leading-none text-gray-500 transition hover:border-primary hover:text-primary"
              title="怎么写才有效"
              aria-haspopup="dialog"
              @click.stop="showIntroHelp = true"
            >
              ?
            </button>
          </label>
          <textarea
            v-model="teamIntro"
            rows="3"
            maxlength="500"
            placeholder="例如：涉及指标口径的先转元数据专家确认，再让数据助手取数；取数只读，禁止写库。"
            class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          ></textarea>
          <p class="mt-1 flex items-start justify-between gap-2 text-[11px] text-gray-400">
            <span>写协作流程与能力边界；这段话会进团主专家的系统提示词，不影响主助手的选人判断。</span>
            <span class="shrink-0 tabular-nums">{{ (teamIntro || '').length }}/500</span>
          </p>
        </div>

        <!-- 成员只读：成员属于版本级调度范围，这里不给改 -->
        <div class="rounded-xl border border-gray-200">
          <div
            class="flex items-center justify-between rounded-t-xl border-b border-gray-100 bg-gray-50/70 px-3 py-2"
          >
            <span class="text-xs font-semibold text-gray-700">团队成员</span>
            <span class="text-[11px] text-gray-400">{{ childCount }} 位子专家</span>
          </div>
          <div class="divide-y divide-gray-100">
            <div
              v-for="member in members"
              :key="member.id"
              class="flex items-start gap-2 px-3 py-2"
            >
              <div
                class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center overflow-hidden rounded-md bg-gray-100 text-[10px] font-bold text-gray-400"
              >
                <img
                  v-if="member.avatar_url"
                  :src="member.avatar_url"
                  class="h-full w-full object-cover"
                  alt=""
                />
                <span v-else>{{ (member.display_name || "E").slice(0, 1) }}</span>
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="truncate text-xs font-medium text-gray-800">{{
                    member.display_name
                  }}</span>
                  <span
                    class="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium"
                    :class="
                      member.is_lead ? 'bg-sky-50 text-sky-700' : 'bg-emerald-50 text-emerald-700'
                    "
                    >{{ member.is_lead ? "主专家" : "子专家" }}</span
                  >
                  <span
                    v-if="member.issue"
                    class="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium"
                    :class="member.issue.cls"
                    >{{ member.issue.label }}</span
                  >
                </div>
              </div>
            </div>
            <div v-if="!members.length" class="px-3 py-4 text-center text-[11px] text-gray-400">
              还没有成员：去「配置调度范围」勾选可委派的子智能体
            </div>
          </div>
          <div class="rounded-b-xl border-t border-gray-100 bg-gray-50/70 px-3 py-2">
            <p class="text-[11px] leading-relaxed text-gray-500">
              成员不在这里单独维护：改主专家的调度范围，这个团就跟着变。
            </p>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="flex w-full items-center justify-between gap-2">
        <button
          type="button"
          class="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
          title="打开「配置与发布」，在工具里给 sub_agent_call 勾选可委派的子智能体"
          @click="emit('configure-scope')"
        >
          配置调度范围
        </button>
        <div class="flex gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
            @click="close"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-lg bg-primary px-4 py-1.5 text-xs font-semibold text-white transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="saving"
            @click="submit"
          >
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
    </template>
  </Modal>

  <!-- 写法指引弹层：z 比「专家团设置」本身（10010）高一层 -->
  <Teleport to="body">
    <div
      v-if="showIntroHelp"
      class="fixed inset-0 z-[10020] flex items-center justify-center bg-black/40 p-4"
      @click.self="showIntroHelp = false"
    >
      <div class="w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-lg font-bold text-gray-900">团队介绍&提示词怎么写？</h3>
            <p class="mt-1 text-sm text-gray-500">
              这段话会写进团主专家的系统提示词（团队身份段），模型会照着执行；它不进「描述信息」，所以不会影响主助手的委派选人。
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="关闭"
            @click="showIntroHelp = false"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="max-h-[70vh] overflow-y-auto px-6 py-5">
          <TeamIntroGuide hide-intro @apply="applyIntroExample" />
        </div>
        <div class="flex justify-end border-t border-gray-100 px-6 py-4">
          <button
            type="button"
            class="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary-dark"
            @click="showIntroHelp = false"
          >知道了</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
