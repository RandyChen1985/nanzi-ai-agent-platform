"""知识库访问申请的历史入口，内部委托 ``ResourceAccessRequestService``。

保留这个类是因为 ``ragflow.py`` 的旧端点仍在用它，而且旧端点的调用签名是
``create(dataset_id=..., dataset_name=..., user=..., reason=..., dept_id=...)``。
直接让调用方改成统一签名会把「旧接口保持可用」这条约束打破，所以在这里做一层
参数映射：知识库场景固定 ``resource_type='dataset'``。

新代码请直接用 ``ResourceAccessRequestService``。
"""

from app.services.resource_access_request_service import (  # noqa: F401
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessRequestError,
    ResourceAccessRequestService,
)


class KnowledgeAccessRequestService(ResourceAccessRequestService):
    """兼容包装：把知识库旧签名映射到统一服务。"""

    async def create(
        self,
        *,
        dataset_id: str,
        dataset_name=None,
        user=None,
        reason=None,
        dept_id=None,
    ):
        return await super().create(
            resource_type="dataset",
            resource_id=dataset_id,
            resource_name=dataset_name,
            user=user or {},
            reason=reason,
            dept_id=dept_id,
        )

    async def pending_for(self, dataset_id: str, user_id: int):  # type: ignore[override]
        return await super().pending_for("dataset", dataset_id, user_id)
