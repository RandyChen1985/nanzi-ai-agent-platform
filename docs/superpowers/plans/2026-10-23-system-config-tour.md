# 系统配置功能引导 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给系统配置页加一套全局共用的 13 步引导，状态存 Redis 单键，播完在 tab 栏右侧留一个只给自己看的重播入口。

**Architecture:** 复用 EmbedChat 的 `ChatTour.vue` 做展示层（只加一个 `missingGraceMs` 参数），新增 `useSystemConfigTour` 管"全局完成态 + sessionStorage 断点"，页面侧在 `@enter` 里切 tab / 切配置分组 / 等锚点后 `refresh()`。后端在 `app/api/portal/endpoints/system.py` 加一对 `GET/PUT /tour`，读写 `agent:tour:system_config`。

**Tech Stack:** FastAPI + Pydantic 2 + redis.asyncio（后端）；Vue 3 + TS + Tailwind（前端）；pytest（后端单测 + 前端源码契约测试）。

**Spec:** `docs/superpowers/specs/2026-10-23-system-config-tour-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/api/portal/endpoints/system.py` | 修改 | 新增 `SystemConfigTourState` / `SystemConfigTourUpdate` 模型与两个端点 |
| `tests/api/portal/test_system_config_tour_api.py` | 新建 | FakeRedis 单测：默认值、写入、读回、503、权限依赖 |
| `frontend/src/components/embed/ChatTour.vue` | 修改 | 新增 `missingGraceMs?: number` prop（默认 450） |
| `frontend/src/components/system/systemConfigTourSteps.ts` | 新建 | 13 步定义 + `SYSTEM_CONFIG_TOUR_VERSION` |
| `frontend/src/composables/system/useSystemConfigTour.ts` | 新建 | 全局状态、自动播判定、断点、完成/跳过上报、重播 |
| `frontend/src/views/SystemConfig.vue` | 修改 | 挂载 ChatTour、切 tab/分组、重播按钮、`data-tour` 锚点、修 `config-item-` id |
| `frontend/src/components/system/ModelRegistry.vue` | 修改 | `model-add` / `model-row-actions` 锚点 |
| `tests/frontend/test_system_config_tour_contract.py` | 新建 | 步骤顺序、锚点存在、权限门槛、端点/键名、断点、id 前缀一致性 |
| `tests/CHECKLIST.md` | 修改 | 补该特性的测试与交付记录 |

---

## Task 1: 后端引导状态端点

**Files:**
- Create: `tests/api/portal/test_system_config_tour_api.py`
- Modify: `app/api/portal/endpoints/system.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

```python
"""系统配置引导状态：全局单键 Redis 读写。"""

import json

import pytest

from app.api.portal.endpoints import system as system_endpoints


pytestmark = pytest.mark.no_infrastructure

TOUR_KEY = "agent:tour:system_config"


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    async def get(self, key):
        assert key == TOUR_KEY
        return self.value

    async def set(self, key, value):
        assert key == TOUR_KEY
        self.value = value
        self.saved = value


async def _resolved(value):
    return value


def admin_user():
    return {"user_id": "7", "user_name": "admin", "real_name": "超级管理员", "role": "admin"}


