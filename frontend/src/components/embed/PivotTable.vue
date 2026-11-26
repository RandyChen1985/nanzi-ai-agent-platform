<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  TableCellsIcon,
  TrashIcon,
  ArrowPathIcon,
  ChevronDownIcon,
} from "@heroicons/vue/24/outline";

interface Field {
  name: string;
  label: string;
  type: string;
}

interface MeasureField {
  name: string;
  label: string;
  aggType: "sum" | "avg" | "count" | "max" | "min";
}

const props = defineProps<{
  data: any[];
  fields: Field[];
}>();

// Selected configurations
const rowFields = ref<Field[]>([]);
const colFields = ref<Field[]>([]);
const measureFields = ref<MeasureField[]>([]);

// Dragging state
const dragField = ref<Field | null>(null);

// Drop zones definitions
type Zone = "rows" | "columns" | "values";

const handleDragStart = (field: Field) => {
  dragField.value = field;
};

const handleDrop = (zone: Zone) => {
  if (dragField.value) {
    const field = dragField.value;
    if (zone === "rows") {
      if (!rowFields.value.some((f) => f.name === field.name)) {
        rowFields.value.push(field);
        // Remove from col if present
        colFields.value = colFields.value.filter((f) => f.name !== field.name);
      }
    } else if (zone === "columns") {
      if (!colFields.value.some((f) => f.name === field.name)) {
        colFields.value.push(field);
        // Remove from row if present
        rowFields.value = rowFields.value.filter((f) => f.name !== field.name);
      }
    } else if (zone === "values") {
      // Allow multiple aggregation of the same field if needed, or unique
      if (!measureFields.value.some((m) => m.name === field.name)) {
        measureFields.value.push({
          name: field.name,
          label: field.label,
          aggType: field.type === "number" ? "sum" : "count",
        });
      }
    }
  }
  dragField.value = null;
};

// Easy click assignment handlers for accessibility and mobile
const addFieldToZone = (field: Field, zone: Zone) => {
  if (zone === "rows") {
    if (!rowFields.value.some((f) => f.name === field.name)) {
      rowFields.value.push(field);
      colFields.value = colFields.value.filter((f) => f.name !== field.name);
    }
  } else if (zone === "columns") {
    if (!colFields.value.some((f) => f.name === field.name)) {
      colFields.value.push(field);
      rowFields.value = rowFields.value.filter((f) => f.name !== field.name);
    }
  } else if (zone === "values") {
    if (!measureFields.value.some((m) => m.name === field.name)) {
      measureFields.value.push({
        name: field.name,
        label: field.label,
        aggType: field.type === "number" ? "sum" : "count",
      });
    }
  }
};

const removeFieldFromZone = (name: string, zone: Zone) => {
  if (zone === "rows") {
    rowFields.value = rowFields.value.filter((f) => f.name !== name);
  } else if (zone === "columns") {
    colFields.value = colFields.value.filter((f) => f.name !== name);
  } else if (zone === "values") {
    measureFields.value = measureFields.value.filter((m) => m.name !== name);
  }
};

const changeAggType = (index: number, type: MeasureField["aggType"]) => {
  if (measureFields.value[index]) {
    measureFields.value[index].aggType = type;
  }
};

const clearAll = () => {
  rowFields.value = [];
  colFields.value = [];
  measureFields.value = [];
};

