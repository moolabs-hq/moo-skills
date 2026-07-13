from fastapi import FastAPI
app = FastAPI()
@app.get("/one")
def one(): pass
