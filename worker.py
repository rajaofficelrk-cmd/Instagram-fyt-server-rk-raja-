import json
import os
import time
from datetime import datetime, timezone


STATE_FILE = "state.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def default_state():
    return {
        "job_id": None,
        "status": "idle",
        "total": 0,
        "processed": 0,
        "success": 0,
        "failed": 0,
        "items": [],
        "message": "",
        "created_at": None,
        "updated_at": None
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_state()


def save_state(state):
    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(temp, STATE_FILE)


def process_item(item):
    """
    Yahan tumhara legitimate background task chalega.

    Example:
        print("Processing:", item)

    Real external-service action ko yahan
    authorization/rate limits ke according implement karo.
    """

    print(f"⚙️ Processing: {item}")

    # Demo processing time
    time.sleep(2)

    return True


def worker_loop():
    print("🚀 Raja Worker Online")

    while True:

        try:
            state = load_state()

            if state["status"] != "running":
                time.sleep(3)
                continue

            if state["processed"] >= state["total"]:
                state["status"] = "completed"
                state["message"] = "All items completed"
                state["updated_at"] = now()

                save_state(state)

                print("✅ Job completed")
                time.sleep(3)
                continue

            index = state["processed"]
            item = state["items"][index]

            print(
                f"📌 {index + 1}/{state['total']} "
                f"→ {item}"
            )

            try:
                result = process_item(item)

                if result:
                    state["success"] += 1
                else:
                    state["failed"] += 1

            except Exception as item_error:
                print(
                    f"❌ Item error: {item_error}"
                )

                state["failed"] += 1

            state["processed"] += 1
            state["updated_at"] = now()

            save_state(state)

        except Exception as error:
            print(f"⚠️ Worker error: {error}")
            time.sleep(5)


if __name__ == "__main__":
    worker_loop()
