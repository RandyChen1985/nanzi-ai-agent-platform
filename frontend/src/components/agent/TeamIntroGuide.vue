<script setup lang="ts">
/**
 * 「团队介绍&提示词」写法指引：这段文字会作为系统提示词的「你的团队」段，
 * 模型会照着执行，所以它既是说明也是约束——写得好能约束行为，写得空等于没写。
 *
 * 现在挂在「?」弹层里（能力标签的「?」也是弹层，两处交互统一）。弹层已有标题，
 * 用 `hide-intro` 隐掉组件自带的标题与导语，避免一个弹层里出现两遍。
 *
 * 内容分四块：三条法则 / 破坏性写法 / 场景模板 / 填写前自查。
 * 「破坏性写法」那一节来自真实事故：有团主因为介绍里写了「不查不写」，把
 * 「我不动手」读成了「我不委派」，于是只回一句「请你自己去【资产查询】办」。
 */
const RULES = [
  "写「什么情况找谁」和先后顺序——成员名单模型本来就有，缺的是判断依据",
  "写禁止项（「只读不写库」「不处理报销」），比写期望更能约束行为",
  "短句祈使句，一条一行；1~5 句最有效，超过会稀释其他指令",
];

/**
 * 破坏性写法：这些句子本身读起来很合理，但会让团主少干活或干错活。
 * `bad` 是用户的原始写法，`effect` 是模型实际会怎么理解，`good` 是能达成同一目的的写法。
 */
const DANGEROUS = [
  {
    bad: "「不查不写」「这里什么都不许做」",
    effect: "模型连「转交」都不做了，只回一句「请你自己去【资产查询】办理」——它把「我不动手」读成了「我不委派」",
    good: "「查数、写库由成员执行，你负责判断该交给谁」",
  },
  {
    bad: "「所有需求一律转给 XX」",
    effect: "闲聊、问时间、问你是谁也会被推给子专家；用户被推来推去，体验比不转更差",
    good: "「涉及设备台账的转资产查询；其他问题你自己答完」",
  },
  {
    bad: "在团介绍里写「必须直选，禁止委派」",
    effect: "这条会被泛化到整个团，连只读查询都不敢转。它本该只约束某一个成员",
    good: "写进「资产操作」那位子专家的描述里，而不是团介绍",
  },
  {
    bad: "把成员名单抄进介绍（「本团有 A、B、C 三位专家」）",
    effect: "成员增删后这里不会同步，模型按旧名单叫人，调一个不存在的标识",
    good: "不写名单——系统会自动附上当前可委派的成员清单",
  },
  {
    bad: "「严禁回答任何业务问题」",
    effect: "团主退化成纯转发壳：用户问一句简单的话也要走一遍委派，还常常转错人",
    good: "「业务结论以成员返回为准，你可以先帮用户澄清需求」",
  },
  {
    bad: "自相矛盾的两条（「必须自己查清楚」+「不许调用任何工具」）",
    effect: "两条都执行不了，模型随机挑一条，行为时好时坏、难以复现",
    good: "只留一条，把另一条改写成条件句（「若信息不足，先问用户」）",
  },
];

/** 场景模板：覆盖最常见的几种团，点一下即可填入再改 */
const CASES = [
  {
    key: "query",
    label: "数据查询型",
    hint: "口径 → 取数两段式，只有只读操作",
    text: [
      "涉及指标口径、字段含义的问题，先转元数据专家确认口径，再让数据助手取数。",
      "取数一律只读，禁止生成写库语句。",
      "超出以上范围的问题，直接说明该找哪个智能体，不要勉强作答。",
    ].join("\n"),
  },
  {
    key: "mixed",
    label: "查询 + 写操作混合",
    hint: "写操作必须用户亲手选人，查询可以正常转交",
    text: [
      "查询类请求转资产查询。",
      "新增、修改、报废等写操作，必须由用户直接选定「资产操作」专家办理，你不要代为提交。",
      "写操作前先确认目标对象和影响范围，信息不全时先追问。",
      "你自己能直接回答的流程性问题就自己答，不要为了走流程而转交。",
    ].join("\n"),
  },
  {
    key: "export",
    label: "文档 / 清单产出",
    hint: "先取数再产出，避免空条件导出",
    text: [
      "需要按模板产出清单或报告时，转模板导出专家，并说明模板要求与筛选条件。",
      "不要在未取数或条件不完整的情况下让成员直接导出。",
      "产出物由成员生成，你负责确认口径与范围是否与用户诉求一致。",
    ].join("\n"),
  },
  {
    key: "support",
    label: "客服 / 工单分流",
    hint: "先分类，再决定自答还是转交",
    text: [
      "先判断用户诉求属于咨询、报障还是投诉，再决定交给谁。",
      "报障类必须收集设备编号、发生时间和现象；信息不全先追问，不要直接转交。",
      "咨询类问题若能直接答，就直接答完，不要一律转给人工。",
    ].join("\n"),
  },
  {
    key: "audit",
    label: "只读风控 / 合规审查",
    hint: "结论必须带依据，禁止自行定性",
    text: [
      "所有查询只读，不得生成任何写库、改配置的语句。",
      "涉及权限、审批、合规结论的，转合规审查专家，你不要自行判断。",
      "成员返回的结论若缺少依据，要求补充来源后再回复用户。",
    ].join("\n"),
  },
];

