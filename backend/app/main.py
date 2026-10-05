from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import scenes, plans, terrain

app = FastAPI(title=settings.app_title)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scenes.router, prefix="/api")
app.include_router(plans.router, prefix="/api")
app.include_router(terrain.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}