"""Owned media indexing, immutable capture, thumbnails, and streaming.

Wiring (INTEGRATE-001 does this in app/main.py)::

    media = MediaService(database, settings, config.data_dir)
    app.state.media = media
    auth.baseline_gate = media.baseline_status   # gate multi-user activation
    app.include_router(media_router)             # BEFORE the /api/{path} catch-all

The catch-all matters: FastAPI matches routes in registration order, so a media
router added after it never runs.
"""

from __future__ import annotations

from .routes import router
from .service import LocatedMedia, MediaError, MediaService

__all__ = ["LocatedMedia", "MediaError", "MediaService", "router"]
