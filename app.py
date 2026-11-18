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


# ---------------- COOKIE PARSER ----------------
def parse_cookie(cookie_str):
    cookie = {}
    for part in cookie_str.strip().split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            cookie[k.strip()] = v.strip()
    return cookie


# ---------------- CLIENT BUILDER ----------------
def make_client(cookie_str):
    cookie = parse_cookie(cookie_str)
    cl = Client()

    # ⭐ CRITICAL: timeout + delay (hang fix)
    cl.request_timeout = 30
    cl.delay_range = [1, 2]

    cl.set_settings({
        "cookies": cookie,
        "user_agent": "Instagram 219.0.0.12.117 Android",
    })
    return cl


# ---------------- LOGIN VALIDATOR (NEVER HANGS) ----------------
def login_and_validate(cl, cookie_str):
    """Return (ok: bool, reason: str). Never loops, never hangs."""
    cookie = parse_cookie(cookie_str)
    sessionid = cookie.get("sessionid")

    if not sessionid:
        return False, "sessionid missing in cookies"

    try:
        ok = cl.login_by_sessionid(sessionid)
        if not ok:
            return False, "Invalid sessionid"
        return True, "ok"

    except ChallengeRequired:
        return False, "CHALLENGE REQUIRED — manual login needed"

    except PleaseWaitFewMinutes:
        return False, "RATE LIMITED — wait 5-10 min"

    except RateLimitError:
        return False, "RATE LIMIT — too many requests"

    except LoginRequired:
        return False, "COOKIES EXPIRED"

    except ClientError as e:
        return False, f"CLIENT ERROR: {str(e)[:100]}"

    except requests.exceptions.Timeout:
        return False, "TIMEOUT — IP blocked by Instagram or slow network"

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
            return   # ⭐ exit, no infinite retry

        log(f"✅ [ID {idx}] Logged in as user_id={cl.user_id}")

        for i, msg in enumerate(messages, 1):
            if state["stop_flag"]:
                log(f"🛑 [ID {idx}] Stop flag received. Exiting.")
                return

            try:
                cl.direct_send(msg, thread_ids=[int(thread_id)])
                with lock:
                    state["totalSent"] += 1
                log(f"✅ [ID {idx}] Sent {i}/{len(messages)}: {msg[:40]}")

            except PleaseWaitFewMinutes:
                log(f"⚠️ [ID {idx}] Rate limited. Sleeping 60s...")
                time.sleep(60)
                with lock:
                    state["totalFailed"] += 1

            except Exception as e:
                with lock:
                    state["totalFailed"] += 1
                log(f"❌ [ID {idx}] Failed {i}/{len(messages)}: {str(e)[:80]}")

            time.sleep(delay)

        log(f"🎉 [ID {idx}] Finished all {len(messages)} messages.")

    except Exception as e:
        log(f"❌ [ID {idx}] Thread crashed: {str(e)[:120]}")
        with lock:
            state["totalFailed"] += 1


# ---------------- RUNNERS ----------------
def run_parallel(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting PARALLEL Mode (Ek Sath)...")
    threads = []
    for idx, line in enumerate(cookie_lines, 1):
        t = threading.Thread(
            target=send_messages_from_client,
            args=(line, thread_id, messages, delay, sender_name, idx),
            daemon=True,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    finalize()


def run_sequential(cookie_lines, thread_id, messages, delay, sender_name):
    log(f"🚀 Starting SEQUENTIAL Mode (Ek Ke Baad Ek)...")
    for idx, line in enumerate(cookie_lines, 1):
        if state["stop_flag"]:
            break
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

        messages = [
            line.strip()
            for line in message_file.read().decode("utf-8", errors="ignore").splitlines()
            if line.strip()
        ]
        if not messages:
            raise ValueError("Message file is empty")

        cookie_lines = [c.strip() for c in cookies_raw.splitlines() if c.strip()]
        if not cookie_lines:
            raise ValueError("No cookies provided")

        if not thread_id.isdigit():
            raise ValueError("Invalid thread_id (numeric only)")

        log(f"📁 Total {len(cookie_lines)} IDs found. Mode: {run_mode.upper()}")

        target = run_parallel if run_mode == "parallel" else run_sequential
        threading.Thread(
            target=target,
            args=(cookie_lines, thread_id, messages, delay, sender_name),
            daemon=True,
        ).start()

        return jsonify({"status": "success", "message": f"Started in {run_mode} mode"})

    except Exception as e:
        with lock:
            state["isRunning"] = False
        log(f"❌ Start error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/stop", methods=["POST"])
def stop():
    with lock:
        state["stop_flag"] = True
    log("🛑 Stop requested. Threads will exit after current message.")
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


# ---------------- KEEP ALIVE (Render) ----------------
def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass
        time.sleep(300)


if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
