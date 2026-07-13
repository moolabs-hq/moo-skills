from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def attribution_middleware(request, call_next):
    return await call_next(request)

@app.get("/v1/items")
async def list_items():
    return []
