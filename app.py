import os
import time
import uuid
import requests
import threading
import re
from flask import Flask, render_template, request, jsonify

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

# ✅ CRITICAL FIX: Use RLock instead of Lock to prevent Deadlocks (Server Freeze)
state_lock = threading.RLock()
stop_flag = False

def add_log(message):
    timestamp = time.strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] > {message}"
    with state_lock:
        server_state["logs"].append(log_msg)
        if len(server_state["logs"]) > 150:
            server_state["logs"].pop(0)
    print(log_msg)

def extract_cookie_value(cookies_str, key):
    match = re.search(rf'{key}=([^;]+)', cookies_str)
    return match.group(1).strip() if match else None

def send_insta_message(cookies_str, thread_id, text):
    csrf_token = extract_cookie_value(cookies_str, "csrftoken")
    if not csrf_token:
        return False, "CSRF token missing in cookies!"

    # ✅ FIX: Mobile API Endpoint (i.instagram.com) instead of www.instagram.com
    url = "https://i.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    
    # ✅ FIX: Real Mobile App Headers
    headers = {
        "User-Agent": "Instagram 269.0.0.18.75 Android (28/9; 480dpi; 1080x1920; samsung; SM-G950F; dreamlte; samsungexynos8895; en_US; 312395952)",
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
        "action": "send_item",
        "client_context": client_context
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=15, allow_redirects=False)
        
        # If redirected, it's 100% an IP block or expired cookies
        if response.status_code in [301, 302, 303, 307, 308]:
            return False, "BLOCKED: Instagram redirect to Login. (Render IP Blacklisted or Expired Cookies)"
            
        # Handle JSON errors safely
        try:
            json_data = response.json()
        except ValueError:
            if response.status_code == 429:
                return False, "RATE LIMIT: Too many requests! Increase Time Delay."
            return False, f"HTTP Error {response.status_code}: Invalid response from IG."

        if response.status_code == 200 and json_data.get("status") == "ok":
            return True, "Success"
        else:
            error_msg = json_data.get("message") or json_data.get("error_type") or "Unknown Error"
            if "login_required" in error_msg or "checkpoint" in error_msg:
                return False, f"COOKIES EXPIRED: {error_msg}"
            return False, f"API Error: {error_msg}"
            
    except requests.exceptions.Timeout:
        return False, "TIMEOUT: Instagram took too long to respond."
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

def worker_thread(cookies, thread_id, time_delay, sender_name, messages):
    global stop_flag
    
    with state_lock:
        server_state["totalSent"] = 0
        server_state["totalFailed"] = 0
        server_state["logs"] = []
    
    add_log("🚀 Background worker started...")
    
    for idx, msg in enumerate(messages):
        if stop_flag:
            add_log("🛑 Execution stopped by user.")
            break

        clean_msg = msg.strip()
        if not clean_msg:
            continue

        # Here Name Upar, Message Niche
        full_message = f"{sender_name}\n{clean_msg}"
        add_log(f"Sending message [{idx + 1}/{len(messages)}]...")

        success, err_msg = send_insta_message(cookies, thread_id, full_message)

        if success:
            with state_lock:
                server_state["totalSent"] += 1
            add_log(f"✅ Message {idx + 1} sent successfully!")
        else:
            with state_lock:
                server_state["totalFailed"] += 1
            add_log(f"❌ Failed {idx + 1}: {err_msg}")
            
            # If fatal error, break immediately to save time
            if "BLOCKED" in err_msg or "COOKIES EXPIRED" in err_msg or "RATE LIMIT" in err_msg:
                add_log("⚠️ FATAL ERROR: Stopping process. Fix Cookies/IP to continue.")
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
        time_delay = int(request.form.get("time_delay", 15))
        sender_name = request.form.get("sender_name", "").strip()
        file = request.files.get("message_file")

        if not cookies or not thread_id or not file or not sender_name:
            return jsonify({"status": "error", "message": "All fields are required!"})

        if time_delay < 5:
            time_delay = 5

        # 🧩 UNIVERSAL FILE DECODER (Chahe jo file ho, sab chalegi)
        file_bytes = file.read()
        file_content = ""
        for encoding in ['utf-8', 'latin-1', 'cp1252', 'utf-16']:
            try:
                file_content = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if not file_content:
            file_content = file_bytes.decode('utf-8', errors='ignore')

        messages = [line.strip() for line in file_content.splitlines() if line.strip()]

        if not messages:
            return jsonify({"status": "error", "message": "Uploaded text file is empty!"})

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
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"})

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
