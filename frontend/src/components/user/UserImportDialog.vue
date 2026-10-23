<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[12000] flex items-center justify-center bg-gray-900/50 p-3 backdrop-blur-sm sm:p-5"
      @click.self="onOverlayClick"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-import-title"
        class="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      >
        <!-- Header -->
        <div class="flex shrink-0 items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <h2 id="user-import-title" class="text-lg font-bold text-gray-900">批量导入用户</h2>
              <span class="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
                Excel 批量建号
              </span>
            </div>
            <p class="mt-0.5 text-xs text-gray-400">
              下载模板填写账号、姓名、邮箱与角色，确认后批量生成账号并回显初始密码
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="submitting"
            title="关闭"
            @click="requestClose"
          >
            <XMarkIcon class="h-5 w-5" />
          </button>
        </div>

        <!-- Step indicator -->
        <div class="shrink-0 border-b border-gray-100 bg-gray-50/70 px-6 py-2.5">
          <ol class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-medium">
            <li v-for="(item, index) in steps" :key="item.key" class="flex items-center gap-2">
              <span
                class="flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold"
                :class="stepIndex > index
                  ? 'bg-emerald-100 text-emerald-700'
                  : stepIndex === index
                    ? 'bg-primary text-white'
                    : 'bg-gray-200 text-gray-500'"
              >
                <CheckIcon v-if="stepIndex > index" class="h-3 w-3" />
                <template v-else>{{ index + 1 }}</template>
              </span>
              <span :class="stepIndex >= index ? 'text-gray-800' : 'text-gray-400'">{{ item.label }}</span>
              <ChevronRightIcon v-if="index < steps.length - 1" class="h-3 w-3 text-gray-300" />
            </li>
          </ol>
        </div>

        <!-- Body -->
        <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <!-- Step 1: upload -->
          <section v-if="step === 'upload'" class="space-y-5">
            <div class="flex flex-col gap-3 rounded-xl border border-indigo-100 bg-indigo-50/60 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div class="flex items-start gap-2.5">
                <DocumentArrowDownIcon class="mt-0.5 h-5 w-5 shrink-0 text-indigo-500" />
                <div>
                  <p class="text-sm font-semibold text-indigo-900">第一步：下载导入模板</p>
                  <p class="mt-0.5 text-xs leading-relaxed text-indigo-700/80">
                    模板包含「账号、姓名、邮箱、角色」四列，并在说明页列出当前全部业务角色编码与名称。
                  </p>
                </div>
              </div>
              <button
                type="button"
                class="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-sm font-medium text-indigo-700 shadow-sm transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
                :disabled="downloadingTemplate"
                @click="downloadTemplate"
              >
                <ArrowDownTrayIcon class="h-4 w-4" :class="{ 'animate-bounce': downloadingTemplate }" />
                {{ downloadingTemplate ? "生成中..." : "下载模板" }}
              </button>
            </div>

            <div
              class="rounded-2xl border-2 border-dashed p-8 text-center transition-colors"
              :class="dragging ? 'border-primary bg-primary/5' : 'border-gray-300 bg-gray-50/50'"
              @click="openFilePicker"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
            >
              <input
                ref="fileInput"
                type="file"
                accept=".xlsx"
                class="hidden"
                @click.stop
                @change="onFileChange"
              />
              <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white shadow-sm">
                <ArrowUpTrayIcon class="h-6 w-6 text-primary" />
              </div>
              <p class="mt-3 text-sm font-medium text-gray-800">
                {{ uploading ? "正在解析 Excel..." : "点击选择或拖拽 .xlsx 文件到此处" }}
              </p>
              <p class="mt-1 text-xs text-gray-400">
                单个文件不超过 2MB，单次最多 500 行；上传后先预览确认，不会立即建号
              </p>
              <div v-if="uploading" class="mx-auto mt-4 h-1 w-40 overflow-hidden rounded-full bg-gray-200">
                <div class="h-full w-1/2 animate-pulse rounded-full bg-primary"></div>
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-3">
              <div
                v-for="tip in uploadTips"
                :key="tip.title"
                class="rounded-xl border border-gray-100 bg-white p-3.5 shadow-sm"
              >
                <p class="text-xs font-semibold text-gray-700">{{ tip.title }}</p>
                <p class="mt-1 text-xs leading-relaxed text-gray-500">{{ tip.body }}</p>
              </div>
            </div>
          </section>

          <!-- Step 2: confirm -->
          <section v-else-if="step === 'confirm'" class="space-y-4">
            <div class="flex flex-col gap-2.5 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div class="flex min-w-0 items-center gap-2 text-xs text-gray-600">
                <DocumentCheckIcon class="h-4 w-4 shrink-0 text-primary" />
                <span class="truncate font-medium text-gray-800" :title="fileName">{{ fileName }}</span>
                <span class="shrink-0 rounded-full bg-white px-2 py-0.5 font-mono text-[11px] text-gray-500">
                  共 {{ rows.length }} 行
                </span>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  @click="addRow"
                >
                  <PlusIcon class="h-3.5 w-3.5" />
                  补充新增
                </button>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="selectedCount === 0"
                  @click="removeSelected"
                >
                  <TrashIcon class="h-3.5 w-3.5" />
                  删除所选
                </button>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  @click="restartUpload"
                >
                  <ArrowPathIcon class="h-3.5 w-3.5" />
                  重新上传
                </button>
              </div>
            </div>

            <!-- 建号选项：随机/固定密码、是否把密码发到用户邮箱 -->
            <div class="grid gap-4 rounded-xl border border-gray-200 bg-white p-4 lg:grid-cols-2">
              <div class="space-y-2">
                <p class="text-sm font-semibold text-gray-800">初始密码</p>
                <div class="flex flex-wrap items-center gap-4 text-sm text-gray-700">
                  <label class="inline-flex cursor-pointer items-center gap-1.5">
                    <input
                      v-model="passwordMode"
                      type="radio"
                      value="random"
                      class="h-3.5 w-3.5 border-gray-300 text-primary focus:ring-primary"
                    />
                    随机密码（每个账号不同）
                  </label>
                  <label class="inline-flex cursor-pointer items-center gap-1.5">
                    <input
                      v-model="passwordMode"
                      type="radio"
                      value="fixed"
                      class="h-3.5 w-3.5 border-gray-300 text-primary focus:ring-primary"
                    />
                    固定密码（所有账号相同）
                  </label>
                </div>
                <p v-if="passwordMode === 'random'" class="text-xs leading-relaxed text-gray-400">
                  每个账号生成独立的 12 位随机密码，满足等保复杂度要求，且不会包含账号名。
                </p>
                <div v-else class="space-y-1">
                  <div class="relative">
                    <input
                      v-model="fixedPassword"
                      :type="showFixedPassword ? 'text' : 'password'"
                      maxlength="32"
                      autocomplete="new-password"
                      placeholder="输入统一初始密码（8-32 位，至少 3 类字符）"
                      class="w-full rounded-lg border px-3 py-2 pr-14 text-sm outline-none focus:ring-2 focus:ring-primary/20"
                      :class="fixedPasswordIssue ? 'border-amber-300' : 'border-gray-300'"
                    />
                    <button
                      type="button"
                      class="absolute inset-y-0 right-0 px-3 text-xs font-medium text-gray-500 hover:text-primary"
                      @click="showFixedPassword = !showFixedPassword"
                    >
                      {{ showFixedPassword ? "隐藏" : "显示" }}
                    </button>
                  </div>
                  <p
                    class="text-xs leading-relaxed"
                    :class="fixedPasswordIssue ? 'text-red-600' : 'text-gray-400'"
                  >
                    {{
                      fixedPasswordIssue ||
                      "所有本次导入账号共用该密码；不能包含任一账号名，建议用户首次登录后修改。"
                    }}
                  </p>
                </div>
              </div>

              <div class="flex flex-col justify-between gap-2 border-t border-gray-100 pt-3 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <p class="text-sm font-semibold text-gray-800">发送密码到用户邮箱</p>
                    <p class="mt-0.5 text-xs leading-relaxed text-gray-500">
                      开启后，账号创建成功会自动把「账号 + 初始密码」发到用户邮箱。
                    </p>
                  </div>
                  <Switch v-model="sendPasswordEmail" :disabled="!emailServiceAvailable" />
                </div>
                <p
                  class="text-xs leading-relaxed"
                  :class="!emailServiceAvailable
                    ? 'text-red-600'
                    : sendPasswordEmail
                      ? 'text-amber-600'
                      : 'text-gray-400'"
                >
                  {{
                    !emailServiceAvailable
                      ? "平台未开启全局邮件服务，请先在「系统配置 → 邮件服务」中启用并保存。"
                      : sendPasswordEmail
                        ? "使用平台全局邮件服务发送；结果页会显示每封成功/失败及原因，账号仍会创建。"
                        : "关闭时请在结果页复制或下载密码，再通过安全渠道分发。"
                  }}
                </p>
              </div>
            </div>

            <!-- 重复数据的显式提示：只在行内标红容易被忽略，这里按类型汇总，
                 并说明「不会导入」与「如何继续」，避免看起来像被静默跳过。 -->
            <div
              v-if="existedRows.length || emailTakenRows.length || roleMissingRows.length"
              class="rounded-xl border border-amber-200 bg-amber-50/80 px-4 py-3"
              role="status"
            >
              <div class="flex items-start gap-2">
                <ExclamationTriangleIcon class="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <div class="space-y-1 text-xs leading-relaxed text-amber-800">
                  <p class="font-semibold">导入前检查：以下数据不会被导入</p>
                  <p v-if="existedRows.length">
                    账号已存在 <strong>{{ existedRows.length }}</strong> 行<span v-if="existedSelected > 0">（其中 {{ existedSelected }} 行仍处于勾选状态，取消勾选后才能提交）</span>：已在表格中标红，可取消勾选，或把账号改成新名称。
                  </p>
                  <p v-if="emailTakenRows.length">
                    邮箱已被占用 <strong>{{ emailTakenRows.length }}</strong> 行：请更换邮箱，或取消勾选这些行。
                  </p>
                  <p v-if="roleMissingRows.length">
                    角色未选择或未匹配 <strong>{{ roleMissingRows.length }}</strong> 行：请在「角色」列选择业务角色后再导入。
                  </p>
                </div>
              </div>
            </div>

            <div class="overflow-hidden rounded-xl border border-gray-200">
              <div class="max-h-[46vh] overflow-auto">
                <table class="min-w-[920px] w-full border-collapse text-left text-sm">
                  <thead class="sticky top-0 z-10 bg-gray-50 text-xs text-gray-500">
                    <tr>
                      <th class="w-9 px-3 py-2.5">
                        <input
                          type="checkbox"
                          class="h-3.5 w-3.5 cursor-pointer rounded border-gray-300"
                          :checked="allSelected"
                          :indeterminate.prop="someSelected"
                          aria-label="全选"
                          @change="toggleAll"
                        />
                      </th>
                      <th class="w-10 px-2 py-2.5 font-medium">行</th>
                      <th class="w-44 px-2 py-2.5 font-medium">账号</th>
                      <th class="w-28 px-2 py-2.5 font-medium">姓名</th>
                      <th class="w-52 px-2 py-2.5 font-medium">邮箱</th>
                      <th class="w-36 px-2 py-2.5 font-medium">角色</th>
                      <th class="min-w-[200px] px-2 py-2.5 font-medium">校验结果</th>
                      <th class="sticky right-0 z-20 w-11 bg-gray-50 px-2 py-2.5 shadow-[-8px_0_8px_-8px_rgba(15,23,42,0.25)]"></th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-100">
                    <tr
                      v-for="row in rows"
                      :key="row.uid"
                      class="align-top transition-colors"
                      :class="rowClass(row)"
                    >
                      <td class="px-3 pt-3">
                        <input
                          v-model="row.selected"
                          type="checkbox"
                          class="h-3.5 w-3.5 cursor-pointer rounded border-gray-300"
                          :aria-label="`选择第 ${row.rowNumber || '新' } 行`"
                        />
                      </td>
                      <td class="px-2 pt-3 font-mono text-xs text-gray-400">
                        {{ row.rowNumber || "新增" }}
                      </td>
                      <td class="px-2 pt-2">
                        <input
                          v-model="row.user_name"
                          type="text"
                          maxlength="50"
                          placeholder="账号"
                          class="w-full rounded-lg border px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/20"
                          :class="row.user_name.trim() ? 'border-gray-300' : 'border-amber-300 bg-amber-50/40'"
                          @input="onNameEdited(row)"
                        />
                      </td>
                      <td class="px-2 pt-2">
                        <input
                          v-model="row.real_name"
                          type="text"
                          maxlength="50"
                          placeholder="姓名（选填）"
                          class="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/20"
                        />
                      </td>
                      <td class="px-2 pt-2">
                        <input
                          v-model="row.email"
                          type="email"
                          maxlength="254"
                          placeholder="name@example.com"
                          class="w-full rounded-lg border px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/20"
                          :class="emailLooksValid(row.email) ? 'border-gray-300' : 'border-amber-300 bg-amber-50/40'"
                          @input="onEmailEdited(row)"
                        />
                      </td>
                      <td class="px-2 pt-2">
                        <select
                          v-model="row.role_id"
                          class="w-full rounded-lg border px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/20"
                          :class="row.role_id === null ? 'border-amber-300 bg-amber-50/40' : 'border-gray-300'"
                          @change="row.role_from_default = false"
                        >
                          <option :value="null">请选择角色</option>
                          <option v-for="role in roles" :key="role.id" :value="role.id">
                            {{ role.name }}（{{ role.code }}）
                          </option>
                        </select>
                        <p v-if="row.role_from_default && row.role_input === ''" class="mt-1 text-[11px] text-gray-400">
                          角色留空，已默认选择 default
                        </p>
                      </td>
                      <td class="px-2 pt-3">
                        <ul v-if="rowIssues(row).length" class="space-y-0.5 text-[11px] leading-relaxed">
                          <li
                            v-for="issue in rowIssues(row)"
                            :key="issue"
                            class="flex items-start gap-1"
                            :class="isHardBlocking(row) ? 'text-red-600' : 'text-amber-600'"
                          >
                            <ExclamationTriangleIcon class="mt-0.5 h-3 w-3 shrink-0" />
                            <span>{{ issue }}</span>
                          </li>
                        </ul>
                        <span v-else class="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600">
                          <CheckCircleIcon class="h-3.5 w-3.5" />
                          可导入
                        </span>
                      </td>
                      <td
                        class="sticky right-0 z-10 w-11 border-l border-gray-100 px-2 pt-3 text-center shadow-[-8px_0_8px_-8px_rgba(15,23,42,0.25)]"
                        :class="rowClass(row) || 'bg-white'"
                      >
                        <button
                          type="button"
                          class="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
                          title="删除此行"
                          @click="removeRow(row)"
                        >
                          <TrashIcon class="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                    <tr v-if="rows.length === 0">
                      <td colspan="8" class="px-4 py-12 text-center text-sm text-gray-400">
                        没有可确认的数据行，请返回重新上传或补充新增
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              <span>已选 <strong class="font-mono text-gray-800">{{ selectedCount }}</strong> 行</span>
              <span v-if="blockingSelected > 0" class="text-amber-600">
                其中 {{ blockingSelected }} 行需处理后才能导入
              </span>
              <span v-else-if="selectedCount > 0" class="text-emerald-600">所选行校验通过</span>
              <span v-if="rechecking" class="text-xs text-gray-400">正在检查账号/邮箱占用…</span>
              <span class="text-gray-400">账号已存在 / 邮箱被占用的行不会导入，提交时后端还会再校验一次</span>
            </div>
          </section>

          <!-- Step 3: result -->
          <section v-else class="space-y-5">
            <div class="grid gap-3 sm:grid-cols-4">
              <div class="rounded-xl border border-emerald-100 bg-emerald-50/70 p-4">
                <p class="text-xs font-medium text-emerald-700">成功创建</p>
                <p class="mt-1 text-2xl font-bold text-emerald-700">{{ created.length }}</p>
              </div>
              <div
                class="rounded-xl border p-4"
                :class="failed.length ? 'border-red-100 bg-red-50/70' : 'border-gray-100 bg-gray-50'"
              >
                <p class="text-xs font-medium" :class="failed.length ? 'text-red-700' : 'text-gray-500'">创建失败</p>
                <p class="mt-1 text-2xl font-bold" :class="failed.length ? 'text-red-700' : 'text-gray-400'">
                  {{ failed.length }}
                </p>
              </div>
              <div class="rounded-xl border border-amber-100 bg-amber-50/70 p-4">
                <p class="text-xs font-medium text-amber-700">密码仅本次显示</p>
                <p class="mt-1 text-xs leading-relaxed text-amber-700/80">
                  请立即复制或下载；离开本页后无法再次查看明文密码。
                </p>
              </div>
              <div
                class="rounded-xl border p-4"
                :class="!sendEmailRequested
                  ? 'border-gray-100 bg-gray-50'
                  : emailFailedCount
                    ? 'border-red-100 bg-red-50/70'
                    : 'border-sky-100 bg-sky-50/70'"
              >
                <p
                  class="text-xs font-medium"
                  :class="!sendEmailRequested
                    ? 'text-gray-500'
                    : emailFailedCount ? 'text-red-700' : 'text-sky-700'"
                >
                  密码邮件
                </p>
                <template v-if="sendEmailRequested">
                  <p
                    class="mt-1 text-2xl font-bold"
                    :class="emailFailedCount ? 'text-red-700' : 'text-sky-700'"
                  >
                    {{ emailSentCount }}<span class="ml-1 text-sm font-normal">成功</span>
                  </p>
                  <p v-if="emailFailedCount" class="text-xs text-red-600">失败 {{ emailFailedCount }}</p>
                  <p v-else-if="created.length" class="text-xs text-sky-600">已全部提交</p>
                  <p v-else class="text-xs text-gray-500">无账号可发送</p>
                </template>
                <p v-else class="mt-1 text-sm font-semibold text-gray-500">未开启</p>
              </div>
            </div>

            <div
              v-if="sendEmailRequested && emailFailedCount > 0"
              class="rounded-xl border border-amber-200 bg-amber-50/80 px-4 py-3 text-xs leading-relaxed text-amber-800"
            >
              <p class="font-semibold">有 {{ emailFailedCount }} 个账号的密码邮件未发出</p>
              <p>账号已创建成功。请复制或下载密码后通过其他安全渠道发送；失败原因如下：</p>
              <ul class="mt-2 space-y-1">
                <li
                  v-for="group in emailFailureGroups"
                  :key="group.reason"
                  class="break-words rounded-lg bg-amber-100/70 px-2.5 py-1.5"
                >
                  <span class="font-medium">{{ group.reason }}</span>
                  <span class="ml-1 text-amber-700/80">（{{ group.count }} 个）</span>
                </li>
              </ul>
            </div>
            <div
              v-else-if="sendEmailRequested"
              class="rounded-xl border border-sky-100 bg-sky-50/70 px-4 py-3 text-xs leading-relaxed text-sky-800"
            >
              <template v-if="created.length">
                <p class="font-semibold">密码邮件已提交给邮件服务器（{{ emailSentCount }} 封）</p>
                <p>
                  提交成功不等于一定进入收件箱；如果用户没收到，请先检查垃圾邮件/收件规则，
                  也可到「系统配置 → 邮件服务」用「测试发送」确认投递链路。
                </p>
              </template>
              <template v-else>
                <p class="font-semibold">没有创建成功的账号，未发送密码邮件</p>
                <p>请查看下方失败明细，修正后重新导入。</p>
              </template>
            </div>
            <div
              v-else
              class="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs leading-relaxed text-gray-600"
            >
              <p class="font-semibold">本次未开启密码邮件</p>
              <p>账号密码只在本页显示，请复制或下载后通过安全渠道分发。</p>
            </div>

            <div v-if="created.length" class="space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h3 class="text-sm font-bold text-gray-800">账号与初始密码</h3>
                <div class="flex flex-wrap gap-2">
                  <button
                    type="button"
                    class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                    @click="copyAll"
                  >
                    <ClipboardDocumentListIcon class="h-3.5 w-3.5" />
                    复制全部
                  </button>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                    @click="downloadCsv"
                  >
                    <ArrowDownTrayIcon class="h-3.5 w-3.5" />
                    下载 CSV
                  </button>
                </div>
              </div>
              <div class="overflow-hidden rounded-xl border border-gray-200">
                <div class="max-h-[38vh] overflow-auto">
                  <table class="w-full min-w-[720px] border-collapse text-left text-sm">
                    <thead class="sticky top-0 bg-gray-50 text-xs text-gray-500">
                      <tr>
                        <th class="px-3 py-2.5 font-medium">账号</th>
                        <th class="px-3 py-2.5 font-medium">姓名</th>
                        <th class="px-3 py-2.5 font-medium">邮箱</th>
                        <th class="px-3 py-2.5 font-medium">角色</th>
                        <th class="px-3 py-2.5 font-medium">初始密码</th>
                        <th v-if="sendEmailRequested" class="px-3 py-2.5 font-medium">密码邮件</th>
                        <th class="w-20 px-3 py-2.5"></th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">
                      <tr v-for="row in created" :key="row.user_id">
                        <td class="px-3 py-2.5 font-medium text-gray-900">{{ row.user_name }}</td>
                        <td class="px-3 py-2.5 text-xs text-gray-600">{{ row.real_name || "—" }}</td>
                        <td class="px-3 py-2.5 text-xs text-gray-500">{{ row.email || "—" }}</td>
                        <td class="px-3 py-2.5 text-xs text-gray-600">{{ row.role_name || "—" }}</td>
                        <td class="px-3 py-2.5">
                          <code class="rounded bg-gray-100 px-2 py-1 font-mono text-xs text-gray-900">{{ row.password }}</code>
                        </td>
                        <td v-if="sendEmailRequested" class="px-3 py-2.5 align-top text-xs">
                          <span v-if="row.email_sent" class="font-medium text-emerald-600">已发送</span>
                          <div v-else-if="row.email_error" class="max-w-[220px]">
                            <span class="font-medium text-red-600">发送失败</span>
                            <span class="mt-0.5 block break-words text-[11px] leading-snug text-red-500">
                              {{ row.email_error }}
                            </span>
                          </div>
                          <span v-else class="text-amber-600">状态未知</span>
                        </td>
                        <td class="px-3 py-2.5 text-right">
                          <button
                            type="button"
                            class="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-primary"
                            title="复制密码"
                            @click="copyOne(row)"
                          >
                            <ClipboardDocumentIcon class="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div v-if="failed.length" class="space-y-2">
              <h3 class="text-sm font-bold text-gray-800">失败明细（以下账号未创建）</h3>
              <div class="overflow-hidden rounded-xl border border-red-100">
                <table class="w-full border-collapse text-left text-sm">
                  <thead class="bg-red-50/60 text-xs text-red-700">
                    <tr>
                      <th class="px-3 py-2.5 font-medium">账号</th>
                      <th class="px-3 py-2.5 font-medium">邮箱</th>
                      <th class="px-3 py-2.5 font-medium">失败原因</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-red-50">
                    <tr v-for="(row, index) in failed" :key="`${row.user_name}-${index}`">
                      <td class="px-3 py-2.5 font-medium text-gray-800">{{ row.user_name || "—" }}</td>
                      <td class="px-3 py-2.5 text-xs text-gray-500">{{ row.email || "—" }}</td>
                      <td class="px-3 py-2.5 text-xs text-red-600">{{ row.error }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>

        <!-- Footer -->
        <div class="flex shrink-0 flex-col gap-2 border-t border-gray-100 bg-gray-50/60 px-6 py-3.5 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-xs text-gray-400">
            <template v-if="step === 'confirm'">导入后账号立即启用，初始密码需由用户首次登录后自行修改。</template>
            <template v-else-if="step === 'result'">建议将账号密码通过安全渠道单独发送给对应用户。</template>
            <template v-else>支持 .xlsx；导入前会先做账号、姓名、邮箱与角色校验。</template>
          </p>
          <div class="flex items-center justify-end gap-2">
            <button
              v-if="step === 'confirm'"
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-3.5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              :disabled="submitting"
              @click="restartUpload"
            >
              返回上传
            </button>
            <button
              v-if="step !== 'result'"
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-3.5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              :disabled="submitting || uploading"
              @click="requestClose"
            >
              取消
            </button>
            <button
              v-if="step === 'confirm'"
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!canSubmit"
              @click="submitImport"
            >
              <ArrowUpTrayIcon v-if="!submitting" class="h-4 w-4" />
              <span
                v-else
                class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
              ></span>
              {{ submitting ? "正在创建..." : `确认导入 ${selectedCount} 个账号` }}
            </button>
            <button
              v-if="step === 'result'"
              type="button"
              class="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-dark"
              @click="requestClose"
            >
              完成
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  ArrowDownTrayIcon,
  ArrowPathIcon,
  ArrowUpTrayIcon,
  CheckCircleIcon,
  CheckIcon,
  ChevronRightIcon,
  ClipboardDocumentIcon,
  ClipboardDocumentListIcon,
  DocumentArrowDownIcon,
  DocumentCheckIcon,
  ExclamationTriangleIcon,
  PlusIcon,
  TrashIcon,
  XMarkIcon,
} from "@heroicons/vue/24/outline";
import axios from "../../utils/axios";
import Switch from "../Switch.vue";
import { copyToClipboard } from "../../utils/clipboard";
import { extractApiErrorDetail } from "../../utils/apiErrorDetail";
import { useToast } from "../../composables/useToast";

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "imported"): void;
}>();