@pytest.mark.asyncio
async def test_get_tour_returns_defaults_when_key_missing(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    result = await system_endpoints.get_system_config_tour(admin_user())

    assert result["data"] == {
        "version": 0,
        "completed_at": "",
        "completed_by": 0,
        "completed_by_name": "",
        "skipped": False,
    }
    assert redis.saved is None  # 不写空键


@pytest.mark.asyncio
async def test_put_tour_records_completer_and_skipped(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    result = await system_endpoints.update_system_config_tour(
        system_endpoints.SystemConfigTourUpdate(
            version=1,
            completed_at="2026-10-23T15:02:11+08:00",
            skipped=True,
        ),
        admin_user(),
    )

    saved = json.loads(redis.saved)
    assert saved["version"] == 1
    assert saved["skipped"] is True
    assert saved["completed_by"] == 7
    assert saved["completed_by_name"] == "超级管理员"
    assert result["data"]["completed_at"] == "2026-10-23T15:02:11+08:00"


@pytest.mark.asyncio
async def test_get_tour_reads_back_written_state(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    await system_endpoints.update_system_config_tour(
        system_endpoints.SystemConfigTourUpdate(version=2, completed_at="2026-10-24T09:00:00+08:00"),
        admin_user(),
    )
    result = await system_endpoints.get_system_config_tour(admin_user())

    assert result["data"]["version"] == 2
    assert result["data"]["completed_by_name"] == "超级管理员"


@pytest.mark.asyncio
async def test_tour_endpoint_returns_503_without_redis(monkeypatch):
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(None))

    with pytest.raises(system_endpoints.HTTPException) as exc:
        await system_endpoints.get_system_config_tour(admin_user())
    assert exc.value.status_code == 503


def test_tour_endpoints_require_menu_permission_only():
    import inspect

    for fn in (
        system_endpoints.get_system_config_tour,
        system_endpoints.update_system_config_tour,
    ):
        source = inspect.getsource(fn)
        assert 'require_permission("menu", "menu:system:config")' in source
        assert "config_save" not in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/api/portal/test_system_config_tour_api.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_system_config_tour'`

- [ ] **Step 3: 实现端点**（追加到 `system.py` 末尾）

```python
SYSTEM_CONFIG_TOUR_KEY = "agent:tour:system_config"


class SystemConfigTourState(BaseModel):
    version: int = 0
    completed_at: str = ""
    completed_by: int = 0
    completed_by_name: str = ""
    skipped: bool = False


class SystemConfigTourUpdate(BaseModel):
    version: int = 0
    completed_at: str = ""
    skipped: bool = False


def _tour_user_name(user: Dict[str, Any]) -> str:
    return str(user.get("real_name") or user.get("user_name") or "")


async def _read_tour_state() -> SystemConfigTourState:
    redis_client = await redis.get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis 服务不可用")
    raw = await redis_client.get(SYSTEM_CONFIG_TOUR_KEY)
    if not raw:
        return SystemConfigTourState()
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    try:
        return SystemConfigTourState(**json.loads(text))
    except Exception as exc:
        logging.warning("系统配置引导状态解析失败，按未完成处理: %s", exc)
        return SystemConfigTourState()


@router.get("/tour", summary="读取系统配置引导的全局完成状态")
async def get_system_config_tour(
    user: Dict = Depends(require_permission("menu", "menu:system:config")),
):
    """引导是系统级、全局一份：任何能进系统配置页的人都可以读。"""
    state = await _read_tour_state()
    return {"code": 0, "data": state.model_dump()}


@router.put("/tour", summary="写入系统配置引导的全局完成状态")
async def update_system_config_tour(
    body: SystemConfigTourUpdate,
    user: Dict = Depends(require_permission("menu", "menu:system:config")),
):
    """结束播放（看完或跳过）都会写完成态；只要求菜单权限，任何人都能结束。"""
    redis_client = await redis.get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis 服务不可用")

    state = SystemConfigTourState(
        version=int(body.version),
        completed_at=body.completed_at.strip(),
        completed_by=int(user.get("user_id") or 0),
        completed_by_name=_tour_user_name(user),
        skipped=bool(body.skipped),
    )
    await redis_client.set(SYSTEM_CONFIG_TOUR_KEY, state.model_dump_json())
    return {"code": 0, "data": state.model_dump()}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/api/portal/test_system_config_tour_api.py -v`
Expected: 5 passed

- [ ] **Step 5: 不提交**（本项目约定：由用户决定 commit）

---

## Task 2: ChatTour 支持可调缺失宽限

**Files:**
- Modify: `frontend/src/components/embed/ChatTour.vue:37-56`

- [ ] **Step 1: 写失败断言**（Task 7 的契约测试会覆盖；此处先手改源码再统一验证，属于组件参数而非新行为）

- [ ] **Step 2: 改 prop 与常量**

```ts
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    steps: TourStep[]
    initialIndex?: number
    /**
     * 目标缺失多久才判定该步不可用。默认 450ms 够 EmbedChat 展开弹层；
     * 系统配置页切 tab、切分组都会整块重渲染，需要更宽的窗口。
     */
    missingGraceMs?: number
  }>(),
  { initialIndex: 0, missingGraceMs: 450 },
)
```

把 `MISSING_GRACE_MS` 常量删除，`measure()` 里改用 `props.missingGraceMs`。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

---

## Task 3: 步骤定义

**Files:**
- Create: `frontend/src/components/system/systemConfigTourSteps.ts`

- [ ] **Step 1: 写文件**

```ts
/**
 * 系统配置功能引导的步骤定义。
 *
 * 顺序刻意按「参数配置」左侧分组导航走，让引导本身在教那列是分组导航。
 * `tab` / `group` 是进入该步前要切过去的位置，由页面侧在 @enter 里执行。
 */

export type SysTourTab = 'models' | 'tools' | 'configs' | 'branding' | 'diagnostics' | 'logs' | 'langfuse'

export interface SystemConfigTourStep {
  id: string
  selector: string
  title: string
  body: string
  tab?: SysTourTab
  group?: string
}

export const SYSTEM_CONFIG_TOUR_VERSION = 1

export const SYSTEM_CONFIG_TOUR_STEPS: SystemConfigTourStep[] = [
  {
    id: 'sys-tabs',
    selector: '[data-tour="sys-tabs"]',
    title: '七个模块',
    body: '系统配置分七块。这次只细讲「模型管理」和「参数配置」，其余几块等用到再看。',
    tab: 'models',
  },
  {
    id: 'model-add',
    selector: '[data-tour="model-add"]',
    title: '先配模型',
    body: '新部署第一件事：点这里添加至少一个模型并启用，否则对话和 Embed 都没法用。',
  },
  {
    id: 'model-row-actions',
    selector: '[data-tour="model-row-actions"]',
    title: '测试连接',
    body: '保存后点「测试连接」，跑通再启用。密钥或地址错了，这里会显示完整请求过程。',
  },
  {
    id: 'config-toolbar',
    selector: '[data-tour="config-toolbar"]',
    title: '改完要保存',
    body: '所有参数改完点这里保存（⌘S / Ctrl+S）。左侧分组上的黄点表示该组有未保存的改动。',
    tab: 'configs',
  },
  {
    id: 'config-app_public_url',
    selector: '[data-tour="config-app_public_url"]',
    title: '对外访问地址',
    body: '填平台的公网地址（协议 + 域名 + 前缀）。下载链接、重置密码邮件、MCP 公网地址、报表深链都由它拼出来。',
    group: 'general',
  },
  {
    id: 'config-agent_context_max_tokens',
    selector: '[data-tour="config-agent_context_max_tokens"]',
    title: '上下文预算',
    body: '发给模型的上下文上限，默认 64k。超了就压缩早期对话或截断，按你的模型窗口调。',
    group: 'agent_context',
  },
  {
    id: 'config-llm_model_name',
    selector: '[data-tour="config-llm_model_name"]',
    title: '默认模型',
    body: '全局默认模型。选一个已启用、且在你的网络环境里能连通的。',
    group: 'agent',
  },
  {
    id: 'config-multimodal_model_name',
    selector: '[data-tour="config-multimodal_model_name"]',
    title: '多模态模型（可选）',
    body: '当前模型不支持识图时，用这个模型把图片解析成文字。不配也能用，只是传图片会提示不支持。',
    group: 'agent',
  },
  {
    id: 'config-embed_api_url',
    selector: '[data-tour="config-embed_api_url"]',
    title: '向量服务地址',
    body: '元数据描述与记忆摘要的向量计算都走这里。可点上方「从模型管理加载」自动填。',
    group: 'agent',
  },
  {
    id: 'config-metadata_provider',
    selector: '[data-tour="config-metadata_provider"]',
    title: '元数据来源',
    body: 'local 用本地手工字典；ragflow 走语义检索自动取描述。只有用了 RAGFlow 才选后者。',
    group: 'metadata',
  },
  {
    id: 'config-knowledge_base_enabled',
    selector: '[data-tour="config-knowledge_base_enabled"]',
    title: '知识库总开关（可选）',
    body: '用知识库才打开。关掉后下面的 RAGFlow 参数会隐藏，知识库管理与检索工具也一起禁用。',
    group: 'knowledge',
  },
  {
    id: 'config-sandbox_policy',
    selector: '[data-tour="config-sandbox_policy"]',
    title: '沙箱策略',
    body: 'local 直接在宿主机跑；docker / k8s 隔离执行；ssh 连远程机；e2b 走云端。按部署形态选，下面的参数会跟着变。',
    group: 'sandbox',
  },
  {
    id: 'config-email_service_enabled',
    selector: '[data-tour="config-email_service_enabled"]',
    title: '系统邮件（可选）',
    body: '打开后配置 SMTP，用来发审核通知、重置密码等系统邮件。不配不影响主流程。',
    group: 'email',
  },
]

export const SYSTEM_CONFIG_TOUR_TOTAL = SYSTEM_CONFIG_TOUR_STEPS.length
```

---

## Task 4: 状态机

**Files:**
- Create: `frontend/src/composables/system/useSystemConfigTour.ts`

- [ ] **Step 1: 写文件**

```ts
import axios from '@/utils/axios'
import { SYSTEM_CONFIG_TOUR_STEPS, SYSTEM_CONFIG_TOUR_VERSION } from '../../components/system/systemConfigTourSteps'

/**
 * 系统配置引导状态机。
 *
 * 与 EmbedChat 最大的差别：完成态是**全局一份**（Redis 单键），
 * 所以断点只放 sessionStorage —— 全局存步数会让下一个人从半路开始播。
 */

const ENDPOINT = '/api/portal/system/tour'
const STORAGE_KEY = 'sysconfig_tour_progress'

export interface SystemConfigTourState {
  version: number
  completed_at: string
  completed_by: number
  completed_by_name: string
  skipped: boolean
}

const emptyState = (): SystemConfigTourState => ({
  version: 0,
  completed_at: '',
  completed_by: 0,
  completed_by_name: '',
  skipped: false,
})

const readProgress = (): number => {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return 0
    const parsed = JSON.parse(raw) as { version?: number; step?: number }
    if (Number(parsed.version) !== SYSTEM_CONFIG_TOUR_VERSION) return 0
    const step = Number(parsed.step) || 0
    return Math.min(Math.max(step, 0), SYSTEM_CONFIG_TOUR_STEPS.length - 1)
  } catch {
    return 0
  }
}

const writeProgress = (step: number) => {
  try {
    window.sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: SYSTEM_CONFIG_TOUR_VERSION, step }),
    )
  } catch {
    // 隐私模式下写不进去，忽略
  }
}

export function useSystemConfigTour() {
  const state = ref<SystemConfigTourState>(emptyState())

  const isCompletedForCurrentVersion = computed(
    () => Boolean(state.value.completed_at) && state.value.version === SYSTEM_CONFIG_TOUR_VERSION,
  )

  const loadState = async (): Promise<SystemConfigTourState> => {
    try {
      const res = await axios.get(ENDPOINT)
      const data = (res.data?.data || {}) as Partial<SystemConfigTourState>
      state.value = {
        version: Number(data.version) || 0,
        completed_at: String(data.completed_at || ''),
        completed_by: Number(data.completed_by) || 0,
        completed_by_name: String(data.completed_by_name || ''),
        skipped: Boolean(data.skipped),
      }
    } catch {
      // 接口失败按未完成处理：宁可多播一次，也不让新部署的人漏掉引导
      state.value = emptyState()
    }
    return state.value
  }

  const shouldAutoPlay = () => !isCompletedForCurrentVersion.value

  /** 结束播放：看完或跳过都写全局完成态（"任何人都能结束播放"） */
  const markCompleted = async (skipped: boolean) => {
    const payload = {
      version: SYSTEM_CONFIG_TOUR_VERSION,
      completed_at: new Date().toISOString(),
      skipped,
    }
    state.value = {
      ...state.value,
      version: payload.version,
      completed_at: payload.completed_at,
      skipped,
    }
    try {
      await axios.put(ENDPOINT, payload)
    } catch {
      // 失败不重试：下次进来仍是未完成，可接受
    }
  }

  return {
    state,
    isCompletedForCurrentVersion,
    loadState,
    shouldAutoPlay,
    markCompleted,
    initialStep: readProgress,
    persistStep: writeProgress,
  }
}
```

> 导入 `ref` / `computed` 需要 `import { computed, ref } from 'vue'` —— 写在文件第一行。

---

## Task 5: 页面集成

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`
- Modify: `frontend/src/components/system/ModelRegistry.vue`

- [ ] **Step 1: tab 栏加重播按钮与 `sys-tabs` 锚点**

把现有

```html
<div class="-mx-1 overflow-x-auto px-1" style="-webkit-overflow-scrolling: touch;">
  <div class="inline-flex min-w-full gap-1 rounded-lg bg-gray-100 p-1 sm:min-w-0">
```

改成

```html
<div class="-mx-1 flex items-center gap-2 overflow-x-auto px-1" style="-webkit-overflow-scrolling: touch;">
  <div data-tour="sys-tabs" class="inline-flex min-w-0 flex-1 gap-1 rounded-lg bg-gray-100 p-1">
```

并在该滚动容器之后、`</div>`（flex 行）之前插入：

```html
<button
  v-if="sysTour.isCompletedForCurrentVersion.value"
  type="button"
  data-tour="sys-tour-replay"
  class="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-transparent px-2 text-[11px] font-bold text-gray-400 transition-colors hover:border-primary/20 hover:bg-primary/5 hover:text-primary dark:text-gray-500"
  title="重看系统配置引导（只影响自己，不改动其他管理员的状态）"
  @click="replaySystemTour"
>
  <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
  引导
</button>
```

- [ ] **Step 2: 配置项行加 `data-tour` 并把 `id` 提到根节点**

`v-for="item in getVisibleItems(...)"` 的根 `div` 增加：

```html
:data-tour="`config-${item.key}`"
:id="`config-item-${item.key}`"
```

并删除左侧列 `div` 上原有的 `:id="`config-key-${item.key}`"`。

- [ ] **Step 3: 工具条加锚点**

`<div data-config-toolbar ...>` 改成 `<div data-config-toolbar data-tour="config-toolbar" ...>`。

- [ ] **Step 4: 挂载 ChatTour 与步骤编排**

模板末尾（与现有根 `div` 同级，`Teleport` 会处理定位）加：

```html
<ChatTour
  ref="sysTourRef"
  v-model="sysTourActive"
  :steps="SYSTEM_CONFIG_TOUR_STEPS"
  :initial-index="sysTourStepIndex"
  :missing-grace-ms="2500"
  @change="handleSysTourChange"
  @enter="handleSysTourEnter"
  @missing-target="handleSysTourMissing"
  @finish="finishSystemTour(false)"
  @skip="finishSystemTour(true)"
/>
```

- [ ] **Step 5: 脚本逻辑**

```ts
import ChatTour from '@/components/embed/ChatTour.vue'
import {
  SYSTEM_CONFIG_TOUR_STEPS,
  SYSTEM_CONFIG_TOUR_VERSION,
} from '@/components/system/systemConfigTourSteps'
import { useSystemConfigTour } from '@/composables/system/useSystemConfigTour'

const sysTour = useSystemConfigTour()
const sysTourActive = ref(false)
const sysTourStepIndex = ref(0)
const sysTourRef = ref<InstanceType<typeof ChatTour> | null>(null)
const sysTourSkippedIndices = ref<number[]>([])
let sysTourSkipGuard = -1

const waitForSysTourAnchor = async (selector: string, timeout = 2000) => {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeout) {
    if (document.querySelector(selector)) return true
    await new Promise((resolve) => window.setTimeout(resolve, 60))
  }
  return false
}

/** 跳过当前步：同一 index 只跳一次，连续 3 次就直接结束 */
const skipSysTourStep = (index: number) => {
  if (sysTourSkipGuard === index) return
  sysTourSkipGuard = index
  sysTourSkippedIndices.value = [...sysTourSkippedIndices.value, index]
  if (sysTourSkippedIndices.value.length >= 3) {
    finishSystemTour(true)
    return
  }
  window.setTimeout(() => sysTourRef.value?.goNext?.(), 0)
}

const handleSysTourEnter = async (index: number, step: typeof SYSTEM_CONFIG_TOUR_STEPS[number]) => {
  if (step.tab && activeTab.value !== step.tab) activeTab.value = step.tab
  if (step.group && realizedActiveCategory.value !== step.group) {
    selectConfigCategory(step.group, false)
  }
  const found = await waitForSysTourAnchor(step.selector)
  if (!found) {
    skipSysTourStep(index)
    return
  }
  sysTourRef.value?.refresh?.()
}

const handleSysTourMissing = (index: number) => skipSysTourStep(index)

const handleSysTourChange = (index: number) => {
  sysTourStepIndex.value = index
  sysTour.persistStep(index)
}

const finishSystemTour = async (skipped: boolean) => {
  sysTourActive.value = false
  await sysTour.markCompleted(skipped)
}

const replaySystemTour = async () => {
  activeTab.value = 'models'
  sysTourStepIndex.value = 0
  sysTourSkipGuard = -1
  sysTourSkippedIndices.value = []
  await nextTick()
  sysTourActive.value = true
}
```

- [ ] **Step 6: 自动播**

在 `onMounted` 的 `await fetchConfigs()` 之后追加：

```ts
window.setTimeout(async () => {
  if (!canSave) return
  if (window.innerWidth < 768) return
  const query = new URLSearchParams(window.location.search).get('tour')
  if (query === '0' || query === 'off') return
  if (!configGroups.value || Object.keys(configGroups.value).length === 0) return
  await sysTour.loadState()
  if (!sysTour.shouldAutoPlay()) return
  activeTab.value = 'models'
  sysTourStepIndex.value = sysTour.initialStep()
  await nextTick()
  sysTourActive.value = true
}, 700)
```

- [ ] **Step 7: ModelRegistry 锚点**

「+ 添加模型」按钮加 `data-tour="model-add"`；列表行右侧操作区容器加 `data-tour="model-row-actions"`。

- [ ] **Step 8: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误

---

## Task 6: 前端契约测试

**Files:**
- Create: `tests/frontend/test_system_config_tour_contract.py`

- [ ] **Step 1: 写测试**

```python
"""系统配置引导契约。

锁定：13 步顺序、锚点真的渲染、自动播只看 config_save、重播不动全局、
断点走 sessionStorage、配置项 id 与搜索定位前缀一致。
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]

STEP_IDS = [
    "sys-tabs",
    "model-add",
    "model-row-actions",
    "config-toolbar",
    "config-app_public_url",
    "config-agent_context_max_tokens",
    "config-llm_model_name",
    "config-multimodal_model_name",
    "config-embed_api_url",
    "config-metadata_provider",
    "config-knowledge_base_enabled",
    "config-sandbox_policy",
    "config-email_service_enabled",
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _steps() -> str:
    return _read("frontend/src/components/system/systemConfigTourSteps.ts")


def test_tour_has_thirteen_steps_in_agreed_order():
    found = re.findall(r"id: '([a-z0-9_-]+)'", _steps())
    assert found == STEP_IDS


def test_every_selector_is_data_tour_attribute():
    selectors = re.findall(r'selector: \'\[data-tour="([a-z0-9_-]+)"\]\'', _steps())
    assert len(selectors) == len(STEP_IDS)


def test_anchors_render_in_page_sources():
    ui = _read("frontend/src/views/SystemConfig.vue")
    models = _read("frontend/src/components/system/ModelRegistry.vue")

    assert 'data-tour="sys-tabs"' in ui
    assert 'data-tour="config-toolbar"' in ui
    assert ':data-tour="`config-${item.key}`"' in ui
    assert 'data-tour="model-add"' in models
    assert 'data-tour="model-row-actions"' in models


def test_only_config_save_can_autoplay_and_mobile_is_skipped():
    ui = _read("frontend/src/views/SystemConfig.vue")

    assert "if (!canSave) return" in ui
    assert "window.innerWidth < 768" in ui
    assert "query === 'off'" in ui


def test_replay_requires_completed_current_version_and_does_not_write_global():
    ui = _read("frontend/src/views/SystemConfig.vue")
    composable = _read("frontend/src/composables/system/useSystemConfigTour.ts")

    assert "sysTour.isCompletedForCurrentVersion.value" in ui
    assert "replaySystemTour" in ui
    # 重播只切 tab 起播，不调 markCompleted
    replay_body = ui.split("const replaySystemTour")[1].split("</script>")[0]
    assert "markCompleted" not in replay_body
    assert "SYSTEM_CONFIG_TOUR_VERSION" in composable


def test_progress_uses_session_storage_and_global_endpoint():
    composable = _read("frontend/src/composables/system/useSystemConfigTour.ts")

    assert "sessionStorage" in composable
    assert "/api/portal/system/tour" in composable


def test_config_row_id_matches_search_lookup_prefix():
    ui = _read("frontend/src/views/SystemConfig.vue")

    assert ':id="`config-item-${item.key}`"' in ui
    assert "getElementById(`config-item-${result.item.key}`)" in ui
    assert "config-key-" not in ui


def test_chat_tour_exposes_configurable_missing_grace():
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    assert "missingGraceMs" in tour
    assert "props.missingGraceMs" in tour
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/frontend/test_system_config_tour_contract.py -v --confcutdir=tests/frontend`
Expected: 全部通过（若失败按提示补源码）

- [ ] **Step 3: 回归相邻契约**

Run: `python -m pytest tests/frontend/test_embed_tour_contract.py tests/frontend/test_system_config_mobile_tabs_contract.py -v --confcutdir=tests/frontend`
Expected: 通过（若 mobile tabs 断言依赖 tab 容器结构，按新结构同步更新该测试）

---

## Task 7: 清单与验证

- [ ] **Step 1: 更新 `tests/CHECKLIST.md`**，按现有格式补「系统配置引导」条目（后端单测 + 前端契约 + 手测点：首次进入自动播 / 播完重播按钮出现 / 点重播只自己播 / 其他账号不再自动播 / 移动端不播 / `?tour=0` 关闭）

- [ ] **Step 2: 全量相关测试**

```bash
python -m pytest tests/api/portal/test_system_config_tour_api.py tests/api/portal/test_embed_tour_prefs.py -v
python -m pytest tests/frontend/test_system_config_tour_contract.py tests/frontend/test_embed_tour_contract.py --confcutdir=tests/frontend -v
cd frontend && npx vue-tsc --noEmit
```

- [ ] **Step 3: 汇报改动**（不自行 commit；提醒用户在控制台执行 `./dev.sh` 后手测）

---

## 风险与验收

- **首次进入被切成模型管理**：引导按自己的顺序走，已确认接受
- **升级后老环境播一次**：已确认接受
- **遮罩镂空可点**：用户能边看边配；保存不自动推进
- 验收：新部署管理员进入 → 13 步走完 → tab 栏出现「引导」→ 换一个 admin 账号登录不再自动播 → 点「引导」只自己重播
