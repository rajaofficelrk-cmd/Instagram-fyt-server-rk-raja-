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
    MediaNotFound, DirectMessageError
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
    cl.request_timeout = 60
    cl.delay_range = [5, 15] # Random API delays
    
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
        return False, "Session ID format is invalid or missing"
    
    sessionid = match.group(1)
    try:
        ok = cl.login_by_sessionid(sessionid)
        if not ok:
            return False, "Invalid Session ID"
            
        try:
            user_info = cl.account_info()
            if not user_info:
                return False, "Account info could not be fetched"
        except Exception:
            pass 
            
        return True, "OK"

    except ChallengeRequired:
        return False, "CHALLENGE REQUIRED — Manual verification needed"
    except PleaseWaitFewMinutes:
        return False, "RATE LIMITED — Wait 10 mins"
    except RateLimitError:
        return False, "RATE LIMIT EXCEEDED"
    except LoginRequired:
        return False, "COOKIES EXPIRED"
    except ClientError as e:
        return False, f"CLIENT ERROR: {str(e)[:100]}"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT — IP Blocked or Slow Network"
    except Exception as e:
        return False, f"UNKNOWN ERROR: {str(e)[:100]}"

# ---------------- BULLETPROOF MESSAGE SENDER (With Typing Simulation) ----------------
def send_messages_from_client(cookie_line, thread_id, messages, delay, sender_name, idx):
    cl = None
    try:
        cl = make_client(cookie_line)
        log(f"🔐 [ID {idx}] Logging in..")
        
        ok, reason = login_and_validate(cl, cookie_line)
        if not ok:
            log(f"❌ [ID {idx}] Login failed: {reason}")
            with lock:
                state["totalFailed"] += 1
            return

        log(f"✅ [ID {idx}] Logged in as @{cl.username}")

        for i, msg in enumerate(messages, 1):
            if state["stop_flag"]:
                log(f"🛑 [ID {idx}] Stop flag received. Exiting.")
                return
            
            final_msg = f"{sender_name} {msg}" if (sender_name and sender_name.strip()) else msg
            
            # ⭐ TYPING SIMULATION LOGIC ⭐
            # Insaan 1 letter type karne mein approx 0.1 sec leta hai.
            # Agar message 50 words ka hai (approx 250 letters), toh 25 sec lagega.
            # Hum maximum 10 second tak simulate karenge taaki server hang na lage.
            typing_time = min(len(final_msg) * 0.05, 10.0) 
            
            log(f"⌨️ [ID {idx}] Typing simulation for {typing_time:.1f}s...")
            time.sleep(typing_time) # Yahan script rukegi jaise koi type kar raha ho
            # ---------------------------------

            max_retries = 4
            success = False
            
            for attempt in range(max_retries):
                try:
                    try:
                        cl.direct_send(final_msg, thread_ids=[int(thread_id)])
                    except Exception as e:
                        if "thread" in str(e).lower() or "not found" in str(e).lower():
                            cl.direct_send(final_msg, user_ids=[int(thread_id)])
                        else:
                            raise e 

                    success = True
                    break 

                except (ClientForbiddenError, ClientThrottledError, PleaseWaitFewMinutes, RateLimitError) as e:
                    wait_time = (attempt + 1) * random.randint(15, 30) 
                    log(f"⚠️ [ID {idx}] Blocked ({type(e).__name__}). Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    
                    try: cl.get_timeline_feed() 
                    except Exception: pass
                
                except ClientLoginRequired:
                    log(f"🔄 [ID {idx}] Session dropped mid-send. Re-logging...")
                    ok, _ = login_and_validate(cl, cookie_line)
                    if not ok:
                        log(f"❌ [ID {idx}] Re-login failed. Skipping ID.")
                        break
                        
                except UserNotFound:
                    log(f"❌ [ID {idx}] User/Thread ID '{thread_id}' not found on Instagram.")
                    break 

                except Exception as e:
                    log(f"❌ [ID {idx}] Attempt {attempt+1} error: {str(e)[:60]}")
                    time.sleep(10) 

            if success:
                with lock:
                    state["totalSent"] += 1
                log(f"✅ [ID {idx}] Sent {i}/{len(messages)}: {final_msg[:30]}...")
            else:
                with lock:
                    state["totalFailed"] += 1
                log(f"❌ [ID {idx}] Failed {i}/{len(messages)} after {max_retries} attempts.")
                if i == 1 and attempt == max_retries - 1:
                    log(f"⛔ [ID {idx}] Stopping this account to prevent Ban.")
                    break 
            
            # Main delay between messages (Tumhare UI se aayega)
            time.sleep(delay)

        log(f"🎉 [ID {idx}] Task completed.")

    except Exception as e:
        log(f"❌ [ID {idx}] Thread Crashed: {str(e)[:100]}")
        with lock:
            state["totalFailed"] += 1
    finally:
        if cl:
            del cl
        gc.collect()
        log(f"🧹 [ID {idx}] Memory freed.")

# ---------------- RUNNERS ----------------
def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting PARALLEL Mode...")
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        t = threading.Thread(target=send_messages_from_client, args=(line, thread_id, messages, delay, sender_name, idx), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2) 
    for t in threads: t.join()
    finalize()

def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting SEQUENTIAL Mode...")
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: 
            log("🛑 Stopping sequential runner...")
            break
        send_messages_from_client(line, thread_id, messages, delay, sender_name, idx)
    finalize()

def finalize():
    with lock:
        state["isRunning"] = False
        state["stop_flag"] = False
    log("🎉 All jobs finished successfully.")

# ---------------- FLASK ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "uptime": time.time()}), 200

@app.route("/api/start", methods=["POST"])
def start():
    with lock:
        if state["isRunning"]:
            return jsonify({"status": "error", "message": "Task already running!"}), 400
        state["isRunning"] = True
        state["stop_flag"] = False
        state["totalSent"] = 0
        state["totalFailed"] = 0
        state["logs"] = []

    try:
        cookies_raw = request.form.get("cookies", "").strip()
        run_mode = request.form.get("run_mode", "sequential")
        thread_id = request.form.get("thread_id", "").strip()
        delay = int(request.form.get("time_delay", 60))
        sender_name = request.form.get("sender_name", "").strip()

        message_file = request.files.get("message_file")
        if not message_file:
            raise ValueError("Message file missing")

        messages = [line.strip() for line in message_file.read().decode("utf-8", errors="ignore").splitlines() if line.strip()]
        if not messages:
            raise ValueError("Message file is empty")

        cookie_lines = parse_cookies_smart(cookies_raw)
        if not cookie_lines:
            raise ValueError("No valid 'sessionid' found in cookies!")

        if not thread_id.isdigit():
            raise ValueError("Invalid Thread ID. Only numbers allowed.")

        log(f"📁 Found {len(cookie_lines)} ID(s). Mode: {run_mode.upper()}. Delay: {delay}s")

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(target=target, args=(cookie_lines, thread_id, messages, delay, sender_name), daemon=True).start()

        return jsonify({"status": "success", "message": f"Started with {len(cookie_lines)} IDs"})

    except Exception as e:
        with lock:
            state["isRunning"] = False
        log(f"❌ Start Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/stop", methods=["POST"])
def stop():
    with lock:
        state["stop_flag"] = True
    log("🛑 Stop command received. Waiting for threads to finish safely...")
    return jsonify({"status": "success", "message": "Stopping gracefully..."})

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
    if not url:
        log("⚠️ RENDER_EXTERNAL_URL missing in Environment Variables.")
        return
        
    url = url.replace("http://", "https://") + "/health"
    log(f"♻️ Keep-Alive thread active for: {url}")
    
    while True:
        try:
            requests.get(url, timeout=20)
        except Exception:
            pass 
        time.sleep(240) 

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
