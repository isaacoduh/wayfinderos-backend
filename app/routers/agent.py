from __future__ import annotations

from fastapi import APIRouter

from app.routers import agent_build, agent_chat, agent_regeneration

router = APIRouter()
router.include_router(agent_regeneration.router)
router.include_router(agent_build.router)
router.include_router(agent_chat.router)
