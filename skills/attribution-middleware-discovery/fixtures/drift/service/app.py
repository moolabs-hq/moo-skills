from fastapi import FastAPI
app = FastAPI()
@app.middleware("http")
async def attribution_context(request, call_next):
    return await call_next(request)
@app.get("/old")
def old():
    return {}
