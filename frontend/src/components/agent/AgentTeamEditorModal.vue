<script setup lang="ts">
/**
 * 专家团设置：改「这个智能体是不是团主专家」、团的展示文案，以及**成员名单**。
 *
 * 两条保存路径刻意分开，因为它们生效方式本来就不一样：
 * - 团身份与文案是智能体级元数据，点保存立刻生效；
 * - 成员名单存在版本工具的 `delegable_agent_ids` 上，保存时后端复制一份已发布版本、
 *   只替换名单并立即发布（隐式新版本），其余配置原样保留。
 *
 * 成员可编辑的前提是「有已发布版本 + 启用了委派工具」：不满足时把成员区换成说明，
 * 而不是让用户点保存才报错。未启用工具时允许一并启用，但**默认不勾**——
 * 默认勾选会让「只想改团名」也复制一个新版本并强行开启工具。
 *
 * 层级：10010，被版本抽屉（9990）盖住是刻意的；成员选择弹窗再高一层（10020）。
 */
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";
import TeamIntroGuide from "./TeamIntroGuide.vue";
import DelegationScopeModal from "./DelegationScopeModal.vue";
// 只引类型（编译后擦除）。接口返回什么就用什么，避免父组件与本组件各写一份、
// 一改就漂移——这种漂移平时不报错，只在字段增删时才炸，很难查。
import type { DelegationScopeState } from "../../api/agent";

const TEAM_EDITOR_Z_INDEX = 10010;
/** 成员选择必须压在团设置之上：两者同为 10010 会互相盖住。 */
const MEMBER_PICKER_Z_INDEX = 10020;

export interface TeamMemberRow {
  id: string;
  display_name: string;
  name: string;
  description: string;
  avatar_url?: string | null;
  is_lead: boolean;
  issue: { label: string; cls: string } | null;
}

/**
 * 候选智能体行。结构与 DelegationScopeModal 的 `DelegationScopeAgent` 一致，
 * 但按本仓库惯例**不跨 SFC 引类型**（父组件也是本地定义 TeamMemberRow），
 * 避免 SFC 类型导出在不同构建链上的差异。
 */
export interface ScopeAgentRow {
  id: string;
  name: string;
  display_name?: string | null;
  avatar_url?: string | null;
  /** 用来判断「未发布」：LOCAL 引擎没有已发布版本时保存前就该提醒 */
  published_version_number?: number | null;
  engine_type?: string | null;
  description?: string | null;
  capabilities?: string[] | null;
  is_system?: boolean | null;
  is_enabled?: boolean | null;
  created_by?: string | null;
  is_expert_team_lead?: boolean | null;
  team_name?: string | null;
  team_member_ids?: string[] | null;
}

const props = defineProps<{
  model: boolean;
  agent: any | null;
  members: TeamMemberRow[];
  saving?: boolean;
  /** null = 还没取到（按只读处理），不猜。 */
  scopeState?: DelegationScopeState | null;
  /** 可委派的候选智能体，直接转给成员选择弹窗。 */
  scopeAgents?: ScopeAgentRow[];
  authorizedAgentIds?: string[] | null;
  scopeLoading?: boolean;
}>();

const emit = defineEmits<{
  "update:model": [value: boolean];
  save: [
    {
      is_expert_team_lead: boolean;
      team_profile: { name: string; intro: string } | null;
      /** 名单或「一并启用工具」是否真的变了。false 时父组件**不要**写版本。 */
      scope_dirty: boolean;
      delegable_agent_ids: string[];
      enable_delegation_tool: boolean;
    },
  ];
  "configure-scope": [];
}>();

const isLead = ref(false);
/** 写法指引走弹层（与智能体编辑弹窗一致）：内联展开会把设置页撑长 */
const showIntroHelp = ref(false);
/** 成员选择弹窗。声明必须早于下面那个 immediate watch，否则会撞 TDZ。 */
const showMemberPicker = ref(false);
const teamName = ref("");
const teamIntro = ref("");

