"""웹 화면용 로컬 서버: http://127.0.0.1:5173

  .venv/Scripts/python -X utf8 server.py
"""
from flask import Flask, jsonify, request, send_from_directory

import api

app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(api.ROOT / "web", "index.html")


@app.get("/api/meta")
def meta():
    return jsonify(api.meta())


@app.post("/api/plan")
def plan_api():
    return jsonify(api.plan_api(request.get_json(force=True)))


@app.get("/api/items")
def items_api():
    return jsonify(api.items_api(request.args.get("q", "")))


@app.get("/api/manual_recipes")
def manual_list():
    return jsonify(api.manual_list())


@app.post("/api/manual_recipes")
def manual_save():
    try:
        return jsonify(api.manual_save(request.get_json(force=True)))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/manual_recipes/<int:pid>")
def manual_delete(pid):
    return jsonify(api.manual_delete(pid))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5173, debug=False)
