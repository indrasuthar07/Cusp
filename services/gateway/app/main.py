from fastapi import FastAPI

from app.api.auth_routes import router as auth_router
from app.api.health import router as health_router
from app.config.settings import get_settings

# Fail fast during process startup instead of serving with an invalid config.
settings = get_settings()

app = FastAPI(title="Cusp Gateway", version="0.1.0")

app.include_router(health_router)
app.include_router(auth_router)