const { showToast } = useToast();

const MAX_FILE_BYTES = 2 * 1024 * 1024;

type Step = "upload" | "confirm" | "result";
interface RoleOption {
  id: number;
  code: string;
  name: string;
  description?: string | null;
}

interface PreviewItem {
  row: number;
  user_name: string;
  real_name: string;
  email: string;
  role_input: string;
  role_id: number | null;
  role_name: string | null;
  role_from_default: boolean;
  existed: boolean;
  email_taken: boolean;
  issues: string[];
}

interface ConfirmRow {
  uid: number;
  rowNumber: number;
  user_name: string;
  real_name: string;
  email: string;
  role_id: number | null;
  role_input: string;
  role_from_default: boolean;
  existed: boolean;
  email_taken: boolean;
  selected: boolean;
  originalName: string;
  originalEmail: string;
}

interface CheckItem {
  user_name: string;
  existed: boolean;
  email_taken: boolean;
}

interface CreatedRow {
  user_id: number;
  user_name: string;
  real_name: string | null;
  email: string | null;
  role_name: string | null;
  password: string;
  email_sent?: boolean;
  email_error?: string | null;
}

interface FailedRow {
  user_name: string;
  email: string;
  error: string;
}

const steps: { key: Step; label: string }[] = [
  { key: "upload", label: "上传文件" },
  { key: "confirm", label: "确认数据" },
  { key: "result", label: "账号密码" },
];

