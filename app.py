import os
import time
import threading
import concurrent.futures
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
# ⏰ 24/7 KEEP-ALIVE SYSTEM (Render Sleep Fix)
# ==========================================
def keep_alive():
    url = os.environ.get('RENDER_EXTERNAL_URL')
    if not url:
        return
    ping_url = f"{url}/ping"
    while True:
        try:
            requests.get(ping_url, timeout=10)
        except Exception:
            pass
        time.sleep(300) # Har 5 minute mein ping karega

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

# ✅ Safe Login with Timeout (Taaki atke nahi)
def safe_login(cl, sessionid):
    def _login():
        cl.login_by_sessionid(sessionid)
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_login)
        try:
            future.result(timeout=45) # 45 seconds timeout
            return True, "Success"
        except concurrent.futures.TimeoutError:
            return False, "TIMEOUT: Instagram login took too long. (IP Blocked)"
        except Exception as e:
            return False, str(e)

def send_insta_message(cl, thread_id, text):
    try:
        cl.direct_send(text=text, thread_ids=[thread_id])
        return True, "Success"
    except LoginRequired:
        return False, "COOKIES EXPIRED"
    except ClientError as e:
        return False, f"API Error: {str(e)}"
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

# ==========================================
# ⚙️ WORKER (Infinite Loop Mode)
# ==========================================
def worker_thread(cookies, thread_id, time_delay, sender_name, messages):
    global stop_flag
    
    with state_lock:
        server_state["totalSent"] = 0
        server_state["totalFailed"] = 0
        server_state["logs"] = []
    
    add_log("🚀 Background worker started (24/7 Non-Stop)...")
    
    cl = Client()
    cl.request_timeout = 30
    cl.read_timeout = 30

    sessionid = extract_cookie_value(cookies, "sessionid")
    if not sessionid:
        add_log("❌ CRITICAL: Session ID nahi mila.")
        with state_lock:
            server_state["isRunning"] = False
        return

    add_log("🔐 Logging into Instagram...")
    success, msg = safe_login(cl, sessionid)
    
    if not success:
        add_log(f"❌ Login Failed: {msg}")
        with state_lock:
            server_state["isRunning"] = False
        return

    add_log(f"✅ Login Successful! UID: {cl.user_id}")

    # ✅ INFINITE LOOP (Jab tak stop nahi hoga)
    loop_count = 1
    while not stop_flag:
        add_log(f"🔄 Starting File Loop #{loop_count}...")
        
        for msg in messages:
            if stop_flag:
                break
                
            clean_msg = msg.strip()
            if not clean_msg:
                continue

            # Format: Here Name (Upar) + Message (Niche)
            full_message = f"{sender_name}\n{clean_msg}"
            
            with state_lock:
                current_sent = server_state["totalSent"] + server_state["totalFailed"] + 1
                
            add_log(f"Sending message [{current_sent}]...")

            success, err_msg = send_insta_message(cl, thread_id, full_message)

            if success:
                with state_lock:
                    server_state["totalSent"] += 1
                add_log(f"✅ Message {current_sent} sent successfully!")
            else:
                with state_lock:
                    server_state["totalFailed"] += 1
                add_log(f"❌ Failed {current_sent}: {err_msg}")
                
                # Agar fatal error ho toh ruk jao
                if "COOKIES EXPIRED" in err_msg:
                    add_log("⚠️ FATAL: Cookies expired. Stopping.")
                    stop_flag = True
                    break

            # Delay
            if not stop_flag:
                add_log(f"⏱️ Waiting {time_delay} seconds...")
                for _ in range(time_delay):
                    if stop_flag: break
                    time.sleep(1)
        
        loop_count += 1

    with state_lock:
        server_state["isRunning"] = False
    add_log("🎉 Task stopped by user.")

# ==========================================
# 🌐 ROUTES
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

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
