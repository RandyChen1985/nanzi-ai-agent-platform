<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between border-b border-gray-100 pb-4">
      <div>
        <h3 class="text-lg font-semibold text-gray-800">消息通知配置</h3>
        <p class="text-sm text-gray-500 mt-1">配置您在平台内的个人消息通知通道，支持钉钉、企微机器人以及 SMTP 邮件发送。</p>
      </div>
      <button 
        @click="fetchConfigs"
        :disabled="loading"
        class="inline-flex items-center px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 active:bg-blue-200 rounded-lg transition-colors duration-200 disabled:opacity-50"
      >
        <svg v-if="loading" class="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5 text-blue-600" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        刷新配置
      </button>
    </div>

    <!-- Main Container -->
    <div v-if="loading && Object.keys(configs).length === 0" class="flex flex-col items-center justify-center py-12">
      <svg class="animate-spin h-8 w-8 text-blue-500" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span class="text-sm text-gray-500 mt-4">正在加载通知配置...</span>
    </div>

    <div v-else class="space-y-6">
      <!-- 1. DingTalk Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-blue-50 rounded-lg text-blue-500">
              <!-- Dingtalk Icon (using SVG) -->
              <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12.012 2C6.488 2 2 6.488 2 12.012c0 5.524 4.488 10.012 10.012 10.012 5.524 0 10.012-4.488 10.012-10.012C22.024 6.488 17.536 2 12.012 2zm3.328 14.88h-6.68c-.372 0-.672-.3-.672-.672 0-.256.148-.488.38-.6l6.68-3.232c.332-.16.736.008.868.344.032.088.048.18.048.272 0 .372-.3.672-.672.672l-5.632.008 5.632 2.544c.336.152.484.552.332.888-.112.248-.364.4-.64.4zm.008-5.36h-6.68c-.372 0-.672-.3-.672-.672 0-.256.148-.488.38-.6l6.68-3.232c.332-.16.736.008.868.344.032.088.048.18.048.272 0 .372-.3.672-.672.672l-5.632.008 5.632 2.544c.336.152.484.552.332.888-.112.248-.364.4-.64.4z"/>
              </svg>
            </div>
            <div>
              <div class="flex items-center">
                <h4 class="font-medium text-gray-800">钉钉群机器人通知</h4>
                <button
                  type="button"
                  class="ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-colors cursor-pointer focus:outline-none"
                  title="查看配置指引与资源获取"
                  aria-label="查看配置指引与资源获取"
                  @click.stop="openGuide('dingtalk')"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </div>
              <p class="text-xs text-gray-400 mt-0.5">申请审批、报警等结果推送至钉钉群自定义机器人。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.dingtalk.is_enabled" class="sr-only peer" @change="onToggleChannel('dingtalk')">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
          </label>
        </div>

        <!-- DingTalk Form (Transition Expand) -->
        <div v-if="configs.dingtalk.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">Webhook 地址</label>
            <input 
              type="text" 
              v-model="configs.dingtalk.webhook_url"
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">钉钉群 &rarr; 智能群助手 &rarr; 添加机器人 &rarr; 自定义 &rarr; 复制 Webhook 地址。</p>
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">加签密钥 (可选)</label>
            <input 
              type="password" 
              v-model="configs.dingtalk.secret"
              placeholder="SEC..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">若机器人启用了「加签」安全设置，请在此填写 SEC 开头的密钥。</p>
          </div>
          
          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('dingtalk')"
              :disabled="testingChannel['dingtalk'] || savingChannel['dingtalk']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['dingtalk']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('dingtalk')"
              :disabled="testingChannel['dingtalk'] || savingChannel['dingtalk']"
              class="px-4 py-2 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['dingtalk']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 2. WeChat Work Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-green-50 rounded-lg text-green-600">
              <!-- WeChat Icon -->
              <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8.22 2c-4.12 0-7.46 3-7.46 6.7 0 2.22 1.13 4.2 2.91 5.38L2.73 17c-.1.25.04.53.3.6a.6.6 0 0 0 .28-.01l3.05-1.52c.6.14 1.22.21 1.86.21 4.12 0 7.46-3 7.46-6.7S12.34 2 8.22 2zm6.26 8.35c.19 0 .37.01.56.03.35-2.73-2.02-5.18-5.32-5.18-3.7 0-6.7 2.46-6.7 5.5 0 1.84.97 3.47 2.48 4.45L4.54 18c-.08.2.03.43.23.49.08.02.16.01.23-.02l2.67-1.33c.53.13 1.09.2 1.67.2.22 0 .43-.01.65-.02-.13-.37-.2-.76-.2-1.17 0-3.2 2.66-5.8 5.92-5.8z"/>
              </svg>
            </div>
            <div>
              <div class="flex items-center">
                <h4 class="font-medium text-gray-800">企业微信群机器人通知</h4>
                <button
                  type="button"
                  class="ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-gray-400 hover:text-green-600 hover:bg-green-50 transition-colors cursor-pointer focus:outline-none"
                  title="查看配置指引与资源获取"
                  aria-label="查看配置指引与资源获取"
                  @click.stop="openGuide('wechat_work')"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </div>
              <p class="text-xs text-gray-400 mt-0.5">将平台通知推送至企微群的自定义小助手。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.wechat_work.is_enabled" class="sr-only peer" @change="onToggleChannel('wechat_work')">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"></div>
          </label>
        </div>

        <!-- WeChat Work Form -->
        <div v-if="configs.wechat_work.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">Webhook 地址</label>
            <input 
              type="text" 
              v-model="configs.wechat_work.webhook_url"
              placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500/20 focus:border-green-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">企业微信群聊 &rarr; 添加群机器人 &rarr; 新建机器人 &rarr; 复制 Webhook 地址。</p>
          </div>

          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('wechat_work')"
              :disabled="testingChannel['wechat_work'] || savingChannel['wechat_work']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['wechat_work']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('wechat_work')"
              :disabled="testingChannel['wechat_work'] || savingChannel['wechat_work']"
              class="px-4 py-2 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['wechat_work']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 3. Feishu Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-cyan-50 rounded-lg text-cyan-600">
              <!-- Feishu Icon (Paper plane / Lark icon) -->
              <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </div>
            <div>
              <div class="flex items-center">
                <h4 class="font-medium text-gray-800">飞书群机器人通知</h4>
                <button
                  type="button"
                  class="ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-gray-400 hover:text-cyan-600 hover:bg-cyan-50 transition-colors cursor-pointer focus:outline-none"
                  title="查看配置指引与资源获取"
                  aria-label="查看配置指引与资源获取"
                  @click.stop="openGuide('feishu')"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </div>
              <p class="text-xs text-gray-400 mt-0.5">申请审批、任务执行与巡检结果推送至飞书自定义机器人。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.feishu.is_enabled" class="sr-only peer" @change="onToggleChannel('feishu')">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-cyan-600"></div>
          </label>
        </div>

        <!-- Feishu Form -->
        <div v-if="configs.feishu.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">Webhook 地址</label>
            <input 
              type="text" 
              v-model="configs.feishu.webhook_url"
              placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">飞书群聊 &rarr; 设置 &rarr; 群机器人 &rarr; 添加机器人 &rarr; 自定义机器人 &rarr; 复制 Webhook 地址。</p>
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">签名密钥 (可选)</label>
            <input 
              type="password" 
              v-model="configs.feishu.secret"
              placeholder="若开启签名校验请输入秘钥"
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">若飞书机器人启用了「签名校验」安全设置，请在此填写秘钥。</p>
          </div>

          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('feishu')"
              :disabled="testingChannel['feishu'] || savingChannel['feishu']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['feishu']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('feishu')"
              :disabled="testingChannel['feishu'] || savingChannel['feishu']"
              class="px-4 py-2 text-xs font-medium text-white bg-cyan-600 hover:bg-cyan-700 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['feishu']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 4. SMTP Email Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-orange-50 rounded-lg text-orange-500">
              <!-- Email Icon -->
              <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
              </svg>
            </div>
            <div>
              <div class="flex items-center">
                <h4 class="font-medium text-gray-800">邮件通知 (SMTP)</h4>
                <button
                  type="button"
                  class="ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-gray-400 hover:text-orange-500 hover:bg-orange-50 transition-colors cursor-pointer focus:outline-none"
                  title="查看配置指引与资源获取"
                  aria-label="查看配置指引与资源获取"
                  @click.stop="openGuide('email')"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </div>
              <p class="text-xs text-gray-400 mt-0.5">绑定第三方 SMTP 服务器进行系统报警和申请邮件投递。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.email.is_enabled" class="sr-only peer" @change="onToggleChannel('email')">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-orange-500"></div>
          </label>
        </div>

        <!-- Email SMTP Form -->
        <div v-if="configs.email.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <!-- 邮件服务来源：默认走平台统一配置 -->
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">邮件服务来源</label>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <label
                class="flex items-start space-x-2.5 p-3 rounded-lg border cursor-pointer transition-colors"
                :class="configs.email.smtp_source === 'global' ? 'border-orange-400 bg-orange-50/50' : 'border-gray-200 hover:border-gray-300'"
              >
                <input
                  type="radio"
                  value="global"
                  v-model="configs.email.smtp_source"
                  class="mt-0.5 h-4 w-4 text-orange-500 focus:ring-orange-500/30 cursor-pointer"
                />
                <span class="flex-1 min-w-0">
                  <span class="block text-sm font-medium text-gray-800">使用全局邮件服务（推荐）</span>
                  <span class="block text-[11px] text-gray-400 mt-0.5">由平台统一配置，无需在此填写服务器信息</span>
                </span>
              </label>
              <label
                class="flex items-start space-x-2.5 p-3 rounded-lg border cursor-pointer transition-colors"
                :class="configs.email.smtp_source === 'custom' ? 'border-orange-400 bg-orange-50/50' : 'border-gray-200 hover:border-gray-300'"
              >
                <input
                  type="radio"
                  value="custom"
                  v-model="configs.email.smtp_source"
                  class="mt-0.5 h-4 w-4 text-orange-500 focus:ring-orange-500/30 cursor-pointer"
                />
                <span class="flex-1 min-w-0">
                  <span class="block text-sm font-medium text-gray-800">使用自定义 SMTP</span>
                  <span class="block text-[11px] text-gray-400 mt-0.5">自行填写专属 SMTP 服务器与授权码</span>
                </span>
              </label>
            </div>
          </div>

          <!-- 全局概览：只展示服务器与发件人，绝不显示密码 -->
          <div v-if="configs.email.smtp_source !== 'custom'" class="rounded-lg border border-gray-150 bg-gray-50/70 p-3.5 space-y-2">
            <!-- 已启用（判据来自 /auth/me，全角色可用） -->
            <template v-if="mailServiceAvailable !== false">
              <!-- 明细需系统配置权限；普通用户读不到，只给中性说明，
                   既不说假话，也不把内网 SMTP 中继地址暴露给所有登录用户 -->
              <template v-if="globalEmailDetail">
                <div class="flex items-center justify-between text-xs">
                  <span class="text-gray-500">SMTP 服务器</span>
                  <span class="font-mono text-gray-800">{{ globalEmailDetail.host }}:{{ globalEmailDetail.port }}</span>
                </div>
                <div class="flex items-center justify-between text-xs">
                  <span class="text-gray-500">加密方式</span>
                  <span class="font-mono text-gray-800">{{ globalEmailDetail.security }}</span>
                </div>
                <div class="flex items-center justify-between text-xs">
                  <span class="text-gray-500">发件人</span>
                  <span class="font-mono text-gray-800 truncate ml-3">{{ globalEmailDetail.senderName }} &lt;{{ globalEmailDetail.from }}&gt;</span>
                </div>
              </template>
              <p v-else class="text-xs text-gray-600 leading-relaxed">
                {{
                  mailServiceAvailable === true
                    ? '已由平台统一配置，无需在此填写服务器信息。'
                    : '暂时无法确认平台邮件服务状态，可先点「测试连通性」验证。'
                }}
              </p>
              <p class="text-[11px] text-gray-400 pt-0.5">以上由平台统一管理，如需调整请联系管理员。</p>
            </template>
            <p v-else class="text-xs text-amber-600 leading-relaxed">
              平台尚未启用邮件服务，请联系管理员配置，或改用自定义 SMTP。
            </p>
          </div>

          <!-- 自定义 SMTP 明细 -->
          <template v-if="configs.email.smtp_source === 'custom'">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div class="md:col-span-2">
                <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">SMTP 服务地址</label>
                <input 
                  type="text" 
                  v-model="configs.email.smtp_host"
                  placeholder="smtp.example.com"
                  class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
                />
              </div>
              <div>
                <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">端口</label>
                <input 
                  type="number" 
                  v-model="configs.email.smtp_port"
                  placeholder="465"
                  class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
                />
              </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">发件人账号/邮箱</label>
                <input 
                  type="email" 
                  v-model="configs.email.smtp_user"
                  placeholder="user@example.com"
                  class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
                />
              </div>
              <div>
                <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">授权码/密码</label>
                <input 
                  type="password" 
                  v-model="configs.email.smtp_password"
                  placeholder="填写您的邮箱授权码或密码"
                  class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
                />
              </div>
            </div>

            <div>
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">发件人显示昵称</label>
              <input 
                type="text" 
                v-model="configs.email.sender_name"
                placeholder="AI Agent"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
            </div>
          </template>

          <!-- 收件人区 -->
          <div class="rounded-lg border border-gray-150 p-3.5 space-y-3">
            <label class="flex items-start space-x-2.5 cursor-pointer" :class="!canIncludeSelf ? 'opacity-60 cursor-not-allowed' : ''">
              <input
                type="checkbox"
                v-model="configs.email.include_self"
                :disabled="!canIncludeSelf"
                class="mt-0.5 h-4 w-4 rounded text-orange-500 focus:ring-orange-500/30 cursor-pointer disabled:cursor-not-allowed"
              />
              <span class="flex-1 min-w-0">
                <span class="block text-sm font-medium text-gray-800">同时发送到我的邮箱</span>
                <span class="block text-[11px] text-gray-400 mt-0.5">
                  当前邮箱：{{ myEmail || '未设置' }}
                </span>
              </span>
            </label>
            <p v-if="!canIncludeSelf" class="text-[11px] text-amber-600">请先在「基本信息」中填写邮箱，之后开启此开关即可抄送自己。</p>

            <div>
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">其他收件人邮箱 (可选)</label>
              <input 
                type="text" 
                v-model="configs.email.recipients"
                placeholder="a@example.com, b@example.com"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
              <p class="text-[11px] text-gray-400 mt-1">除「我的邮箱」外额外投递的收件人，多个地址用逗号或分号分隔。</p>
            </div>
          </div>

          <!-- 测试结果留在卡片里，不随 toast 消失；失败原因往往就是下一步该做什么 -->
          <div v-if="emailTestResult" class="pt-2">
            <div
              class="rounded-lg border px-3.5 py-2.5 text-xs leading-relaxed flex items-start space-x-2"
              :class="emailTestResult.ok
                ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                : 'border-rose-200 bg-rose-50 text-rose-800'"
            >
              <span class="shrink-0 font-semibold">{{ emailTestResult.ok ? '测试通过' : '测试失败' }}</span>
              <span class="min-w-0 break-words">{{ emailTestResult.message }}</span>
            </div>
          </div>

          <!-- 提示在左、按钮成组靠右：与钉钉/企微/飞书三张卡片保持同一版式，
               按钮组必须整体作为一个 flex 项，否则 justify-between 会把
               「测试连通性」单独均分到行的正中间。 -->
          <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 pt-2">
            <p
              class="text-[11px]"
              :class="emailTestBlockedReason ? 'text-amber-600' : 'text-gray-400'"
            >
              {{ emailTestBlockedReason || '连通性测试只发到「我的邮箱」，不会发给上面的其他收件人。' }}
            </p>
            <div class="flex items-center space-x-3 ml-auto shrink-0">
              <button 
                @click="testConfig('email')"
                :disabled="emailTestDisabled"
                :title="emailTestBlockedReason"
                class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
              >
                <span v-if="testingChannel['email']" class="inline-flex items-center">
                  <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                  正在测试...
                </span>
                <span v-else>测试连通性</span>
              </button>
              <button 
                @click="saveConfig('email')"
                :disabled="testingChannel['email'] || savingChannel['email']"
                class="px-4 py-2 text-xs font-medium text-white bg-orange-500 hover:bg-orange-600 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
              >
                <span v-if="savingChannel['email']" class="inline-flex items-center">
                  <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                  保存中...
                </span>
                <span v-else>保存配置</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 渠道配置与资源获取引导弹窗 -->
    <Modal
      :show="showGuideModal"
      :title="activeGuide.title"
      size="max-w-2xl"
      @close="showGuideModal = false"
    >
      <div class="space-y-3.5 sm:space-y-5 py-1">
        <!-- 渠道副标题与类型徽标 -->
        <div class="flex items-center justify-between p-3 sm:p-3.5 bg-gray-50 rounded-xl border border-gray-100">
          <div class="text-xs text-gray-600 leading-relaxed pr-2">{{ activeGuide.subtitle }}</div>
          <span
            class="px-2.5 py-0.5 text-[11px] font-medium rounded-md border shrink-0"
            :class="activeGuide.badgeColor"
          >
            {{ getChannelName(activeGuide.channel) }}通道
          </span>
        </div>

        <!-- 步骤指引卡片 -->
        <div>
          <div class="flex items-center space-x-1.5 mb-2.5 sm:mb-3 text-xs font-semibold text-gray-700 uppercase tracking-wider">
            <span class="w-1.5 h-3.5 bg-blue-600 rounded-sm inline-block"></span>
            <span>配置步骤指引</span>
          </div>
          <div class="space-y-2 sm:space-y-2.5">
            <div
              v-for="(step, index) in activeGuide.steps"
              :key="index"
              class="flex items-start p-2.5 sm:p-3 bg-white rounded-lg border border-gray-100 hover:border-gray-200 transition-colors"
            >
              <span class="flex items-center justify-center w-5 h-5 rounded-full bg-blue-50 text-blue-600 text-xs font-bold shrink-0 mt-0.5 mr-2.5 sm:mr-3">
                {{ index + 1 }}
              </span>
              <div class="flex-1 min-w-0">
                <div class="text-xs font-semibold text-gray-800">{{ step.title }}</div>
                <div class="text-xs text-gray-500 mt-1 leading-relaxed">{{ step.content }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 官方文档与资源链接 -->
        <div v-if="activeGuide.links && activeGuide.links.length > 0">
          <div class="flex items-center space-x-1.5 mb-2 sm:mb-2.5 text-xs font-semibold text-gray-700 uppercase tracking-wider">
            <span class="w-1.5 h-3.5 bg-green-600 rounded-sm inline-block"></span>
            <span>官方资源与文档入口</span>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-2.5">
            <a
              v-for="link in activeGuide.links"
              :key="link.url"
              :href="link.url"
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center justify-between p-2.5 sm:p-3 rounded-lg border border-gray-100 bg-gray-50/70 hover:bg-blue-50/50 hover:border-blue-200 text-xs text-gray-700 hover:text-blue-600 transition-all group"
            >
              <div class="flex items-center space-x-2 truncate">
                <svg class="w-4 h-4 text-gray-400 group-hover:text-blue-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
                <span class="truncate font-medium">{{ link.label }}</span>
              </div>
              <span class="text-[11px] text-gray-400 group-hover:text-blue-500 shrink-0 ml-2">访问 &rarr;</span>
            </a>
          </div>
        </div>

        <!-- 避坑与注意事项提示 -->
        <div v-if="activeGuide.tips" class="p-3 bg-amber-50/80 border border-amber-200/60 rounded-xl">
          <div class="flex items-start space-x-2">
            <svg class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <div class="text-[11px] text-amber-800 leading-relaxed font-normal">
              <strong class="font-medium">配置贴士：</strong>
              {{ activeGuide.tips }}
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <div class="flex justify-end">
          <button
            type="button"
            class="w-full sm:w-auto px-5 py-2.5 sm:py-1.5 text-xs sm:text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 sm:text-gray-700 sm:bg-gray-100 sm:hover:bg-gray-200 rounded-xl sm:rounded-lg shadow-sm sm:shadow-none transition-all cursor-pointer text-center"
            @click="showGuideModal = false"
          >
            我知道了
          </button>
        </div>
      </template>
    </Modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import axios from '../../utils/axios'
import Modal from '../Modal.vue'

const emit = defineEmits(['show-toast'])

const loading = ref(false)
const configs = ref<Record<string, any>>({
  dingtalk: { is_enabled: false, webhook_url: '', secret: '' },
  wechat_work: { is_enabled: false, webhook_url: '' },
  feishu: { is_enabled: false, webhook_url: '', secret: '' },
  email: { is_enabled: false, smtp_source: 'global', include_self: true, smtp_host: '', smtp_port: 465, smtp_user: '', smtp_password: '', sender_name: 'AI Agent', recipients: '' }
})

// 当前登录用户（用于展示/勾选「抄送自己」的邮箱）
const me = ref<{ email?: string } | null>(null)
// 全局邮件服务概览（只含服务器与发件人，绝不含密码）
const globalEmailConfig = ref<Record<string, string>>({})

// 平台邮件服务是否已启用 —— 唯一判据，取自 /auth/me 的 mail_service_available。
//
// **刻意不用 /system/configs 判断**：那是系统配置接口，要求 menu:system:config 权限，
// 普通用户会拿到 403；若拿它判断启用状态，403 就会被误解成「平台未启用」，界面于是
// 理直气壮地对普通用户说了假话（发信其实一直是好的）。这个 bug 不报错、不影响功能，
// 只是让 UI 说了假话，所以必须靠 tests/frontend/test_notification_global_mail_contract.py 钉住。
const mailServiceAvailable = ref<boolean | null>(null)

const myEmail = computed(() => String(me.value?.email || '').trim())
const canIncludeSelf = computed(() => !!myEmail.value)

// 明细（主机/端口/发件人）只对能读到系统配置的角色展示：内网 SMTP 中继地址没有必要
// 暴露给每个登录用户。读不到明细时概览区只说「已由平台统一配置」，而不会声称未启用。
const globalEmailDetail = computed(() => {
  const cfg = globalEmailConfig.value
  const host = String(cfg['email_smtp_host'] || '').trim()
  if (!host) return null
  const from = String(cfg['email_from_address'] || cfg['email_smtp_user'] || '').trim()
  return {
    host,
    port: String(cfg['email_smtp_port'] || '465'),
    security: String(cfg['email_smtp_security'] || 'ssl'),
    senderName: String(cfg['email_sender_name'] || 'AI Agent').trim(),
    from: from || '未设置',
  }
})

const testingChannel = ref<Record<string, boolean>>({})
const savingChannel = ref<Record<string, boolean>>({})

// 最近一次「测试连通性」的结果，持久留在卡片里。
// 只靠全局 toast 不够：它 3.5 秒就消失，失败原因（尤其「平台服务未启用」这类需要
// 管理员动作、或需要用户先去填邮箱的）必须留在原地，用户才看得到该做什么。
const emailTestResult = ref<{ ok: boolean; message: string } | null>(null)

// 配置是否填够、足以发起连通测试 —— 决定「测试连通性」是否置灰。
//
// 自定义来源**镜像后端** SmtpSettings.is_complete()：host 是唯一必需项，账号与密码必须
// 同时填或同时空（免认证的内网中继合法地没有账号密码；只填一个属半套凭据，后端本就会
// 判为不完整）。判定用**表单当前值**，因为 POST /notifications/test 收到的正是
// config_data（脱敏密码在后端反解回真值）——这点与管理员页「测试已保存配置」不同。
const customSmtpConfigured = computed(() => {
  const cfg = configs.value.email || {}
  if (!String(cfg.smtp_host || '').trim()) return false
  const hasUser = !!String(cfg.smtp_user || '').trim()
  const hasPassword = !!String(cfg.smtp_password || '').trim()
  return hasUser === hasPassword
})

// 置灰原因（空串 = 可测）。必须**可见地**展示在按钮旁，否则用户面对一个灰按钮
// 不知道要做什么——title 在触屏上根本看不到。
const emailTestBlockedReason = computed(() => {
  const cfg = configs.value.email || {}
  if (cfg.smtp_source === 'custom') {
    return customSmtpConfigured.value
      ? ''
      : '请先填写 SMTP 服务地址；发件人账号与授权码需同时填写（免认证内网中继可都留空）。'
  }
  // 只有**明确**读到未启用才置灰：状态未知时不锁死按钮，让用户点下去以测试结果为准
  return mailServiceAvailable.value === false
    ? '平台尚未启用邮件服务，请联系管理员配置，或改用自定义 SMTP。'
    : ''
})

const emailTestDisabled = computed(
  () =>
    testingChannel.value['email'] ||
    savingChannel.value['email'] ||
    !!emailTestBlockedReason.value
)

const showGuideModal = ref(false)
const currentGuideChannel = ref<'dingtalk' | 'wechat_work' | 'feishu' | 'email'>('dingtalk')

interface GuideStep {
  title: string
  content: string
}

interface GuideLink {
  label: string
  url: string
}

interface ChannelGuide {
  channel: string
  title: string
  subtitle: string
  badgeColor: string
  steps: GuideStep[]
  links: GuideLink[]
  tips?: string
}

const GUIDE_DATA: Record<string, ChannelGuide> = {
  dingtalk: {
    channel: 'dingtalk',
    title: '钉钉群自定义机器人配置指引',
    subtitle: '通过 Webhook 将任务结果与系统警报推送到钉钉群聊',
    badgeColor: 'bg-blue-50 text-blue-600 border-blue-200',
    steps: [
      { title: '打开群设置', content: '在电脑端钉钉打开需要接收通知的群聊，点击右上角「⚙️ 群设置」图标。' },
      { title: '添加智能群助手', content: '在群设置面板中选择「智能群助手」 ➔ 点击「添加机器人」。' },
      { title: '选择自定义机器人', content: '在机器人库列表中找到并点击「自定义（通过 Webhook 接入自定义服务）」 ➔ 点击「添加」。' },
      { title: '配置安全设置与名称', content: '输入机器人名称（如“AI智能体通知”）。安全设置推荐勾选「加签」，复制出以 SEC 开头的加签密钥；也可添加自定义关键词（如“测试”、“通知”）。' },
      { title: '复制 Webhook 地址', content: '点击完成，系统将生成专属 Webhook 地址（形如 https://oapi.dingtalk.com/robot/send?access_token=...），点击复制。' },
      { title: '填入平台并保存', content: '将 Webhook 地址与加签密钥（若有）粘贴到本平台输入框，点击「测试连通性」，确认群内收到消息后点击「保存配置」。' },
    ],
    links: [
      { label: '钉钉开放平台：自定义机器人接入指南', url: 'https://open.dingtalk.com/document/robots/custom-robot-access' }
    ],
    tips: '若开启了加签安全设置，必须将 SEC 开头的加签秘钥同步填入，否则钉钉服务端会因签名不匹配直接拒收消息。'
  },
  wechat_work: {
    channel: 'wechat_work',
    title: '企业微信群机器人配置指引',
    subtitle: '通过群机器人 Webhook 将分析结论和巡检消息推送至企业微信群',
    badgeColor: 'bg-green-50 text-green-600 border-green-200',
    steps: [
      { title: '进入企业微信群聊', content: '在电脑端打开企业微信，进入需要接收通知的内部群（需拥有群机器人添加权限）。' },
      { title: '添加群机器人', content: '右键点击群聊或点击右上角「…」群信息设置 ➔ 选择「添加群机器人」。' },
      { title: '新建机器人', content: '点击「新创建一个机器人」，设置机器人名称（如“AI平台小助手”）与头像，点击「添加」。' },
      { title: '复制 Webhook 地址', content: '添加成功后，系统会展示对应的 Webhook 地址（形如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...），点击「复制」。' },
      { title: '填入平台并保存', content: '将复制的 Webhook 地址粘贴到本平台输入框，点击「测试连通性」，确认企微群收到消息后点击「保存配置」。' },
    ],
    links: [
      { label: '企业微信开发者中心：群机器人配置说明', url: 'https://developer.work.weixin.qq.com/document/path/91770' }
    ],
    tips: '企业微信机器人单条消息限制 4096 字节，平台已内置 UTF-8 字节数安全截断与网络退避重试，保障消息完整有效。'
  },
  feishu: {
    channel: 'feishu',
    title: '飞书群自定义机器人配置指引',
    subtitle: '利用飞书交互卡片将富文本结论和指标表格优雅投递至飞书群',
    badgeColor: 'bg-cyan-50 text-cyan-600 border-cyan-200',
    steps: [
      { title: '进入飞书群设置', content: '在电脑端飞书打开接收通知的群聊，点击右上角「⚙️ 设置」图标 ➔ 进入「群机器人」菜单。' },
      { title: '添加自定义机器人', content: '点击「添加机器人」按钮 ➔ 在机器人列表中选择「自定义机器人」 ➔ 点击「添加」。' },
      { title: '配置机器人与签名校验', content: '填写机器人名称与描述。安全设置中推荐勾选「签名校验」，复制出提供的密钥（Secret）；若不勾选签名校验，则秘钥留空即可。' },
      { title: '复制 Webhook 地址', content: '点击保存，系统将生成 Webhook 地址（形如 https://open.feishu.cn/open-apis/bot/v2/hook/...），点击复制。' },
      { title: '填入平台并保存', content: '将 Webhook 地址与签名密钥填入本平台输入框，点击「测试连通性」，确认飞书群收到卡片后点击「保存配置」。' },
    ],
    links: [
      { label: '飞书开放平台：自定义机器人使用指南', url: 'https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot' }
    ],
    tips: '飞书通知已升级为 interactive 交互卡片协议，支持完整 Markdown 排版；若机器人开启了签名校验，请务必同时填写签名密钥。'
  },
  email: {
    channel: 'email',
    title: 'SMTP 邮件服务配置指引',
    subtitle: '绑定第三方常用邮箱（QQ/163/企业邮）SMTP 服务发送结果邮件',
    badgeColor: 'bg-orange-50 text-orange-600 border-orange-200',
    steps: [
      { title: '登录邮箱网页端', content: '使用发信账号登录网页端邮箱（如 QQ 邮箱、163 网易邮箱或腾讯/阿里企业邮箱）。' },
      { title: '进入设置开启 SMTP', content: '进入「设置」 ➔ 「账户」，找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务」设置板块。' },
      { title: '生成客户端授权码', content: '开启「POP3/SMTP服务」或「IMAP/SMTP服务」，按照短信验证指引生成 16 位「客户端专属授权码」（或应用密码）。' },
      { title: '查看服务器地址与端口', content: 'QQ 邮箱服务器为 smtp.qq.com（SSL 端口 465）；163 邮箱为 smtp.163.com（SSL 端口 465/994）；企业邮箱参考服务商提供地址（端口 465）。' },
      { title: '填写本平台表单', content: '在服务地址填入 SMTP 服务器，端口填 465，账号填写完整发件邮箱，密码填入刚刚生成的「授权码」，收件人填写接收邮箱（多个用分号分隔）。' },
      { title: '测试与保存', content: '点击「测试连通性」，确认收件箱收到测试邮件后点击「保存配置」。' },
    ],
    links: [
      { label: 'QQ 邮箱帮助：开启 POP3/SMTP 服务与获取授权码', url: 'https://service.mail.qq.com/detail/0/75' },
      { label: '163 邮箱帮助：客户端授权密码设置指南', url: 'https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac2' }
    ],
    tips: '重要注意：SMTP 密码必须填写邮箱服务商生成的「专属客户端授权码/应用密码」，绝非平时登录网页邮箱的登录密码！'
  }
}

const activeGuide = computed(() => GUIDE_DATA[currentGuideChannel.value] || GUIDE_DATA.dingtalk)

const openGuide = (channel: 'dingtalk' | 'wechat_work' | 'feishu' | 'email') => {
  currentGuideChannel.value = channel
  showGuideModal.value = true
}

const getChannelName = (channel: string) => {
  switch (channel) {
    case 'dingtalk': return '钉钉'
    case 'wechat_work': return '企微'
    case 'feishu': return '飞书'
    case 'email': return '邮件'
    default: return channel
  }
}

const fetchMe = async () => {
  try {
    const res = await axios.get('/api/portal/auth/me')
    me.value = res.data || null
    // 三态：true/false 为后端权威判定；请求失败保持 null（未知），
    // 未知与「未启用」必须区分——否则一次瞬时失败会被当成未启用，把可用的服务锁死。
    mailServiceAvailable.value =
      typeof res.data?.data?.mail_service_available === 'boolean'
        ? res.data.data.mail_service_available
        : null
  } catch {
    me.value = null
    mailServiceAvailable.value = null
  }
}

const fetchGlobalEmailConfig = async () => {
  try {
    const res = await axios.get('/api/portal/system/configs')
    const items: Array<{ key: string; value: any }> = res.data?.email || []
    const map: Record<string, string> = {}
    for (const item of items) map[item.key] = item.value
    globalEmailConfig.value = map
  } catch {
    // 无权限（普通用户）或未配置：明细为空 → 概览区退化为「已由平台统一配置」，
    // **不再据此断言「尚未启用」** —— 是否启用看 /auth/me 的标志位。
    globalEmailConfig.value = {}
  }
}

const fetchConfigs = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/notifications/config')
    if (res.data) {
      const raw = res.data
      const email = raw.email || {}
      // 存量兼容：老记录缺 smtp_source/include_self。
      // smtp_source 的兜底必须**镜像后端** EmailDeliveryService.resolve_smtp_source：
      // 填过 smtp_host 的存量记录实际按「自定义」发信，若这里硬兜底成 'global'，
      // 界面会显示「使用全局服务」并藏掉他自己的 SMTP 字段，与实际发信不符；
      // 而用户下一次保存就会把 'global' 落库，静默改用（默认未启用的）全局服务。
      // 后端已回填真实来源，这里是防御性兜底，保证旧后端下也不会误导。
      configs.value = {
        ...raw,
        email: {
          ...email,
          smtp_source: raw.email?.smtp_source || (email.smtp_host ? 'custom' : 'global'),
          include_self: raw.email?.include_self !== false,
        },
      }
    }
  } catch (error: any) {
    console.error("Failed to load configs", error)
    emit('show-toast', error.response?.data?.detail || '获取通知配置失败', 'error')
  } finally {
    loading.value = false
  }
}

