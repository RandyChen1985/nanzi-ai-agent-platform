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


class ThirdPartyUserSyncConfig(BaseModel):
    enabled: bool = False
    connection_config_id: Optional[int] = None
    table_name: Optional[str] = None
    field_map: ThirdPartyUserSyncFieldMap = Field(
        default_factory=lambda: ThirdPartyUserSyncFieldMap(user_name="")
    )
    extra_data_mappings: List[ThirdPartyUserSyncExtraDataMapping] = Field(default_factory=list)
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
