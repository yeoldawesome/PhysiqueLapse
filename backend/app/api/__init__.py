"""HTTP routers, one per area, all mounted under /api."""

from fastapi import APIRouter

from . import dashboard, imports, jobs, photos, poses, settings

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(settings.router)
api.include_router(jobs.router)
api.include_router(photos.router)
api.include_router(poses.router)
api.include_router(imports.router)


def include_optional() -> None:
    """Routers for later milestones register themselves here when their modules exist."""
    for name in ("landmarks", "alignment", "viewer", "exports", "metrics", "review", "snapchat", "snapchat_zips", "measurements", "abs", "sleeves", "faces"):
        try:
            mod = __import__(f"app.api.{name}", fromlist=["router"])
        except ImportError:
            continue
        api.include_router(mod.router)


include_optional()