const step = ref<Step>("upload");
const stepIndex = computed(() => steps.findIndex((item) => item.key === step.value));

const uploading = ref(false);
const submitting = ref(false);
const downloadingTemplate = ref(false);
const dragging = ref(false);
const fileName = ref("");
const fileInput = ref<HTMLInputElement | null>(null);

const roles = ref<RoleOption[]>([]);
const rows = ref<ConfirmRow[]>([]);
const created = ref<CreatedRow[]>([]);
const failed = ref<FailedRow[]>([]);

// 确认页两个选项：随机/固定密码、是否把密码发到用户邮箱
type PasswordMode = "random" | "fixed";
const passwordMode = ref<PasswordMode>("random");
const fixedPassword = ref("");
const showFixedPassword = ref(false);
const sendPasswordEmail = ref(false);
// 预览接口会回报全局邮件服务是否可用；未启用时开关必须禁用并说明原因，
// 不能等提交后才在结果页告诉管理员「一封都没发出去」。
const emailServiceAvailable = ref(false);
// 记录本次提交时的开关状态：结果页据此决定是否展示「密码邮件」列与汇总
const sendEmailRequested = ref(false);

let uidSeed = 0;
const nextUid = () => {
  uidSeed += 1;
  return uidSeed;
};

// 上传请求可能比用户点「关闭」更晚返回：用序号丢弃过期响应，避免关闭后又被旧文件
// 顶到确认页（重新打开时看到上一次的文件）。
let uploadSeq = 0;

