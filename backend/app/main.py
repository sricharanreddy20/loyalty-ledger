"""FastAPI app: the only tier that talks to the database. Docs at /docs.

Run locally:  uvicorn backend.app.main:app --reload
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import reads, writes
from backend.app.errors import install_handlers

app = FastAPI(
    title="Loyalty Points Ledger API",
    description="Accrue, redeem, expire and reverse points with a fully auditable ledger.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:8080").split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],     # lets the page read the total of GET /accounts
)
install_handlers(app)
app.include_router(reads.router)
app.include_router(writes.router)
