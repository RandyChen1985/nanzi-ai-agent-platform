import os
import time
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from app.schemas.response import StandardResponse
from app.core.dependencies import require_api_key

router = APIRouter()

class FileItem(BaseModel):
    name: str = Field(..., description="文件名或目录名")
    path: str = Field(..., description="绝对路径")
    is_dir: bool = Field(..., description="是否是目录")
    size: int = Field(..., description="文件大小（字节），目录为0")
    mtime: float = Field(..., description="修改时间戳")

class FileListResponse(BaseModel):
    current_path: str = Field(..., description="当前浏览的绝对路径")
    parent_path: Optional[str] = Field(None, description="上级目录绝对路径，若在根目录则为 None")
    is_root: bool = Field(..., description="是否已在安全根目录")
    items: List[FileItem] = Field(..., description="子文件和子目录列表")

def get_base_dir() -> str:
    # 默认使用容器内绝对路径 /app/data
    base = "/app/data"
    # 如果不存在或不可写，则平滑降级到当前工作目录下的 data 目录，保证本地开发平稳健康运行
    if not os.path.exists(base):
        base = os.path.abspath("data")
    os.makedirs(base, exist_ok=True)
    return base

@router.get("/list",
    response_model=StandardResponse[FileListResponse],
    summary="浏览服务器文件系统",
    description="以 /app/data 为安全根目录（本地开发兼容为当前工作目录下的 data 目录），层层向下浏览文件和目录，提供安全路径防越权越界限制。"
)
async def list_files(
    path: Optional[str] = Query(None, description="要浏览的绝对路径，如果不传默认返回安全根目录"),
    user_info: Dict[str, Any] = Depends(require_api_key)
):
    base_dir = get_base_dir()
    
    # 确定目标浏览路径
    if not path:
        target_path = base_dir
    else:
        target_path = os.path.abspath(path)
        
    # 安全验证：检查 target_path 是否以 base_dir 开头，防范路径穿越 (Directory Traversal)
    # 使用 base_dir 和 target_path 的规范形式进行前缀比对
    normalized_base = os.path.normpath(base_dir)
    normalized_target = os.path.normpath(target_path)
    
    # 确保 target_path 不逃离 base_dir 范围
    if not normalized_target.startswith(normalized_base):
        raise HTTPException(
            status_code=403,
            detail="安全越权拦截：禁止访问安全根目录以外的文件系统空间。"
        )
        
    if not os.path.exists(normalized_target):
        raise HTTPException(
            status_code=404,
            detail="请求的路径不存在。"
        )
        
    if not os.path.isdir(normalized_target):
        raise HTTPException(
            status_code=400,
            detail="请求的路径不是一个目录。"
        )
        
    items: List[FileItem] = []
    try:
        with os.scandir(normalized_target) as entries:
            for entry in entries:
                # 隐藏以 . 开头的文件/夹
                if entry.name.startswith('.'):
                    continue
                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    items.append(FileItem(
                        name=entry.name,
                        path=entry.path,
                        is_dir=is_dir,
                        size=0 if is_dir else stat.st_size,
                        mtime=stat.st_mtime
                    ))
                except Exception:
                    # 某些系统文件没有读取权限时跳过
                    continue
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"读取目录失败: {str(e)}"
        )
        
    # 按“目录在前，文件在后；名称升序”的规则排序，保障极佳的用户浏览体验
    items.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    
    # 计算 parent_path
    if normalized_target == normalized_base:
        parent_path = None
        is_root = True
    else:
        parent_path = os.path.dirname(normalized_target)
        is_root = False
        
    return StandardResponse(data=FileListResponse(
        current_path=normalized_target,
        parent_path=parent_path,
        is_root=is_root,
        items=items
    ))
