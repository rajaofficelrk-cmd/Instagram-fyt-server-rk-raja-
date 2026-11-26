import os
import re
import time
import threading
import requests
import gc
import random
import traceback
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
    "start_time": 0,
    "active_accounts": {} # Admin Dashboard ke liye live tracking
}
state_lock = threading.Lock()

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] > {msg}"
    with state_lock:
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

    if not session_ids: return []

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

# ---------------- SPINTAX PARSER ----------------
def parse_spintax(text):
    def replace(match):
        options = match.group(1).split('|')
        return random.choice(options)
    for _ in range(3):
        text = re.sub(r'\{([^{}]+)\}', replace, text)
    return text

# ---------------- CLIENT BUILDER ----------------
def make_client(cookie_str):
    cl = Client()
    cl.request_timeout = 30
    cl.delay_range = [3, 8] 
    cl.set_settings({
        "user_agent": "Instagram 269.0.0.18.75 Android (32/12; 480dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458226540)",
        "locale": "en_US",
        "timezone_offset": 19800, 
    })
    return cl

# ---------------- LOGIN VALIDATOR ----------------
def login_and_validate(cl, cookie_str):
    match = re.search(r'sessionid=([^;]+)', cookie_str)
    if not match: return False, "Session ID missing"
    
    sessionid = match.group(1)
    try:
        ok = cl.login_by_sessionid(sessionid)
        if not ok: return False, "Invalid Session ID"
        return True, "OK"
    except ChallengeRequired: return False, "CHALLENGE REQUIRED"
    except PleaseWaitFewMinutes: return False, "RATE LIMITED"
    except RateLimitError: return False, "RATE LIMIT"
    except LoginRequired: return False, "COOKIES EXPIRED"
    except Exception as e: return False, str(e)[:50]

# ---------------- MESSAGE SENDER WORKER ----------------
def send_messages_worker(cookie_line, thread_id, messages, delay, sender_name, idx):
    cl = None
    acc_key = f"ID_{idx}"
    
    with state_lock:
        state["active_accounts"][acc_key] = {"name": f"ID {idx}", "uid": "Loading...", "status": "Logging in...", "sent": 0, "failed": 0}

    try:
        cl = make_client(cookie_line)
        if state["stop_flag"]: return
        
        ok, reason = login_and_validate(cl, cookie_line)
        
        if not ok:
            log(f"[ID {idx}] ❌ Login failed: {reason}")
            with state_lock:
                state["totalFailed"] += 1
                state["active_accounts"][acc_key]["status"] = f"Failed: {reason}"
                state["active_accounts"][acc_key]["failed"] += 1
            return

        # Update Admin Dashboard
        with state_lock:
            state["active_accounts"][acc_key]["name"] = f"@{cl.username}"
            state["active_accounts"][acc_key]["uid"] = cl.user_id
            state["active_accounts"][acc_key]["status"] = "Active (Sending)"

        log(f"[ID {idx}] ✅ Logged in as @{cl.username}")

        for i, raw_msg in enumerate(messages, 1):
            if state["stop_flag"]:
                with state_lock: state["active_accounts"][acc_key]["status"] = "Stopped"
                break
            
            final_msg = f"{sender_name} {parse_spintax(raw_msg)}" if (sender_name and sender_name.strip()) else parse_spintax(raw_msg)
            
            # Typing Simulation
            typing_time = min(len(final_msg) * 0.06, 12.0) 
            with state_lock: state["active_accounts"][acc_key]["status"] = f"Typing ({typing_time:.1f}s)..."
            for _ in range(int(typing_time)):
                if state["stop_flag"]: break
                time.sleep(1)
                
            if state["stop_flag"]: break

            success = False
            for attempt in range(3):
                try:
                    with state_lock: state["active_accounts"][acc_key]["status"] = f"Sending msg {i}..."
                    try:
                        cl.direct_send(final_msg, thread_ids=[int(thread_id)])
                    except Exception as e:
                        if "thread" in str(e).lower() or "not found" in str(e).lower():
                            cl.direct_send(final_msg, user_ids=[int(thread_id)])
                        else: raise e 
                    success = True
                    break 
                    
                except ClientError as e:
                    if "We're sorry" in str(e) or "something went wrong" in str(e):
                        log(f"[ID {idx}] 🚨 Instagram Block: 'We're sorry' error. Stopping this account.")
                        with state_lock: state["active_accounts"][acc_key]["status"] = "Blocked by IG"
                        break # Break retry loop, move to next account
                    else:
                        time.sleep(15)

                except Exception as e:
                    time.sleep(10) 

            if success:
                with state_lock:
                    state["totalSent"] += 1
                    state["active_accounts"][acc_key]["sent"] += 1
                    state["active_accounts"][acc_key]["status"] = f"Waiting {delay}s..."
                log(f"[ID {idx}] ✅ Sent {i}/{len(messages)}: {final_msg[:30]}...")
            else:
                with state_lock:
                    state["totalFailed"] += 1
                    state["active_accounts"][acc_key]["failed"] += 1
                    if state["active_accounts"][acc_key]["status"] != "Blocked by IG":
                        state["active_accounts"][acc_key]["status"] = "Message Failed"
                
            for _ in range(delay):
                if state["stop_flag"]: break
                time.sleep(1)

        with state_lock: state["active_accounts"][acc_key]["status"] = "Completed"

    except Exception as e:
        with state_lock: state["active_accounts"][acc_key]["status"] = "Crashed"
    finally:
        if cl: del cl
        gc.collect()

