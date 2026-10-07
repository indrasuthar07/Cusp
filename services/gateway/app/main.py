from fastapi import FastAPI

from app.api.auth_routes import router as auth_router

app = FastAPI(title="Cusp Gateway", version="0.1.0")

app.include_router(auth_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}