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
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # Max 16MB file upload

# ==========================================
# GLOBAL STATE & THREAD MANAGEMENT
# ==========================================
state = {
    "isRunning": False,
    "totalSent": 0,
    "totalFailed": 0,
    "logs": [],
    "stop_flag": False,
    "active_accounts": {},
    "target_thread": "",
    "message_file": "",
    "gc_users": [],
    "start_time": 0
}
state_lock = threading.Lock()

def log(msg, level="info"):
    """Central Logging System with Timestamps"""
    ts = time.strftime("%H:%M:%S")
    
    if level == "error" or "❌" in msg or "⚠️" in msg:
        prefix = "❌"
    elif level == "success" or "✅" in msg or "🎉" in msg:
        prefix = "✅"
    else:
        prefix = ">"

    line = f"[{ts}] {prefix} {msg}"
    
    with state_lock:
        state["logs"].append(line)
        if len(state["logs"]) > 500:
            state["logs"] = state["logs"][-500:]
    print(line)

# ==========================================
# SMART PARSERS (Cookies & Spintax)
# ==========================================
def parse_cookies_smart(raw_text):
    """Extracts cookies from Netscape, JSON or Text format"""
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

def parse_spintax(text):
    """
    Advanced Spintax Parser.
    Example: {Hi|Hello|Hey} bro, how are you?
    Will randomly pick 'Hi', 'Hello', or 'Hey'
    """
    def replace(match):
        options = match.group(1).split('|')
        return random.choice(options)
    
    for _ in range(3):
        text = re.sub(r'\{([^{}]+)\}', replace, text)
    return text

# ==========================================
# INSTAGRAM CLIENT BUILDER
# ==========================================
def make_client(cookie_str):
    cl = Client()
    cl.request_timeout = 45  # Timeout for slow networks
    
    # Anti-ban: Random delay between API requests
    cl.delay_range = [3, 8] 
    
    # Spoofing as a real Samsung device
    cl.set_settings({
        "user_agent": "Instagram 269.0.0.18.75 Android (32/12; 480dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458226540)",
        "locale": "en_US",
        "timezone_offset": 19800, # India timezone
    })
    
    # --- PROXY SUPPORT ---
    # Agar IP block ho jaye toh niche wali line uncomment karke proxy daalein
    # proxy_url = os.environ.get("PROXY_URL") 
    # if proxy_url: cl.set_proxy(proxy_url)
    
    return cl

# ==========================================
# LOGIN VALIDATION ENGINE
# ==========================================
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
    except ClientForbiddenError:
        return False, "FORBIDDEN (IP Block)"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT (Network Slow)"
    except Exception as e:
        return False, f"UNKNOWN: {str(e)[:80]}"

