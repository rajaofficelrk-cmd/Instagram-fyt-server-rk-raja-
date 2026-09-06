import json
import os
import uuid
from datetime import datetime, timezone

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for
)
from werkzeug.utils import secure_filename

app = Flask(__name__)

UPLOAD_DIR = "uploads"
STATE_FILE = "state.json"

os.makedirs(UPLOAD_DIR, exist_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def default_state():
    return {
        "job_id": None,
        "status": "idle",
        "total": 0,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "items": [],
        "message": "",
        "created_at": None,
        "updated_at": None
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_state()


def save_state(state):
    tmp = STATE_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    os.replace(tmp, STATE_FILE)


@app.route("/")
def index():
    state = load_state()
    return render_template("index.html", state=state)


@app.route("/api/status")
def api_status():
    return jsonify(load_state())


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")

    if not file or not file.filename:
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)

    allowed = {".txt", ".csv"}
    extension = os.path.splitext(filename)[1].lower()

    if extension not in allowed:
        return "Only TXT and CSV files are allowed", 400

    path = os.path.join(
        UPLOAD_DIR,
        f"{uuid.uuid4().hex}_{filename}"
    )

    file.save(path)

    items = []

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                # CSV ki first value ko ID/name maana jayega.
                if "," in line:
                    value = line.split(",")[0].strip()
                else:
                    value = line

                if value:
                    items.append(value)

    except UnicodeDecodeError:
        return "File must be UTF-8 encoded", 400

    state = load_state()

    state.update({
        "job_id": uuid.uuid4().hex,
        "status": "ready",
        "total": len(items),
        "processed": 0,
        "success": 0,
        "failed": 0,
        "items": items,
        "message": "",
        "created_at": now(),
        "updated_at": now()
    })

    save_state(state)

    return redirect(url_for("index"))


@app.route("/add", methods=["POST"])
def add_item():
    value = request.form.get("item", "").strip()

    if not value:
        return redirect(url_for("index"))

    state = load_state()

    state["items"].append(value)
    state["total"] = len(state["items"])

    if state["status"] == "idle":
        state["status"] = "ready"

    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
def start():
    state = load_state()

    if not state["items"]:
        return redirect(url_for("index"))

    if state["processed"] >= state["total"]:
        state["processed"] = 0
        state["success"] = 0
        state["failed"] = 0

    state["status"] = "running"
    state["message"] = "Worker started"
    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/pause", methods=["POST"])
def pause():
    state = load_state()

    state["status"] = "paused"
    state["message"] = "Worker paused"
    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    state = load_state()

    state["status"] = "stopped"
    state["message"] = "Worker stopped"
    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/clear", methods=["POST"])
def clear():
    save_state(default_state())
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "raja-background-worker"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
)