withDefaults(defineProps<{ hideIntro?: boolean }>(), { hideIntro: false });

const emit = defineEmits<{ apply: [value: string] }>();
</script>

<template>
  <div class="text-[11px] leading-relaxed text-gray-700">
    <template v-if="!hideIntro">
      <p class="font-semibold text-gray-800">这段文字会进模型提示词，写法有讲究</p>
      <p class="mt-1 text-gray-600">
        它会作为「你的团队」段写进<b>团主专家自己</b>的系统提示词，模型会照着执行。
        写得好能约束行为，写空话则等于没写。
      </p>
    </template>

    <!-- 三条法则 -->
    <div class="mt-3 space-y-1">
      <p class="font-semibold text-gray-800">三条法则</p>
      <p v-for="rule in RULES" :key="rule" class="flex gap-1.5 text-emerald-700">
        <span class="shrink-0">✓</span><span>{{ rule }}</span>
      </p>
    </div>

    <!-- 破坏性写法：这一节最值钱，写错会让团主少干活 -->
    <div class="mt-4">
      <p class="font-semibold text-gray-800">这些写法会「劝住」团主，别用</p>
      <p class="mt-0.5 text-gray-500">读起来都很合理，但会让它少干活或干错活。</p>
      <div class="mt-2 space-y-2">
        <div
          v-for="item in DANGEROUS"
          :key="item.bad"
          class="rounded-lg border border-rose-200 bg-white p-2.5"
        >
          <p class="font-medium text-rose-700">
            <span class="mr-1">✗</span>{{ item.bad }}
          </p>
          <p class="mt-1 text-gray-600">
            <span class="text-gray-400">会发生什么：</span>{{ item.effect }}
          </p>
          <p class="mt-1 rounded bg-emerald-50 px-2 py-1 text-emerald-800">
            <span class="text-emerald-600">改成：</span>{{ item.good }}
          </p>
        </div>
      </div>
    </div>

    <!-- 场景模板 -->
    <div class="mt-4">
      <p class="font-semibold text-gray-800">按场景套用</p>
      <p class="mt-0.5 text-gray-500">挑最接近的一版填进去，再按你的实际成员改名字。</p>
      <div class="mt-2 space-y-2">
        <div
          v-for="item in CASES"
          :key="item.key"
          class="rounded-lg border border-gray-200 bg-white p-2.5"
        >
          <div class="flex items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="font-semibold text-gray-800">{{ item.label }}</p>
              <p class="text-[10px] text-gray-500">{{ item.hint }}</p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg border border-primary/40 bg-white px-2 py-1 text-[11px] font-medium text-primary transition hover:bg-primary/10"
              @click="emit('apply', item.text)"
            >
              填入这版
            </button>
          </div>
          <pre class="mt-2 whitespace-pre-wrap break-words rounded bg-gray-50 px-2 py-1.5 font-sans text-[11px] leading-relaxed text-gray-600">{{ item.text }}</pre>
        </div>
      </div>
    </div>

    <!-- 自查 -->
    <div class="mt-4 rounded-xl border border-primary/30 bg-primary/5 p-3">
      <p class="font-semibold text-gray-800">填写前自查三问</p>
      <ul class="mt-1 space-y-0.5 text-gray-600">
        <li>· 每一句都能被执行吗？出现「专业」「高效」「一站式」这类词就是不能。</li>
        <li>· 有没有哪句会让它「什么都不做」？凡是「不许 / 禁止」都补一句「那该由谁做」。</li>
        <li>· 跟成员的职责描述冲突吗？冲突时模型行为会摇摆，以成员的描述为准。</li>
      </ul>
    </div>
  </div>
</template>
