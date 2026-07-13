from fastapi import APIRouter, Depends, FastAPI

app = FastAPI()
app.add_middleware(AuthenticationMiddleware)

@app.get("/global")
def global_route():
    pass

router = APIRouter(dependencies=[Depends(require_auth)])
@router.get("/router")
def router_route():
    pass

@app.get("/handler", dependencies=[Depends(require_auth)])
def handler_route():
    pass

@app.get("/unknown")
def unknown_route():
    pass
