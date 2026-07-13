from fastapi import FastAPI
from routes import router as api_router
import admin as admin_routes

app = FastAPI()
app.add_middleware(AttributionMiddleware)  # noqa: F821
app.include_router(api_router, prefix="/v1")
app.include_router(admin_routes.router, prefix="/v2")
