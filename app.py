import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)

STATE_FILE = "state.json"
UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

worker_lock = threading.Lock()
worker_started = False


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
    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(temp, STATE_FILE)


def process_item(item):
    """
    Generic authorized background task.

    Yahan legitimate processing logic add ki ja sakti hai.
    """

    print(f"Processing: {item}")

    time.sleep(2)

    return True


def background_worker():
    global worker_started

    print("Background worker started")

    while True:
        try:
            state = load_state()

            if state["status"] != "running":
                time.sleep(3)
                continue

            if state["processed"] >= state["total"]:
                state["status"] = "completed"
                state["message"] = "All items completed"
                state["updated_at"] = now()

                save_state(state)

                time.sleep(3)
                continue

            index = state["processed"]
            item = state["items"][index]

            try:
                result = process_item(item)

                if result:
                    state["success"] += 1
                else:
                    state["failed"] += 1

            except Exception as error:
                print("Item error:", error)
                state["failed"] += 1

            state["processed"] += 1
            state["updated_at"] = now()

            save_state(state)

        except Exception as error:
            print("Worker error:", error)
            time.sleep(5)


def start_worker():
    global worker_started

    with worker_lock:

        if worker_started:
            return

        worker_started = True

        thread = threading.Thread(
            target=background_worker,
            daemon=True
        )

        thread.start()


@app.route("/")
def index():
    start_worker()

    state = load_state()

    return render_template(
        "index.html",
        state=state
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "raja-server"
    })


@app.route("/api/status")
def api_status():
    start_worker()

    return jsonify(load_state())


@app.route("/add", methods=["POST"])
def add_item():

    value = request.form.get(
        "item",
        ""
    ).strip()

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


@app.route("/upload", methods=["POST"])
def upload():

    file = request.files.get("file")

    if not file or not file.filename:
        return redirect(url_for("index"))

    filename = secure_filename(
        file.filename
    )

    extension = os.path.splitext(
        filename
    )[1].lower()

    if extension not in {".txt", ".csv"}:
        return "Only TXT and CSV files are allowed", 400

    path = os.path.join(
        UPLOAD_DIR,
        f"{uuid.uuid4().hex}_{filename}"
    )

    file.save(path)

    items = []

    try:

        with open(
            path,
            "r",
            encoding="utf-8-sig"
        ) as f:

            for line in f:

                line = line.strip()

                if not line:
                    continue

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
        "message": f"{len(items)} items loaded",
        "created_at": now(),
        "updated_at": now()
    })

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
    state["message"] = "Background processing started"
    state["updated_at"] = now()

    save_state(state)

    start_worker()

    return redirect(url_for("index"))


@app.route("/pause", methods=["POST"])
def pause():

    state = load_state()

    state["status"] = "paused"
    state["message"] = "Processing paused"
    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():

    state = load_state()

    state["status"] = "stopped"
    state["message"] = "Processing stopped"
    state["updated_at"] = now()

    save_state(state)

    return redirect(url_for("index"))


@app.route("/clear", methods=["POST"])
def clear():

    save_state(default_state())

    return redirect(url_for("index"))


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    start_worker()

    app.run(
        host="0.0.0.0",
        port=port
    )