# ==========================================
# CORE MESSAGE SENDER WORKER
# ==========================================
def send_messages_worker(cookie_line, thread_id, messages, delay, sender_name, idx):
    cl = None
    acc_key = f"ID_{idx}"
    
    with state_lock:
        state["active_accounts"][acc_key] = {
            "name": f"ID {idx}", "uid": "Loading...", "status": "Logging in...", "sent": 0, "failed": 0
        }
    
    log(f"[ID {idx}] 🔐 Logging in... (Wait 10-15s)")
    
    try:
        cl = make_client(cookie_line)
        if state["stop_flag"]: return
        
        ok, reason = login_and_validate(cl, cookie_line)
        
        if not ok:
            log(f"[ID {idx}] ❌ Login failed: {reason}", "error")
            with state_lock:
                state["totalFailed"] += 1
                state["active_accounts"][acc_key]["status"] = f"Failed: {reason}"
            return

        # Fetch GC Info for Admin Panel
        gc_users_list = []
        try:
            if thread_id.isdigit():
                thread_info = cl.direct_thread(int(thread_id))
                for user in thread_info.users:
                    gc_users_list.append(f"@{user.username}")
                with state_lock: state["gc_users"] = gc_users_list
        except Exception:
            with state_lock: state["gc_users"] = ["DM (Single User / ID Invalid)"]

        with state_lock:
            state["active_accounts"][acc_key]["name"] = f"@{cl.username}"
            state["active_accounts"][acc_key]["uid"] = cl.user_id
            state["active_accounts"][acc_key]["status"] = "Active (Sending)"

        log(f"[ID {idx}] ✅ Logged in as @{cl.username}", "success")

        for i, raw_msg in enumerate(messages, 1):
            if state["stop_flag"]:
                with state_lock: state["active_accounts"][acc_key]["status"] = "Stopped by User"
                break
            
            # 1. Apply Spintax (Randomize text)
            msg_spintax = parse_spintax(raw_msg)
            
            # 2. Apply Here Name (Prefix)
            final_msg = f"{sender_name} {msg_spintax}" if (sender_name and sender_name.strip()) else msg_spintax
            
            # 3. Typing Simulation
            typing_time = min(len(final_msg) * 0.06, 12.0) 
            with state_lock: state["active_accounts"][acc_key]["status"] = f"Typing ({typing_time:.1f}s)..."
            log(f"[ID {idx}] ⌨️ Typing simulation for {typing_time:.1f}s...")
            
            for _ in range(int(typing_time)):
                if state["stop_flag"]: break
                time.sleep(1)
                
            if state["stop_flag"]: break

            # 4. Sending Logic with Retries
            max_retries = 3
            success = False
            
            for attempt in range(max_retries):
                try:
                    with state_lock: state["active_accounts"][acc_key]["status"] = f"Sending msg {i}..."
                    
                    # SMART FALLBACK: Thread ID -> User ID
                    try:
                        cl.direct_send(final_msg, thread_ids=[int(thread_id)])
                    except Exception as e:
                        if "thread" in str(e).lower() or "not found" in str(e).lower() or "We're sorry" in str(e):
                            cl.direct_send(final_msg, user_ids=[int(thread_id)])
                        else:
                            raise e 
                    
                    success = True
                    break # Success
                    
                except (ClientForbiddenError, ClientThrottledError, PleaseWaitFewMinutes, RateLimitError) as e:
                    wait_time = (attempt + 1) * random.randint(15, 30) 
                    log(f"[ID {idx}] ⚠️ Blocked ({type(e).__name__}). Waiting {wait_time}s...", "error")
                    time.sleep(wait_time)
                    try: cl.get_timeline_feed() 
                    except Exception: pass
                
                except ClientLoginRequired:
                    log(f"[ID {idx}] 🔄 Session dropped mid-send. Re-logging...")
                    ok, _ = login_and_validate(cl, cookie_line)
                    if not ok: break
                        
                except UserNotFound:
                    log(f"[ID {idx}] ❌ User/Thread ID '{thread_id}' not found.", "error")
                    break 

                except Exception as e:
                    log(f"[ID {idx}] ❌ Attempt {attempt+1} error: {str(e)[:60]}", "error")
                    time.sleep(10) 

            # 5. Result Processing
            if success:
                with state_lock:
                    state["totalSent"] += 1
                    state["active_accounts"][acc_key]["sent"] += 1
                    state["active_accounts"][acc_key]["status"] = f"Waiting {delay}s..."
                log(f"[ID {idx}] ✅ Sent {i}/{len(messages)}: {final_msg[:30]}...", "success")
            else:
                with state_lock:
                    state["totalFailed"] += 1
                    state["active_accounts"][acc_key]["failed"] += 1
                    state["active_accounts"][acc_key]["status"] = "Blocked / Failed"
                log(f"[ID {idx}] ❌ Failed to send message {i} after retries.", "error")
                if i == 1:
                    log(f"[ID {idx}] ⛔ Stopping account to prevent Ban.", "error")
                    break 
            
            # 6. Main Delay between messages
            for _ in range(delay):
                if state["stop_flag"]: break
                time.sleep(1)

        with state_lock: state["active_accounts"][acc_key]["status"] = "Completed"

    except Exception as e:
        with state_lock: state["active_accounts"][acc_key]["status"] = "Crashed"
        log(f"[ID {idx}] ❌ Crashed: {str(e)[:80]}", "error")
        traceback.print_exc()
    finally:
        if cl: del cl
        gc.collect() # Memory Management for Render
        log(f"[ID {idx}] 🧹 Memory freed.")

