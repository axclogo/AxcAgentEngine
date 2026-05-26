"""English: This documentation describes the related engine component behavior.
中文：健康检查"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
	return {"status": "ok"}


@router.get("/ready")
async def ready():
	return {"status": "ready"}
