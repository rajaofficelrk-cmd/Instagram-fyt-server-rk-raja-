import csv
import json
import os
import threading
import time
import uuid
from datetime import datetime
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

STATE_FILE = "state.json"
UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

worker_lock = threading.Lock()
worker_running = False


def now():
    return datetime.utcnow().isoformat()


def default_state():
    return {
        "job_id": None,
        "status": "idle",
        "sender_id": "",
        "recipient_uid": "",
        "manual_message": "",
        "message_file": "",
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
            data = json.load(f)

        base = default_state()
        base.update(data)
        return base

    except Exception:
        return default_state()


def save_state(state):
    state["updated_at"] = now()

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=4)

    os.replace(temp_file, STATE_FILE)


def read_message_file(path):
    messages = []

    extension = os.path.splitext(path)[1].lower()

    if extension == ".txt":
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()

                if line:
                    messages.append({
                        "recipient_uid": "",
                        "message": line
                    })

    elif extension == ".csv":
        with open(
            path,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:
                recipient = (
                    row.get("recipient_uid")
                    or row.get("uid")
                    or row.get("recipient")
                    or ""
                ).strip()

                message = (
                    row.get("message")
                    or row.get("text")
                    or ""
                ).strip()

                if message:
                    messages.append({
                        "recipient_uid": recipient,
                        "message": message
                    })

    return messages


def process_item(item):
    """
    Generic authorized processing placeholder.

    Replace this function only with an API/action that you are
    authorized to automate.
    """

    time.sleep(2)

    return True


def background_worker():
    global worker_running

    while True:

        try:
            state = load_state()

            if state["status"] != "running":
                time.sleep(1)
                continue

            items = state.get("items", [])

            if state["processed"] >= len(items):
                state["status"] = "completed"
                state["message"] = "Job completed"
                save_state(state)
                continue

            index = state["processed"]
            item = items[index]

            try:
                success = process_item(item)

                if success:
                    state["success"] += 1
                else:
                    state["failed"] += 1

            except Exception:
                state["failed"] += 1

            state["processed"] += 1

            state["message"] = (
                f"Processing "
                f"{state['processed']}/{state['total']}"
            )

            save_state(state)

        except Exception as e:
            try:
                state = load_state()
                state["message"] = f"Worker error: {e}"
                save_state(state)
            except Exception:
                pass

        time.sleep(1)


def start_worker():
    global worker_running

    with worker_lock:

        if worker_running:
            return

        worker_running = True

        thread = threading.Thread(
            target=background_worker,
            daemon=True
        )

        thread.start()


@app.route("/")
def index():
    start_worker()
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "raja-server"
    })


@app.route("/api/status")
def api_status():
    return jsonify(load_state())


@app.route("/add", methods=["POST"])
def add_job():

    sender_id = request.form.get(
        "sender_id",
        ""
    ).strip()

    recipient_uid = request.form.get(
        "recipient_uid",
        ""
    ).strip()

    manual_message = request.form.get(
        "message",
        ""
    ).strip()

    message_file = request.files.get("message_file")

    items = []

    # Manual message
    if manual_message:

        items.append({
            "recipient_uid": recipient_uid,
            "message": manual_message
        })

    # Uploaded TXT / CSV
    if message_file and message_file.filename:

        filename = message_file.filename

        extension = os.path.splitext(
            filename
        )[1].lower()

        if extension not in [".txt", ".csv"]:
            return jsonify({
                "ok": False,
                "error": "Only TXT or CSV files are allowed"
            }), 400

        safe_name = (
            f"{uuid.uuid4().hex}_{filename}"
        )

        path = os.path.join(
            UPLOAD_DIR,
            safe_name
        )

        message_file.save(path)

        try:
            file_items = read_message_file(path)
            items.extend(file_items)

        except Exception as e:
            return jsonify({
                "ok": False,
                "error": f"File read error: {e}"
            }), 400

        saved_filename = filename

    else:
        saved_filename = ""

    if not items:

        return jsonify({
            "ok": False,
            "error": "Message ya message file add karo"
        }), 400

    state = default_state()

    state["job_id"] = uuid.uuid4().hex[:12]
    state["status"] = "ready"
    state["sender_id"] = sender_id
    state["recipient_uid"] = recipient_uid
    state["manual_message"] = manual_message
    state["message_file"] = saved_filename
    state["items"] = items
    state["total"] = len(items)
    state["message"] = "Job ready"
    state["created_at"] = now()

    save_state(state)
    start_worker()

    return jsonify({
        "ok": True,
        "message": "Job created",
        "job_id": state["job_id"],
        "total": state["total"]
    })


@app.route("/upload", methods=["POST"])
def upload_file():

    file = request.files.get("file")

    if not file or not file.filename:
        return jsonify({
            "ok": False,
            "error": "File select karo"
        }), 400

    extension = os.path.splitext(
        file.filename
    )[1].lower()

    if extension not in [".txt", ".csv"]:
        return jsonify({
            "ok": False,
            "error": "Only TXT or CSV files allowed"
        }), 400

    filename = (
        f"{uuid.uuid4().hex}_{file.filename}"
    )

    path = os.path.join(
        UPLOAD_DIR,
        filename
    )

    file.save(path)

    try:
        items = read_message_file(path)

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400

    state = load_state()

    state["message_file"] = file.filename
    state["items"] = items
    state["total"] = len(items)
    state["processed"] = 0
    state["success"] = 0
    state["failed"] = 0
    state["status"] = "ready"
    state["message"] = (
        f"{len(items)} messages loaded"
    )

    save_state(state)
    start_worker()

    return jsonify({
        "ok": True,
        "total": len(items)
    })


@app.route("/start", methods=["POST"])
def start():

    state = load_state()

    if not state["items"]:
        return jsonify({
            "ok": False,
            "error": "Pehle message add/upload karo"
        }), 400

    if state["processed"] >= state["total"]:
        return jsonify({
            "ok": False,
            "error": "Job already completed"
        }), 400

    state["status"] = "running"
    state["message"] = "Job started"

    save_state(state)
    start_worker()

    return jsonify({
        "ok": True
    })


@app.route("/pause", methods=["POST"])
def pause():

    state = load_state()

    if state["status"] == "running":
        state["status"] = "paused"
        state["message"] = "Job paused"
        save_state(state)

    return jsonify({
        "ok": True
    })


@app.route("/stop", methods=["POST"])
def stop():

    state = load_state()

    state["status"] = "stopped"
    state["message"] = "Job stopped"

    save_state(state)

    return jsonify({
        "ok": True
    })


@app.route("/clear", methods=["POST"])
def clear():

    state = default_state()
    state["message"] = "Cleared"

    save_state(state)

    return jsonify({
        "ok": True
    })


if __name__ == "__main__":

    start_worker()

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
