<script setup lang="ts">
import { ref, onMounted, watch, computed, nextTick, shallowRef, onUnmounted } from "vue";
import { metadataApi } from "../../api/metadata";
import type {
  Relationship,
  Table,
  AllTablesDataset,
} from "../../api/metadata";
import { useUser } from "../../composables/useUser";
import { CircleStackIcon, KeyIcon, LightBulbIcon, LinkIcon } from "@heroicons/vue/24/outline";
import { formatRelationshipJoinTypeLabel, normalizeRelationshipJoinType } from "../../utils/relationshipJoinType";
import SmartRelationshipModal from "./SmartRelationshipModal.vue";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { GraphChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";

echarts.use([CanvasRenderer, GraphChart, TooltipComponent, LegendComponent]);

const { isAdmin: _isAdmin, hasPermission } = useUser();

const props = defineProps<{
  datasetId: number;
  tables: Table[];
}>();

const relationships = ref<Relationship[]>([]);
const loading = ref(false);
const showModal = ref(false);
const showSmartRelModal = ref(false);
const saving = ref(false);

// View Mode: cards (default, Hub/Star Cards), list (Table List), graph (Interactive ER Canvas)
const viewMode = ref<"cards" | "list" | "graph">(
  (localStorage.getItem("nanzi_relationship_view_mode") as any) || "cards"
);

const setViewMode = (mode: "cards" | "list" | "graph") => {
  viewMode.value = mode;
  localStorage.setItem("nanzi_relationship_view_mode", mode);
  if (mode === "graph") {
    nextTick(() => initOrUpdateGraph());
  }
};

// Search Filter
const searchQuery = ref("");

// UI State
const error = ref("");
const modalError = ref("");
const editingId = ref<number | null>(null);
const deleteId = ref<number | null>(null);

const form = ref<Relationship>({
  source_table_id: 0,
  target_table_id: 0,
  join_condition: "",
  join_type: "left",
  description: "",
});

// Field Selection Logic
const sourceField = ref("");
const targetField = ref("");

// 跨数据集：全平台所有数据集 + 表列表
const allTablesList = ref<AllTablesDataset[]>([]);

const sourceColumns = computed(() => {
  const table = props.tables.find((t) => t.id === form.value.source_table_id);
  return table ? table.columns : [];
});

// targetColumns 改为从 allTablesList 中查找，支持跨数据集
const targetColumns = computed(() => {
  for (const ds of allTablesList.value) {
    const found = ds.tables.find((t) => t.id === form.value.target_table_id);
    if (found) {
      if (found.columns && found.columns.length > 0) {
        return found.columns;
      }
      const localTable = props.tables.find((t) => t.id === form.value.target_table_id);
      return localTable ? localTable.columns : [];
    }
  }
  const table = props.tables.find((t) => t.id === form.value.target_table_id);
  return table ? table.columns : [];
});

const applyJoinCondition = () => {
  if (sourceField.value && targetField.value) {
    const sourceTable = props.tables.find(
      (t) => t.id === form.value.source_table_id
    );
    let targetPhysicalName = "";
    for (const ds of allTablesList.value) {
      const found = ds.tables.find((t) => t.id === form.value.target_table_id);
      if (found) {
        targetPhysicalName = found.physical_name;
        break;
      }
    }
    if (!targetPhysicalName) {
      const localTarget = props.tables.find((t) => t.id === form.value.target_table_id);
      if (localTarget) targetPhysicalName = localTarget.physical_name;
    }
    if (sourceTable && targetPhysicalName) {
      form.value.join_condition = `${sourceTable.physical_name}.${sourceField.value} = ${targetPhysicalName}.${targetField.value}`;
    }
  }
};

// Reset fields when tables change
watch(
  () => form.value.source_table_id,
  () => {
    sourceField.value = "";
  }
);
watch(
  () => form.value.target_table_id,
  () => {
    targetField.value = "";
  }
);

// Helper: Parse Join Fields from condition
const parseJoinFields = (condition?: string) => {
  if (!condition) return { sourceField: "", targetField: "", raw: "" };
  try {
    const parts = condition.split("=");
    if (parts.length === 2) {
      const left = parts[0]?.trim() || "";
      const right = parts[1]?.trim() || "";
      return {
        sourceField: left.includes(".") ? left.split(".").pop() || left : left,
        targetField: right.includes(".") ? right.split(".").pop() || right : right,
        raw: condition,
      };
    }
  } catch (e) {
    console.warn("parseJoinFields error", e);
  }
  return { sourceField: "", targetField: "", raw: condition };
};

// Table Info Helpers
const getTableMeta = (id: number) => {
  const local = props.tables.find((t) => t.id === id);
  if (local) {
    return {
      id,
      physical_name: local.physical_name,
      term: local.term || "",
      isLocal: true,
      datasetName: "当前数据集",
      fullName: `${local.physical_name}${local.term ? ` (${local.term})` : ""}`,
    };
  }
  for (const ds of allTablesList.value) {
    const t = ds.tables.find((x) => x.id === id);
    if (t) {
      return {
        id,
        physical_name: t.physical_name,
        term: t.term || "",
        isLocal: false,
        datasetName: ds.display_name || ds.dataset_name,
        fullName: `${ds.dataset_name}.${t.physical_name}${t.term ? ` (${t.term})` : ""}`,
      };
    }
  }
  return {
    id,
    physical_name: `Table#${id}`,
    term: "",
    isLocal: false,
    datasetName: "未知",
    fullName: `Unknown#${id}`,
  };
};

// Filtered Relationships
const filteredRelationships = computed(() => {
  if (!searchQuery.value.trim()) return relationships.value;
  const q = searchQuery.value.toLowerCase().trim();
  return relationships.value.filter((r) => {
    const srcMeta = getTableMeta(r.source_table_id);
    const tgtMeta = getTableMeta(r.target_table_id);
    const cond = r.join_condition?.toLowerCase() || "";
    const desc = r.description?.toLowerCase() || "";
    return (
      srcMeta.physical_name.toLowerCase().includes(q) ||
      srcMeta.term.toLowerCase().includes(q) ||
      tgtMeta.physical_name.toLowerCase().includes(q) ||
      tgtMeta.term.toLowerCase().includes(q) ||
      cond.includes(q) ||
      desc.includes(q)
    );
  });
});

// Grouped by Source Table (Hub / Star Schema)
interface TableRelationshipGroup {
  tableId: number;
  physical_name: string;
  term: string;
  isLocal: boolean;
  datasetName: string;
  relationships: Relationship[];
}

const groupedRelationships = computed<TableRelationshipGroup[]>(() => {
  const groupsMap = new Map<number, TableRelationshipGroup>();
  
  filteredRelationships.value.forEach((rel) => {
    const srcId = rel.source_table_id;
    if (!groupsMap.has(srcId)) {
      const meta = getTableMeta(srcId);
      groupsMap.set(srcId, {
        tableId: srcId,
        physical_name: meta.physical_name,
        term: meta.term,
        isLocal: meta.isLocal,
        datasetName: meta.datasetName,
        relationships: [],
      });
    }
    groupsMap.get(srcId)!.relationships.push(rel);
  });

  return Array.from(groupsMap.values());
});

const fetchRelationships = async () => {
  loading.value = true;
  error.value = "";
  try {
    const res = await metadataApi.getRelationships(props.datasetId);
    relationships.value = res.data;
    if (viewMode.value === "graph") {
      nextTick(() => initOrUpdateGraph());
    }
  } catch (e) {
    console.error(e);
    error.value = "无法加载关系列表";
  } finally {
    loading.value = false;
  }
};

// 获取全平台表列表（用于跨数据集目标表选择）
const fetchAllTables = async () => {
  try {
    const res = await metadataApi.getAllTables();
    allTablesList.value = res.data;
  } catch (e) {
    console.error("[RelationshipList] Failed to fetch all tables:", e);
  }
};

const openCreate = (prefillSourceTableId?: number) => {
  editingId.value = null;
  const firstTable = props.tables[0];
  const defaultId = prefillSourceTableId || (firstTable && firstTable.id ? firstTable.id : 0);
  form.value = {
    source_table_id: defaultId,
    target_table_id: defaultId,
    join_condition: "",
    join_type: "left",
    description: "",
  };
  modalError.value = "";
  showModal.value = true;
};

const openEdit = (r: Relationship) => {
  editingId.value = r.id || null;
  form.value = {
    ...r,
    join_type: normalizeRelationshipJoinType(r.join_type),
  };
  
  nextTick(() => {
    if (r.join_condition) {
      try {
        const parts = r.join_condition.split("=");
        if (parts.length === 2) {
          const leftPart = parts[0]!.trim();
          const rightPart = parts[1]!.trim();
          
          const leftField = leftPart.split(".").pop();
          const rightField = rightPart.split(".").pop();
          
          if (leftField) sourceField.value = leftField;
          if (rightField) targetField.value = rightField;
        }
      } catch (e) {
        console.warn("Failed to parse join condition for UI select", e);
      }
    }
  });
  
  modalError.value = "";
  showModal.value = true;
};

// Batch Selection State
const selectedRelIds = ref<number[]>([]);
const showBatchDeleteModal = ref(false);
const batchDeleting = ref(false);

const isAllRelsSelected = computed(() => {
  if (filteredRelationships.value.length === 0) return false;
  return filteredRelationships.value.every((r) => r.id && selectedRelIds.value.includes(r.id));
});

const isSomeRelsSelected = computed(() => {
  return (
    filteredRelationships.value.some((r) => r.id && selectedRelIds.value.includes(r.id)) &&
    !isAllRelsSelected.value
  );
});

const toggleSelectAllRels = () => {
  if (isAllRelsSelected.value) {
    selectedRelIds.value = [];
  } else {
    selectedRelIds.value = filteredRelationships.value.map((r) => r.id).filter(Boolean) as number[];
  }
};

const toggleSelectRel = (id: number) => {
  const idx = selectedRelIds.value.indexOf(id);
  if (idx > -1) {
    selectedRelIds.value.splice(idx, 1);
  } else {
    selectedRelIds.value.push(id);
  }
};

const handleBatchDelete = () => {
  if (selectedRelIds.value.length === 0) return;
  showBatchDeleteModal.value = true;
};

const confirmBatchDelete = async () => {
  if (selectedRelIds.value.length === 0) return;
  batchDeleting.value = true;
  try {
    await metadataApi.batchDeleteRelationships(selectedRelIds.value);
    selectedRelIds.value = [];
    showBatchDeleteModal.value = false;
    await fetchRelationships();
  } catch (e: any) {
    console.error("Batch delete relationships failed", e);
    error.value = "批量删除失败";
    setTimeout(() => (error.value = ""), 3000);
  } finally {
    batchDeleting.value = false;
  }
};

const confirmDelete = async () => {
  if (!deleteId.value) return;
  try {
    await metadataApi.deleteRelationship(deleteId.value);
    selectedRelIds.value = selectedRelIds.value.filter((id) => id !== deleteId.value);
    deleteId.value = null;
    fetchRelationships();
  } catch (e) {
    console.error(e);
    error.value = "删除失败";
    setTimeout(() => (error.value = ""), 3000);
    deleteId.value = null;
  }
};

const handleSave = async () => {
  modalError.value = "";
  if (
    !form.value.source_table_id ||
    !form.value.target_table_id ||
    !form.value.join_condition
  ) {
    modalError.value = "请填写必要信息 (源表, 目标表, 关联条件)";
    return;
  }
  saving.value = true;
  try {
    const payload: Relationship = {
      ...form.value,
      join_type: normalizeRelationshipJoinType(form.value.join_type),
    };
    if (editingId.value) {
      await metadataApi.updateRelationship(editingId.value, payload);
    } else {
      await metadataApi.createRelationship(props.datasetId, payload);
    }
    showModal.value = false;
    fetchRelationships();
  } catch (e: any) {
    console.error(e);
    modalError.value = "保存失败: " + (e.response?.data?.detail || e.message);
  } finally {
    saving.value = false;
  }
};

// 判断某个 table_id 是否属于当前数据集（源数据集）
const isCurrentDataset = (tableId: number) => {
  return props.tables.some((t) => t.id === tableId);
};

// Interactive ER Graph Canvas Logic
const chartContainer = ref<HTMLElement | null>(null);
const chartInstance = shallowRef<echarts.ECharts | null>(null);

const initOrUpdateGraph = () => {
  if (!chartContainer.value) return;
  if (!chartInstance.value) {
    chartInstance.value = echarts.init(chartContainer.value);
    chartInstance.value.on("click", (params: any) => {
      if (params.dataType === "edge" && params.data?.relData) {
        openEdit(params.data.relData);
      }
    });
  }

  // Build nodes from involved tables
  const involvedTableIds = new Set<number>();
  relationships.value.forEach((r) => {
    involvedTableIds.add(r.source_table_id);
    involvedTableIds.add(r.target_table_id);
  });
  props.tables.forEach((t) => {
    if (t.id) involvedTableIds.add(t.id);
  });

  const nodes = Array.from(involvedTableIds).map((id) => {
    const meta = getTableMeta(id);
    const degree = relationships.value.filter(
      (r) => r.source_table_id === id || r.target_table_id === id
    ).length;
    return {
      id: String(id),
      name: meta.physical_name,
      symbolSize: Math.max(36, Math.min(68, 36 + degree * 8)),
      itemStyle: {
        color: !meta.isLocal
          ? "#f59e0b" // Amber for cross-dataset
          : meta.physical_name.startsWith("fact_")
          ? "#4f46e5" // Indigo for fact
          : meta.physical_name.startsWith("dim_")
          ? "#10b981" // Emerald for dim
          : "#6366f1", // Default Primary
        shadowBlur: 8,
        shadowColor: "rgba(0, 0, 0, 0.15)",
      },
      value: meta.term || meta.physical_name,
      label: {
        show: true,
        formatter: (params: any) => `${params.data.name}${meta.term ? `\n[${meta.term}]` : ""}`,
        fontSize: 11,
        color: "#1f2937",
        fontWeight: "bold",
      },
      meta,
    };
  });

  const links = relationships.value.map((rel) => {
    const fields = parseJoinFields(rel.join_condition);
    return {
      source: String(rel.source_table_id),
      target: String(rel.target_table_id),
      lineStyle: {
        curveness: 0.15,
        color: !isCurrentDataset(rel.target_table_id) ? "#f59e0b" : "#818cf8",
        width: 2.5,
        type: !isCurrentDataset(rel.target_table_id) ? "dashed" : "solid",
      },
      symbol: ["none", "arrow"],
      symbolSize: [0, 8],
      label: {
        show: true,
        formatter: `${formatRelationshipJoinTypeLabel(rel.join_type)}`,
        fontSize: 10,
        color: "#6b7280",
        backgroundColor: "rgba(255, 255, 255, 0.85)",
        padding: [2, 4],
        borderRadius: 4,
      },
      relData: rel,
      fields,
    };
  });

  const option = {
    tooltip: {
      formatter: (params: any) => {
        if (params.dataType === "node") {
          const meta = params.data.meta;
          return `<div class="font-bold text-sm text-gray-900">${meta.physical_name}</div>
                  <div class="text-xs text-gray-500">${meta.term || "暂无业务术语"}</div>
                  <div class="text-[10px] text-gray-400 mt-1 font-mono">${meta.datasetName}</div>`;
        } else if (params.dataType === "edge") {
          const rel = params.data.relData;
          return `<div class="font-bold text-xs text-purple-600">${formatRelationshipJoinTypeLabel(rel.join_type)}</div>
                  <div class="text-xs font-mono text-gray-800 mt-1 bg-gray-100 p-1.5 rounded">${rel.join_condition}</div>
                  ${rel.description ? `<div class="text-xs text-gray-500 mt-1">${rel.description}</div>` : ""}
                  <div class="text-[10px] text-blue-500 mt-1.5">点击此连线可直接编辑</div>`;
        }
        return "";
      },
      backgroundColor: "rgba(255, 255, 255, 0.96)",
      borderColor: "#e5e7eb",
      borderWidth: 1,
      padding: 10,
      textStyle: { color: "#1f2937" },
      extraCssText: "box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); border-radius: 8px;",
    },
    series: [
      {
        type: "graph",
        layout: "force",
        data: nodes,
        links: links,
        roam: true,
        draggable: true,
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: [0, 8],
        label: {
          position: "bottom",
          distance: 6,
        },
        force: {
          repulsion: 380,
          edgeLength: [120, 220],
          gravity: 0.1,
        },
        lineStyle: {
          color: "source",
          curveness: 0.15,
        },
        emphasis: {
          focus: "adjacency",
          lineStyle: {
            width: 4,
          },
        },
      },
    ],
  };

  chartInstance.value.setOption(option, true);
};

const handleResize = () => {
  chartInstance.value?.resize();
};

watch(() => props.datasetId, fetchRelationships);

onMounted(() => {
  fetchRelationships();
  fetchAllTables();
  window.addEventListener("resize", handleResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
  chartInstance.value?.dispose();
});
</script>

<template>
  <div class="space-y-3">
    <!-- Toolbar -->
    <div class="flex flex-wrap justify-between items-center gap-3 bg-white p-3 rounded-xl border border-gray-100 shadow-sm">
      <div class="flex items-center gap-3 flex-1 min-w-[280px]">
        <label v-if="filteredRelationships.length > 0 && hasPermission('element:metadata:edit')" class="flex items-center gap-2 cursor-pointer select-none text-xs font-bold text-gray-600 hover:text-gray-900 shrink-0 ml-1">
          <input 
            type="checkbox" 
            :checked="isAllRelsSelected"
            :indeterminate="isSomeRelsSelected"
            @change="toggleSelectAllRels"
            class="w-4 h-4 text-purple-600 rounded border-gray-300 focus:ring-purple-500 cursor-pointer"
          />
          <span>全选</span>
        </label>
        
        <!-- Search Input -->
        <div class="relative flex-1 max-w-xs">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </span>
          <input 
            v-model="searchQuery" 
            type="search"
            class="block w-full pl-9 pr-3 py-1.5 border border-gray-200 rounded-lg text-xs bg-gray-50 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-purple-500 focus:border-purple-500 transition-all focus:bg-white" 
            placeholder="搜索源表/目标表/字段/描述..."
          >
        </div>
        
        <span class="text-xs text-gray-400 font-medium hidden md:inline shrink-0">
          共 {{ relationships.length }} 条关系
          <template v-if="searchQuery"> (过滤出 {{ filteredRelationships.length }} 条)</template>
        </span>
      </div>

      <div class="flex items-center gap-3">
        <!-- View Mode Switcher -->
        <div class="flex items-center p-1 bg-gray-100/80 rounded-lg border border-gray-200/60 shrink-0">
          <button 
            type="button"
            @click="setViewMode('cards')"
            :class="[viewMode === 'cards' ? 'bg-white text-purple-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-800', 'px-2.5 py-1 text-xs rounded-md transition-all flex items-center gap-1.5']"
            title="主表聚合卡片视图"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>
            <span>卡片聚合</span>
          </button>
          <button 
            type="button"
            @click="setViewMode('list')"
            :class="[viewMode === 'list' ? 'bg-white text-purple-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-800', 'px-2.5 py-1 text-xs rounded-md transition-all flex items-center gap-1.5']"
            title="结构化详细列表"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>
            <span>结构列表</span>
          </button>
          <button 
            type="button"
            @click="setViewMode('graph')"
            :class="[viewMode === 'graph' ? 'bg-white text-purple-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-800', 'px-2.5 py-1 text-xs rounded-md transition-all flex items-center gap-1.5']"
            title="内置交互式 ER 拓扑图谱"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"/></svg>
            <span>ER 图谱</span>
          </button>
        </div>

        <div v-if="selectedRelIds.length > 0 && hasPermission('element:metadata:edit')" class="flex items-center gap-2">
          <span class="text-xs bg-purple-50 text-purple-700 px-2.5 py-1 rounded-full font-bold border border-purple-200">
            已选 {{ selectedRelIds.length }} 项
          </span>
          <button 
            @click="handleBatchDelete"
            class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 text-xs font-bold shadow-sm"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            批量删除
          </button>
        </div>

        <button
          v-if="hasPermission('element:metadata:edit')"
          @click="showSmartRelModal = true"
          class="bg-indigo-600 hover:bg-indigo-700 text-white px-3.5 py-1.5 rounded-lg transition-all shadow-md flex items-center gap-1.5 text-xs font-bold h-8 shrink-0"
          title="基于字段语义与既有关联，由 AI 智能推断可能的 FK / Join 关系"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
          智能发现
        </button>
        <button
          v-if="hasPermission('element:metadata:edit')"
          @click="openCreate()"
          class="bg-purple-600 hover:bg-purple-700 text-white px-3.5 py-1.5 rounded-lg transition-all shadow-md flex items-center gap-1.5 text-xs font-bold h-8 shrink-0"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          新建关系
        </button>
      </div>
    </div>

    <!-- Error Banner -->
    <div
      v-if="error"
      class="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm border border-red-100 flex items-center gap-2"
    >
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
      <span>{{ error }}</span>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="flex justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600"></div>
    </div>

    <!-- Empty State -->
    <div
      v-else-if="relationships.length === 0"
      class="text-center py-12 bg-purple-50/40 rounded-xl border border-dashed border-purple-200"
    >
      <div class="w-12 h-12 rounded-2xl bg-purple-100 text-purple-600 flex items-center justify-center mx-auto mb-3">
        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
      </div>
      <p class="text-purple-900 font-bold text-base">暂无定义的实体关系</p>
      <p class="text-xs text-purple-600/80 mt-1 max-w-md mx-auto">
        定义数据表之间的 Join 路径与主外键逻辑，让 AI 智能体掌握跨表分析与多维关联。
      </p>
      <div class="flex items-center justify-center gap-3">
        <button 
          v-if="hasPermission('element:metadata:edit')"
          @click="showSmartRelModal = true"
          class="mt-4 px-4 py-1.5 bg-indigo-600 text-white rounded-lg text-xs font-bold shadow-sm hover:bg-indigo-700 transition-all inline-flex items-center gap-1.5"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
          智能发现关系
        </button>
      </div>
    </div>

    <!-- VIEW 1: Hub / Star Schema Cards (主表聚合卡片视图) -->
    <div v-else-if="viewMode === 'cards'" class="space-y-4">
      <div 
        v-for="group in groupedRelationships" 
        :key="group.tableId"
        class="bg-white rounded-xl border border-gray-200/80 shadow-xs hover:shadow-md transition-all overflow-hidden"
      >
        <!-- Group Header (Source Table) -->
        <div class="px-5 py-3.5 bg-gradient-to-r from-purple-50/60 via-indigo-50/40 to-white border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg bg-white border border-purple-100 shadow-2xs flex items-center justify-center text-purple-600 font-mono font-bold text-sm">
              <CircleStackIcon class="w-4 h-4" />
            </div>
            <div>
              <div class="flex items-center gap-2">
                <span class="font-mono font-bold text-gray-900 text-sm">{{ group.physical_name }}</span>
                <span v-if="group.term" class="text-xs text-gray-500 font-medium">({{ group.term }})</span>
                <span v-if="!group.isLocal" class="px-1.5 py-0.5 text-[10px] font-bold bg-amber-100 text-amber-700 rounded border border-amber-200">
                  {{ group.datasetName }}
                </span>
              </div>
              <p class="text-[11px] text-gray-400 mt-0.5">主干源表 · 聚合了 {{ group.relationships.length }} 条向外关联规则</p>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <span class="text-xs bg-purple-100/70 text-purple-700 px-2.5 py-0.5 rounded-full font-bold">
              {{ group.relationships.length }} 个分支
            </span>
            <button 
              v-if="hasPermission('element:metadata:edit')"
              @click="openCreate(group.tableId)"
              class="text-xs text-purple-600 hover:text-purple-800 bg-white hover:bg-purple-50 border border-purple-200 px-2.5 py-1 rounded-lg font-bold transition-all flex items-center gap-1"
              title="为此表添加新的关联分支"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
              <span>+ 关联</span>
            </button>
          </div>
        </div>

        <!-- Group Body: Relationship Branches -->
        <div class="p-4 space-y-2.5 bg-gray-50/30">
          <div 
            v-for="r in group.relationships" 
            :key="r.id"
            :class="['p-3.5 rounded-xl border transition-all flex flex-col md:flex-row md:items-center justify-between gap-3 bg-white', r.id && selectedRelIds.includes(r.id) ? 'border-purple-400 ring-2 ring-purple-100 shadow-sm' : 'border-gray-100 hover:border-purple-200 hover:shadow-xs']"
          >
            <!-- Left: Checkbox + Field flow -->
            <div class="flex items-center gap-3 flex-1 min-w-0">
              <div v-if="hasPermission('element:metadata:edit') && r.id" class="shrink-0 flex items-center" @click.stop>
                <input 
                  type="checkbox" 
                  :checked="selectedRelIds.includes(r.id)"
                  @change="toggleSelectRel(r.id)"
                  class="w-4 h-4 text-purple-600 rounded border-gray-300 focus:ring-purple-500 cursor-pointer"
                />
              </div>

              <!-- Flow Visual -->
              <div class="flex flex-wrap items-center gap-2 flex-1 min-w-0">
                <!-- Source Field -->
                <div class="px-2.5 py-1 rounded-lg bg-purple-50 border border-purple-100 text-purple-800 font-mono text-xs font-bold flex items-center gap-1.5 shrink-0">
                  <KeyIcon class="w-3 h-3 text-purple-400" />
                  <span>{{ parseJoinFields(r.join_condition).sourceField || '字段' }}</span>
                </div>

                <!-- Join Connector -->
                <div class="flex items-center gap-1 text-gray-400 shrink-0">
                  <div class="h-0.5 w-3 bg-gray-200"></div>
                  <span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-gray-100 text-gray-600 border border-gray-200/80 whitespace-nowrap">
                    {{ formatRelationshipJoinTypeLabel(r.join_type) }}
                  </span>
                  <svg class="w-4 h-4 text-purple-500 -ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                </div>

                <!-- Target Table & Field -->
                <div class="px-2.5 py-1 rounded-lg bg-emerald-50 border border-emerald-100 text-emerald-800 font-mono text-xs font-bold flex items-center gap-1.5 shrink-0 max-w-full md:max-w-md truncate">
                  <LinkIcon class="w-3 h-3 text-emerald-500" />
                  <span class="truncate">{{ getTableMeta(r.target_table_id).physical_name }}</span>
                  <span v-if="getTableMeta(r.target_table_id).term" class="text-emerald-600 text-[11px] font-normal truncate">
                    ({{ getTableMeta(r.target_table_id).term }})
                  </span>
                  <span class="text-emerald-600 font-bold">.</span>
                  <span class="text-emerald-900 font-black">{{ parseJoinFields(r.join_condition).targetField || 'id' }}</span>
                  <span v-if="!isCurrentDataset(r.target_table_id)" class="px-1.5 py-0.2 text-[9px] font-bold bg-amber-200/80 text-amber-800 rounded">
                    跨库: {{ getTableMeta(r.target_table_id).datasetName }}
                  </span>
                </div>

                <!-- Description / SQL Tag -->
                <div v-if="r.description" class="text-xs text-gray-400 truncate max-w-xs ml-2 hidden lg:inline" :title="r.description">
                  📝 {{ r.description }}
                </div>
              </div>
            </div>

            <!-- Right: Actions -->
            <div class="flex items-center gap-2 shrink-0 self-end md:self-center">
              <button 
                @click="openEdit(r)"
                class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                title="编辑关系"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
              </button>
              <button 
                v-if="hasPermission('element:metadata:delete_table')"
                @click="deleteId = r.id!"
                class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                title="删除关系"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- VIEW 2: Table List View (结构化详细列表) -->
    <div v-else-if="viewMode === 'list'" class="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-gray-50/80 border-b border-gray-200 text-gray-500 font-bold uppercase tracking-wider">
              <th v-if="hasPermission('element:metadata:edit')" class="py-3 px-3 w-10 text-center">
                <input 
                  type="checkbox" 
                  :checked="isAllRelsSelected"
                  :indeterminate="isSomeRelsSelected"
                  @change="toggleSelectAllRels"
                  class="w-4 h-4 text-purple-600 rounded border-gray-300 focus:ring-purple-500 cursor-pointer"
                />
              </th>
              <th class="py-3 px-4">源表 (Left)</th>
              <th class="py-3 px-3 text-center">关联类型</th>
              <th class="py-3 px-4">目标表 (Right)</th>
              <th class="py-3 px-4">关联条件 (ON)</th>
              <th class="py-3 px-4">描述</th>
              <th v-if="hasPermission('element:metadata:edit')" class="py-3 px-4 text-right w-24">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr 
              v-for="r in filteredRelationships" 
              :key="r.id"
              :class="['hover:bg-purple-50/30 transition-colors', r.id && selectedRelIds.includes(r.id) ? 'bg-purple-50/50' : '']"
            >
              <td v-if="hasPermission('element:metadata:edit')" class="py-3 px-3 text-center" @click.stop>
                <input 
                  type="checkbox" 
                  :checked="selectedRelIds.includes(r.id!)"
                  @change="toggleSelectRel(r.id!)"
                  class="w-4 h-4 text-purple-600 rounded border-gray-300 focus:ring-purple-500 cursor-pointer"
                />
              </td>
              <td class="py-2.5 px-4 font-mono">
                <div class="flex items-start gap-1.5">
                  <CircleStackIcon class="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
                  <div class="min-w-0">
                    <div class="font-bold text-gray-900 leading-snug">
                      {{ getTableMeta(r.source_table_id).physical_name }}
                    </div>
                    <div v-if="getTableMeta(r.source_table_id).term" class="text-[11px] font-sans font-normal text-gray-500 leading-tight mt-0.5">
                      {{ getTableMeta(r.source_table_id).term }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="py-2.5 px-3 text-center">
                <span class="px-2 py-0.5 bg-purple-50 text-purple-700 border border-purple-100 rounded text-[11px] font-bold">
                  {{ formatRelationshipJoinTypeLabel(r.join_type) }}
                </span>
              </td>
              <td class="py-2.5 px-4 font-mono">
                <div class="flex items-start gap-1.5">
                  <LinkIcon class="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                      <span class="font-bold text-gray-900 leading-snug">
                        {{ getTableMeta(r.target_table_id).physical_name }}
                      </span>
                      <span v-if="!isCurrentDataset(r.target_table_id)" class="px-1.5 py-0.2 text-[9px] font-sans font-bold bg-amber-100 text-amber-700 rounded border border-amber-200 shrink-0">
                        跨库
                      </span>
                    </div>
                    <div v-if="getTableMeta(r.target_table_id).term" class="text-[11px] font-sans font-normal text-gray-500 leading-tight mt-0.5">
                      {{ getTableMeta(r.target_table_id).term }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="py-3 px-4">
                <code 
                  class="bg-gray-900 text-green-400 px-2.5 py-1 rounded font-mono text-[11px] cursor-pointer hover:bg-black transition-colors"
                  :title="r.join_condition"
                  @click="openEdit(r)"
                >
                  {{ r.join_condition }}
                </code>
              </td>
              <td class="py-3 px-4 text-gray-500 truncate max-w-xs">
                {{ r.description || '-' }}
              </td>
              <td v-if="hasPermission('element:metadata:edit')" class="py-3 px-4 text-right">
                <div class="flex items-center justify-end gap-1.5">
                  <button 
                    @click="openEdit(r)"
                    class="p-1 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                    title="编辑关系"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                  </button>
                  <button 
                    v-if="hasPermission('element:metadata:delete_table')"
                    @click="deleteId = r.id!"
                    class="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                    title="删除关系"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- VIEW 3: Interactive ER Graph View (内置交互式 ER 拓扑图谱) -->
    <div v-else-if="viewMode === 'graph'" class="bg-white p-4 rounded-xl border border-gray-200 shadow-sm space-y-3">
      <div class="flex items-center justify-between text-xs text-gray-500 px-1 border-b pb-2">
        <div class="flex items-center gap-4">
          <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-indigo-600 inline-block"></span> 事实表 (fact_*)</span>
          <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span> 维度表 (dim_*)</span>
          <span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block"></span> 跨库关联表</span>
        </div>
        <span class="text-gray-400 flex items-center gap-1"><LightBulbIcon class="w-3.5 h-3.5" /> 拖拽节点可调整布局，点击关系连线可直接编辑</span>
      </div>
      <div ref="chartContainer" class="w-full h-[540px] rounded-lg bg-gray-50/40"></div>
    </div>

    <!-- Modal -->
    <div
      v-if="showModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      @click.self="showModal = false"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-2xl overflow-hidden border border-gray-100 animate-fade-in-up"
      >
        <div
          class="p-6 border-b border-gray-100 flex justify-between items-center bg-purple-50"
        >
          <h3 class="font-bold text-gray-900">
            {{ editingId ? "编辑实体关系" : "新建实体关系" }}
          </h3>
          <button
            @click="showModal = false"
            class="text-gray-400 hover:text-gray-600"
          >
            &times;
          </button>
        </div>

        <!-- Modal Error -->
        <div v-if="modalError" class="px-6 pt-4 pb-0">
          <div
            class="bg-red-50 text-red-600 px-3 py-2 rounded text-xs border border-red-100"
          >
            {{ modalError }}
          </div>
        </div>

        <div class="p-6 space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1"
                >源表 (Left)</label
              >
              <select
                v-model="form.source_table_id"
                class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
              >
                <option v-for="t in tables" :key="t.id" :value="t.id">
                  {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                </option>
              </select>
              <!-- Field Selector -->
              <div class="mt-2">
                <label
                  class="block text-[10px] uppercase font-bold text-gray-400 mb-1"
                  >关联字段</label
                >
                <select
                  v-model="sourceField"
                  @change="applyJoinCondition"
                  class="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-purple-400 outline-none bg-white disabled:bg-gray-100"
                >
                  <option value="">-- 选择字段 --</option>
                  <option
                    v-for="col in sourceColumns"
                    :key="col.physical_name"
                    :value="col.physical_name"
                  >
                    {{ col.physical_name }}
                    {{ col.term ? `[${col.term}]` : "" }}
                  </option>
                </select>
              </div>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1"
                >目标表 (Right)</label
              >
              <!-- 跨数据集分组下拉 -->
              <select
                v-model="form.target_table_id"
                class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
              >
                <!-- 当前数据集（同库关联）-->
                <optgroup :label="'当前数据集'">
                  <option v-for="t in tables" :key="t.id" :value="t.id">
                    {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                  </option>
                </optgroup>
                <!-- 其他数据集（跨库关联）-->
                <template v-for="ds in allTablesList" :key="ds.dataset_id">
                  <optgroup
                    v-if="ds.tables.some(t => !tables.find(lt => lt.id === t.id))"
                    :label="`${ds.display_name} [跨数据集]`"
                  >
                    <option
                      v-for="t in ds.tables.filter(t => !tables.find(lt => lt.id === t.id))"
                      :key="t.id"
                      :value="t.id"
                    >
                      {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                    </option>
                  </optgroup>
                </template>
              </select>
              <!-- Field Selector：跨数据集时字段无法自动推断，提示手动填写 -->
              <div class="mt-2">
                <label
                  class="block text-[10px] uppercase font-bold text-gray-400 mb-1"
                  >关联字段</label
                >
                <select
                  v-model="targetField"
                  @change="applyJoinCondition"
                  class="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-purple-400 outline-none bg-white disabled:bg-gray-100"
                >
                  <option value="">-- 选择字段 --</option>
                  <option
                    v-for="col in targetColumns"
                    :key="col.physical_name"
                    :value="col.physical_name"
                  >
                    {{ col.physical_name }}
                    {{ col.term ? `[${col.term}]` : "" }}
                  </option>
                </select>
              </div>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >关联类型</label
            >
            <div class="flex flex-wrap gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="left"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">Left Join · One to Many (1:N)</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="inner"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">Inner Join</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="one_to_one"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">One to One</span>
              </label>
            </div>
            <p class="mt-1.5 text-xs text-gray-500">
              一对多请选第一项；历史数据里的 <code class="font-mono">ONE_TO_MANY</code> 会自动映射到此项。
            </p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >关联条件 (ON ...)</label
            >
            <input
              v-model="form.join_condition"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-purple-500 focus:outline-none"
              placeholder="e.g. t1.user_id = t2.id"
            />
            <p class="text-xs text-gray-400 mt-1">使用表别名或完整表名均可。</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >描述</label
            >
            <textarea
              v-model="form.description"
              rows="2"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
            ></textarea>
          </div>
        </div>
        <div class="p-6 bg-gray-50 flex justify-end gap-3">
          <button
            @click="showModal = false"
            class="px-4 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50"
          >
            取消
          </button>
          <button
            @click="handleSave"
            :disabled="saving"
            class="px-6 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-bold shadow-md disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>
    <!-- Delete Modal -->
    <div
      v-if="deleteId"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      @click.self="deleteId = null"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up"
      >
        <div class="p-6 text-center">
          <div
            class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100"
          >
            <svg
              class="w-8 h-8 text-red-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除?</h3>
          <p class="text-sm text-gray-500 mb-6">
            您确定要删除此关联关系吗？<br />此操作无法撤销。
          </p>
          <div class="flex gap-3 justify-center">
            <button
              @click="deleteId = null"
              class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white"
            >
              取消
            </button>
            <button
              @click="confirmDelete"
              class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30"
            >
              确认删除
            </button>
          </div>
        </div>
      </div>
    </div>
    <!-- Batch Delete Modal -->
    <div
      v-if="showBatchDeleteModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      @click.self="showBatchDeleteModal = false"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up"
      >
        <div class="p-6 text-center">
          <div
            class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100"
          >
            <svg
              class="w-8 h-8 text-red-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <h3 class="text-lg font-bold text-gray-900 mb-2">确认批量删除实体关系?</h3>
          <p class="text-sm text-gray-500 mb-6">
            您确定要删除已选中的 <b class="text-red-600">{{ selectedRelIds.length }}</b> 条关联关系吗？<br />此操作无法撤销。
          </p>
          <div class="flex gap-3 justify-center">
            <button
              @click="showBatchDeleteModal = false"
              :disabled="batchDeleting"
              class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white"
            >
              取消
            </button>
            <button
              @click="confirmBatchDelete"
              :disabled="batchDeleting"
              class="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30 disabled:opacity-50 flex items-center gap-2"
            >
              <svg v-if="batchDeleting" class="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
              <span>{{ batchDeleting ? '正在删除...' : '确认批量删除' }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Smart Relationship Discovery Modal (选表/计时/取消/采纳工作台) -->
    <SmartRelationshipModal
      :show="showSmartRelModal"
      :dataset-id="props.datasetId"
      :tables="props.tables"
      @close="showSmartRelModal = false"
      @saved="fetchRelationships"
    />
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
