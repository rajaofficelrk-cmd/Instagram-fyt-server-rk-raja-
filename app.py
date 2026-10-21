import os
import time
import uuid
import requests
import threading
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Global state tracker
server_state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": []
}

stop_flag = False

def add_log(message):
    server_state["logs"].append(message)
    # Keep only last 100 logs
    if len(server_state["logs"]) > 100:
        server_state["logs"].pop(0)

def extract_csrf(cookies_str):
    for item in cookies_str.split(";"):
        if "csrftoken" in item:
            return item.split("=")[1].strip()
    return None

def send_insta_message(cookies_str, thread_id, text):
    csrf_token = extract_csrf(cookies_str)
    if not csrf_token:
        return False, "CSRF token not found in cookies"

    url = "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookies_str,
        "X-CSRFToken": csrf_token,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.instagram.com",
        "Referer": "https://www.instagram.com/direct/inbox/"
    }

    client_context = str(uuid.uuid4())
    payload = {
        "thread_ids": f"[{thread_id}]",
        "text": text,
        "client_context": client_context
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=15)
        if response.status_code == 200 and response.json().get("status") == "ok":
            return True, "Success"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:100]}"
    except Exception as e:
        return False, str(e)

def worker_thread(cookies, thread_id, time_delay, sender_name, messages):
    global stop_flag
    server_state["isRunning"] = True
    server_state["totalSent"] = 0
    server_state["totalFailed"] = 0
    server_state["logs"] = []
    
    add_log("> 🚀 Server task started...")
    
    for idx, msg in enumerate(messages):
        if stop_flag:
            add_log("> 🛑 Execution stopped by user.")
            break

        full_message = f"{sender_name} : {msg.strip()}"
        add_log(f"> Sending [{idx + 1}/{len(messages)}]: '{full_message}'")

        success, err_msg = send_insta_message(cookies, thread_id, full_message)

        if success:
            server_state["totalSent"] += 1
            add_log("> ✅ Sent successfully!")
        else:
            server_state["totalFailed"] += 1
            add_log(f"> ❌ Failed: {err_msg}")

        if idx < len(messages) - 1 and not stop_flag:
            add_log(f"> ⏱️ Waiting {time_delay} seconds...")
            time.sleep(time_delay)

    server_state["isRunning"] = False
    add_log("> 🎉 Task finished.")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_process():
    global stop_flag
    if server_state["isRunning"]:
        return jsonify({"status": "error", "message": "Server is already running!"})

    cookies = request.form.get("cookies", "").strip()
    thread_id = request.form.get("thread_id", "").strip()
    time_delay = int(request.form.get("time_delay", 10))
    sender_name = request.form.get("sender_name", "").strip()
    file = request.files.get("message_file")

    if not cookies or not thread_id or not file:
        return jsonify({"status": "error", "message": "All fields are required!"})

    lines = file.read().decode("utf-8").splitlines()
    messages = [line for line in lines if line.strip()]

    if not messages:
        return jsonify({"status": "error", "message": "Message file is empty!"})

    stop_flag = False
    thread = threading.Thread(
        target=worker_thread,
        args=(cookies, thread_id, time_delay, sender_name, messages)
    )
    thread.daemon = True
    thread.start()

    return jsonify({"status": "success", "message": "Started successfully"})

@app.route("/api/stop", methods=["POST"])
def stop_process():
    global stop_flag
    stop_flag = True
    return jsonify({"status": "success", "message": "Stopping..."})

@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(server_state)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