const saveConfig = async (channel: string, successMessage?: string) => {
  savingChannel.value[channel] = true
  try {
    const res = await axios.put('/api/portal/notifications/config', {
      channel_type: channel,
      config_data: configs.value[channel]
    })
    if (res.data && res.data.status === 'success') {
      emit('show-toast', successMessage || `${getChannelName(channel)}配置保存成功`, 'success')
      await fetchConfigs() // 重新拉取以更新打星号
    }
  } catch (error: any) {
    emit('show-toast', error.response?.data?.detail || '保存配置失败', 'error')
  } finally {
    savingChannel.value[channel] = false
  }
}

// 开关本身也要持久化：关闭后表单（含保存按钮）会收起，若不在此保存，刷新后状态会回弹
const onToggleChannel = async (channel: string) => {
  const enabled = configs.value[channel]?.is_enabled
  await saveConfig(channel, `${getChannelName(channel)}通知已${enabled ? '开启' : '关闭'}`)
}

const testConfig = async (channel: string) => {
  testingChannel.value[channel] = true
  if (channel === 'email') emailTestResult.value = null
  try {
    const res = await axios.post('/api/portal/notifications/test', {
      channel_type: channel,
      config_data: configs.value[channel]
    })
    if (res.data && res.data.status === 'success') {
      if (channel === 'email') {
        // 邮件的成功反馈已经持久落在卡片里（紧邻测试按钮上方的绿色提示条），
        // 再弹一次全局 toast 是对同一信息的重复打扰 —— 用户实测反馈「多余」。
        // 其余渠道没有卡片内结果区，仍靠 toast 反馈。
        emailTestResult.value = { ok: true, message: res.data.message || '测试连通成功' }
      } else {
        emit('show-toast', `${getChannelName(channel)}测试连通成功！`, 'success')
      }
    }
  } catch (error: any) {
    const message = error.response?.data?.detail || '测试连通失败'
    emit('show-toast', message, 'error')
    // 失败仍保留 toast：成功可以是安静的，失败要在第一时间被看见。
    // 邮件渠道额外把原因留在卡片里：这条是给用户看的，不能一闪而过
    if (channel === 'email') emailTestResult.value = { ok: false, message }
  } finally {
    testingChannel.value[channel] = false
  }
}

onMounted(() => {
  fetchConfigs()
  fetchMe()
  fetchGlobalEmailConfig()
})

// 换了来源（全局 ↔ 自定义）之后，上一次的结论已不适用；留着会误导，
// 比如从「自定义」切到「全局」后仍然显示针对自定义 SMTP 的失败原因。
watch(
  () => configs.value.email?.smtp_source,
  () => { emailTestResult.value = null }
)
</script>
