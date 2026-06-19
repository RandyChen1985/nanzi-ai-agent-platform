from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import Base
from sqlalchemy import Column, Integer, String, Text, DateTime, func

# ORM Model
class SlashCommand(Base):
    __tablename__ = "slash_commands"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String(50), nullable=False)
    command = Column(Text, nullable=False)
    sort_order = Column(Integer, default=0)
    created_by = Column(String(64), default="system")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

# Pydantic Schemas
class SlashCommandBase(BaseModel):
    label: str
    command: str
    sort_order: int = 0

class SlashCommandCreate(SlashCommandBase):
    pass

class SlashCommandUpdate(SlashCommandBase):
    pass

class SlashCommandReorderItem(BaseModel):
    id: int
    sort_order: int

class SlashCommandReorderRequest(BaseModel):
    items: List[SlashCommandReorderItem]

class SlashCommandResponse(SlashCommandBase):
    id: int
    created_by: str
    created_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

# Service
class SlashCommandService:
    @staticmethod
    async def list_commands(session: AsyncSession, user: dict) -> List[SlashCommand]:
        """
        List commands visible to the user:
        1. System/Admin commands (created_by in ['system', 'admin'])
        2. User's own commands
        
        Sorting: User's own commands first, then by sort_order.
        """
        from sqlalchemy import case, or_
        
        username = user.get("user_name", "unknown")
        
        # Filter Logic
        filter_condition = or_(
            SlashCommand.created_by.in_(['system', 'admin']),
            SlashCommand.created_by == username
        )
        
        # Sorting Logic: Own commands (0) first, others (1) second
        priority_sort = case(
            (SlashCommand.created_by == username, 0),
            else_=1
        )
        
        stmt = select(SlashCommand).where(filter_condition).order_by(
            priority_sort.asc(),
            SlashCommand.sort_order.asc()
        )
        
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def create_command(session: AsyncSession, data: SlashCommandCreate, user_name: str) -> SlashCommand:
        cmd = SlashCommand(**data.model_dump(), created_by=user_name)
        session.add(cmd)
        await session.commit()
        await session.refresh(cmd)
        return cmd

    @staticmethod
    async def update_command(session: AsyncSession, cmd_id: int, data: SlashCommandUpdate, user: dict) -> Optional[SlashCommand]:
        cmd = await session.get(SlashCommand, cmd_id)
        if not cmd:
            return None
        
        # Permission Check
        is_admin = user.get("role") == "admin"
        is_owner = cmd.created_by == user.get("user_name")
        
        if not (is_admin or is_owner):
            return None # Or raise PermissionError, but returning None works for 404/403 ambiguity in simple service
        
        for key, value in data.model_dump().items():
            setattr(cmd, key, value)
            
        await session.commit()
        await session.refresh(cmd)
        return cmd

    @staticmethod
    async def delete_command(session: AsyncSession, cmd_id: int, user: dict) -> bool:
        cmd = await session.get(SlashCommand, cmd_id)
        if not cmd:
            return False
        
        # Permission Check
        is_admin = user.get("role") == "admin"
        is_owner = cmd.created_by == user.get("user_name")
        
        if not (is_admin or is_owner):
            return False
        
        await session.delete(cmd)
        await session.commit()
        return True

    @staticmethod
    async def reorder_commands(session: AsyncSession, data: SlashCommandReorderRequest, user: dict) -> bool:
        """
        Batch update sort_order for commands owned by the user or if user is admin.
        """
        username = user.get("user_name")
        is_admin = user.get("role") == "admin"
        
        # 1. Collect all IDs
        ids = [item.id for item in data.items]
        
        # 2. Fetch all matching commands to verify ownership
        stmt = select(SlashCommand).where(SlashCommand.id.in_(ids))
        result = await session.execute(stmt)
        commands = {c.id: c for c in result.scalars().all()}
        
        # 3. Update orders
        for item in data.items:
            cmd = commands.get(item.id)
            if not cmd:
                continue
            
            # Check ownership (unless admin)
            if not is_admin and cmd.created_by != username:
                continue
                
            cmd.sort_order = item.sort_order
            
        await session.commit()
        return True