// 确认页改账号/邮箱后的实时查重：防抖 450ms，序号丢弃过期响应
let recheckTimer: ReturnType<typeof setTimeout> | null = null;
let recheckSeq = 0;
const rechecking = ref(false);

const uploadTips = [
  {
    title: "角色怎么填",
    body: "填写业务角色编码或名称（如 default、分析师）；留空则使用 default，匹配不上的行可在确认页重新选择。",
  },
  {
    title: "重复数据怎么处理",
    body: "账号已存在或邮箱被占用时会在确认页标红并默认不勾选；文件内重复的账号也会被标出。",
  },
  {
    title: "密码与账号身份",
    body: "初始密码在确认页选择随机或固定，只在结果页显示一次；导入账号均为普通用户，如需管理员请在导入后单独调整。",
  },
];

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const emailLooksValid = (email: string) => emailPattern.test(email.trim());

const duplicateNames = computed(() => {
  const counts = new Map<string, number>();
  for (const row of rows.value) {
    const key = row.user_name.trim().toLowerCase();
    if (!key) continue;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([key]) => key));
});

const selectedRows = computed(() => rows.value.filter((row) => row.selected));
const selectedCount = computed(() => selectedRows.value.length);
const allSelected = computed(
  () => rows.value.length > 0 && rows.value.every((row) => row.selected),
);
const someSelected = computed(
  () => rows.value.some((row) => row.selected) && !allSelected.value,
);
const blockingSelected = computed(
  () => selectedRows.value.filter((row) => rowIssues(row).length > 0).length,
);