// MULTI-DIMENSIONAL PIVOT ALGORITHM
const pivotResult = computed(() => {
  const rawData = props.data || [];
  if (rawData.length === 0 || (rowFields.value.length === 0 && colFields.value.length === 0 && measureFields.value.length === 0)) {
    return { headers: [], rows: [], hasData: false };
  }

  // 1. Get unique values combinations for Row headers
  const uniqueRowsMap = new Map<string, string[]>();
  const uniqueColsMap = new Map<string, string[]>();

  rawData.forEach((item) => {
    // Generate Row key
    const rowVals = rowFields.value.map((rf) => String(item[rf.name] ?? "N/A"));
    const rowKey = rowVals.join(" || ");
    if (rowFields.value.length > 0 && !uniqueRowsMap.has(rowKey)) {
      uniqueRowsMap.set(rowKey, rowVals);
    }

    // Generate Col key
    const colVals = colFields.value.map((cf) => String(item[cf.name] ?? "N/A"));
    const colKey = colVals.join(" || ");
    if (colFields.value.length > 0 && !uniqueColsMap.has(colKey)) {
      uniqueColsMap.set(colKey, colVals);
    }
  });

  // Sort keys for deterministic order
  const sortedRowKeys = Array.from(uniqueRowsMap.keys()).sort();
  const sortedColKeys = Array.from(uniqueColsMap.keys()).sort();

  // If columns but no column combinations exist (e.g. list was empty, which shouldn't happen if length > 0)
  if (colFields.value.length > 0 && sortedColKeys.length === 0) {
    sortedColKeys.push("");
  }
  // If rows but no row combinations
  if (rowFields.value.length > 0 && sortedRowKeys.length === 0) {
    sortedRowKeys.push("");
  }

  // 2. Prepare headers structure
  // Header can have multiple rows if column dimension depth is high.
  // Level represents depth of column categories, plus 1 for measures
  const colDepth = colFields.value.length;
  const headerRowCount = Math.max(colDepth, 1);
  const headersMatrix: any[][] = Array.from({ length: headerRowCount }, () => []);

  // Row header offset cells at top-left
  for (let r = 0; r < headerRowCount; r++) {
    // Place row headers names at the last row of headers
    if (r === headerRowCount - 1) {
      rowFields.value.forEach((rf) => {
        headersMatrix[r]!.push({
          label: rf.label,
          rowSpan: 1,
          colSpan: 1,
          isDimension: true,
        });
      });
    } else {
      // Empty placeholder cells
      rowFields.value.forEach(() => {
        headersMatrix[r]!.push({
          label: "",
          rowSpan: 1,
          colSpan: 1,
          isEmptyOffset: true,
        });
      });
    }
  }

  // Build column headers
  // For each sorted column combination, we build the headers.
  // Each measure field will be repeated under each column category combination.
  const colCombinations = sortedColKeys.length > 0 ? sortedColKeys : [""];
  const finalMeasures = measureFields.value.length > 0 ? measureFields.value : [{ name: "_count", label: "计数", aggType: "count" as const }];

  colCombinations.forEach((colKey) => {
    const colVals = uniqueColsMap.get(colKey) || [];

    // Push each level of col dimensions
    for (let r = 0; r < colDepth; r++) {
      headersMatrix[r]!.push({
        label: colVals[r] ?? "",
        rowSpan: 1,
        // Colspan is the number of measures under it
        colSpan: finalMeasures.length,
        isCategory: true,
      });
    }

    // If depth is 0 (no col dimension, just measures)
    if (colDepth === 0) {
      // Just measures
    }
  });

  // Last row of headers lists the measures names if columns exist
  let measuresHeaderRow: any[] = [];
  if (colDepth > 0) {
    const measureRow: any[] = [];
    // Space for row dimensions
    rowFields.value.forEach(() => {
      measureRow.push({ label: "", isPadding: true });
    });
    colCombinations.forEach(() => {
      finalMeasures.forEach((m) => {
        measureRow.push({
          label: `${m.label} (${m.aggType.toUpperCase()})`,
          isMeasureName: true,
        });
      });
    });
    measuresHeaderRow = measureRow;
  } else {
    // If no col dimensions, we just list measure names in the main header row
    finalMeasures.forEach((m) => {
      headersMatrix[0]!.push({
        label: `${m.label} (${m.aggType.toUpperCase()})`,
        isMeasureName: true,
      });
    });
  }

  // Combine headers matrix
  const finalHeaders = [...headersMatrix];
  if (measuresHeaderRow.length > 0) {
    finalHeaders.push(measuresHeaderRow);
  }

  // 3. Compute values by grouping data
  const rowDataList: any[][] = [];

  // Group rawData items by rowKey + colKey
  const groupedData = new Map<string, any[]>();
  rawData.forEach((item) => {
    const rowVals = rowFields.value.map((rf) => String(item[rf.name] ?? "N/A"));
    const rowKey = rowVals.join(" || ");

    const colVals = colFields.value.map((cf) => String(item[cf.name] ?? "N/A"));
    const colKey = colVals.join(" || ");

    const groupKey = `${rowKey} |##| ${colKey}`;
    if (!groupedData.has(groupKey)) {
      groupedData.set(groupKey, []);
    }
    groupedData.get(groupKey)!.push(item);
  });

  // Calculate aggregation value
  const aggregate = (items: any[], measureName: string, aggType: string) => {
    if (!items || items.length === 0) return "-";
    
    // For COUNT, we just return size
    if (aggType === "count") return items.length;

    const numericValues = items
      .map((item) => Number(item[measureName]))
      .filter((v) => !isNaN(v));

    if (numericValues.length === 0) return "-";

    switch (aggType) {
      case "sum":
        return numericValues.reduce((sum, v) => sum + v, 0);
      case "avg":
        return numericValues.reduce((sum, v) => sum + v, 0) / numericValues.length;
      case "max":
        return Math.max(...numericValues);
      case "min":
        return Math.min(...numericValues);
      default:
        return "-";
    }
  };

  // Build each data row
  const rowCombinations = sortedRowKeys.length > 0 ? sortedRowKeys : [""];
  rowCombinations.forEach((rowKey) => {
    const rowVals = uniqueRowsMap.get(rowKey) || [];
    const tableRowCells: any[] = [];

    // 1. Push Row Dimension labels
    rowVals.forEach((v) => {
      tableRowCells.push({
        value: v,
        isDimensionValue: true,
      });
    });

    // 2. Push aggregated measures for each col combination
    colCombinations.forEach((colKey) => {
      const groupKey = `${rowKey} |##| ${colKey}`;
      const cellItems = groupedData.get(groupKey) || [];

      finalMeasures.forEach((m) => {
        const val = aggregate(cellItems, m.name, m.aggType);
        tableRowCells.push({
          value: typeof val === "number" ? (Number.isInteger(val) ? val : Number(val.toFixed(2))) : val,
          isNumeric: typeof val === "number",
        });
      });
    });

    rowDataList.push(tableRowCells);
  });

  return {
    headers: finalHeaders,
    rows: rowDataList,
    hasData: true,
  };
});

