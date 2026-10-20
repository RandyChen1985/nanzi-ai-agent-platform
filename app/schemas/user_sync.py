from typing import List, Literal, Optional

from pydantic import BaseModel, Field


SchedulePreset = Literal["off", "hourly", "daily", "weekly"]


class ThirdPartyUserSyncExtraDataMapping(BaseModel):
    json_key: str = Field(..., description="写入本平台 extra_data 的 JSON 键名")
    source_column: str = Field(..., description="来源表字段列名")


class ThirdPartyUserSyncFieldMap(BaseModel):
    user_name: str = Field(..., description="第三方用户名列（作为两边系统映射主键）")
    real_name: Optional[str] = None
    remark: Optional[str] = None
    email: Optional[str] = None


class ThirdPartyUserSyncDeptSync(BaseModel):
    """部门归属同步：可选、单列、默认只增不减。

    平台部门树是本地维护的，同步只把人挂到**已存在**的部门上：来源部门编码找不到
    就跳过该人的部门归属并计数（账号照常同步），不按 code 凭空建部门——来源只有编码，
    没有父级信息，自动建只会堆出一层没有层级的部门。
    """

    enabled: bool = False
    source_column: Optional[str] = Field(
        None, description="来源表的部门编码列；与 Department.code 对齐"
    )
    mode: Literal["add", "replace"] = "add"


class ThirdPartyUserSyncConfig(BaseModel):
    enabled: bool = False
    connection_config_id: Optional[int] = None
    table_name: Optional[str] = None
    field_map: ThirdPartyUserSyncFieldMap = Field(
        default_factory=lambda: ThirdPartyUserSyncFieldMap(user_name="")
    )
    extra_data_mappings: List[ThirdPartyUserSyncExtraDataMapping] = Field(default_factory=list)
    dept_sync: ThirdPartyUserSyncDeptSync = Field(default_factory=ThirdPartyUserSyncDeptSync)
    schedule: SchedulePreset = "off"


class ThirdPartyUserSyncConfigUpdate(ThirdPartyUserSyncConfig):
    pass


class ThirdPartyUserSyncRunRequest(BaseModel):
    user_names: Optional[List[str]] = Field(
        default=None,
        description="指定同步的用户名列表；为空则同步全部第三方用户",
    )
    config: Optional[ThirdPartyUserSyncConfigUpdate] = Field(
        default=None,
        description="可选：使用当前表单配置预览/执行；未传则读取已保存配置",
    )


class ThirdPartyUserSyncPreviewRequest(BaseModel):
    config: Optional[ThirdPartyUserSyncConfigUpdate] = Field(
        default=None,
        description="可选：使用当前表单配置预览；未传则读取已保存配置",
    )
