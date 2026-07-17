import asyncio
import os
import sys
import yaml
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

# Ensure app modules are visible
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.models.metadata import MetaDataset, MetaTable, MetaColumn, MetaRelationship
from app.services.metadata_service import MetadataService

# YAML Path
YAML_FILE = "architech/meta-schemal/user_provided_meta.yaml"

async def import_metadata():
    if not os.path.exists(YAML_FILE):
        print(f"❌ YAML file not found: {YAML_FILE}")
        return

    print(f"📖 Reading {YAML_FILE}...")
    with open(YAML_FILE, 'r', encoding='utf-8') as f:
        meta_data = yaml.safe_load(f)

    async with AsyncSessionLocal() as db:
        # 1. Create/Update Dataset
        dataset_name = meta_data.get("domain", "default_dataset")
        print(f"🔹 Processing Dataset: {dataset_name}")
        
        # Check existing
        stmt = select(MetaDataset).where(MetaDataset.name == dataset_name)
        result = await db.execute(stmt)
        dataset = result.scalar_one_or_none()
        
        ds_info = {
            "name": dataset_name,
            "display_name": meta_data.get("domain"), # Use domain as display name for now
            "description": meta_data.get("description"),
            "data_source": "clickhouse"
        }
        
        if dataset:
            print(f"   -> Updating existing dataset ID {dataset.id}")
            for k, v in ds_info.items():
                setattr(dataset, k, v)
        else:
            print(f"   -> Creating new dataset")
            dataset = MetaDataset(**ds_info)
            db.add(dataset)
        
        await db.flush() # Get ID
        dataset_id = dataset.id
        
        # 2. Process Tables (Entities)
        table_map = {} # physical_name -> table_id
        
        entities = meta_data.get("entities") or meta_data.get("tables", [])
        print(f"🔹 Processing {len(entities)} Tables...")
        
        for entity in entities:
            phy_name = entity["name"]
            term = entity.get("term", phy_name)
            desc = entity.get("description", "")
            
            # Prepare columns
            cols_data = []
            for col in entity.get("columns", []):
                cols_data.append({
                    "physical_name": col["name"],
                    "term": col.get("term", col["name"]),
                    "type": col.get("type", "String"),
                    "description": col.get("description", ""),
                    "enums": col.get("enums", []),
                    "examples": col.get("examples", []),
                    "foreign_key": col.get("foreign_key"),
                    "is_primary": 1 if col["name"] == "rowkey" else 0 # Simple heuristic for rowkey
                })
            
            # Use Service to Save Table & Columns (Handles Upsert)
            # Adapt Service input format
            table_payload = {
                "physical_name": phy_name,
                "term": term,
                "description": desc,
                "synonyms": entity.get("synonyms", []),
                "columns": cols_data
            }
            
            saved_table = await MetadataService.save_table_metadata(db, dataset_id, table_payload)
            table_map[phy_name] = saved_table.id
            print(f"   -> Saved Table: {term} ({phy_name})")

        # 3. Process Relationships
        relationships = meta_data.get("relationships", [])
        if relationships:
            print(f"🔹 Processing {len(relationships)} Relationships...")
            
            for rel in relationships:
                # Source/Target format: "table.column"
                # But our YAML has "db.table.column" sometimes? 
                # Let's parse carefully. The YAML examples:
                # source: "yovole_dm_clickhouse_prod.ck_fact_nanzi_devicepoint_hbase.jf"
                # target: "yovole_dm_clickhouse_prod.ck_fact_nanzi_resroom_hbase.rowkey"
                
                src_full = rel["source"]
                tgt_full = rel["target"]
                
                # Split by last dot to get table_name and column_name
                # Try direct lookup first
                src_tid = table_map.get(src_full)
                if src_tid:
                    src_table_name = src_full
                else:
                     src_table_name = src_full.rsplit('.', 1)[0]
                     src_tid = table_map.get(src_table_name)

                tgt_tid = table_map.get(tgt_full)
                if tgt_tid:
                    tgt_table_name = tgt_full
                else:
                     tgt_table_name = tgt_full.rsplit('.', 1)[0]
                     tgt_tid = table_map.get(tgt_table_name)
                
                if not src_tid or not tgt_tid:
                    print(f"   ⚠️ Skipping relationship: Tables not found ({src_table_name} -> {tgt_table_name})")
                    continue
                
                # Create Relationship Record
                # Check duplication? For now just append or ignore.
                # Ideally should check if exists.
                
                new_rel = MetaRelationship(
                    source_table_id=src_tid,
                    target_table_id=tgt_tid,
                    join_condition=f"{src_full} = {tgt_full}",
                    join_type="LEFT", # Default
                    description=rel.get("description", "")
                )
                db.add(new_rel)
                print(f"   -> Added Relation: {src_table_name} -> {tgt_table_name}")

        await db.commit()
        print("✅ Metadata Import Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(import_metadata())
