"""内部接口：沙箱（或本地工作区）里的脚本回连平台取凭据。

**不属于对外集成的 API**，所以刻意不挂在 `/api/v1` 下：

* v1 的鉴权依赖是 ``require_api_key``（[`app/core/dependencies.py`](../../core/dependencies.py)
  的 ``verify_v1_api_access``），白名单只跳过细粒度权限、**仍要求登录态**；
  沙箱里既没有 session，也没法带 API key。
* 这里唯一的门是**票据**（:mod:`app.services.secrets.sandbox_ticket`），
  它绑定用户 + 会话 + 有效期，且 ``aud`` 参与签名，不能挪给别的接口用。

部署侧还有一道：``/internal/`` 不应暴露到公网，网关层只允许集群内 / 沙箱网段
访问。票据是第二道门，不是唯一一道。
"""
