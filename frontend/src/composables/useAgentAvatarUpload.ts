/**
 * 智能体头像上传（含大图裁剪）的共享逻辑。
 *
 * 全局 AI 头像的裁剪上传在 ChatSettings 里、智能体的在编辑弹窗与版本配置抽屉里，
 * 三处流程一致（大图 Canvas 裁剪 / SVG 免裁剪 / 时间戳唯一文件名），所以收敛到这里，
 * 避免第三份拷贝继续漂移。
 *
 * 注意：上传成功只把 URL 交给调用方回填表单，**不直接写库**——智能体编辑是
 * 「表单 + 保存」语义，上传即写库会让「不保存也生效」。
 *
 * 两种落点：已有智能体走 `POST /agents/{id}/avatar/upload`（只清自己的旧文件）；
 * 新建流程尚无 id，走 `POST /agents/avatar/upload` 以待绑定前缀落盘。
 */
import { ref } from "vue";
import axios from "@/utils/axios";
import { useToast } from "@/composables/useToast";

const MAX_SELECT_BYTES = 30 * 1024 * 1024; // 允许选大图，裁剪后输出 256×256
const CROPPABLE_TYPES = ["image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"];

type Notify = (message: string, type?: "success" | "error" | "warning" | "info") => void;

export interface UseAgentAvatarUploadOptions {
  /**
   * 当前智能体 ID；返回空表示「新建流程尚未保存」，此时走待绑定上传接口
   * （`POST /avatar/upload`，文件以 `pending_` 前缀落盘，保存时 URL 直接写库）。
   */
  getAgentId?: () => string | undefined;
  /** 上传成功后的回调：把短路径写回表单字段即可。 */
  onUploaded: (avatarUrl: string) => void;
  /** 提示函数；默认走全局 useToast。 */
  notify?: Notify;
}

export function useAgentAvatarUpload(options: UseAgentAvatarUploadOptions) {
  const fallbackToast = useToast().showToast;
  const notify: Notify = options.notify ?? ((message, type = "info") => fallbackToast(message, type));

  const uploading = ref(false);
  const showCropper = ref(false);
  const cropperSrc = ref("");

  /**
   * 打开系统文件选择器。
   *
   * `input` 由调用方的模板 ref 传入（而非本组合式自持 ref）：`noUnusedLocals` 下
   * 只被 `ref="..."` 字符串引用的局部变量会被判为未使用，因此调用方需要在模板表达式
   * 中使用该变量，这里就以参数形式接收。
   */
  const pickFile = (input?: HTMLInputElement | null) => {
    input?.click();
  };

  const uploadBlob = async (blob: Blob, filename = "agent-avatar.png") => {
    const agentId = options.getAgentId?.();
    // 新建流程还没有 agent_id：走待绑定上传，保存时 URL 直接写进 avatar_url。
    const endpoint = agentId
      ? `/api/portal/agents/${encodeURIComponent(agentId)}/avatar/upload`
      : "/api/portal/agents/avatar/upload";
    uploading.value = true;
    try {
      const formData = new FormData();
      formData.append("file", new File([blob], filename, { type: blob.type || "image/png" }));
      const res = await axios.post(endpoint, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const uploadedUrl = res.data?.data?.avatar_url;
      if (!uploadedUrl) {
        notify("上传头像失败，返回数据异常", "error");
        return;
      }
      options.onUploaded(String(uploadedUrl));
      notify("头像已上传，保存后生效", "success");
      showCropper.value = false;
    } catch (error: any) {
      notify(error?.response?.data?.detail || "上传头像失败，请稍后重试", "error");
    } finally {
      uploading.value = false;
    }
  };

  const handleFileChange = async (event: Event) => {
    const target = event.target as HTMLInputElement;
    const file = target.files?.[0];
    target.value = "";
    if (!file) return;

    if (file.size > MAX_SELECT_BYTES) {
      notify("图片过大，请选择 30MB 以内的图片", "warning");
      return;
    }
    // 矢量图无需裁剪，直接上传
    if (file.type === "image/svg+xml") {
      await uploadBlob(file, file.name || "agent-avatar.svg");
      return;
    }
    if (!CROPPABLE_TYPES.includes(file.type)) {
      notify("仅支持 PNG、JPEG、WebP、GIF、SVG 格式图片", "error");
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      cropperSrc.value = (e.target?.result as string) || "";
      showCropper.value = true;
    };
    reader.readAsDataURL(file);
  };

  const handleCropped = async (blob: Blob) => {
    await uploadBlob(blob);
  };

  return {
    uploading,
    showCropper,
    cropperSrc,
    pickFile,
    handleFileChange,
    handleCropped,
  };
}
