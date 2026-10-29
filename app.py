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
    url = os.environ.get('RENDER_EXTERNAL_URL')
    if not url:
        print("[Keep-Alive] Local environment detected. Skipping.")
        return
    
    ping_url = f"{url}/ping"
    print(f"[Keep-Alive] Started. Pinging {ping_url} every 5 minutes.")
    
    while True:
        try:
            requests.get(ping_url, timeout=10)
        except Exception:
            pass
        time.sleep(300)

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
        return False, "COOKIES EXPIRED"
    except ClientError as e:
        err_msg = str(e)
        if "404" in err_msg:
            return False, "HTTP Error 404"
        elif "429" in err_msg:
            return False, "RATE LIMIT"
        return False, f"API Error: {err_msg}"
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

# ==========================================
# ⚙️ WORKER THREADS (Sequential & Parallel)
# ==========================================

def run_sequential(cookie_lines, thread_id, time_delay, sender_name, messages):
    """Ek ID poori file bhejegi, fir dusri ID shuru hogi."""
    add_log("🚀 Starting SEQUENTIAL Mode (Ek ke baad ek)...")
    loop_count = 1
    
    while not stop_flag:
        add_log(f"🔄 Starting Round #{loop_count}")
        
        for i, cookies in enumerate(cookie_lines):
            if stop_flag: break
            acc_num = i + 1
            sessionid = extract_cookie_value(cookies, "sessionid")
            if not sessionid:
                add_log(f"⚠️ [ID {acc_num}] Session ID missing. Skip.")
                continue

            cl = Client()
            try:
                add_log(f"🔐 [ID {acc_num}] Logging in...")
                cl.login_by_sessionid(sessionid)
                add_log(f"✅ [ID {acc_num}] Login Successful (UID: {cl.user_id})")
            except Exception as e:
                add_log(f"❌ [ID {acc_num}] Login Failed: {str(e)}")
                continue

            for idx, msg in enumerate(messages):
                if stop_flag: break
                clean_msg = msg.strip()
                if not clean_msg: continue

                full_message = f"{sender_name}\n{clean_msg}"
                
                with state_lock:
                    total_processed = server_state["totalSent"] + server_state["totalFailed"] + 1
                    
                add_log(f"[ID {acc_num}] Sending msg [{total_processed}]...")
                success, err_msg = send_insta_message(cl, thread_id, full_message)

                if success:
                    with state_lock: server_state["totalSent"] += 1
                    add_log(f"✅ [ID {acc_num}] Sent successfully!")
                else:
                    with state_lock: server_state["totalFailed"] += 1
                    add_log(f"❌ [ID {acc_num}] Failed: {err_msg}")
                    
                    if "COOKIES EXPIRED" in err_msg or "RATE LIMIT" in err_msg:
                        add_log(f"⚠️ [ID {acc_num}] Stopping this ID.")
                        break

                # Delay between messages
                if not stop_flag:
                    for _ in range(time_delay):
                        if stop_flag: break
                        time.sleep(1)
            
            add_log(f"⏳ [ID {acc_num}] Completed file. Moving to next...")
            time.sleep(3) # Gap between switching IDs
            
        loop_count += 1


def run_parallel(cookie_lines, thread_id, time_delay, sender_name, messages):
    """Saari IDs ek saath messages bhejengi."""
    add_log("🚀 Starting PARALLEL Mode (Ek Sath)...")
    
    def single_id_worker(cookies, acc_num):
        sessionid = extract_cookie_value(cookies, "sessionid")
        if not sessionid: return
        
        cl = Client()
        try:
            add_log(f"🔐 [ID {acc_num}] Logging in...")
            cl.login_by_sessionid(sessionid)
            add_log(f"✅ [ID {acc_num}] Login Successful (UID: {cl.user_id})")
        except Exception as e:
            add_log(f"❌ [ID {acc_num}] Login Failed: {str(e)}")
            return

        loop_count = 1
        while not stop_flag:
            add_log(f"🔄 [ID {acc_num}] Round #{loop_count}")
            for msg in messages:
                if stop_flag: break
                clean_msg = msg.strip()
                if not clean_msg: continue

                full_message = f"{sender_name}\n{clean_msg}"
                
                with state_lock:
                    total_processed = server_state["totalSent"] + server_state["totalFailed"] + 1
                    
                add_log(f"[ID {acc_num}] Sending msg [{total_processed}]...")
                success, err_msg = send_insta_message(cl, thread_id, full_message)

                if success:
                    with state_lock: server_state["totalSent"] += 1
                    add_log(f"✅ [ID {acc_num}] Sent successfully!")
                else:
                    with state_lock: server_state["totalFailed"] += 1
                    add_log(f"❌ [ID {acc_num}] Failed: {err_msg}")
                    
                    if "COOKIES EXPIRED" in err_msg or "RATE LIMIT" in err_msg:
                        add_log(f"⚠️ [ID {acc_num}] Stopping this ID.")
                        return

                if not stop_flag:
                    for _ in range(time_delay):
                        if stop_flag: break
                        time.sleep(1)
            loop_count += 1

    threads = []
    for i, cookies in enumerate(cookie_lines):
        t = threading.Thread(target=single_id_worker, args=(cookies, i+1))
        t.daemon = True
        t.start()
        threads.append(t)
        time.sleep(3) # Gap so all accounts don't login at the exact same millisecond
        
    for t in threads:
        t.join()


# ==========================================
# 🧠 MASTER WORKER
# ==========================================
def worker_thread(cookies_raw, thread_id, time_delay, sender_name, messages, run_mode):
    global stop_flag
    
    with state_lock:
        server_state["totalSent"] = 0
        server_state["totalFailed"] = 0
        server_state["logs"] = []
    
    # Split cookies into lines
    cookie_lines = [line.strip() for line in cookies_raw.split('\n') if line.strip()]
    
    if not cookie_lines:
        add_log("❌ CRITICAL: Koi valid Cookies nahi mile.")
        with state_lock: server_state["isRunning"] = False
        return

    add_log(f"📁 Total {len(cookie_lines)} IDs found. Mode: {run_mode.upper()}")

    if run_mode == 'sequential':
        run_sequential(cookie_lines, thread_id, time_delay, sender_name, messages)
    else:
        run_parallel(cookie_lines, thread_id, time_delay, sender_name, messages)

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
        run_mode = request.form.get("run_mode", "sequential").strip()
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
            args=(cookies, thread_id, time_delay, sender_name, messages, run_mode)
        )
        thread.daemon = True
        thread.start()

        return jsonify({"status": "success", "message": f"Started successfully in {run_mode} mode!"})
        
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
