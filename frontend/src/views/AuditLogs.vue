<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-semibold text-gray-900">审计日志</h1>
      <div class="flex items-center space-x-4">
        <!-- Error Filter Checkbox (New) -->
        <label class="flex items-center space-x-2 cursor-pointer group bg-white px-3 py-2 border border-gray-200 rounded-md shadow-sm hover:border-red-200 transition-all">
          <input 
            type="checkbox" 
            v-model="filters.only_errors" 
            @change="applyFilters"
            class="w-4 h-4 text-red-600 border-gray-300 rounded focus:ring-red-500" 
          />
          <span class="text-sm font-black text-gray-600 group-hover:text-red-600">⚠️ 仅看错误</span>
        </label>

        <button
          @click="fetchLogs"
          class="inline-flex items-center px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
        >
          <svg class="-ml-1 mr-2 h-5 w-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
          刷新
        </button>
        <button
          v-if="userInfo?.role === 'admin'"
          @click="showExportDialog = true"
          class="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-md shadow-sm text-sm font-medium hover:bg-blue-700"
        >
          <svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
          导出
        </button>
      </div>
    </div>

    <!-- Statistics Cards -->
    <div v-if="statistics" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white rounded-lg shadow p-6">
        <div class="flex items-center">
          <div class="flex-shrink-0 bg-blue-100 rounded-md p-3"><svg class="h-6 w-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg></div>
          <div class="ml-5 w-0 flex-1"><dl><dt class="text-sm font-medium text-gray-500 truncate">总请求数</dt><dd class="text-lg font-semibold text-gray-900">{{ statistics.total_requests }}</dd></dl></div>
        </div>
      </div>
      <div class="bg-white rounded-lg shadow p-6">
        <div class="flex items-center">
          <div class="flex-shrink-0 rounded-md p-3" :class="statistics.success_rate >= 90 ? 'bg-green-100' : 'bg-red-100'"><svg class="h-6 w-6" :class="statistics.success_rate >= 90 ? 'text-green-600' : 'text-red-600'" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg></div>
          <div class="ml-5 w-0 flex-1"><dl><dt class="text-sm font-medium text-gray-500 truncate">成功率</dt><dd class="text-lg font-semibold" :class="statistics.success_rate >= 90 ? 'text-green-600' : 'text-red-600'">{{ statistics.success_rate }}%</dd></dl></div>
        </div>
      </div>
      <div class="bg-white rounded-lg shadow p-6">
        <div class="flex items-center">
          <div class="flex-shrink-0 bg-yellow-100 rounded-md p-3"><svg class="h-6 w-6 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg></div>
          <div class="ml-5 w-0 flex-1"><dl><dt class="text-sm font-medium text-gray-500 truncate">平均响应时间</dt><dd class="text-lg font-semibold text-gray-900">{{ statistics.avg_response_time }} ms</dd></dl></div>
        </div>
      </div>
      <div class="bg-white rounded-lg shadow p-6">
        <div class="flex items-center">
          <div class="flex-shrink-0 bg-red-100 rounded-md p-3"><svg class="h-6 w-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg></div>
          <div class="ml-5 w-0 flex-1"><dl><dt class="text-sm font-medium text-gray-500 truncate">错误数</dt><dd class="text-lg font-semibold text-red-600">{{ statistics.error_count }}</dd></dl></div>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="bg-white shadow-sm rounded-lg border border-gray-200">
      <div @click="showFilters = !showFilters" class="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 border-b border-gray-200">
        <div class="flex items-center space-x-2"><svg class="h-5 w-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" /></svg><h3 class="text-sm font-medium text-gray-900">筛选条件</h3></div>
        <svg class="h-5 w-5 text-gray-400 transition-transform duration-200" :class="{ 'rotate-180': showFilters }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" /></svg>
      </div>

      <div v-show="showFilters" class="p-4">
        <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
          <!-- Time Range -->
          <div><label class="block text-sm font-medium text-gray-700 mb-1">开始时间</label><input v-model="filters.start_time" type="datetime-local" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none" /></div>
          <div><label class="block text-sm font-medium text-gray-700 mb-1">结束时间</label><input v-model="filters.end_time" type="datetime-local" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none" /></div>

          <!-- Feature Name (Now a Dropdown) -->
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">功能点</label>
            <select v-model="filters.feature_name" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none">
              <option value="">全部功能点</option>
              <option v-for="f in availableFeatures" :key="f" :value="f">{{ f }}</option>
            </select>
          </div>

          <!-- Method -->
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">HTTP方法</label>
            <select v-model="filters.method" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none"><option value="">全部</option><option value="GET">GET</option><option value="POST">POST</option><option value="PUT">PUT</option><option value="DELETE">DELETE</option><option value="PATCH">PATCH</option></select>
          </div>

          <!-- User Name -->
          <div v-if="userInfo?.role === 'admin'"><label class="block text-sm font-medium text-gray-700 mb-1">用户名</label><input v-model="filters.user_name" type="search" placeholder="搜索用户名" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none" /></div>

          <!-- Endpoint -->
          <div><label class="block text-sm font-medium text-gray-700 mb-1">接口路径</label><input v-model="filters.endpoint" type="search" placeholder="搜索路径" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none" /></div>

          <!-- Client IP -->
          <div><label class="block text-sm font-medium text-gray-700 mb-1">客户端IP</label><input v-model="filters.client_ip" type="search" placeholder="搜索IP" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none" /></div>

          <!-- Page Size -->
          <div><label class="block text-sm font-medium text-gray-700 mb-1">每页条数</label><select v-model.number="size" @change="page = 1; fetchLogs();" class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm text-sm focus:ring-blue-500 focus:border-blue-500 outline-none"><option :value="10">10</option><option :value="20">20</option><option :value="50">50</option><option :value="100">100</option></select></div>
        </div>

        <div class="flex items-center space-x-3 mt-4">
          <button @click="applyFilters" class="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700"><svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" /></svg>应用筛选</button>
          <button @click="resetFilters" class="inline-flex items-center px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:bg-gray-50"><svg class="-ml-1 mr-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>重置</button>
        </div>
      </div>
    </div>

    <!-- Table Card -->
    <div class="bg-white shadow-sm rounded-lg border border-gray-200 overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">用户</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">功能点</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">方法</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">接口路径</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">状态码</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">耗时</th>
              <th scope="col" class="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">时间</th>
              <th scope="col" class="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody class="bg-white divide-y divide-gray-200">
            <tr v-for="log in logs" :key="log.id" class="hover:bg-gray-50 transition-colors">
              <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900"><div class="flex items-center"><div class="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold mr-3 text-xs">{{ (log.user_name || "?").charAt(0).toUpperCase() }}</div>{{ log.user_name || "系统" }}</div></td>
              <td class="px-6 py-4 whitespace-nowrap text-sm"><span class="font-bold text-gray-700 bg-gray-100 px-2 py-1 rounded text-[11px]">{{ log.feature_name || '其它' }}</span></td>
              <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500"><span class="inline-flex items-center px-2.5 py-0.5 rounded text-xs font-medium font-mono" :class="{'bg-blue-100 text-blue-800': log.method === 'GET', 'bg-green-100 text-green-800': log.method === 'POST', 'bg-yellow-100 text-yellow-800': log.method === 'PUT' || log.method === 'PATCH', 'bg-red-100 text-red-800': log.method === 'DELETE', 'bg-gray-100 text-gray-800': !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(log.method)}">{{ log.method }}</span></td>
              <td class="px-6 py-4 text-sm text-gray-600 font-mono max-w-xs truncate" :title="log.endpoint">{{ log.endpoint }}</td>
              <td class="px-6 py-4 whitespace-nowrap text-sm"><span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium" :class="{'bg-green-100 text-green-800': log.status_code >= 200 && log.status_code < 300, 'bg-yellow-100 text-yellow-800': log.status_code >= 300 && log.status_code < 400, 'bg-red-100 text-red-800': log.status_code >= 400}">{{ log.status_code }}</span></td>
              <td class="px-6 py-4 whitespace-nowrap text-sm" :class="{'text-green-600': log.process_time_ms < 100, 'text-yellow-600': log.process_time_ms >= 100 && log.process_time_ms < 500, 'text-red-600': log.process_time_ms >= 500}">{{ log.process_time_ms.toFixed(2) }} ms</td>
              <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{{ formatDate(log.created_at) }}</td>
              <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium"><button @click="viewLogDetail(log.id, log.created_at)" class="text-blue-600 hover:text-blue-900 font-bold">查看</button></td>
            </tr>
            <tr v-if="loading"><td colspan="8" class="px-6 py-12 text-center text-gray-500"><svg class="animate-spin h-8 w-8 mx-auto text-gray-400 mb-2" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>加载中...</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Footer/Pagination -->
      <div class="bg-gray-50 px-4 py-3 border-t border-gray-200 flex items-center justify-between sm:px-6">
        <div class="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
          <div><p class="text-sm text-gray-700">第 <span class="font-medium">{{ page }}</span> 页，共 <span class="font-medium">{{ total }}</span> 条结果</p></div>
          <div>
            <nav class="relative z-0 inline-flex rounded-md shadow-sm -space-x-px">
              <button :disabled="page <= 1" @click="page--; fetchLogs();" class="relative inline-flex items-center px-2 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"><svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" /></svg></button>
              <button :disabled="logs.length < size" @click="page++; fetchLogs();" class="relative inline-flex items-center px-2 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"><svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" /></svg></button>
            </nav>
          </div>
        </div>
      </div>
    </div>

    <!-- Detail Dialog -->
    <div v-if="showDetailDialog" class="fixed z-10 inset-0 overflow-y-auto" @click.self="showDetailDialog = false"><div class="flex items-center justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0"><div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity"></div><div class="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-4xl sm:w-full"><div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4"><div class="flex items-start justify-between mb-4"><h3 class="text-lg leading-6 font-medium text-gray-900">日志详情</h3><button @click="showDetailDialog = false" class="text-gray-400 hover:text-gray-500"><svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg></button></div><div v-if="logDetail" class="space-y-4"><div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700">功能点</label><p class="mt-1 text-sm text-gray-900 font-bold">{{ logDetail.feature_name || "未知" }}</p></div><div><label class="block text-sm font-medium text-gray-700">用户名</label><p class="mt-1 text-sm text-gray-900">{{ logDetail.user_name }}</p></div><div><label class="block text-sm font-medium text-gray-700">Trace ID</label><p class="mt-1 text-sm text-gray-900 font-mono text-[10px]">{{ logDetail.trace_id }}</p></div><div><label class="block text-sm font-medium text-gray-700">状态码</label><p class="mt-1 text-sm"><span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium" :class="{'bg-green-100 text-green-800': logDetail.status_code >= 200 && logDetail.status_code < 300, 'bg-yellow-100 text-yellow-800': logDetail.status_code >= 300 && logDetail.status_code < 400, 'bg-red-100 text-red-800': logDetail.status_code >= 400}">{{ logDetail.status_code }}</span></p></div><div><label class="block text-sm font-medium text-gray-700">处理时间</label><p class="mt-1 text-sm text-gray-900">{{ logDetail.process_time_ms?.toFixed(2) }} ms</p></div><div><label class="block text-sm font-medium text-gray-700">时间</label><p class="mt-1 text-sm text-gray-900">{{ formatDate(logDetail.created_at) }}</p></div><div class="col-span-2"><label class="block text-sm font-medium text-gray-700">接口路径</label><p class="mt-1 text-sm text-gray-900 font-mono break-all text-[10px]">{{ logDetail.method }} {{ logDetail.endpoint }}</p></div></div><div><label class="block text-sm font-medium text-gray-700 mb-2">请求参数</label><div class="bg-gray-50 rounded-md p-4 max-h-60 overflow-auto"><pre class="text-[10px] text-gray-800 whitespace-pre-wrap font-mono">{{ formatJSON(logDetail.request_params) }}</pre></div></div><div><label class="block text-sm font-medium text-gray-700 mb-2">响应内容</label><div class="bg-gray-50 rounded-md p-4 max-h-60 overflow-auto"><pre class="text-[10px] text-gray-800 whitespace-pre-wrap font-mono">{{ formatJSON(logDetail.response_body) }}</pre></div></div></div></div><div class="bg-gray-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse"><button @click="showDetailDialog = false" class="w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 sm:ml-3 sm:w-auto sm:text-sm">关闭</button></div></div></div></div>

    <!-- Export Dialog -->
    <div v-if="showExportDialog" class="fixed z-10 inset-0 overflow-y-auto" @click.self="showExportDialog = false"><div class="flex items-center justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0"><div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity"></div><div class="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-lg sm:w-full"><div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4"><div class="flex items-start justify-between mb-4"><h3 class="text-lg leading-6 font-medium text-gray-900">导出日志</h3><button @click="showExportDialog = false" class="text-gray-400 hover:text-gray-500"><svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg></button></div><div class="space-y-4"><div><label class="block text-sm font-medium text-gray-700 mb-2">导出格式</label><div class="grid grid-cols-2 gap-3"><button @click="exportFormat = 'csv'" class="flex items-center justify-center px-4 py-3 border rounded-md text-sm font-medium" :class="exportFormat === 'csv' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-300 text-gray-700 hover:bg-gray-50'">CSV 格式</button><button @click="exportFormat = 'json'" class="flex items-center justify-center px-4 py-3 border rounded-md text-sm font-medium" :class="exportFormat === 'json' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-300 text-gray-700 hover:bg-gray-50'">JSON 格式</button></div></div></div></div><div class="bg-gray-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse"><button @click="exportLogs" :disabled="exporting" class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-50 disabled:cursor-not-allowed">{{ exporting ? "导出中..." : "开始导出" }}</button><button @click="showExportDialog = false" class="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 focus:outline-none sm:mt-0 sm:w-auto sm:text-sm">取消</button></div></div></div></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from "vue";