// 每次换团都重置：残留上一个团的文案会被误存进当前团
// 盯 `model` 而不是 agent 引用：同一个团取消后再打开，agent 对象没变，
// 只盯 agent 会让上次没保存的脏数据（改了没存的团名、删掉的成员）留在表单里。
watch(
  () => [props.model, props.agent] as const,
  ([open, agent]) => {
    if (!agent) {
      isLead.value = false;
      teamName.value = "";
      teamIntro.value = "";
      return;
    }
    if (!open) return;
    isLead.value = agent.is_expert_team_lead === true;
    teamName.value = agent.team_profile?.name || "";
    teamIntro.value = agent.team_profile?.intro || "";
    showIntroHelp.value = false;
    showMemberPicker.value = false;
  },
  { immediate: true },
);

const close = () => emit("update:model", false);

/* childCount 已由 draftMemberIds.length 取代：列表现在跟着草稿走，不再读只读的 members。 */

/** 从写法指引里「填入示例」：填完收起，用户接着改 */
const applyIntroExample = (value: string) => {
  teamIntro.value = value;
  showIntroHelp.value = false;
};

/* ===== 成员编辑 ===== */
/** 草稿名单：只包含子专家。团主自己永远不进名单（它是委派发起方，不是目标）。 */
const draftMemberIds = ref<string[]>([]);
/**
 * 未启用委派工具时，是否同意这次保存一并启用。
 *
 * **默认不勾**：默认勾选会让「我只想改个团名」也顺手复制一个新版本、
 * 强行开启委派工具；而关掉团开关时成员区被隐藏，用户根本看不见这个勾。
 * 现在改成用户主动点开成员区（或明确勾选）才产生意图。
 */
const enableTool = ref(false);

const scopeReady = computed(() => props.scopeState?.exists_published === true);
const toolEnabled = computed(() => props.scopeState?.sub_agent_call_enabled === true);
/** 有已发布版本、且（工具已启用 或 用户同意一并启用）才能改成员。 */
const canEditMembers = computed(
  () => scopeReady.value && (toolEnabled.value || enableTool.value),
);
const memberCap = computed(() => props.scopeState?.max_members ?? 20);
/**
 * 名单是否被显式限定过。
 *
 * false = 未配置 = 运行时「全部已授权子专家都能委派」。这时界面不能显示
 * 「还没有成员」（那看着像没人可用），更要提醒：一旦在这里保存名单，
 * 就会从「全量」收窄成「仅名单内」。
 */
const scopeConfigured = computed(() => props.scopeState?.scope_configured === true);
/**
 * 是否真的处于「工具已启用但没限定名单」= 全量可委派。
 *
 * 不能只看 `!scopeConfigured`：工具根本没启用时后端同样返回 `false`，
 * 那种情况下一个子专家都委派不了，说「现在可以委派全部、保存后会收窄」
 * 是从头到尾的错误描述。
 */
const scopeUnrestricted = computed(() => toolEnabled.value && !scopeConfigured.value);

/** 换团或拿到新状态时重置草稿，避免把上一个团的名单带过去。 */
watch(
  () => [props.agent?.id, props.scopeState] as const,
  ([agentId, scope]) => {
    if (!agentId) {
      draftMemberIds.value = [];
      return;
    }
    draftMemberIds.value = [...(scope?.delegable_agent_ids ?? [])];
    enableTool.value = false;
    showMemberPicker.value = false;
  },
  { immediate: true },
);

/**
 * 单个候选的问题标签。
 *
 * 与列表页 `memberIssue` 同口径：停用优先，其次 LOCAL 引擎还没有已发布版本。
 * 新加进来的成员不在打开弹窗时的快照里，不自己算一遍就只剩首字母方块、
 * 也看不到「未发布」——用户要等到保存、甚至等委派失败才知道这个成员不可用。
 */
