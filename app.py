import os
import re
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, PleaseWaitFewMinutes, ChallengeRequired,
    RateLimitError, ClientError
)

app = Flask(__name__)

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

# ---------------- CLIENT BUILDER ----------------
def make_client(cookie_str):
    cl = Client()
    cl.request_timeout = 30
    cl.delay_range = [1, 2]
    cl.set_settings({
        "user_agent": "Instagram 219.0.0.12.117 Android",
    })
    return cl

# ---------------- LOGIN VALIDATOR ----------------
def login_and_validate(cl, cookie_str):
    match = re.search(r'sessionid=([^;]+)', cookie_str)
    if not match:
        return False, "sessionid missing"
    
    sessionid = match.group(1)
    try:
        ok = cl.login_by_sessionid(sessionid)
        if not ok:
            return False, "Invalid sessionid"
        return True, "ok"

    except ChallengeRequired:
        return False, "CHALLENGE REQUIRED"
    except PleaseWaitFewMinutes:
        return False, "RATE LIMITED"
    except RateLimitError:
        return False, "RATE LIMIT"
    except LoginRequired:
        return False, "COOKIES EXPIRED"
    except ClientError as e:
        return False, f"CLIENT ERROR: {str(e)[:100]}"
    except requests.exceptions.Timeout:
        return False, "TIMEOUT — IP blocked"
    except Exception as e:
        return False, f"UNKNOWN: {str(e)[:100]}"

# ---------------- MESSAGE SENDER ----------------
def send_messages_from_client(cookie_line, thread_id, messages, delay, sender_name, idx):
    try:
        cl = make_client(cookie_line)
        log(f"🔐 [ID {idx}] Logging in..")
        
        ok, reason = login_and_validate(cl, cookie_line)
        if not ok:
            log(f"❌ [ID {idx}] Login failed: {reason}")
            with lock:
                state["totalFailed"] += 1
            return

        log(f"✅ [ID {idx}] Logged in! Sending messages...")

        if sender_name and sender_name.strip() != "":
            try:
                cl.direct_send(sender_name, thread_ids=[int(thread_id)])
                log(f"🏷️ [ID {idx}] Sent Here Name: {sender_name}")
                time.sleep(2)
            except Exception as e:
                log(f"⚠️ [ID {idx}] Could not send Here Name: {str(e)[:50]}")

        for i, msg in enumerate(messages, 1):
            if state["stop_flag"]:
                log(f"🛑 [ID {idx}] Stop flag received. Exiting.")
                return
            try:
                cl.direct_send(msg, thread_ids=[int(thread_id)])
                with lock:
                    state["totalSent"] += 1
                log(f"✅ [ID {idx}] Sent {i}/{len(messages)}: {msg[:30]}...")
            except PleaseWaitFewMinutes:
                log(f"⚠️ [ID {idx}] Rate limited. Sleeping 60s...")
                time.sleep(60)
                with lock:
                    state["totalFailed"] += 1
            except Exception as e:
                with lock:
                    state["totalFailed"] += 1
                log(f"❌ [ID {idx}] Failed: {str(e)[:80]}")
            
            time.sleep(delay)

        log(f"🎉 [ID {idx}] Finished sending.")

    except Exception as e:
        log(f"❌ [ID {idx}] Thread crashed: {str(e)[:120]}")
        with lock:
            state["totalFailed"] += 1

# ---------------- RUNNERS ----------------
def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting PARALLEL Mode (Ek Sath)...")
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        t = threading.Thread(target=send_messages_from_client, args=(line, thread_id, messages, delay, sender_name, idx), daemon=True)
        t.start()
        threads.append(t)
    for t in threads: t.join()
    finalize()

def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting SEQUENTIAL Mode...")
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]: break
        send_messages_from_client(line, thread_id, messages, delay, sender_name, idx)
    finalize()

def finalize():
    with lock:
        state["isRunning"] = False
        state["stop_flag"] = False
    log("🎉 Job finished.")

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start():
    with lock:
        if state["isRunning"]:
            return jsonify({"status": "error", "message": "Already running"}), 400
        state["isRunning"] = True
        state["stop_flag"] = False
        state["totalSent"] = 0
        state["totalFailed"] = 0
        state["logs"] = []

    try:
        cookies_raw = request.form.get("cookies", "").strip()
        run_mode = request.form.get("run_mode", "parallel")
        thread_id = request.form.get("thread_id", "").strip()
        delay = int(request.form.get("time_delay", 20))
        sender_name = request.form.get("sender_name", "Here").strip()

        message_file = request.files.get("message_file")
        if not message_file:
            raise ValueError("Message file missing")

        messages = [line.strip() for line in message_file.read().decode("utf-8", errors="ignore").splitlines() if line.strip()]
        if not messages:
            raise ValueError("Message file is empty")

        cookie_lines = parse_cookies_smart(cookies_raw)
        if not cookie_lines:
            raise ValueError("Koi valid 'sessionid' nahi mila!")

        if not thread_id.isdigit():
            raise ValueError("Invalid thread_id (sirf numbers daalo)")

        log(f"📁 Total {len(cookie_lines)} valid IDs found. Mode: {run_mode.upper()}")

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(target=target, args=(cookie_lines, thread_id, messages, delay, sender_name), daemon=True).start()

        return jsonify({"status": "success", "message": f"Started with {len(cookie_lines)} IDs"})

    except Exception as e:
        with lock:
            state["isRunning"] = False
        log(f"❌ Start error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/stop", methods=["POST"])
def stop():
    with lock:
        state["stop_flag"] = True
    log("🛑 Stop requested...")
    return jsonify({"status": "success", "message": "Stopping..."})

@app.route("/api/status")
def status():
    with lock:
        return jsonify({
            "isRunning": state["isRunning"],
            "totalSent": state["totalSent"],
            "totalFailed": state["totalFailed"],
            "logs": state["logs"][-100:],
        })

# ---------------- STRONG KEEP ALIVE ----------------
def keep_alive():
    # Render apna URL khud deta hai, par fallback ke liye manual URL bhi check karega
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://r-rk-raja-2uey.onrender.com")
    
    # URL ko theek karna (agar http:// laga ho toh https:// karna)
    if url.startswith("http://"):
        url = url.replace("http://", "https://")
    
    log(f"♻️ Keep-Alive started for: {url}")
    
    while True:
        try:
            # Khud ko ping karega taaki Render soye na
            requests.get(url, timeout=15)
        except Exception:
            pass
        time.sleep(240) # Har 4 minute mein ping karega (15 min timeout se pehle)

if __name__ == "__main__":
    # Keep alive thread start karo
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