def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        t = threading.Thread(target=send_messages_worker, args=(line, thread_id, messages, delay, sender_name, idx), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2) 
    for t in threads: t.join()
    finalize()

def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        send_messages_worker(line, thread_id, messages, delay, sender_name, idx)
    finalize()

def finalize():
    with state_lock:
        state["isRunning"] = False
        state["stop_flag"] = False
    log("🎉 All jobs finished.")

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin")
def admin():
    # Admin Login Check
    auth = request.authorization
    if not auth or auth.username != "rkraja70" or auth.password != "pra.chi_sgarma200":
        return "Access Denied. Please provide correct Username and Password.", 401, {'WWW-Authenticate': 'Basic realm="Admin Login"'}
    return render_template("admin.html")

@app.route("/api/admin_status")
def admin_status():
    with state_lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "active_accounts": state["active_accounts"],
            "logs": state["logs"][-50:]
        })

@app.route("/health")
def health(): return jsonify({"status": "ok"}), 200

@app.route("/api/start", methods=["POST"])
def start():
    with state_lock:
        if state["isRunning"]: return jsonify({"status": "error", "message": "Already running!"}), 400
        state["isRunning"] = True
        state["stop_flag"] = False
        state["totalSent"] = 0
        state["totalFailed"] = 0
        state["logs"] = []
        state["active_accounts"] = {}

    try:
        cookies_raw = request.form.get("cookies", "").strip()
        run_mode = request.form.get("run_mode", "parallel")
        thread_id = request.form.get("thread_id", "").strip()
        delay = int(request.form.get("time_delay", 30)) 
        sender_name = request.form.get("sender_name", "").strip()

        message_file = request.files.get("message_file")
        messages = [line.strip() for line in message_file.read().decode("utf-8", errors="ignore").splitlines() if line.strip()]
        cookie_lines = parse_cookies_smart(cookies_raw)

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(target=target, args=(cookie_lines, thread_id, messages, delay, sender_name), daemon=True).start()

        return jsonify({"status": "success", "message": "Started"})
    except Exception as e:
        with state_lock: state["isRunning"] = False
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/stop", methods=["POST"])
def stop():
    with state_lock:
        state["stop_flag"] = True
        state["isRunning"] = False
    return jsonify({"status": "success", "message": "Stopping..."})

@app.route("/api/status")
def status():
    with state_lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "totalSent": state["totalSent"],
            "totalFailed": state["totalFailed"],
            "logs": state["logs"][-150:], 
        })

def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        url = url.replace("http://", "https://") + "/health"
        while True:
            try: requests.get(url, timeout=15)
            except: pass 
            time.sleep(240) 

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
