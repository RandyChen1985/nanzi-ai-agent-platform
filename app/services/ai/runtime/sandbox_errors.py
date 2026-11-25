"""沙箱后端不可用的统一异常基类。

历史上 Docker / K8s / e2b / SSH 四条策略的失败处理各不相同：

* Docker 抛 ``DockerSandboxUnavailableError``、K8s 抛 ``K8sSandboxUnavailableError``，
  调用方（``assistant_agent_runner``）据此降级——摘掉 Bash 与脚本工具，只保留宿主
  文件工具，前端显示"沙箱降级"浮标；
* e2b / SSH 失败时 ``get_local_workspace`` 直接 ``return None``，调用方误判为"工作区
  已就绪"并**清空降级标记**；此时若智能体只挂了 Bash（没有文件工具触发
  ``PermissionError``），Bash 会在平台后端的 cwd 下执行——正是各模块文档里承诺
  "绝不静默降级到宿主机"要防的事。

把"沙箱不可用"收敛成一个基类后，调用方只需捕获基类即可覆盖全部策略，新增策略
（或新失败分支）不会再因为"不在这两个 isinstance 名单里"而静默 fail-open。
"""

from __future__ import annotations


class SandboxUnavailableError(RuntimeError):
    """任何沙箱后端（docker / k8s / e2b / ssh / local）不可用。

    调用方必须据此**降级或拒绝**，绝不能回退到宿主机直接执行用户命令。

    Attributes:
        policy: 出问题的沙箱策略名（``docker`` / ``k8s`` / ``e2b`` / ``ssh`` / ``local``）。
        reason_code: 稳定的机器可读错误码，前端与审计日志按它分类。
        user_message: 面向用户的中文提示。
    """

    def __init__(
        self,
        message: str,
        *,
        policy: str = "",
        reason_code: str = "sandbox_unavailable",
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.policy = policy
        self.reason_code = reason_code
        self.user_message = user_message or (
            "沙箱不可用，命令未执行。请检查沙箱配置或联系管理员。"
        )


__all__ = ["SandboxUnavailableError"]
