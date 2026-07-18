<template>
  <div class="bg-white rounded-2xl shadow-sm border border-gray-100 h-full flex flex-col overflow-hidden">
    <div class="px-5 py-3.5 border-b border-gray-100 flex-shrink-0">
      <h2 class="text-sm font-bold text-gray-900 flex items-center">
        <span class="w-1.5 h-1.5 rounded-full bg-primary mr-2"></span>
        智能体 Token 消耗分布
      </h2>
    </div>
    <div class="p-5 flex-1 flex flex-col min-h-0">
      <div class="flex-1 min-h-0 w-full relative flex items-center justify-center">
        <div v-if="!data || data.length === 0" class="text-gray-400 text-sm">
          暂无 Token 消耗数据
        </div>
        <v-chart v-else class="h-full w-full" :option="chartOption" autoresize />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { PieChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";

use([
  CanvasRenderer,
  PieChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
]);

const props = defineProps<{
  data: Array<{ name: string; total_tokens: number }>;
}>();

const chartOption = computed(() => {
  if (!props.data || props.data.length === 0) return {};

  const chartData = props.data.map(item => ({
    name: item.name,
    value: item.total_tokens
  }));

  return {
    tooltip: {
      trigger: "item",
      formatter: "{b}: {c} ({d}%)",
      backgroundColor: "rgba(255, 255, 255, 0.95)",
      borderWidth: 1,
      borderColor: "#e5e7eb",
      textStyle: { color: "#374151", fontSize: 13 },
      padding: [8, 12]
    },
    legend: {
      orient: "horizontal",
      bottom: "0%",
      left: "center",
      icon: "circle",
      textStyle: { color: "#4b5563", fontSize: 11 },
      pageButtonPosition: "end",
      type: "scroll"
    },
    series: [
      {
        name: "Token 消耗",
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 8,
          borderColor: "#fff",
          borderWidth: 2
        },
        label: {
          show: false,
          position: "center"
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 14,
            fontWeight: "bold",
            formatter: "{b}\n{d}%"
          },
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: "rgba(0, 0, 0, 0.1)"
          }
        },
        labelLine: {
          show: false
        },
        data: chartData,
        // HSL 莫兰迪色系渐变配制
        color: [
          "#818cf8",
          "#f472b6",
          "#34d399",
          "#fb7185",
          "#fbbf24",
          "#60a5fa",
          "#a78bfa"
        ]
      }
    ]
  };
});
</script>