const scopeAgentIssue = (row?: ScopeAgentRow): { label: string; cls: string } | null => {
  if (!row) return null;
  if (row.is_enabled === false) return { label: "已停用", cls: "bg-gray-100 text-gray-600" };
  const isLocal = (row.engine_type || "LOCAL") === "LOCAL";
  if (isLocal && (row.published_version_number ?? null) === null) {
    return { label: "未发布", cls: "bg-rose-50 text-rose-600" };
  }
  return null;
};

/** 列表行：主专家单独一行（不可移除），其余按草稿名单顺序展示。 */
const memberRows = computed(() => {
  const lead = props.members.find((row) => row.is_lead) || null;
  const rows = draftMemberIds.value.map((id) => {
    const fromTeam = props.members.find((row) => row.id === id);
    const fromAgents = (props.scopeAgents || []).find((row) => row.id === id);
    return {
      id,
      display_name:
        fromTeam?.display_name || fromAgents?.display_name || fromAgents?.name || id,
      // 头像与标签都要能回落到候选列表：新加的成员只存在于 scopeAgents 里
      avatar_url: fromTeam?.avatar_url ?? fromAgents?.avatar_url ?? null,
      issue: fromTeam?.issue ?? scopeAgentIssue(fromAgents),
    };
  });
  return { lead, rows };
});

/**
 * 只有真的改了才写版本。
 *
 * 否则「只改了团名字」也会复制一份新版本，版本号一路涨、历史里全是空改动。
 * 名单按集合比较：顺序不影响调度，来回拖两下不算改动。
 */
const scopeDirty = computed(() => {
  if (!canEditMembers.value) return false;
  // 关掉团开关时成员区是隐藏的，用户看不到自己「改了什么」，
  // 这时候绝对不能因为后台状态变化替他发一个版本。
  if (!isLead.value) return false;
  if (!toolEnabled.value && enableTool.value) return true;
  const before = [...(props.scopeState?.delegable_agent_ids ?? [])].sort().join("\u0000");
  const after = [...draftMemberIds.value].sort().join("\u0000");
  return before !== after;
});

const removeMember = (id: string) => {
  draftMemberIds.value = draftMemberIds.value.filter((item) => item !== id);
};

const onMembersPicked = (ids: string[]) => {
  // 弹窗已经把团主与 main 挡在外面，这里只去掉重复，保持用户选择顺序
  draftMemberIds.value = Array.from(new Set(ids.map((id) => String(id))));
  showMemberPicker.value = false;
};

