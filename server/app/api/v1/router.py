"""Aggregate /api/v1 router."""

from fastapi import APIRouter

from app.api.v1 import admin, auth, batches, jobs, me, sources, subscription

router = APIRouter(prefix="/api/v1")

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(me.router, tags=["me"])
router.include_router(sources.router, prefix="/sources", tags=["sources"])
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
router.include_router(batches.router, prefix="/batches", tags=["batches"])
router.include_router(subscription.router, prefix="/subscription", tags=["subscription"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