// 重复数据的汇总口径：整份文件里有多少行已存在 / 邮箱被占用 / 角色未匹配，
// 以及其中有多少仍被勾选（勾选状态下提交按钮会被禁用）。
const existedRows = computed(() => rows.value.filter((row) => row.existed));
const emailTakenRows = computed(() => rows.value.filter((row) => row.email_taken));
const roleMissingRows = computed(() => rows.value.filter((row) => row.role_id === null));
const existedSelected = computed(
  () => existedRows.value.filter((row) => row.selected).length,
);

/** 固定密码的客户端校验，口径与后端 validate_fixed_password 对齐。 */
const fixedPasswordIssue = computed(() => {
  if (passwordMode.value !== "fixed") return "";
  const password = fixedPassword.value;
  if (!password) return "请输入固定密码";
  if (password.length < 8 || password.length > 32) return "密码长度必须为 8 到 32 个字符";
  if (new TextEncoder().encode(password).length > 72) return "密码字节长度超过系统上限（最大 72 字节）";
  if (/\s/.test(password)) return "密码不能包含空格";
  const categories = [/[A-Z]/, /[a-z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((pattern) =>
    pattern.test(password),
  ).length;
  if (categories < 3) {
    return "需至少包含大写字母、小写字母、数字、特殊符号中的 3 种";
  }
  const hit = selectedRows.value.find((row) => {
    const name = row.user_name.trim();
    return name.length >= 3 && password.toLowerCase().includes(name.toLowerCase());
  });
  if (hit) return `固定密码不能包含账号名「${hit.user_name.trim()}」`;
  return "";
});

const canSubmit = computed(
  () =>
    selectedCount.value > 0 &&
    blockingSelected.value === 0 &&
    !fixedPasswordIssue.value &&
    !submitting.value,
);

const emailSentCount = computed(
  () => created.value.filter((row) => row.email_sent).length,
);
const emailFailedCount = computed(
  () => created.value.filter((row) => sendEmailRequested.value && !row.email_sent).length,
);
const emailToastNote = computed(() => {
  if (!sendEmailRequested.value || created.value.length === 0) return "";
  return emailFailedCount.value
    ? `；密码邮件失败 ${emailFailedCount.value} 封`
    : `；密码邮件已提交 ${emailSentCount.value} 封`;
});
const emailFailureGroups = computed(() => {
  const counts = new Map<string, number>();
  for (const row of created.value) {
    if (!sendEmailRequested.value || row.email_sent) continue;
    const reason = (row.email_error || "邮件发送失败").trim();
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()].map(([reason, count]) => ({ reason, count }));
});

function isHardBlocking(row: ConfirmRow): boolean {
  return row.existed || row.email_taken || missingName(row) || !emailLooksValid(row.email);
}

function missingName(row: ConfirmRow): boolean {
  return !row.user_name.trim();
}

function rowIssues(row: ConfirmRow): string[] {
  const issues: string[] = [];
  if (missingName(row)) {
    issues.push("账号不能为空");
  } else if (row.user_name.trim().length > 50) {
    issues.push("账号长度不能超过 50 个字符");
  } else if (/\s/.test(row.user_name.trim())) {
    issues.push("账号不能包含空格");
  } else if (duplicateNames.value.has(row.user_name.trim().toLowerCase())) {
    issues.push("文件内账号重复，请保留一行");
  }

  if (!row.email.trim()) {
    issues.push("邮箱不能为空");
  } else if (!emailLooksValid(row.email)) {
    issues.push("邮箱格式不正确");
  }

  if (row.role_id === null) {
    issues.push(
      row.role_input
        ? `角色「${row.role_input}」未匹配到内置角色，请选择`
        : "请选择业务角色",
    );
  }

  if (row.existed) {
    issues.push("账号已存在，不会导入；请取消勾选或修改账号名");
  }
  if (row.email_taken) {
    issues.push("邮箱已被其他账号占用，不会导入；请更换邮箱或取消勾选");
  }
  return issues;
}

function rowClass(row: ConfirmRow): string {
  // 用不透明底色：右侧删除列是 sticky 的，半透明会把横向滚过的内容透出来
  if (row.existed || row.email_taken) return "bg-red-50";
  return rowIssues(row).length ? "bg-amber-50" : "";
}

function onNameEdited(row: ConfirmRow): void {
  if (row.user_name.trim().toLowerCase() !== row.originalName.trim().toLowerCase()) {
    row.existed = false;
  }
  scheduleRecheck();
}

function onEmailEdited(row: ConfirmRow): void {
  if (row.email.trim().toLowerCase() !== row.originalEmail.trim().toLowerCase()) {
    row.email_taken = false;
  }
  scheduleRecheck();
}

function cancelRecheck(): void {
  recheckSeq += 1;
  rechecking.value = false;
  if (recheckTimer) {
    clearTimeout(recheckTimer);
    recheckTimer = null;
  }
}

function scheduleRecheck(): void {
  const seq = ++recheckSeq;
  if (recheckTimer) clearTimeout(recheckTimer);
  recheckTimer = setTimeout(() => {
    recheckTimer = null;
    void recheckRows(seq);
  }, 450);
}

function applyRecheck(
  snapshot: { user_name: string; email: string }[],
  items: CheckItem[],
): void {
  rows.value.forEach((row, index) => {
    const expected = snapshot[index];
    const item = items[index];
    if (!expected || !item) return;
    // 请求期间用户可能又改了值：只把结果应用到仍然匹配快照的行
    if (
      row.user_name.trim() !== expected.user_name ||
      row.email.trim() !== expected.email
    ) {
      return;
    }
    row.existed = Boolean(item.existed);
    row.email_taken = Boolean(item.email_taken);
  });
}

async function recheckRows(seq: number): Promise<void> {
  if (seq !== recheckSeq || !props.visible || step.value !== "confirm") return;
  const snapshot = rows.value.map((row) => ({
    user_name: row.user_name.trim(),
    email: row.email.trim(),
  }));
  if (snapshot.length === 0) return;

  rechecking.value = true;
  try {
    const response = await axios.post("/api/portal/management/users/import/check", {
      rows: snapshot,
    });
    if (seq !== recheckSeq || !props.visible) return;
    applyRecheck(
      snapshot,
      Array.isArray(response.data?.items) ? response.data.items : [],
    );
  } catch {
    // 实时查重失败不打断输入；提交前还会再查一次，最终以 confirm 为准
  } finally {
    if (seq === recheckSeq) rechecking.value = false;
  }
}

async function recheckSelectedNow(): Promise<number> {
  const snapshot = rows.value.map((row) => ({
    user_name: row.user_name.trim(),
    email: row.email.trim(),
  }));
  if (snapshot.length === 0) return 0;

  rechecking.value = true;
  try {
    const response = await axios.post("/api/portal/management/users/import/check", {
      rows: snapshot,
    });
    applyRecheck(
      snapshot,
      Array.isArray(response.data?.items) ? response.data.items : [],
    );
    return rows.value.filter(
      (row) => row.selected && (row.existed || row.email_taken),
    ).length;
  } finally {
    rechecking.value = false;
  }
}

function resetAll(): void {
  uploadSeq += 1;
  cancelRecheck();
  step.value = "upload";
  uploading.value = false;
  submitting.value = false;
  dragging.value = false;
  fileName.value = "";
  rows.value = [];
  created.value = [];
  failed.value = [];
  passwordMode.value = "random";
  fixedPassword.value = "";
  showFixedPassword.value = false;
  sendPasswordEmail.value = false;
  emailServiceAvailable.value = false;
  sendEmailRequested.value = false;
  if (fileInput.value) fileInput.value.value = "";
}

function onOverlayClick(): void {
  // 结果页的明文密码只出现一次：点遮罩误关会让管理员直接丢掉刚生成的密码，
  // 这里只允许用「完成」或右上角关闭按钮显式离开。
  if (step.value === "result" && created.value.length > 0) return;
  requestClose();
}

function requestClose(): void {
  uploadSeq += 1;
  if (submitting.value) {
    showToast("正在创建账号，请等待完成后再关闭", "warning");
    return;
  }
  emit("close");
}

function restartUpload(): void {
  resetAll();
}

function openFilePicker(): void {
  if (uploading.value) return;
  fileInput.value?.click();
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) void uploadFile(file);
  input.value = "";
}

function onDrop(event: DragEvent): void {
  dragging.value = false;
  if (uploading.value) return;
  const file = event.dataTransfer?.files?.[0];
  if (file) void uploadFile(file);
}

async function uploadFile(file: File): Promise<void> {
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    showToast("仅支持 .xlsx 格式的 Excel 文件", "warning");
    return;
  }
  if (file.size > MAX_FILE_BYTES) {
    showToast("文件超过 2MB，请拆分后分批导入", "warning");
    return;
  }

  const seq = ++uploadSeq;
  uploading.value = true;
  try {
    const formData = new FormData();
    formData.append("file", file);
    const response = await axios.post("/api/portal/management/users/import/preview", formData);
    if (seq !== uploadSeq || !props.visible) return;
    const data = response.data ?? {};
    roles.value = Array.isArray(data.roles) ? data.roles : [];
    fileName.value = String(data.filename || file.name);
    rows.value = (Array.isArray(data.items) ? data.items : []).map(toConfirmRow);
    emailServiceAvailable.value = data.email_service_available === true;
    if (!emailServiceAvailable.value) sendPasswordEmail.value = false;
    created.value = [];
    failed.value = [];
    step.value = "confirm";

    const importable = rows.value.filter((row) => !rowIssues(row).length && row.selected).length;
    if (rows.value.length === 0) {
      showToast("文件里没有可导入的数据行", "warning");
    } else {
      showToast(`解析完成：共 ${rows.value.length} 行，其中 ${importable} 行可直接导入`, "success");
    }
  } catch (error: any) {
    if (seq !== uploadSeq || !props.visible) return;
    showToast(
      extractApiErrorDetail(error?.response?.data, "解析 Excel 失败，请确认文件格式后重试"),
      "error",
    );
  } finally {
    if (seq === uploadSeq) uploading.value = false;
  }
}

