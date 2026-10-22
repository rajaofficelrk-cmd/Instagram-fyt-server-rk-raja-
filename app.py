import os
import time
import threading
from flask import Flask, render_template, request, jsonify
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
import requests
import re

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

server_state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": []
}

state_lock = threading.RLock()
stop_flag = False

# ==========================================
# ⏰ KEEP-ALIVE SYSTEM (Render Sleep Fix)
# ==========================================
def keep_alive():
    # Render apna URL automatically environment variable me daal deta hai
    url = os.environ.get('RENDER_EXTERNAL_URL')
    if not url:
        print("[Keep-Alive] Local environment detected. Skipping.")
        return
    
    ping_url = f"{url}/ping"
    print(f"[Keep-Alive] Started. Pinging {ping_url} every 5 minutes.")
    
    while True:
        try:
            requests.get(ping_url, timeout=10)
        except Exception as e:
            pass
        time.sleep(300) # 300 seconds = 5 minutes

# Server start hote hi keep-alive thread chalu karein
threading.Thread(target=keep_alive, daemon=True).start()

# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================
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

def send_insta_message(cl, thread_id, text):
    try:
        cl.direct_send(text=text, thread_ids=[thread_id])
        return True, "Success"
    except LoginRequired:
        return False, "COOKIES EXPIRED: Please update your sessionid."
    except ClientError as e:
        err_msg = str(e)
        if "404" in err_msg:
            return False, "HTTP Error 404: Endpoint blocked or changed by Instagram."
        elif "429" in err_msg:
            return False, "RATE LIMIT: Too many requests! Increase Time Delay."
        return False, f"API Error: {err_msg}"
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

def worker_thread(cookies, thread_id, time_delay, sender_name, messages):
    global stop_flag
    
    with state_lock:
        server_state["totalSent"] = 0
        server_state["totalFailed"] = 0
        server_state["logs"] = []
    
    add_log("🚀 Background worker started (24/7 Mode)...")
    
    cl = Client()
    sessionid = extract_cookie_value(cookies, "sessionid")
    
    if not sessionid:
        add_log("❌ CRITICAL: Session ID nahi mila. Please provide valid cookies.")
        with state_lock:
            server_state["isRunning"] = False
        return

    try:
        add_log("🔐 Logging into Instagram via Session ID...")
        cl.login_by_sessionid(sessionid)
        add_log(f"✅ Login Successful! UID: {cl.user_id}")
    except Exception as e:
        add_log(f"❌ Login Failed: {str(e)}")
        with state_lock:
            server_state["isRunning"] = False
        return

    for idx, msg in enumerate(messages):
        if stop_flag:
            add_log("🛑 Execution stopped by user.")
            break

        clean_msg = msg.strip()
        if not clean_msg:
            continue

        full_message = f"{sender_name}\n{clean_msg}"
        add_log(f"Sending message [{idx + 1}/{len(messages)}]...")

        success, err_msg = send_insta_message(cl, thread_id, full_message)

        if success:
            with state_lock:
                server_state["totalSent"] += 1
            add_log(f"✅ Message {idx + 1} sent successfully!")
        else:
            with state_lock:
                server_state["totalFailed"] += 1
            add_log(f"❌ Failed {idx + 1}: {err_msg}")
            
            if "COOKIES EXPIRED" in err_msg or "RATE LIMIT" in err_msg or "404" in err_msg:
                add_log("⚠️ FATAL ERROR: Stopping process.")
                break

        if idx < len(messages) - 1 and not stop_flag:
            add_log(f"⏱️ Waiting {time_delay} seconds...")
            for _ in range(time_delay):
                if stop_flag: break
                time.sleep(1)

    with state_lock:
        server_state["isRunning"] = False
    add_log("🎉 Task finished.")

# ==========================================
# 🌐 ROUTES
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

# ✅ PING ROUTE (Ise UptimeRobot bhi use karega)
@app.route("/ping")
def ping():
    return "OK", 200

@app.route("/api/start", methods=["POST"])
def start_process():
    global stop_flag
    
    with state_lock:
        if server_state["isRunning"]:
            return jsonify({"status": "error", "message": "Server is already running!"})

    try:
        cookies = request.form.get("cookies", "").strip()
        thread_id = request.form.get("thread_id", "").strip()
        time_delay = int(request.form.get("time_delay", 20))
        sender_name = request.form.get("sender_name", "").strip()
        file = request.files.get("message_file")

        if not cookies or not thread_id or not file or not sender_name:
            return jsonify({"status": "error", "message": "All fields are required!"})

        if time_delay < 10:
            time_delay = 10

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
