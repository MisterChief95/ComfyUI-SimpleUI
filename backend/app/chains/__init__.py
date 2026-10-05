"""Saved sequential workflow chains.

Wiring (app/main.py)::

    chains = ChainService(database, repository, generations, submit_stage)
    app.state.chains = chains
    app.include_router(chains_router)   # BEFORE the /api/{path} catch-all
"""

from __future__ import annotations

from .routes import router
from .service import ChainError, ChainService

__all__ = ["ChainError", "ChainService", "router"]