// Watch props fields to auto-reset if query context changes
watch(
  () => props.fields,
  () => {
    clearAll();
  },
  { deep: true }
);
</script>

<template>
  <div class="flex flex-col lg:flex-row h-full min-h-[500px] bg-slate-50 dark:bg-gray-950/20 text-gray-700 dark:text-gray-200 overflow-hidden">
    <!-- Left Field Config Sidebar -->
    <div class="w-full lg:w-64 shrink-0 bg-white dark:bg-gray-900 border-b lg:border-b-0 lg:border-r border-gray-100 dark:border-gray-800 flex flex-col p-4">
      <div class="flex items-center justify-between pb-3 border-b border-gray-100 dark:border-gray-800 mb-4">
        <h4 class="text-sm font-bold uppercase tracking-wider text-gray-800 dark:text-gray-100 flex items-center gap-1.5">
          <TableCellsIcon class="w-4 h-4 text-primary" />
          <span>可用数据字段</span>
        </h4>
        <button
          @click="clearAll"
          class="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors text-gray-400 hover:text-red-500"
          title="重置透视表"
        >
          <ArrowPathIcon class="w-3.5 h-3.5" />
        </button>
      </div>

      <!-- Fields List -->
      <div class="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
        <div
          v-for="field in fields"
          :key="field.name"
          draggable="true"
          @dragstart="handleDragStart(field)"
          class="group p-2.5 bg-gray-50 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-800 rounded-xl cursor-grab active:cursor-grabbing hover:border-primary/30 hover:bg-white dark:hover:bg-gray-800 transition-all flex items-center justify-between"
        >
          <div class="min-w-0">
            <p class="text-xs font-bold text-gray-800 dark:text-gray-200 truncate">{{ field.label }}</p>
            <span class="text-[8px] font-mono text-gray-400 block tracking-tight truncate uppercase">
              {{ field.name }} · {{ field.type }}
            </span>
          </div>

          <!-- Quick assignment Actions -->
          <div class="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 shrink-0">
            <button
              @click="addFieldToZone(field, 'rows')"
              class="p-1 bg-white dark:bg-gray-700 hover:bg-primary hover:text-white dark:hover:bg-primary border border-gray-200 dark:border-gray-600 rounded text-[9px] font-bold transition-colors shadow-sm"
              title="加入行维度"
            >
              行
            </button>
            <button
              @click="addFieldToZone(field, 'columns')"
              class="p-1 bg-white dark:bg-gray-700 hover:bg-primary hover:text-white dark:hover:bg-primary border border-gray-200 dark:border-gray-600 rounded text-[9px] font-bold transition-colors shadow-sm"
              title="加入列维度"
            >
              列
            </button>
            <button
              @click="addFieldToZone(field, 'values')"
              class="p-1 bg-white dark:bg-gray-700 hover:bg-primary hover:text-white dark:hover:bg-primary border border-gray-200 dark:border-gray-600 rounded text-[9px] font-bold transition-colors shadow-sm"
              title="加入数值度量"
            >
              值
            </button>
          </div>
        </div>
        
        <div v-if="fields.length === 0" class="text-center py-10 opacity-50">
          <p class="text-xs font-bold text-gray-400">无可分析字段</p>
        </div>
      </div>
    </div>

    <!-- Right Pivot Work Board -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Config Drop Zones Area -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 p-4 bg-white dark:bg-gray-900 border-b border-gray-100 dark:border-gray-800">
        <!-- Rows Zone -->
        <div
          @dragover.prevent
          @drop="handleDrop('rows')"
          class="border border-dashed border-gray-200 dark:border-gray-800 hover:border-primary/40 rounded-xl p-3 bg-gray-50/20 dark:bg-gray-900/10 transition-colors min-h-[90px] flex flex-col justify-between"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">行维度 (Rows)</span>
            <span class="text-[9px] text-gray-300 dark:text-gray-600 font-medium">拖入/点击加入</span>
          </div>
          <div class="flex flex-wrap gap-1.5 items-center">
            <div
              v-for="rf in rowFields"
              :key="rf.name"
              class="inline-flex items-center space-x-1 px-2 py-0.5 bg-primary/5 dark:bg-primary-dark/20 text-primary-dark dark:text-primary border border-primary/10 dark:border-primary-dark/30 rounded text-[10px] font-bold shadow-sm"
            >
              <span>{{ rf.label }}</span>
              <button @click="removeFieldFromZone(rf.name, 'rows')" class="hover:text-red-500 transition-colors">
                <TrashIcon class="w-2.5 h-2.5" />
              </button>
            </div>
            <div v-if="rowFields.length === 0" class="text-[10px] text-gray-400 italic py-1">未添加行维度</div>
          </div>
        </div>

        <!-- Columns Zone -->
        <div
          @dragover.prevent
          @drop="handleDrop('columns')"
          class="border border-dashed border-gray-200 dark:border-gray-800 hover:border-primary/40 rounded-xl p-3 bg-gray-50/20 dark:bg-gray-900/10 transition-colors min-h-[90px] flex flex-col justify-between"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">列维度 (Columns)</span>
            <span class="text-[9px] text-gray-300 dark:text-gray-600 font-medium">拖入/点击加入</span>
          </div>
          <div class="flex flex-wrap gap-1.5 items-center">
            <div
              v-for="cf in colFields"
              :key="cf.name"
              class="inline-flex items-center space-x-1 px-2 py-0.5 bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 border border-amber-100 dark:border-amber-900/30 rounded text-[10px] font-bold shadow-sm"
            >
              <span>{{ cf.label }}</span>
              <button @click="removeFieldFromZone(cf.name, 'columns')" class="hover:text-red-500 transition-colors">
                <TrashIcon class="w-2.5 h-2.5" />
              </button>
            </div>
            <div v-if="colFields.length === 0" class="text-[10px] text-gray-400 italic py-1">未添加列维度</div>
          </div>
        </div>

        <!-- Values Zone -->
        <div
          @dragover.prevent
          @drop="handleDrop('values')"
          class="border border-dashed border-gray-200 dark:border-gray-800 hover:border-primary/40 rounded-xl p-3 bg-gray-50/20 dark:bg-gray-900/10 transition-colors min-h-[90px] flex flex-col justify-between"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[10px] font-bold text-gray-400 uppercase tracking-widest">数值度量 (Values)</span>
            <span class="text-[9px] text-gray-300 dark:text-gray-600 font-medium">拖入/点击加入</span>
          </div>
          <div class="flex flex-wrap gap-1.5 items-center">
            <div
              v-for="(mf, idx) in measureFields"
              :key="mf.name"
              class="inline-flex items-center space-x-1.5 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border border-emerald-100 dark:border-emerald-900/30 rounded text-[10px] font-bold shadow-sm relative group/val"
            >
              <span>{{ mf.label }}</span>

              <!-- Aggregation Type Switcher -->
              <div class="relative inline-flex items-center gap-0.5">
                <select
                  :value="mf.aggType"
                  @change="changeAggType(idx, ($event.target as HTMLSelectElement).value as any)"
                  class="bg-transparent border-none p-0 pr-3.5 focus:ring-0 text-[9px] text-emerald-600 dark:text-emerald-400 font-bold cursor-pointer uppercase appearance-none"
                >
                  <option value="sum">求和</option>
                  <option value="avg">平均</option>
                  <option value="count">计数</option>
                  <option value="max">最大</option>
                  <option value="min">最小</option>
                </select>
                <ChevronDownIcon class="w-2.5 h-2.5 text-emerald-600 dark:text-emerald-400 absolute right-0 pointer-events-none" />
              </div>

              <button @click="removeFieldFromZone(mf.name, 'values')" class="hover:text-red-500 transition-colors">
                <TrashIcon class="w-2.5 h-2.5" />
              </button>
            </div>
            <div v-if="measureFields.length === 0" class="text-[10px] text-gray-400 italic py-1">未添加数值度量</div>
          </div>
        </div>
      </div>

      <!-- Pivot Table Rendering Panel -->
      <div class="flex-1 overflow-auto p-6 custom-scrollbar relative">
        <div
          v-if="pivotResult.hasData"
          class="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden"
        >
          <div class="max-w-full overflow-x-auto">
            <table class="w-full border-collapse text-xs select-text">
              <thead>
                <tr
                  v-for="(headerRow, rIdx) in pivotResult.headers"
                  :key="rIdx"
                  class="bg-slate-50 dark:bg-gray-800/60 border-b border-gray-200 dark:border-gray-800/80"
                >
                  <th
                    v-for="(cell, cIdx) in headerRow"
                    :key="cIdx"
                    :rowspan="cell.rowSpan ?? 1"
                    :colspan="cell.colSpan ?? 1"
                    class="px-4 py-3 text-left font-bold text-gray-700 dark:text-gray-300 border-r border-gray-200 dark:border-gray-800"
                    :class="[
                      cell.isDimension ? 'bg-primary/5 text-primary-dark dark:text-primary' : '',
                      cell.isCategory ? 'bg-amber-500/5 text-amber-700 dark:text-amber-400 text-center' : '',
                      cell.isMeasureName ? 'bg-emerald-500/5 text-emerald-700 dark:text-emerald-400 text-[10px] uppercase font-mono' : '',
                      cell.isPadding ? 'bg-transparent border-none' : ''
                    ]"
                  >
                    {{ cell.label }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, rIdx) in pivotResult.rows"
                  :key="rIdx"
                  class="border-b border-gray-100 dark:border-gray-800 hover:bg-slate-50/50 dark:hover:bg-gray-800/30 transition-colors"
                >
                  <td
                    v-for="(cell, cIdx) in row"
                    :key="cIdx"
                    class="px-4 py-2.5 border-r border-gray-100 dark:border-gray-800 font-medium"
                    :class="[
                      cell.isDimensionValue ? 'bg-primary/5 text-gray-900 dark:text-gray-100 font-bold' : '',
                      cell.isNumeric ? 'text-right font-mono' : 'text-left'
                    ]"
                  >
                    {{ cell.value }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Empty State Prompt -->
        <div
          v-else
          class="flex flex-col items-center justify-center h-full min-h-[250px] text-gray-400 space-y-4"
        >
          <div class="p-4 bg-gray-100 dark:bg-gray-800 rounded-full">
            <TableCellsIcon class="w-12 h-12 text-gray-400" />
          </div>
          <div class="text-center max-w-sm">
            <p class="font-bold text-gray-600 dark:text-gray-300">交互式数据多维透视</p>
            <p class="text-xs opacity-75 mt-1.5 leading-relaxed">
              请从左侧“可用数据字段”中点击或拖拽字段放入上方“行维度”、“列维度”和“数值度量”中。系统将在前端运行秒级交叉分析聚合。
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
