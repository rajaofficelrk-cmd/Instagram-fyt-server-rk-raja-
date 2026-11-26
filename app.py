import os
import re
import time
import threading
import requests
import gc
import random
from flask import Flask, render_template, request, jsonify
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, PleaseWaitFewMinutes, ChallengeRequired,
    RateLimitError, ClientError, ClientForbiddenError, 
    ClientThrottledError, ClientLoginRequired, UserNotFound,
    MediaNotFound
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ---------------- GLOBAL STATE ----------------
state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": [],
    "stop_flag": False,
}
lock = threading.Lock()

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] > {msg}"
    with lock:
        state["logs"].append(line)
        if len(state["logs"]) > 500:
            state["logs"] = state["logs"][-500:]
    print(line)

# ---------------- SMART COOKIE PARSER ----------------
def parse_cookies_smart(raw_text):
    session_ids = re.findall(r'sessionid[="\':\s]+([a-zA-Z0-9%:_\-]+)', raw_text)
    ds_user_ids = re.findall(r'ds_user_id[="\':\s]+([0-9]+)', raw_text)
    csrf_tokens = re.findall(r'csrftoken[="\':\s]+([a-zA-Z0-9_\-]+)', raw_text)

    def unique(seq):
        seen = set()
        return [x for x in seq if not (x in seen or seen.add(x))]

    session_ids = unique(session_ids)
    ds_user_ids = unique(ds_user_ids)
    csrf_tokens = unique(csrf_tokens)

    if not session_ids:
        return []

    accounts = []
    for i in range(len(session_ids)):
        sid = session_ids[i]
        duid = ds_user_ids[i] if i < len(ds_user_ids) else ""
        csrf = csrf_tokens[i] if i < len(csrf_tokens) else ""
        
        c_str = f"sessionid={sid}"
        if duid: c_str += f"; ds_user_id={duid}"
        if csrf: c_str += f"; csrftoken={csrf}"
        accounts.append(c_str)
    
    return accounts

# ---------------- ROBUST CLIENT BUILDER ----------------
def make_client(cookie_str):
    cl = Client()
    cl.request_timeout = 20  # ⭐ Timeout 20s kar diya taaki login pe atke nahi
    cl.delay_range = [5, 15] 
    
    cl.set_settings({
        "user_agent": "Instagram 269.0.0.18.75 Android (32/12; 480dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458226540)",
        "locale": "en_US",
        "timezone_offset": 19800, 
    })
    return cl

# ---------------- ADVANCED LOGIN VALIDATOR ----------------
def login_and_validate(cl, cookie_str):
    match = re.search(r'sessionid=([^;]+)', cookie_str)
    if not match:
        return False, "Session ID missing"
    
    sessionid = match.group(1)
    try:
        ok = cl.login_by_sessionid(sessionid)
        if not ok:
            return False, "Invalid Session ID"
        return True, "OK"

    except ChallengeRequired:
        return False, "CHALLENGE REQUIRED"
    except PleaseWaitFewMinutes:
        return False, "RATE LIMITED"
    except RateLimitError:
        return False, "RATE LIMIT"
    except LoginRequired:
        return False, "COOKIES EXPIRED"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT — IP Blocked"
    except Exception as e:
        return False, f"ERROR: {str(e)[:100]}"

