import asyncio
import os
import sys
# Ensure app modules are visible
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.services.metadata_service import MetadataService

async def seed_data():
    async with AsyncSessionLocal() as db:
        # 1. Create Dataset: NanZi Resources
        print("Creating 'nanzi_resources' dataset...")
        ds_data = {
            "name": "nanzi_resources",
            "display_name": "南孜资源池",
            "description": "包含机房(Room)、机柜(Cabinet)、物理机(Server)、虚拟机(VM)等核心资源信息的元数据。",
            "tags": ["core", "resources", "idc"],
            "data_source": "clickhouse"
        }
        
        existing = await MetadataService.get_dataset_by_name(db, ds_data["name"])
        if existing:
            print("Dataset already exists, skipping creation.")
            ds = existing
        else:
            ds = await MetadataService.create_dataset(db, ds_data)

        # 2. Create Table: res_room
        print("Creating 'res_room' table...")
        room_data = {
            "physical_name": "res_room",
            "term": "机房",
            "description": "存放物理机设备的物理空间单元。",
            "columns": [
                {"physical_name": "room_id", "term": "机房ID", "type": "String", "description": "唯一标识"},
                {"physical_name": "room_name", "term": "机房名称", "type": "String", "description": "如：上海一区"},
                {"physical_name": "region", "term": "所属区域", "type": "String", "description": "如：华东"}
            ]
        }
        await MetadataService.save_table_metadata(db, ds.id, room_data)

        # 3. Create Table: res_server (Physical Server)
        print("Creating 'res_server' table...")
        server_data = {
            "physical_name": "res_server",
            "term": "物理机",
            "description": "运行在机柜中的物理计算设备。",
            "columns": [
                {"physical_name": "server_id", "term": "服务器ID", "type": "String"},
                {"physical_name": "hostname", "term": "主机名", "type": "String"},
                {"physical_name": "ip_address", "term": "IP地址", "type": "String"},
                {"physical_name": "status", "term": "状态", "type": "String", 
                 "enums": [{"value": "running", "label": "运行中"}, {"value": "stopped", "label": "已关机"}]},
                 {"physical_name": "cpu_usage", "term": "CPU使用率", "type": "Float"}
            ]
        }
        await MetadataService.save_table_metadata(db, ds.id, server_data)

        print("✅ Data Seeding Completed!")

if __name__ == "__main__":
    asyncio.run(seed_data())