import axios from "axios";
import { useToast } from "../composables/useToast";

const { showToast: addToast } = useToast();

interface AuditLog { id: number; user_name: string; feature_name: string; endpoint: string; method: string; status_code: number; process_time_ms: number; client_ip?: string; created_at: string; }
interface LogDetail { id: number; trace_id: string; user_name: string; feature_name: string; endpoint: string; method: string; status_code: number; process_time_ms: number; client_ip?: string; request_params?: any; response_body?: any; error_message?: string; created_at: string; }
interface Statistics { total_requests: number; success_count: number; error_count: number; avg_response_time: number; success_rate: number; }
interface Filters { start_time: string; end_time: string; method: string; min_status: number | null; max_status: number | null; user_name: string; endpoint: string; client_ip: string; feature_name: string; only_errors: boolean; }

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const userInfo = ref<any>(null);
const logs = ref<AuditLog[]>([]);
const availableFeatures = ref<string[]>([]);
const page = ref(1);
const size = ref(20);
const total = ref(0);
const loading = ref(false);
const statistics = ref<Statistics | null>(null);
const showFilters = ref(false);
const filters = reactive<Filters>({ start_time: "", end_time: "", method: "", min_status: null, max_status: null, user_name: "", endpoint: "", client_ip: "", feature_name: "", only_errors: false });
const showDetailDialog = ref(false);
const logDetail = ref<LogDetail | null>(null);
const showExportDialog = ref(false);
const exportFormat = ref<"csv" | "json">("csv");
const exporting = ref(false);