# ---------------- BULLETPROOF MESSAGE SENDER ----------------
def send_messages_from_client(cookie_line, thread_id, messages, delay, sender_name, idx):
    cl = None
    try:
        cl = make_client(cookie_line)
        
        if state["stop_flag"]: return # Stop check before login
        
        log(f"🔐 [ID {idx}] Logging in..")
        ok, reason = login_and_validate(cl, cookie_line)
        
        if not ok:
            log(f"❌ [ID {idx}] Login failed: {reason}")
            with lock: state["totalFailed"] += 1
            return

        log(f"✅ [ID {idx}] Logged in as @{cl.username}")

        for i, msg in enumerate(messages, 1):
            if state["stop_flag"]:
                log(f"🛑 [ID {idx}] Stop flag received. Exiting.")
                return
            
            final_msg = f"{sender_name} {msg}" if (sender_name and sender_name.strip()) else msg
            
            # Typing Simulation
            typing_time = min(len(final_msg) * 0.05, 10.0) 
            log(f"⌨️ [ID {idx}] Typing for {typing_time:.1f}s...")
            time.sleep(typing_time) 

            if state["stop_flag"]: return # Stop check before send

            max_retries = 3
            success = False
            
            for attempt in range(max_retries):
                try:
                    try:
                        cl.direct_send(final_msg, thread_ids=[int(thread_id)])
                    except Exception as e:
                        if "thread" in str(e).lower() or "not found" in str(e).lower():
                            cl.direct_send(final_msg, user_ids=[int(thread_id)])
                        else: raise e 
                    success = True
                    break 

                except (ClientForbiddenError, ClientThrottledError, PleaseWaitFewMinutes, RateLimitError) as e:
                    wait_time = (attempt + 1) * random.randint(15, 25) 
                    log(f"⚠️ [ID {idx}] Blocked. Waiting {wait_time}s... ({attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                except ClientLoginRequired:
                    log(f"🔄 [ID {idx}] Session dropped. Re-logging...")
                    ok, _ = login_and_validate(cl, cookie_line)
                    if not ok: break
                except Exception as e:
                    log(f"❌ [ID {idx}] Attempt {attempt+1} error: {str(e)[:60]}")
                    time.sleep(10) 

            if success:
                with lock: state["totalSent"] += 1
                log(f"✅ [ID {idx}] Sent {i}/{len(messages)}: {final_msg[:30]}...")
            else:
                with lock: state["totalFailed"] += 1
                log(f"❌ [ID {idx}] Failed {i}/{len(messages)}.")
                if i == 1: break # Agar pehla hi fail ho toh account rok do
            
            # Delay between messages
            for _ in range(delay):
                if state["stop_flag"]: break
                time.sleep(1)

        log(f"🎉 [ID {idx}] Task completed.")

    except Exception as e:
        log(f"❌ [ID {idx}] Crashed: {str(e)[:100]}")
        with lock: state["totalFailed"] += 1
    finally:
        if cl: del cl
        gc.collect()
        log(f"🧹 [ID {idx}] Memory freed.")

# ---------------- RUNNERS ----------------
def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting PARALLEL Mode (Multiple IDs at once)...")
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        t = threading.Thread(target=send_messages_from_client, args=(line, thread_id, messages, delay, sender_name, idx), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2) 
    for t in threads: t.join()
    finalize()

def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting SEQUENTIAL Mode (One by One)...")
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        send_messages_from_client(line, thread_id, messages, delay, sender_name, idx)
    finalize()

def finalize():
    with lock:
        state["isRunning"] = False
        state["stop_flag"] = False
    log("🎉 All jobs finished.")

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

@app.route("/api/start", methods=["POST"])
def start():
    with lock:
        if state["isRunning"]:
            return jsonify({"status": "error", "message": "Task already running!"}), 400
        # Reset state for new run
        state["isRunning"] = True
        state["stop_flag"] = False
        state["totalSent"] = 0
        state["totalFailed"] = 0
        state["logs"] = []

    try:
        cookies_raw = request.form.get("cookies", "").strip()
        run_mode = request.form.get("run_mode", "parallel") # Default parallel
        thread_id = request.form.get("thread_id", "").strip()
        delay = int(request.form.get("time_delay", 20)) 
        sender_name = request.form.get("sender_name", "").strip()

        message_file = request.files.get("message_file")
        if not message_file: raise ValueError("Message file missing")

        messages = [line.strip() for line in message_file.read().decode("utf-8", errors="ignore").splitlines() if line.strip()]
        if not messages: raise ValueError("Message file is empty")

        cookie_lines = parse_cookies_smart(cookies_raw)
        if not cookie_lines: raise ValueError("No valid 'sessionid' found!")

        if not thread_id.isdigit(): raise ValueError("Invalid Thread ID. Only numbers allowed.")

        log(f"📁 Found {len(cookie_lines)} ID(s). Mode: {run_mode.upper()}")

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(target=target, args=(cookie_lines, thread_id, messages, delay, sender_name), daemon=True).start()

        return jsonify({"status": "success", "message": f"Started with {len(cookie_lines)} IDs"})

    except Exception as e:
        with lock: state["isRunning"] = False
        log(f"❌ Start Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/stop", methods=["POST"])
def stop():
    with lock:
        state["stop_flag"] = True
        state["isRunning"] = False # ⭐ YAHAN FIX HUA HAI: Turant UI unlock karega
        
    log("🛑 Force stop triggered! Background threads will die shortly.")
    return jsonify({"status": "success", "message": "Stopped immediately."})

@app.route("/api/status")
def status():
    with lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "totalSent": state["totalSent"],
            "totalFailed": state["totalFailed"],
            "logs": state["logs"][-150:], 
        })

# ---------------- RENDER KEEP ALIVE ----------------
def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url: return
    url = url.replace("http://", "https://") + "/health"
    while True:
        try: requests.get(url, timeout=15)
        except Exception: pass 
        time.sleep(240) 

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
