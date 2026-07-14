from fastapi import APIRouter, FastAPI
import httpx

app = FastAPI()
router = APIRouter(prefix="/api")
app.include_router(router, prefix="/v1")

@router.get("/users")
def users(claims):
    customer = claims.customer_id
    assert UUID(customer)  # noqa: F821
    return httpx.get(
        "https://internal",
        headers=inject_thread_id({"thread_id": current_thread_id()}),  # noqa: F821
    )
