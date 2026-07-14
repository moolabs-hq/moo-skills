from fastapi import FastAPI

app = FastAPI()

@app.get("/one-two")
def first():
    pass

@app.get("/one/two")
def second():
    pass
