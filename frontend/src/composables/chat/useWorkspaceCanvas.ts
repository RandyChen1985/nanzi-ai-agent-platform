import { onUnmounted, ref, watch } from "vue";
import axios from "@/utils/axios";
import {
  openWorkspaceFileInCanvas,
  isSameWorkspacePreviewPath,
  shouldAttachWorkspaceSourcePath,
} from "@/utils/workspaceFilePreview";

export type WorkspaceCanvasType = "html" | "code" | "mermaid" | "pdf" | "csv" | "image" | "compare";

export interface WorkspaceCanvasPayload {
  type: WorkspaceCanvasType;
  title: string;
  content: string;
  sourcePath?: string;
  compareContent?: string;
  compareTitle?: string;
}

export interface UseWorkspaceCanvasOptions {
  getConversationId: () => string;
  resolveFileUrl: (url: string) => string;
  showToast: (message: string, type?: "success" | "error" | "info") => void;
  normalizeDirectPayloadTitle?: boolean;
}

export function useWorkspaceCanvas(options: UseWorkspaceCanvasOptions) {
  const canvasVisible = ref(false);
  const canvasFromWorkspace = ref(false);
  const workspaceCanvasPreviewPath = ref<string | null>(null);
  const canvasData = ref<WorkspaceCanvasPayload | null>(null);
  const activeBlobUrl = ref("");

  const revokeActiveBlobUrl = () => {
    if (!activeBlobUrl.value) return;
    try {
      URL.revokeObjectURL(activeBlobUrl.value);
    } catch (error) {
      console.warn("Revoke blob URL error:", error);
    }
    activeBlobUrl.value = "";
  };

  const closeCanvas = () => {
    canvasVisible.value = false;
    revokeActiveBlobUrl();
  };

  watch(canvasVisible, (visible) => {
    if (visible) return;
    canvasFromWorkspace.value = false;
    workspaceCanvasPreviewPath.value = null;
    revokeActiveBlobUrl();
  });

  onUnmounted(revokeActiveBlobUrl);

  const handleWorkspaceFilePreview = async (payload: { path: string; name: string }) => {
    if (
      canvasVisible.value &&
      canvasFromWorkspace.value &&
      isSameWorkspacePreviewPath(workspaceCanvasPreviewPath.value, payload.path)
    ) {
      closeCanvas();
      workspaceCanvasPreviewPath.value = null;
      return;
    }
    canvasFromWorkspace.value = true;
    await openWorkspaceFileInCanvas({
      path: payload.path,
      name: payload.name,
      conversationId: options.getConversationId(),
      showToast: options.showToast,
      activeBlobUrlRef: activeBlobUrl,
      onOpen: (data) => {
        workspaceCanvasPreviewPath.value = payload.path;
        canvasData.value = data as WorkspaceCanvasPayload;
        canvasVisible.value = true;
      },
    });
  };

  const handleOpenCanvas = async (payload: WorkspaceCanvasPayload) => {
    revokeActiveBlobUrl();
    if (payload.type === "compare") {
      try {
        const url = new URL(payload.content.replace("canvas://", "http://localhost/"));
        const leftPath = url.searchParams.get("left") || "";
        const rightPath = url.searchParams.get("right") || "";
        const [leftContent, rightContent] = await Promise.all([
          axios.get(options.resolveFileUrl(leftPath)).then((response) => response.data),
          axios.get(options.resolveFileUrl(rightPath)).then((response) => response.data),
        ]);
        canvasData.value = {
          type: "compare",
          title: payload.title || "数据对比",
          content: leftContent,
          compareContent: rightContent,
          compareTitle: rightPath.split("/").pop() || "对比文件",
        };
        canvasVisible.value = true;
      } catch (error: any) {
        console.error("加载对比文件失败:", error);
        let message = "加载对比文件失败";
        if (error.response?.data?.detail) message = error.response.data.detail;
        else if (error.response?.status === 404) message = "对比的文件不存在，可能已被删除或尚未生成。";
        else if (error.response?.status === 403) message = "权限拦截：无权访问该对比文件。";
        else message = error.message || String(error);
        options.showToast(message, "error");
      }
      return;
    }

    if (payload.content.startsWith("canvas://file")) {
      try {
        const url = new URL(payload.content.replace("canvas://", "http://localhost/"));
        let filePath = url.searchParams.get("path") || "";
        if (filePath.includes("###HTML_TAG_PLACEHOLDER_")) {
          filePath = filePath.replace(/###HTML_TAG_PLACEHOLDER_\d+###/g, "").trim();
        }
        const resolvedUrl = options.resolveFileUrl(filePath);
        const normalizedPath = ((filePath.toLowerCase().split("?")[0] ?? "").split("#")[0] ?? "");
        const isOfficeFile = [".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx", ".ppt"].some((extension) => normalizedPath.endsWith(extension));
        if (isOfficeFile) {
          const response = await axios.get(resolvedUrl, { responseType: "blob" });
          const filename = filePath.split("/").pop() || "download";
          const blobUrl = URL.createObjectURL(response.data);
          const link = document.createElement("a");
          link.href = blobUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(blobUrl);
          options.showToast(`已开始下载 ${filename}`, "success");
          return;
        }
        if (payload.type === "pdf" || payload.type === "image" || payload.type === "csv") {
          const response = await axios.get(resolvedUrl, { responseType: "blob" });
          const blobUrl = URL.createObjectURL(response.data);
          activeBlobUrl.value = blobUrl;
          canvasData.value = { type: payload.type, title: payload.title || filePath.split("/").pop() || "文件预览", content: blobUrl };
        } else {
          const content = await axios.get(resolvedUrl).then((response) => response.data);
          const filename = payload.title || filePath.split("/").pop() || "文件预览";
          canvasData.value = {
            type: payload.type,
            title: filename,
            content,
            sourcePath: shouldAttachWorkspaceSourcePath(filePath, filename) ? filePath : undefined,
          };
        }
        canvasVisible.value = true;
      } catch (error: any) {
        console.error("加载本地文件失败:", error);
        let message = "加载本地文件失败";
        if (error.response?.data?.detail) message = error.response.data.detail;
        else if (error.response?.status === 404) message = "预览的文件不存在，请确认路径是否正确。";
        else if (error.response?.status === 403) message = "安全拦截：无权访问该服务器文件。";
        else message = error.message || String(error);
        options.showToast(message, "error");
      }
      return;
    }

    canvasData.value = options.normalizeDirectPayloadTitle
      ? { type: payload.type, title: payload.title || "文件预览", content: payload.content }
      : payload;
    canvasVisible.value = true;
  };

  return {
    canvasVisible,
    canvasFromWorkspace,
    canvasData,
    handleWorkspaceFilePreview,
    handleOpenCanvas,
    closeCanvas,
    revokeActiveBlobUrl,
  };
}
