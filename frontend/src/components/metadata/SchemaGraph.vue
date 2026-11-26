<script setup lang="ts">
import { ref, onMounted, watch, onUnmounted, shallowRef } from 'vue'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import type { Table, Relationship } from '../../api/metadata'
import { metadataApi } from '../../api/metadata'
import { formatRelationshipJoinTypeLabel } from '../../utils/relationshipJoinType'

echarts.use([CanvasRenderer, GraphChart, TooltipComponent, LegendComponent])

const props = defineProps<{
  datasetId: number,
  tables: Table[]
}>()

const relationships = ref<Relationship[]>([])
const loading = ref(false)
const chartContainer = ref<HTMLElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)

const initChart = () => {
    if (chartContainer.value && !chartInstance.value) {
        chartInstance.value = echarts.init(chartContainer.value)
    }
}

const updateChart = () => {
    if (!chartInstance.value) return

    // Nodes
    const nodes = props.tables.map((table) => ({
        id: String(table.id),
        name: table.physical_name,
        category: 0, 
        symbolSize: 40,
        itemStyle: {
            color: table.physical_name.startsWith('fact_') ? '#4f46e5' : // Indigo
                   table.physical_name.startsWith('dim_') ? '#10b981' : // Emerald
                   '#6366f1' // Default
        },
        value: table.term || '',
        label: {
            show: true,
            formatter: '{b}',
            fontSize: 10
        },
        // Custom data
        term: table.term,
        desc: table.description
    }))

    // Edges
    const links = relationships.value.map(rel => ({
        source: String(rel.source_table_id),
        target: String(rel.target_table_id),
        lineStyle: {
            curveness: 0.2,
            color: '#cbd5e1',
            width: 2
        },
        symbol: ['none', 'arrow'],
        // Custom data
        join_type: rel.join_type,
        join_condition: rel.join_condition,
        description: rel.description
    }))

    const option = {
        tooltip: {
            formatter: (params: any) => {
                if (params.dataType === 'node') {
                    const data = params.data
                    return `<div class="font-bold">${data.name}</div>
                            <div class="text-xs text-gray-300">${data.term || ''}</div>`
                } else {
                    const data = params.data
                    return `<div class="font-bold text-xs">${formatRelationshipJoinTypeLabel(data.join_type)}</div>
                            <div class="text-xs text-gray-300">ON ${data.join_condition}</div>`
                }
            },
            backgroundColor: 'rgba(17, 24, 39, 0.9)',
            borderColor: '#374151',
            textStyle: { color: '#fff' }
        },
        series: [
            {
                type: 'graph',
                layout: 'force',
                data: nodes,
                links: links,
                roam: true,
                draggable: true,
                label: {
                    show: true,
                    position: 'bottom',
                    color: '#374151'
                },
                force: {
                    repulsion: 300,
                    edgeLength: [80, 150],
                    friction: 0.1
                },
                lineStyle: {
                    color: 'source',
                    curveness: 0.3
                },
                emphasis: {
                    focus: 'adjacency',
                    lineStyle: { width: 4 }
                }
            }
        ]
    }

    chartInstance.value.setOption(option)
}

const fetchData = async () => {
  loading.value = true
  try {
    const res = await metadataApi.getRelationships(props.datasetId)
    relationships.value = res.data
    // Wait for DOM update
    setTimeout(updateChart, 100)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

watch(() => props.datasetId, fetchData)

onMounted(() => {
    initChart()
    fetchData()
    window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
    window.removeEventListener('resize', handleResize)
    if (chartInstance.value) {
        chartInstance.value.dispose()
    }
})

const handleResize = () => {
    chartInstance.value?.resize()
}
</script>

<template>
  <div class="w-full h-[600px] bg-slate-50 rounded-xl border border-gray-200 shadow-inner relative overflow-hidden">
     <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/50 z-10">
        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
     </div>
     
     <div v-if="!loading && tables.length === 0" class="absolute inset-0 flex items-center justify-center text-gray-400">
        No tables to visualize
     </div>

     <!-- Chart Container -->
     <div ref="chartContainer" class="w-full h-full"></div>
     
     <!-- Legend -->
     <div class="absolute top-4 left-4 bg-white/80 backdrop-blur p-3 rounded-lg border border-gray-100 shadow-sm text-xs pointer-events-none select-none">
        <div class="font-bold text-gray-700 mb-2">Legend</div>
        <div class="flex items-center gap-2 mb-1">
           <span class="w-3 h-3 rounded-full bg-primary"></span> <span>Fact Table</span>
        </div>
        <div class="flex items-center gap-2">
           <span class="w-3 h-3 rounded-full bg-emerald-500"></span> <span>Dim/Other</span>
        </div>
        <div class="mt-2 text-[10px] text-gray-400">
           Scroll to zoom • Drag nodes
        </div>
     </div>
  </div>
</template>
