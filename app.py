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

def extract_cookie_value(cookies_str, key):
    match = re.search(rf'{key}=([^;]+)', cookies_str)
    return match.group(1).strip() if match else None

def send_insta_message(cookies_str, thread_id, text):
    csrf_token = extract_cookie_value(cookies_str, "csrftoken")
    if not csrf_token:
        return False, "CSRF token missing in cookies!"

    url = "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    
    # Real Desktop Chrome Headers
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
        "Accept-Language": "en-US,en;q=0.9"
    }

    client_context = str(uuid.uuid4())
    
    # Instagram Web Broadcast Payload format
    payload = {
        "thread_ids": f"[{thread_id}]",
        "text": text,
        "action": "send_item",
        "client_context": client_context
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=20, allow_redirects=False)
        
        # Check for IP block or redirect
        if response.status_code in [301, 302, 303, 307, 308]:
            return False, "Blocked! Instagram redirected the request (IP/Account check)."
            
        # Try parsing JSON response
        try:
            json_data = response.json()
        except Exception:
            return False, f"Invalid Server Response (HTTP {response.status_code}): {response.text[:150]}"

        if response.status_code == 200 and json_data.get("status") == "ok":
            return True, "Success"
        else:
            # Print exact instagram error message
            error_msg = json_data.get("message") or json_data.get("error_type") or str(json_data)
            return False, f"Instagram API Error: {error_msg}"
            
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

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
        
        if "Blocked" in err_msg or "login_required" in err_msg:
            add_log("⚠️ Stopping process due to Instagram security block.")
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
    print("🚀 Starting Local Server on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
