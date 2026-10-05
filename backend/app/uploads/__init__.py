"""Private, streamed uploads staged for ComfyUI loader inputs.

Wiring (app/main.py)::

    uploads = UploadService(database, settings, config.data_dir, comfy)
    app.state.uploads = uploads
    app.include_router(uploads_router)   # BEFORE the /api/{path} catch-all
"""

from __future__ import annotations

from .routes import router
from .service import UploadError, UploadService

__all__ = ["UploadError", "UploadService", "router"]
