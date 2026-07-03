/** 工作空间系统 slash 指令 */
export const WORKSPACE_SLASH_COMMAND = "/workspace";

export const WORKSPACE_SYSTEM_COMMAND_ID = "sys_workspace";

export function isWorkspaceSlashCommand(cmd: string): boolean {
  return String(cmd || "").trim() === WORKSPACE_SLASH_COMMAND;
}
