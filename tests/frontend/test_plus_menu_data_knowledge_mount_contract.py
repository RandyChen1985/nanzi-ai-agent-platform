"""Contract: 加号菜单「数据&知识库」浮层一键挂载数据集 / 知识库。

需求来源（用户截图）：加号菜单里「选择记忆记录」后面加一个「数据&知识库」，
展开两个 tab（数据集 / 知识库），直接在浮层里挂载，省得打开知识库面板与数据门户。

这组契约锁住四件事：
1. 入口位置与能力门禁（两个 tab 都不可用时不渲染）；
2. 两个 tab 的数据源与两个面板同源、按 tab 懒加载；
3. 挂载动作写出的附件 chip 与两个面板完全一致（唯一事实源 `sessionMountChips`）；
4. 勾选后浮层不关闭，且不借道打开两个抽屉。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
CASCADE = ROOT / "frontend/src/components/embed/DataKnowledgeCascadeMenu.vue"
CHIPS = ROOT / "frontend/src/utils/sessionMountChips.ts"
KNOWLEDGE_PORTAL = ROOT / "frontend/src/composables/useKnowledgePortal.ts"
DATASET_MOUNT = ROOT / "frontend/src/composables/useDatasetMount.ts"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice(content: str, start_marker: str, end_marker: str) -> str:
    start = content.index(start_marker)
    end = content.index(end_marker, start)
    return content[start:end]


def test_entry_sits_right_after_memory_and_before_skills():
    content = _read(CHAT_INPUT)

    memory_at = content.index(">选择记忆记录<")
    entry_at = content.index(">数据&amp;知识库<")
    skills_at = content.index(">技能中心<")

    assert memory_at < entry_at < skills_at
    assert 'data-testid="plus-menu-data-knowledge"' in content

    # 两个 tab 都不可用时整个入口不渲染，避免点开一个全灰的浮层
    assert 'v-if="showDataKnowledgeEntry"' in content
    assert "const showDataKnowledgeEntry = computed(() => canMountDatasets.value || canMountKnowledgeBases.value);" in content

    # 入口是「可展开」的级联项：悬停/点击都走 open，不关闭加号菜单去开抽屉
    entry = _slice(content, 'data-testid="plus-menu-data-knowledge"', "</button>")
    assert '@click.stop="openDataKnowledgeCascade"' in entry
    assert "emit('select-knowledge-base'" not in entry
    assert "emit('select-local-fs'" not in entry


def test_cascade_menu_has_two_tabs_with_mounted_counts():
    content = _read(CASCADE)

    # 两个 tab 的文案与计数（已挂载 / 总数）
    assert "'datasets'" in content and "'knowledge'" in content
    assert "const tabLabel = (tab: MountTab) => (tab === 'datasets' ? '数据集' : '知识库')" in content
    assert "const tabCount = (tab: MountTab) =>" in content
    # 页签本身只在「智能体不支持数据查询」时禁用；「已绑定数据集」仍可切过去看提示
    assert ":disabled=\"tab === 'datasets' ? datasetDisabled : knowledgeDisabled\"" in content
    assert "const datasetTabDisabled = computed(() => props.datasetDisabled)" in content
    assert 'role="tablist"' in content
    assert "data-knowledge-tab-" in content

    # 搜索框按当前 tab 换 placeholder
    assert ":placeholder=\"metaDatasetTab ? '搜索数据集' : '搜索知识库'\"" in content
    assert "data-testid=\"data-knowledge-search\"" in content

    # 空态引导指向真实存在的路由（写错会 404）
    assert 'href="/dashboard/metadata"' in content
    assert 'href="/dashboard/knowledge-bases"' in content


def test_cascade_menu_loads_from_the_same_endpoints_as_the_two_panels():
    content = _read(CASCADE)

    assert "'/api/portal/metadata/datasets/accessible'" in content
    assert "'/api/portal/ragflow/datasets'" in content
    assert "include_missing: false" in content

    # 知识库必须按 total 翻页拉全：面板那份 page_size 100 的窗口会被 meta-* 数据集挤掉，
    # 导致「有权限的库一个都不显示」（后端接口注释里明确警告过）
    assert "const KNOWLEDGE_PAGE_SIZE = 1000" in content
    assert "const fetchAllKnowledgeBases = async (): Promise<any[]> => {" in content
    assert "page_size: KNOWLEDGE_PAGE_SIZE" in content
    assert "collected.length >= total" in content
    # 不能出现硬编码的 100 窗口（注释里提到它是可以的，代码里不允许）
    assert "page_size: 100," not in content

    # 按 tab 懒加载：首次进入只拉当前 tab，切 tab 才拉另一个
    assert "if (loadedDatasets.value && !force) return" in content
    assert "if (loadedKnowledge.value && !force) return" in content
    assert "const ensureTabLoaded = (tab: MountTab = activeTab.value) => {" in content
    assert "onMounted(() => ensureTabLoaded())" in content
    assert "watch(activeTab, (tab) => ensureTabLoaded(tab))" in content


def test_cascade_menu_respects_session_scope_and_agent_capability():
    content = _read(CASCADE)

    # 会话资源范围：非空时只列范围内资源（与两个面板同口径）
    assert "allowedDatasetIds?: string[] | null" in content
    assert "allowedKnowledgeBaseIds?: string[] | null" in content
    assert "const isWithinScope = (item: MountableItem, allowed: Set<string> | null) =>" in content
    assert "datasets.value.filter((item) => isWithinScope(item, allowedDatasetSet.value))" in content
    assert "knowledgeBases.value.filter((item) => isWithinScope(item, allowedKnowledgeSet.value))" in content

    # 能力门禁：不支持的 tab 置灰并说明原因
    assert "datasetDisabled?: boolean" in content
    assert "knowledgeDisabled?: boolean" in content
    assert "当前智能体不支持数据查询，无法挂载数据集" in content
    assert "当前智能体不支持知识库能力，无法挂载知识库" in content


def test_cascade_menu_surfaces_stale_mounts_instead_of_hiding_them():
    content = _read(CASCADE)

    # 库被删 / 权限收回 / 超出会话范围时，chip 还在，必须可见且可一键移除
    assert "const staleDatasetIds = computed(() =>" in content
    assert "const staleKnowledgeIds = computed(() =>" in content
    assert "已挂载但不在列表" in content
    assert 'data-testid="data-knowledge-stale"' in content
    assert "const removeStale = () => {" in content


def test_chat_input_wires_scope_props_and_chip_toggles():
    content = _read(CHAT_INPUT)

    assert ':allowed-dataset-ids="allowedDatasetIds"' in content
    assert ':allowed-knowledge-base-ids="allowedKnowledgeBaseIds"' in content
    assert ':dataset-disabled="!canMountDatasets"' in content
    assert ':knowledge-disabled="!canMountKnowledgeBases"' in content
    assert ':active-dataset-ids="attachedMetadataDatasetIds"' in content
    assert ':active-knowledge-base-ids="attachedKnowledgeBaseIds"' in content

    # 桌面 flyout 与移动端抽屉都挂上了同一个组件
    assert 'v-if="showDataKnowledgeCascade && !isMobileViewport"' in content
    assert 'v-if="showDataKnowledgeCascade && isMobileViewport"' in content

    # 能力判据与「打开数据门户 / 打开知识库中心」同源
    assert "c.id === DATASET_PORTAL_SYSTEM_COMMAND_ID" in content
    assert "c.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID" in content


def test_toggling_a_mount_keeps_the_flyout_open():
    content = _read(CHAT_INPUT)

    for handler in ("const toggleDatasetMountFromCascade", "const toggleKnowledgeBaseMountFromCascade"):
        body = _slice(content, handler, "};")
        # 勾选只重建 chip，不关闭浮层、不关加号菜单
        assert "showPlusMenu.value = false" not in body
        assert "showDataKnowledgeCascade.value = false" not in body

    # 两个 handler 都走纯函数，不自己拼 chip
    dataset_body = _slice(content, "const toggleDatasetMountFromCascade", "};")
    knowledge_body = _slice(content, "const toggleKnowledgeBaseMountFromCascade", "};")
    assert "syncMetadataDatasetChips(" in dataset_body
    assert "syncKnowledgeBaseChip(" in knowledge_body


def test_embed_chat_scopes_the_flyout_to_project_session_resources():
    content = _read(EMBED_CHAT)

    assert ':allowed-dataset-ids="mountableDatasetScopeIds"' in content
    assert ':allowed-knowledge-base-ids="mountableKnowledgeScopeIds"' in content
    assert "const mountableDatasetScopeIds = computed<string[] | null>(() => {" in content
    assert "const mountableKnowledgeScopeIds = computed<string[] | null>(() => {" in content

    # 未设范围时必须是 null（空数组会把列表过滤成空）
    assert "if (!projectSessionHasDatasetScope.value) return null;" in content
    assert "if (!projectSessionHasKnowledgeScope.value) return null;" in content

    # 范围 ID 直接取 resourceScope 的字段并集：不能从 knowledgeDatasets 反推，
    # 否则没打开过知识库面板时那份列表为空，会把浮层过滤成空
    dataset_block = _slice(content, "const mountableDatasetScopeIds", "});")
    knowledge_block = _slice(content, "const mountableKnowledgeScopeIds", "});")
    assert "resourceScope.value.datasets" in dataset_block
    assert "resourceScope.value.knowledge_bases" in knowledge_block
    assert "knowledgeDatasets.value" not in knowledge_block


def test_chip_writers_share_the_single_source():
    chips = _read(CHIPS)
    knowledge_portal = _read(KNOWLEDGE_PORTAL)
    dataset_mount = _read(DATASET_MOUNT)
    chat_input = _read(CHAT_INPUT)

    # 纯函数模块是唯一写 chip 的地方
    assert "export const KNOWLEDGE_BASE_CHIP_TYPE = \"knowledge_base\";" in chips
    assert "export const METADATA_DATASET_CHIP_TYPE = \"metadata_dataset\";" in chips
    assert "export const readKnowledgeBaseIds" in chips
    assert "export const syncKnowledgeBaseChip" in chips
    assert "export const readMetadataDatasetIds" in chips
    assert "export const syncMetadataDatasetChips" in chips
    assert "export const toggleMountId" in chips

    # 三个调用方都复用，不再各自拼 chip 字面量
    assert "syncKnowledgeBaseChip" in knowledge_portal
    assert "syncMetadataDatasetChips" in dataset_mount
    assert "syncKnowledgeBaseChip(" in chat_input
    assert "syncMetadataDatasetChips(" in chat_input
    assert '"knowledge_base"' not in knowledge_portal
    assert "f.type === \"knowledge_base\"" not in knowledge_portal
    assert "ext: \"metadata_dataset\"" not in dataset_mount
    assert "type: \"knowledge_base\"" not in chat_input


def _run_chips(expression: str):
    script = f"""