function toConfirmRow(item: PreviewItem): ConfirmRow {
  const name = String(item.user_name ?? "");
  const email = String(item.email ?? "");
  const issues = Array.isArray(item.issues) ? item.issues : [];
  return {
    uid: nextUid(),
    rowNumber: Number(item.row ?? 0),
    user_name: name,
    real_name: String(item.real_name ?? ""),
    email,
    role_id: item.role_id ?? null,
    role_input: String(item.role_input ?? ""),
    role_from_default: Boolean(item.role_from_default),
    existed: Boolean(item.existed),
    email_taken: Boolean(item.email_taken),
    selected: issues.length === 0 && item.role_id !== null && name.trim() !== "",
    originalName: name,
    originalEmail: email,
  };
}

function addRow(): void {
  const defaultRole = roles.value.find((role) => role.code.toLowerCase() === "default");
  rows.value.push({
    uid: nextUid(),
    rowNumber: 0,
    user_name: "",
    real_name: "",
    email: "",
    role_id: defaultRole?.id ?? null,
    role_input: "",
    role_from_default: Boolean(defaultRole),
    existed: false,
    email_taken: false,
    selected: true,
    originalName: "",
    originalEmail: "",
  });
}

function toggleAll(): void {
  const next = !allSelected.value;
  rows.value.forEach((row) => {
    row.selected = next;
  });
}

