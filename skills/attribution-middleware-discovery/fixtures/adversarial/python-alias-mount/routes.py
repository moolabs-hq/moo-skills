from fastapi import APIRouter

router = APIRouter(prefix="/api")

@router.get("/users")
def users():
    return []