const fetchFeatures = async () => {
  try {
    const res = await axios.get(`${API_BASE}/api/portal/audit/features`);
    availableFeatures.value = res.data;
  } catch (e) { console.error("Fetch features failed", e); }
};

const fetchLogs = async () => {
  loading.value = true;
  try {
    const params: any = { page: page.value, size: size.value, include_stats: true };
    if (filters.start_time) params.start_time = filters.start_time;
    if (filters.end_time) params.end_time = filters.end_time;
    if (filters.method) params.method = filters.method;
    if (filters.user_name) params.user_name = filters.user_name;
    if (filters.endpoint) params.endpoint = filters.endpoint;
    if (filters.client_ip) params.client_ip = filters.client_ip;
    if (filters.feature_name) params.feature_name = filters.feature_name;
    
    // Apply "Only Errors" logic
    if (filters.only_errors) {
        params.min_status = 400;
    } else {
        if (filters.min_status) params.min_status = filters.min_status;
        if (filters.max_status) params.max_status = filters.max_status;
    }

    const response = await axios.get(`${API_BASE}/api/portal/audit/logs`, { params });
    logs.value = response.data.items || [];
    total.value = response.data.total || 0;
    statistics.value = response.data.statistics || null;
  } catch (error: any) {
    addToast(error.response?.data?.detail || "加载日志失败", "error");
  } finally { loading.value = false; }
};

