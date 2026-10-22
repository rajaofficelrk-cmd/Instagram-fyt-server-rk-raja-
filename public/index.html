
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RK RAJA | Server Panel</title>
<style>
:root {
  --bg: #0a0a0a;
  --card: rgba(20,20,20,.92);
  --border: #303030;
  --text: #eee;
  --accent: #ff0033;
  --green: #00cc66;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 20px;
  min-height: 100vh;
  display: flex;
  justify-content: center;
  background: var(--bg);
  background-image:
    linear-gradient(#0009,#000b),
    url('https://i.ibb.co/KjtjBDRP/file-00000000f49c821197fdc7fcda40847d.png');
  background-position: center;
  background-size: cover;
  background-attachment: fixed;
  color: var(--text);
  font-family: "Courier New", monospace;
}
.container {
  width: 100%;
  max-width: 800px;
  padding: 24px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  align-self: flex-start;
}
header { text-align: center; margin-bottom: 25px; }
header img {
  width: 100px;
  height: 100px;
  object-fit: cover;
  border-radius: 50%;
  border: 3px solid var(--accent);
}
h2 { color: var(--accent); }
label { display: block; margin: 16px 0 7px; }
input, button {
  width: 100%;
  padding: 13px;
  border-radius: 6px;
  font: inherit;
}
input {
  color: white;
  background: #080808;
  border: 1px solid var(--border);
}
button {
  margin-top: 16px;
  cursor: pointer;
  font-weight: bold;
  border: 0;
}
button:disabled { opacity: .5; cursor: not-allowed; }
#startBtn { background: var(--green); color: #000; }
.status {
  margin-top: 20px;
  padding: 15px;
  background: #080808;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow-wrap: anywhere;
}
#terminal {
  height: 230px;
  overflow-y: auto;
  margin-top: 16px;
  padding: 12px;
  background: #000;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.success { color: var(--green); }
.error { color: #ff4444; }
</style>
</head>
<body>
<main class="container">
  <header>
    <img
      src="https://i.ibb.co/KjtjBDRP/file-00000000f49c821197fdc7fcda40847d.png"
      alt="RK RAJA"
    >
    <h2>RK RAJA SERVER</h2>
    <p>TXT FILE VALIDATOR</p>
  </header>

  <form id="form">
    <label for="message_file">Upload TXT file (max 1 MB)</label>
    <input
      id="message_file"
      name="message_file"
      type="file"
      accept=".txt,text/plain"
      required
    >
    <button id="startBtn" type="submit">CHECK FILE</button>
  </form>

  <section class="status">
    <div>Server: <strong id="serverStatus">Checking...</strong></div>
    <div>Lines validated: <strong id="lineCount">0</strong></div>
  </section>

  <section id="terminal" aria-live="polite">
    <div> &gt; Panel initialized.</div>
  </section>
</main>

<script>
const form = document.getElementById("form");
const startBtn = document.getElementById("startBtn");
const terminal = document.getElementById("terminal");
const serverStatus = document.getElementById("serverStatus");
const lineCount = document.getElementById("lineCount");

function log(message, type = "") {
  const div = document.createElement("div");
  div.className = type;
  div.textContent = "> " + message;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

async function checkHealth() {
  try {
    const response = await fetch("/health", {
      cache: "no-store"
    });
    if (!response.ok) throw new Error("Server unavailable");
    const data = await response.json();
    serverStatus.textContent = data.status.toUpperCase();
    serverStatus.className = "success";
  } catch {
    serverStatus.textContent = "OFFLINE";
    serverStatus.className = "error";
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();

  if (!form.reportValidity()) return;

  startBtn.disabled = true;
  lineCount.textContent = "0";

  const formData = new FormData(form);

  try {
    const response = await fetch("/api/start", {
      method: "POST",
      body: formData
    });

    const data = await response.json();

    if (!response.ok || data.status !== "success") {
      throw new Error(data.message || "Request failed");
    }

    lineCount.textContent = String(data.messageCount);
    log(data.message, "success");
  } catch (error) {
    log(error.message || "Network error", "error");
  } finally {
    startBtn.disabled = false;
    await checkHealth();
  }
});

checkHealth();
setInterval(checkHealth, 15000);
</script>
</body>
</html>