function removeRow(row: ConfirmRow): void {
  rows.value = rows.value.filter((item) => item.uid !== row.uid);
}

function removeSelected(): void {
  const count = selectedCount.value;
  if (count === 0) return;
  rows.value = rows.value.filter((row) => !row.selected);
  showToast(`已删除 ${count} 行`, "success");
}

async function submitImport(): Promise<void> {
  if (selectedCount.value === 0) {
    showToast("请至少勾选一行", "warning");
    return;
  }
  if (blockingSelected.value > 0) {
    showToast(`还有 ${blockingSelected.value} 行未通过校验，请处理后再导入`, "warning");
    return;
  }

  cancelRecheck();
  submitting.value = true;
  try {
    try {
      const blocked = await recheckSelectedNow();
      if (blocked > 0) {
        showToast("账号或邮箱已被占用，请修改标红的行后再导入", "warning");
        return;
      }
    } catch {
      showToast("实时查重失败，提交时后端会再次校验", "warning");
    }

    sendEmailRequested.value = sendPasswordEmail.value;
    const payload = {
      rows: selectedRows.value.map((row) => ({
        user_name: row.user_name.trim(),
        real_name: row.real_name.trim(),
        email: row.email.trim(),
        role_id: row.role_id,
      })),
      password_mode: passwordMode.value,
      fixed_password: passwordMode.value === "fixed" ? fixedPassword.value : null,
      send_password_email: sendPasswordEmail.value,
    };
    const response = await axios.post("/api/portal/management/users/import/confirm", payload);
    const data = response.data ?? {};
    created.value = Array.isArray(data.created) ? data.created : [];
    failed.value = Array.isArray(data.failed) ? data.failed : [];
    step.value = "result";
    emit("imported");

    if (created.value.length > 0 && failed.value.length === 0) {
      showToast(`成功创建 ${created.value.length} 个账号${emailToastNote.value}`, "success");
    } else if (created.value.length > 0) {
      showToast(
        `成功 ${created.value.length} 个，失败 ${failed.value.length} 个${emailToastNote.value}，请查看失败明细`,
        "warning",
      );
    } else {
      showToast("没有账号创建成功，请查看失败明细", "error");
    }
  } catch (error: any) {
    showToast(
      extractApiErrorDetail(error?.response?.data, "批量创建失败，请稍后重试"),
      "error",
    );
  } finally {
    submitting.value = false;
  }
}