# ==========================================
# EXECUTION RUNNERS
# ==========================================
def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting PARALLEL Mode ({len(cookie_lines)} accounts)...")
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        t = threading.Thread(target=send_messages_worker, args=(line, thread_id, messages, delay, sender_name, idx), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(3) # Gap so all accounts don't login simultaneously
    for t in threads: t.join()
    finalize()

def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting SEQUENTIAL Mode (One by One)...")
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        send_messages_worker(line, thread_id, messages, delay, sender_name, idx)
    finalize()

def finalize():
    with state_lock:
        state["isRunning"] = False
        state["stop_flag"] = False
    log("🎉 All jobs finished successfully.", "success")

# ==========================================
# FLASK ROUTES & API
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin")
def admin(): 
    auth = request.authorization
    if not auth or auth.username != "rkraja70" or auth.password != "pra.chi_sgarma200":
        return "Access Denied. Please provide correct credentials.", 401, {'WWW-Authenticate': 'Basic realm="Admin Login"'}
    return render_template("admin.html")

@app.route("/api/admin_status")
def admin_status():
    auth = request.authorization
    if not auth or auth.username != "rkraja70" or auth.password != "pra.chi_sgarma200":
        return jsonify({"error": "Unauthorized"}), 401
        
    with state_lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "active_accounts": state["active_accounts"],
            "target_thread": state["target_thread"],
            "message_file": state["message_file"],
            "gc_users": state["gc_users"],
            "logs": state["logs"][-100:]
        })

@app.route("/health")
def health(): return jsonify({"status": "ok"}), 200

@app.route("/api/start", methods=["POST"])
def start():
    with state_lock:
        if state["isRunning"]:
            return jsonify({"status": "error", "message": "Task already running!"}), 400
        # Reset State
        state["isRunning"] = True
        state["stop_flag"] = False
        state["totalSent"] = 0
        state["totalFailed"] = 0
        state["logs"] = []
        state["active_accounts"] = {}
        state["gc_users"] = []
        state["start_time"] = time.time()

    try:
        cookies_raw = request.form.get("cookies", "").strip()
        run_mode = request.form.get("run_mode", "parallel")
        thread_id = request.form.get("thread_id", "").strip()
        delay = int(request.form.get("time_delay", 30)) 
        sender_name = request.form.get("sender_name", "").strip()

        message_file = request.files.get("message_file")
        if not message_file: raise ValueError("Message file missing")
        
        with state_lock:
            state["target_thread"] = thread_id
            state["message_file"] = message_file.filename

        messages = [line.strip() for line in message_file.read().decode("utf-8", errors="ignore").splitlines() if line.strip()]
        if not messages: raise ValueError("Message file is empty")

        cookie_lines = parse_cookies_smart(cookies_raw)
        if not cookie_lines: raise ValueError("No valid 'sessionid' found in cookies!")

        if not thread_id.isdigit(): raise ValueError("Invalid Thread/User ID.")

        log(f"📁 Found {len(cookie_lines)} Account(s). Mode: {run_mode.upper()}. File: {message_file.filename}")

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(target=target, args=(cookie_lines, thread_id, messages, delay, sender_name), daemon=True).start()

        return jsonify({"status": "success", "message": f"Started with {len(cookie_lines)} IDs"})
    except Exception as e:
        with state_lock: state["isRunning"] = False
        log(f"❌ Start Error: {str(e)}", "error")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/stop", methods=["POST"])
def stop():
    with state_lock:
        state["stop_flag"] = True
        state["isRunning"] = False
    log("🛑 Force stop triggered! Background threads will die shortly.", "error")
    return jsonify({"status": "success", "message": "Stopping gracefully..."})

@app.route("/api/status")
def status():
    with state_lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "totalSent": state["totalSent"],
            "totalFailed": state["totalFailed"],
            "logs": state["logs"][-150:], 
        })

# ==========================================
# RENDER KEEP ALIVE (24/7 UPTIME)
# ==========================================
def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        log("⚠️ RENDER_EXTERNAL_URL missing in Env Variables.", "error")
        return
        
    url = url.replace("http://", "https://") + "/health"
    log(f"♻️ Keep-Alive thread active for: {url}")
    
    while True:
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                log(f"⚠️ Keep-Alive ping failed: {res.status_code}")
        except Exception:
            pass 
        time.sleep(240) # Ping every 4 minutes

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