const applyFilters = () => { page.value = 1; fetchLogs(); };
const resetFilters = () => { Object.assign(filters, { start_time: "", end_time: "", method: "", min_status: null, max_status: null, user_name: "", endpoint: "", client_ip: "", feature_name: "", only_errors: false }); page.value = 1; fetchLogs(); };

const viewLogDetail = async (logId: number, createdAt: string) => {
  showDetailDialog.value = true; logDetail.value = null;
  try {
    const res = await axios.get(`${API_BASE}/api/portal/audit/logs/${logId}`, { 
      params: { created_at: createdAt }
    });
    logDetail.value = res.data;
  } catch (error: any) { addToast("加载日志详情失败", "error"); showDetailDialog.value = false; }
};

const exportLogs = async () => {
  exporting.value = true;
  try {
    const params: any = { format: exportFormat.value };
    // Same filter logic for export
    if (filters.start_time) params.start_time = filters.start_time;
    if (filters.end_time) params.end_time = filters.end_time;
    if (filters.method) params.method = filters.method;
    if (filters.user_name) params.user_name = filters.user_name;
    if (filters.endpoint) params.endpoint = filters.endpoint;
    if (filters.client_ip) params.client_ip = filters.client_ip;
    if (filters.feature_name) params.feature_name = filters.feature_name;
    if (filters.only_errors) params.min_status = 400;
    else if (filters.min_status) params.min_status = filters.min_status;

    const res = await axios.get(`${API_BASE}/api/portal/audit/logs/export`, { params, responseType: "blob" });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a"); link.href = url;
    link.setAttribute("download", `audit_logs_${new Date().getTime()}.${exportFormat.value}`);
    document.body.appendChild(link); link.click(); link.remove();
    addToast("导出成功", "success"); showExportDialog.value = false;
  } catch (e) { addToast("导出失败", "error"); }
  finally { exporting.value = false; }
};

const formatDate = (d: string) => d ? new Date(d).toLocaleString("zh-CN") : "-";
const formatJSON = (d: any) => { if (!d) return "-"; try { return JSON.stringify(typeof d === 'string' ? JSON.parse(d) : d, null, 2); } catch { return d; } };
onMounted(() => { const s = localStorage.getItem("user_info"); if (s) userInfo.value = JSON.parse(s); fetchFeatures(); fetchLogs(); });
</script>
