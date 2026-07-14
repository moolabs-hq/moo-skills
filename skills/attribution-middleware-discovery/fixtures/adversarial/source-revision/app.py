from fastapi import FastAPI

app = FastAPI()

@app.get("/revision")
def revision():
    return {}