const ts = require('./frontend/node_modules/typescript');
const fs = require('fs');
const source = fs.readFileSync('frontend/src/utils/sessionMountChips.ts', 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', code)(moduleRef, moduleRef.exports);
const api = moduleRef.exports;
const result = (() => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_knowledge_base_chip_matches_the_panel_format():
    result = _run_chips(
        """
  let files = [{ type: 'skill', url: 'skill-1', filename: '技能', size: 0, ext: 'skill' }];
  files = api.syncKnowledgeBaseChip(files, ['kb-1']);
  files = api.syncKnowledgeBaseChip(files, api.toggleMountId(api.readKnowledgeBaseIds(files), 'kb-2'));
  const mounted = { files, ids: api.readKnowledgeBaseIds(files) };
  const unmounted = api.syncKnowledgeBaseChip(files, api.toggleMountId(mounted.ids, 'kb-1'));
  return { mounted, unmounted, unmountedIds: api.readKnowledgeBaseIds(unmounted) };
"""
    )

    files = result["mounted"]["files"]
    assert result["mounted"]["ids"] == ["kb-1", "kb-2"]
    assert [f["type"] for f in files] == ["skill", "knowledge_base"]
    chip = files[-1]
    assert chip["url"] == "kb-1,kb-2"
    assert chip["filename"] == "已选择 2 个知识库"
    assert chip["size"] == 0
    assert chip["ext"] == "knowledge_base"

    # 取消一个后仍是汇总 chip，文案跟着数量走
    assert result["unmountedIds"] == ["kb-2"]
    assert [f["type"] for f in result["unmounted"]][-1] == "knowledge_base"
    assert result["unmounted"][-1]["filename"] == "已选择 1 个知识库"


def test_metadata_dataset_chips_are_one_per_dataset():
    result = _run_chips(
        """
  let files = [{ type: 'knowledge_base', url: 'kb-1', filename: '已选择 1 个知识库', size: 0, ext: 'knowledge_base' }];
  files = api.syncMetadataDatasetChips(files, ['ds-1'], () => '销售数据集');
  files = api.syncMetadataDatasetChips(
    files,
    api.toggleMountId(api.readMetadataDatasetIds(files), 'ds-2'),
    (id) => (id === 'ds-2' ? '库存数据集' : id),
  );
  const mounted = { files, ids: api.readMetadataDatasetIds(files) };
  const unmounted = api.syncMetadataDatasetChips(
    files,
    api.toggleMountId(mounted.ids, 'ds-1'),
    (id) => id,
  );
  return { mounted, unmounted, unmountedIds: api.readMetadataDatasetIds(unmounted) };
"""
    )

    files = result["mounted"]["files"]
    dataset_chips = [f for f in files if f["type"] == "metadata_dataset"]
    assert result["mounted"]["ids"] == ["ds-1", "ds-2"]
    assert [f["url"] for f in dataset_chips] == ["ds-1", "ds-2"]
    # 数据集名优先沿用首次挂载时的展示名，不被裸 ID 覆盖
    assert [f["filename"] for f in dataset_chips] == ["销售数据集", "库存数据集"]
    assert all(f["ext"] == "metadata_dataset" for f in dataset_chips)
    # 其他类型的 chip 原样保留在前面
    assert files[0]["type"] == "knowledge_base"

    # 取消一个只移除对应 chip，另一个的展示名不丢
    assert result["unmountedIds"] == ["ds-2"]
    remaining = [f for f in result["unmounted"] if f["type"] == "metadata_dataset"]
    assert [f["filename"] for f in remaining] == ["库存数据集"]


def test_unknown_chip_types_are_ignored_when_reading_mounts():
    result = _run_chips(
        """
  const files = [
    { type: 'local_file', url: 'a,b', filename: '服务器文件' },
    { type: 'knowledge_base', url: ' kb-1 , kb-2 ,, ', filename: '已选择 2 个知识库' },
    { type: 'metadata_dataset', url: 'ds-1', filename: '数据集' },
    { type: 'metadata_dataset', url: 'ds-1,ds-2', filename: '数据集' },
  ];
  return { kb: api.readKnowledgeBaseIds(files), ds: api.readMetadataDatasetIds(files) };
"""
    )

    assert result["kb"] == ["kb-1", "kb-2"]
    # 逗号分隔 + 重复 ID 都要归一
    assert result["ds"] == ["ds-1", "ds-2"]
