import { onBeforeUnmount, ref, shallowRef } from 'vue'
import {
  createWorkspaceUploadQueue,
  type WorkspaceUploadQueue,
  type WorkspaceUploadSnapshot,
} from '@/utils/workspaceUploadQueue'
import { uploadToWorkspaceDir } from '@/utils/workspaceFilePreview'

const EMPTY_SNAPSHOT: WorkspaceUploadSnapshot = {
  tasks: [],
  aggregate: {
    total: 0,
    done: 0,
    failed: 0,
    canceled: 0,
    totalBytes: 0,
    loadedBytes: 0,
    active: false,
  },
}

/**
 * 把零依赖的上传队列引擎接到 Vue 生命周期上。
 *
 * 引擎本身不依赖 Vue（因此可被 node 直接加载做行为测试），这里只负责两件事：
 * 持有实例、把引擎快照同步为响应式 ref；同时确保组件卸载时销毁实例，
 * 否则进度定时器与在途请求会泄漏。
 */
export function useWorkspaceUploadQueue(options?: { maxBytes?: number }) {
  const snapshot = ref<WorkspaceUploadSnapshot>(EMPTY_SNAPSHOT)
  const queue = shallowRef<WorkspaceUploadQueue | null>(null)

  const ensure = (): WorkspaceUploadQueue => {
    if (queue.value) return queue.value
    const instance = createWorkspaceUploadQueue({
      maxBytes: options?.maxBytes,
      upload: (file, uploadOptions) =>
        uploadToWorkspaceDir(uploadOptions.parentPath, file as File, {
          onProgress: uploadOptions.onProgress,
          onCommit: uploadOptions.onCommit,
          signal: uploadOptions.signal,
        }),
    })
    instance.subscribe(() => {
      snapshot.value = instance.snapshot()
    })
    queue.value = instance
    return instance
  }

  onBeforeUnmount(() => {
    queue.value?.destroy()
    queue.value = null
  })

  return {
    snapshot,
    enqueue: (files: File[], parentPath: string) => ensure().enqueue(files, parentPath),
    cancel: (taskId: string) => queue.value?.cancel(taskId),
    retry: (taskId: string) => queue.value?.retry(taskId),
    clearFinished: () => queue.value?.clearFinished(),
  }
}
