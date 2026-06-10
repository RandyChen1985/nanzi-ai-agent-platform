<template>
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 h-full items-stretch">
    <!-- Agent Distribution Chart -->
    <div class="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
      <div class="px-6 py-4 border-b border-gray-50 bg-indigo-50/20 flex items-center justify-between flex-shrink-0">
        <h3 class="text-sm font-bold text-indigo-900 flex items-center">
          <svg class="w-4 h-4 mr-2 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
          智能体流量分布
        </h3>
      </div>
      <div class="p-6 flex-1 min-h-64 relative">
          <v-chart v-if="agentStats?.agent_performance?.length > 0" class="h-full w-full" :option="agentPieOption" autoresize />
          <div v-else class="absolute inset-0 flex items-center justify-center text-gray-300 text-xs">暂无智能体调用数据</div>
      </div>
    </div>

    <!-- Agent Performance Table -->
    <div class="lg:col-span-2 bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
      <div class="px-6 py-4 border-b border-gray-50 bg-indigo-50/20 flex items-center justify-between flex-shrink-0">
        <h3 class="text-sm font-bold text-indigo-900 flex items-center">
          <svg class="w-4 h-4 mr-2 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
          智能体性能排行榜
        </h3>
        <span class="text-[10px] text-indigo-400 font-mono uppercase tracking-widest">Performance Metrics</span>
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-50">
          <thead class="bg-gray-50/50">
            <tr>
              <th class="px-6 py-3 text-left text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">智能体名称</th>
              <th class="px-6 py-3 text-left text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">版本</th>
              <th class="px-6 py-3 text-right text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">调用次数</th>
              <th class="px-6 py-3 text-right text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">平均耗时</th>
              <th class="px-6 py-3 text-right text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">成功率</th>
              <th class="px-6 py-3 text-right text-[10px] font-bold text-gray-400 uppercase tracking-widest whitespace-nowrap">健康度</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 bg-white">
            <tr v-for="(agent, index) in agentStats?.agent_performance" :key="agent.agent_id + agent.version + index" class="hover:bg-indigo-50/10 transition-colors">
              <td class="px-6 py-3 whitespace-nowrap">
                <div class="flex items-center">
                  <div class="h-6 w-6 rounded-full bg-indigo-100 text-indigo-600 flex items-center justify-center text-xs font-bold mr-2">
                    {{ agent.name.charAt(0).toUpperCase() }}
                  </div>
                  <div class="text-sm font-medium text-gray-900">{{ agent.name }}</div>
                </div>
              </td>
              <td class="px-6 py-3 whitespace-nowrap text-left text-xs font-mono text-gray-500">
                <span class="bg-gray-100 px-2 py-0.5 rounded text-gray-600">{{ agent.version }}</span>
              </td>
              <td class="px-6 py-3 whitespace-nowrap text-right text-sm text-gray-600 font-mono">{{ agent.calls }}</td>
              <td class="px-6 py-3 whitespace-nowrap text-right text-sm font-mono" :class="agent.avg_latency > 5000 ? 'text-amber-600' : 'text-gray-600'">
                {{ agent.avg_latency }} ms
              </td>
              <td class="px-6 py-3 whitespace-nowrap text-right text-sm font-mono">{{ agent.success_rate }}%</td>
              <td class="px-6 py-3 whitespace-nowrap text-right">
                <div class="w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden inline-block align-middle">
                  <div class="h-full rounded-full" 
                        :class="agent.success_rate >= 90 ? 'bg-green-500' : agent.success_rate >= 70 ? 'bg-yellow-500' : 'bg-red-500'"
                        :style="{ width: `${agent.success_rate}%` }"></div>
                </div>
              </td>
            </tr>
            <tr v-if="!agentStats?.agent_performance?.length">
              <td colspan="5" class="px-6 py-8 text-center text-xs text-gray-400">暂无性能数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { PieChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from "echarts/components";

use([
  CanvasRenderer,
  PieChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
]);

const props = defineProps<{
  agentStats: any;
}>();

const agentPieOption = computed(() => {
  if (!props.agentStats?.agent_performance) return {};
  const data = props.agentStats.agent_performance.map((a: any) => ({
    name: a.name,
    value: a.calls
  }));
  
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    legend: {
      orient: "vertical",
      left: "left",
      top: "center",
      textStyle: { fontSize: 10 },
    },
    series: [
      {
        name: "智能体分布",
        type: "pie",
        radius: ["40%", "70%"],
        center: ["60%", "50%"],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 8, borderColor: "#fff", borderWidth: 2 },
        label: { show: false },
        emphasis: { label: { show: true, fontSize: 12, fontWeight: "bold" } },
        data: data,
      },
    ],
  };
});
</script>
