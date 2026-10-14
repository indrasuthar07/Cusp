from app.api.auth_routes import router as auth_router
from app.api.governance_routes import router as governance_router
from app.api.health import router as health_router
from app.api.mcp_routes import router as mcp_router
from app.config.settings import get_settings
from fastapi import FastAPI

# Fail fast during process startup instead of serving with an invalid config.
settings = get_settings()

app = FastAPI(title="Cusp Gateway", version="0.1.0")

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(governance_router)
app.include_router(mcp_router)