async function downloadTemplate(): Promise<void> {
  downloadingTemplate.value = true;
  try {
    const response = await axios.get("/api/portal/management/users/import/template", {
      responseType: "blob",
    });
    triggerDownload(response.data as Blob, "用户导入模板.xlsx");
  } catch (error: any) {
    showToast("下载模板失败，请稍后重试", "error");
  } finally {
    downloadingTemplate.value = false;
  }
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

async function copyOne(row: CreatedRow): Promise<void> {
  const ok = await copyToClipboard(row.password);
  showToast(ok ? `已复制 ${row.user_name} 的初始密码` : "复制失败，请手动选择", ok ? "success" : "error");
}

async function copyAll(): Promise<void> {
  if (created.value.length === 0) return;
  const header = ["账号", "姓名", "邮箱", "角色", "初始密码"].join("\t");
  const lines = created.value.map((row) =>
    [
      row.user_name,
      row.real_name || "",
      row.email || "",
      row.role_name || "",
      row.password,
    ].join("\t"),
  );
  const ok = await copyToClipboard([header, ...lines].join("\n"));
  showToast(
    ok ? `已复制 ${created.value.length} 个账号的密码` : "复制失败，请改用下载 CSV",
    ok ? "success" : "error",
  );
}

function csvCell(value: string): string {
  if (/[",\r\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function downloadCsv(): void {
  if (created.value.length === 0) return;
  const header = ["账号", "姓名", "邮箱", "角色", "初始密码"];
  const lines = [header.join(",")];
  for (const row of created.value) {
    lines.push(
      [
        row.user_name,
        row.real_name || "",
        row.email || "",
        row.role_name || "",
        row.password,
      ]
        .map((cell) => csvCell(String(cell)))
        .join(","),
    );
  }
  const stamp = new Date()
    .toLocaleString("zh-CN", { hour12: false })
    .replace(/[/:\s-]/g, "")
    .slice(0, 14);
  // 带 BOM，避免 Excel 打开中文 CSV 出现乱码
  const blob = new Blob([`\uFEFF${lines.join("\r\n")}`], {
    type: "text/csv;charset=utf-8;",
  });
  triggerDownload(blob, `导入账号密码_${stamp}.csv`);
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) resetAll();
  },
);
</script>
