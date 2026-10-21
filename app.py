import os
import time
import uuid
import requests
import threading
import logging
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global state tracker
server_state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": []
}

# 🔒 THREAD LOCK: Duplicate requests rokne ke liye
state_lock = threading.Lock()
stop_flag = False

def add_log(message):
    timestamp = time.strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {message}"
    server_state["logs"].append(log_msg)
    if len(server_state["logs"]) > 150:
        server_state["logs"].pop(0)
    print(log_msg)

def extract_csrf(cookies_str):
    # Regex se sahi token nikalega
    import re
    match = re.search(r'csrftoken=([^;]+)', cookies_str)
    return match.group(1).strip() if match else None

def send_insta_message(cookies_str, thread_id, text):
    csrf_token = extract_csrf(cookies_str)
    if not csrf_token:
        return False, "CSRF token not found in cookies"

    url = "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookies_str,
        "X-CSRFToken": csrf_token,
        "X-IG-App-ID": "936619743392459", # ✅ FB/Insta Web App ID (Zaroori hai)
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.instagram.com",
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/"
    }

    client_context = str(uuid.uuid4())
    payload = {
        "thread_ids": f"[{thread_id}]",
        "text": text,
        "client_context": client_context
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=15)
        
        # ✅ Safe JSON check (Agar HTML aaya toh crash nahi hoga)
        try:
            json_data = response.json()
            if response.status_code == 200 and json_data.get("status") == "ok":
                return True, "Success"
            else:
                return False, f"API Error: {json_data.get('message', 'Unknown')}"
        except ValueError:
            return False, f"Invalid JSON (Cookies Expired or Blocked). Code: {response.status_code}"
            
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

        # Here Name Format Fix
        full_message = f"{sender_name}\n{msg.strip()}"
        add_log(f"> Sending [{idx + 1}/{len(messages)}]: '{msg.strip()[:30]}...'")

        success, err_msg = send_insta_message(cookies, thread_id, full_message)

        if success:
            server_state["totalSent"] += 1
            add_log("> ✅ Sent successfully!")
        else:
            server_state["totalFailed"] += 1
            add_log(f"> ❌ Failed: {err_msg}")

        # ✅ Delay Fix (Stop dabane par turant rukega)
        if idx < len(messages) - 1 and not stop_flag:
            add_log(f"> ⏱️ Waiting {time_delay} seconds...")
            for _ in range(time_delay):
                if stop_flag: break
                time.sleep(1)

    server_state["isRunning"] = False
    add_log("> 🎉 Task finished.")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_process():
    global stop_flag
    
    # 🔒 LOCK CHECK
    with state_lock:
        if server_state["isRunning"]:
            return jsonify({"status": "error", "message": "Server is already running!"})
        server_state["isRunning"] = True

    try:
        cookies = request.form.get("cookies", "").strip()
        thread_id = request.form.get("thread_id", "").strip()
        time_delay = int(request.form.get("time_delay", 10))
        sender_name = request.form.get("sender_name", "").strip()
        file = request.files.get("message_file")

        if not cookies or not thread_id or not file:
            raise ValueError("All fields are required!")

        if time_delay < 5:
            raise ValueError("Time delay must be at least 5 seconds to avoid ban!")

        lines = file.read().decode("utf-8", errors="ignore").splitlines()
        messages = [line for line in lines if line.strip()]

        if not messages:
            raise ValueError("Message file is empty!")

        stop_flag = False
        thread = threading.Thread(
            target=worker_thread,
            args=(cookies, thread_id, time_delay, sender_name, messages)
        )
        thread.daemon = True
        thread.start()

        return jsonify({"status": "success", "message": "Started successfully"})
        
    except Exception as e:
        # Agar error aaya toh state reset karo
        with state_lock:
            server_state["isRunning"] = False
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/stop", methods=["POST"])
def stop_process():
    global stop_flag
    stop_flag = True
    return jsonify({"status": "success", "message": "Stopping..."})

@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(server_state)

if __name__ == "__main__":
    # ✅ RENDER PORT FIX
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