const submit = () => {
  if (props.saving) return;
  const name = teamName.value.trim();
  const intro = teamIntro.value.trim();
  emit("save", {
    is_expert_team_lead: isLead.value,
    // 两项都空时传 null：后端也会归一化，这里先传干净值避免存一个空壳对象
    team_profile: name || intro ? { name, intro } : null,
    // 不可编辑 / 没改动时父组件不写版本：传空数组会被后端当成「显式清空 = 禁止委派」，
    // 那是把「用户没碰」误读成「用户要禁掉所有子专家」。
    scope_dirty: scopeDirty.value,
    delegable_agent_ids: scopeDirty.value ? [...draftMemberIds.value] : [],
    enable_delegation_tool: !toolEnabled.value && enableTool.value,
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

        <!-- ===== 团队成员：可直接增删 ===== -->
        <div class="rounded-xl border border-gray-200">
          <div
            class="flex items-center justify-between rounded-t-xl border-b border-gray-100 bg-gray-50/70 px-3 py-2"
          >
            <span class="text-xs font-semibold text-gray-700">团队成员</span>
            <span class="text-[11px] text-gray-400">
              <template v-if="scopeLoading">读取中…</template>
              <template v-else-if="!scopeReady">不可配置</template>
              <template v-else-if="scopeUnrestricted && !draftMemberIds.length">未限制</template>
              <template v-else>{{ draftMemberIds.length }} 位子专家</template>
            </span>
          </div>

          <!-- 没有已发布版本：唯一真正的死路，说清去哪解决 -->
          <div v-if="!scopeLoading && !scopeReady" class="px-3 py-3">
            <div class="flex items-start gap-1.5">
              <span class="mt-px text-xs text-amber-500">⚠</span>
              <span class="text-xs font-medium text-amber-700">该智能体还没有已发布的版本</span>
            </div>
            <p class="mt-1 pl-5 text-[11px] leading-relaxed text-gray-500">
              成员名单保存在版本配置里，需要先发布一个版本。发布之后回到这里就能直接增删成员。
            </p>
          </div>

          <template v-else-if="scopeReady">
            <!-- 未启用委派工具：允许一并启用，用户可取消 -->
            <div
              v-if="!toolEnabled"
              class="border-b border-amber-100 bg-amber-50/70 px-3 py-2.5"
            >
              <div class="flex items-center gap-1.5 text-[11px] font-semibold text-amber-800">
                <span>ⓘ</span><span>这个版本还没启用委派工具</span>
              </div>
              <p class="mt-1 text-[11px] leading-relaxed text-amber-700/90">
                成员名单存放在 <code class="rounded bg-amber-100/80 px-1">sub_agent_call</code> 上，
                勾选后保存会一并启用（同时补上并行委派
                <code class="rounded bg-amber-100/80 px-1">sub_agent_batch_call</code>）。
              </p>
              <label class="mt-1.5 flex cursor-pointer items-center gap-1.5 text-xs text-amber-900">
                <input v-model="enableTool" type="checkbox" class="accent-primary" />
                <span>保存时一并启用委派工具</span>
              </label>
            </div>

            <!-- 未配置名单 = 全量可委派：加人会把它收窄，必须提前说 -->
            <div
              v-if="canEditMembers && scopeUnrestricted"
              class="border-b border-sky-100 bg-sky-50/60 px-3 py-2.5"
            >
              <div class="flex items-center gap-1.5 text-[11px] font-semibold text-sky-800">
                <span>ⓘ</span><span>当前没有限定调度范围</span>
              </div>
              <p class="mt-1 text-[11px] leading-relaxed text-sky-700/90">
                现在主专家可以委派<b>全部已授权</b>的子智能体。保存下面的名单后会收窄为
                <b>仅名单内</b>，名单外的一律拒绝。
              </p>
            </div>

            <!-- 成员列表 -->
            <div v-if="canEditMembers" class="divide-y divide-gray-100">
              <div v-if="memberRows.lead" class="flex items-start gap-2 px-3 py-2">
                <div
                  class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center overflow-hidden rounded-md bg-gray-100 text-[10px] font-bold text-gray-400"
                >
                  <img
                    v-if="memberRows.lead.avatar_url"
                    :src="memberRows.lead.avatar_url"
                    class="h-full w-full object-cover"
                    alt=""
                  />
                  <span v-else>{{ (memberRows.lead.display_name || "E").slice(0, 1) }}</span>
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <span class="truncate text-xs font-medium text-gray-800">{{
                      memberRows.lead.display_name
                    }}</span>
                    <span
                      class="shrink-0 rounded-full bg-sky-50 px-1.5 py-px text-[10px] font-medium text-sky-700"
                      >主专家</span
                    >
                  </div>
                </div>
              </div>

              <div
                v-for="row in memberRows.rows"
                :key="row.id"
                class="group flex items-start gap-2 px-3 py-2"
              >
                <div
                  class="mt-0.5 grid h-6 w-6 shrink-0 place-items-center overflow-hidden rounded-md bg-gray-100 text-[10px] font-bold text-gray-400"
                >
                  <img
                    v-if="row.avatar_url"
                    :src="row.avatar_url"
                    class="h-full w-full object-cover"
                    alt=""
                  />
                  <span v-else>{{ (row.display_name || "E").slice(0, 1) }}</span>
                </div>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <span class="truncate text-xs font-medium text-gray-800">{{
                      row.display_name
                    }}</span>
                    <span
                      class="shrink-0 rounded-full bg-emerald-50 px-1.5 py-px text-[10px] font-medium text-emerald-700"
                      >子专家</span
                    >
                    <span
                      v-if="row.issue"
                      class="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium"
                      :class="row.issue.cls"
                      >{{ row.issue.label }}</span
                    >
                  </div>
                </div>
                <button
                  type="button"
                  class="shrink-0 text-[11px] text-gray-400 opacity-0 transition hover:text-red-500 group-hover:opacity-100"
                  @click="removeMember(row.id)"
                >
                  移除
                </button>
              </div>

              <div v-if="!memberRows.rows.length" class="px-3 py-3 text-center text-[11px] text-gray-400">
                <template v-if="scopeUnrestricted">
                  名单未配置：全部已授权的子智能体都可被委派
                </template>
                <template v-else>还没有成员（当前禁止委派任何子专家）</template>
              </div>

              <button
                type="button"
                class="flex w-full items-center gap-1 px-3 py-2 text-[11px] text-gray-500 transition hover:bg-gray-50 hover:text-gray-800"
                :disabled="draftMemberIds.length >= memberCap"
                :class="
                  draftMemberIds.length >= memberCap
                    ? 'cursor-not-allowed text-gray-300'
                    : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800'
                "
                @click="draftMemberIds.length < memberCap && (showMemberPicker = true)"
              >
                <span class="text-sm leading-none">＋</span><span>添加成员</span>
                <span v-if="draftMemberIds.length >= memberCap" class="text-amber-600">
                  （已达 {{ memberCap }} 个上限，需先移除）
                </span>
              </button>
            </div>

            <!-- 拒绝一并启用：成员区退回只读 -->
            <div v-else class="px-3 py-3">
              <div class="flex items-start gap-1.5">
                <span class="mt-px text-xs text-amber-500">⚠</span>
                <span class="text-xs font-medium text-amber-700">不启用工具就无法配置成员</span>
              </div>
              <p class="mt-1 pl-5 text-[11px] leading-relaxed text-gray-500">
                保留当前选择的话，这次保存只会更新团名称与介绍，成员名单不会变化。
              </p>
            </div>
          </template>

          <div class="rounded-b-xl border-t border-gray-100 bg-gray-50/70 px-3 py-2">
            <p class="text-[11px] leading-relaxed text-gray-500">
              <template v-if="canEditMembers">
                保存后会生成一个新版本，<span class="text-gray-600">仅替换成员名单</span>，提示词与工具配置原样保留。
              </template>
              <template v-else>
                成员名单存放在版本配置里，无法在这里单独维护。
              </template>
            </p>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="flex w-full items-center justify-between gap-2">
        <button
          v-if="scopeReady || scopeLoading"
          type="button"
          class="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
          title="打开「配置与发布」抽屉，改提示词、模型与其他工具"
          @click="emit('configure-scope')"
        >
          打开配置与发布
        </button>
        <button
          v-else
          type="button"
          class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-700 transition hover:bg-amber-100"
          title="成员名单要存进版本配置，先去发布一个版本"
          @click="emit('configure-scope')"
        >
          去发布版本
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

  <!-- 成员选择：直接复用「配置调度范围」那个弹窗，交互与取数规则完全一致 -->
  <DelegationScopeModal
    v-model:model="showMemberPicker"
    tool-name="sub_agent_call"
    :selected-ids="draftMemberIds"
    :agents="scopeAgents || []"
    :authorized-agent-ids="authorizedAgentIds ?? null"
    :z-index="MEMBER_PICKER_Z_INDEX"
    :max-selectable="memberCap"
    :readonly="!canEditMembers"
    @save="onMembersPicked"
  />

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
