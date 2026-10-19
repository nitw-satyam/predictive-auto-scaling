from flask import Flask, jsonify
import time

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "autoscaling-demo"})

@app.get("/cpu")
def cpu_work():
    # Small synthetic CPU workload for local testing.
    total = 0
    for i in range(50000):
        total += (i*i) % 97
    return jsonify({"status": "ok", "value": total})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)