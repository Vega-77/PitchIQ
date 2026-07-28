from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers tables on Base before create_all)
from .database import Base, engine
from .routers import events, matches, substitutions, teams

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PitchIQ API",
    description="Match-day tagging backend.",
    version="0.1.0",
)

# Open CORS: runs on a local network alongside the tablet, no auth yet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teams.router)
app.include_router(matches.router)
app.include_router(substitutions.router)
app.include_router(events.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
