<template>
  <!--
    用户模式「定时任务」入口。

    复用任务调度台，但只列当前用户自己的任务（后端 `/api/v1/tasks/` 对非管理员
    本来就按 user_id 过滤），因此不需要 menu:task_center —— 这是普通用户也有
    的能力。要管全平台任务仍然走 /dashboard/tasks（任务调度台）。
    移动端 Dashboard 主容器为 p-0，这里在 <640px 补齐内边距避免内容贴边；
    sm 以上由 Dashboard 外层提供留白，此处归零不叠加。
  -->
  <div class="px-4 py-4 sm:px-0 sm:py-0">
    <TaskCenter personal-only />
  </div>
</template>

<script setup lang="ts">
import TaskCenter from "./TaskCenter.vue"
</script>
