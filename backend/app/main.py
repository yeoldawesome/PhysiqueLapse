"""FastAPI application: /api routers, background job runner, built frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .jobs import runner

log = logging.getLogger("physiquelapse")


def _register_job_handlers() -> None:
    """Import every service module that defines @job handlers."""
    from .services import ingest  # noqa: F401

    for name in ("landmarks", "classify", "align", "export_video", "snapchat", "snapchat_zip", "physique_filter", "measure"):
        try:
            __import__(f"app.services.{name}")
        except ImportError as e:  # module not written yet or optional dependency missing
            if name in str(e):
                continue
            log.warning("could not import service %s: %s", name, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    _register_job_handlers()
    runner().start()
    log.info("data dir: %s", config.DATA_DIR)
    try:
        yield
    finally:
        runner().stop()


def create_app() -> FastAPI:
    app = FastAPI(title="PhysiqueLapse", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    from .api import api

    app.include_router(api)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    dist = config.FRONTEND_DIST
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "unknown API route")
        if full_path:
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(404) from None
            if candidate.is_file():
                return FileResponse(candidate)
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            {
                "detail": "Frontend not built. Run start.ps1 (or `npm run build` in frontend/), "
                "or use the Vite dev server on port 5173.",
                "api_docs": "/api/docs",
            },
            status_code=200,
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
