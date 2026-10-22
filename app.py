import os
import time
import uuid
import requests
import threading
import re
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global state tracker
server_state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": []
}

# 🔒 Lock for duplicate requests and state sync
state_lock = threading.Lock()
stop_flag = False

def add_log(message):
    timestamp = time.strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] > {message}"
    with state_lock:
        server_state["logs"].append(log_msg)
        if len(server_state["logs"]) > 150:
            server_state["logs"].pop(0)
    print(log_msg)

def extract_csrf(cookies_str):
    match = re.search(r'csrftoken=([^;]+)', cookies_str)
    return match.group(1).strip() if match else None

def send_insta_message(cookies_str, thread_id, text):
    csrf_token = extract_csrf(cookies_str)
    if not csrf_token:
        return False, "CSRF token not found in cookies"

    url = "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    
    # ✅ Real Browser Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Cookie": cookies_str,
        "X-CSRFToken": csrf_token,
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "129477",
        "X-IG-WWW-Claim": "0",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.instagram.com",
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    client_context = str(uuid.uuid4())
    payload = {
        "thread_ids": f"[{thread_id}]",
        "text": text,
        "client_context": client_context
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=15, allow_redirects=False)
        
        if response.status_code in [301, 302, 303, 307, 308]:
            return False, "Blocked! Render IP Blacklisted by Instagram."
            
        if response.status_code == 200:
            try:
                json_data = response.json()
                if json_data.get("status") == "ok":
                    return True, "Success"
                else:
                    return False, f"API Error: {json_data.get('message', 'Unknown')}"
            except ValueError:
                return False, "Invalid JSON (Cookies Expired or Blocked)"
        else:
            return False, f"HTTP Error: {response.status_code}"
            
    except Exception as e:
        return False, str(e)

def worker_thread(cookies, thread_id, time_delay, sender_name, messages):
    global stop_flag
    
    with state_lock:
        server_state["totalSent"] = 0
        server_state["totalFailed"] = 0
        server_state["logs"] = []
    
    add_log("🚀 Server task started...")
    
    for idx, msg in enumerate(messages):
        if stop_flag:
            add_log("🛑 Execution stopped by user.")
            break

        # Here Name Upar, Message Niche
        full_message = f"{sender_name}\n{msg.strip()}"
        add_log(f"Sending message [{idx + 1}/{len(messages)}]...")

        success, err_msg = send_insta_message(cookies, thread_id, full_message)

        with state_lock:
            if success:
                server_state["totalSent"] += 1
                add_log("✅ Sent successfully!")
            else:
                server_state["totalFailed"] += 1
                add_log(f"❌ Failed: {err_msg}")
        
        # Agar block mila toh aage mat bhejo
        if "Blocked" in err_msg:
            add_log("⚠️ Stopping process due to Block/Redirect.")
            break

        if idx < len(messages) - 1 and not stop_flag:
            add_log(f"⏱️ Waiting {time_delay} seconds...")
            for _ in range(time_delay):
                if stop_flag: break
                time.sleep(1)

    with state_lock:
        server_state["isRunning"] = False
    add_log("🎉 Task finished.")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_process():
    global stop_flag
    
    with state_lock:
        if server_state["isRunning"]:
            return jsonify({"status": "error", "message": "Server is already running!"})

    try:
        cookies = request.form.get("cookies", "").strip()
        thread_id = request.form.get("thread_id", "").strip()
        time_delay = int(request.form.get("time_delay", 10))
        sender_name = request.form.get("sender_name", "").strip()
        file = request.files.get("message_file")

        if not cookies or not thread_id or not file or not sender_name:
            raise ValueError("All fields are required!")

        if time_delay < 5:
            raise ValueError("Time delay must be at least 5 seconds!")

        lines = file.read().decode("utf-8", errors="ignore").splitlines()
        messages = [line for line in lines if line.strip()]

        if not messages:
            raise ValueError("Message file is empty!")

        # Sab check hone ke baad hi state ko True karein
        with state_lock:
            server_state["isRunning"] = True

        stop_flag = False
        thread = threading.Thread(
            target=worker_thread,
            args=(cookies, thread_id, time_delay, sender_name, messages)
        )
        thread.daemon = True
        thread.start()

        return jsonify({"status": "success", "message": "Started successfully"})
        
    except Exception as e:
        with state_lock:
            server_state["isRunning"] = False
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/stop", methods=["POST"])
def stop_process():
    global stop_flag
    stop_flag = True
    with state_lock:
        server_state["isRunning"] = False
    return jsonify({"status": "success", "message": "Stopping..."})

@app.route("/api/status", methods=["GET"])
def get_status():
    with state_lock:
        return jsonify(server_state)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
