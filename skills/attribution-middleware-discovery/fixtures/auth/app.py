from fastapi import APIRouter, Depends, FastAPI

app = FastAPI()
app.add_middleware(AuthenticationMiddleware)  # noqa: F821

@app.get("/global")
def global_route():
    pass

router = APIRouter(dependencies=[Depends(require_auth)])  # noqa: F821
@router.get("/router")
def router_route():
    pass

@app.get("/handler", dependencies=[Depends(require_auth)])  # noqa: F821
def handler_route():
    pass

@app.get("/unknown")
def unknown_route():
    pass
