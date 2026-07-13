from flask import Flask

app = Flask(__name__)

@app.get("/flask")
def flask_route():
    return {}
