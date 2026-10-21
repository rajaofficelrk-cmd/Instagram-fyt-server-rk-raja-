const express = require('express');
const multer = require('multer');
const axios = require('axios');
const { v4: uuidv4 } = require('uuid');
const fs = require('fs');
const path = require('path');

const app = express();
const upload = multer({ dest: 'uploads/' });

app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static('public'));

// Server State Tracker
let serverState = {
    isRunning: false,
    totalSent: 0,
    totalFailed: 0,
    logs: []
};
let stopFlag = false;

function addLog(msg) {
    const timestamp = new Date().toLocaleTimeString();
    const logMsg = `[${timestamp}] ${msg}`;
    serverState.logs.push(logMsg);
    if (serverState.logs.length > 150) serverState.logs.shift();
    console.log(logMsg);
}

function extractCsrf(cookiesStr) {
    const match = cookiesStr.match(/csrftoken=([^;]+)/);
    return match ? match[1].trim() : null;
}

async function sendInstaMessage(cookiesStr, threadId, text) {
    const csrfToken = extractCsrf(cookiesStr);
    if (!csrfToken) return { success: false, error: "CSRF token not found in cookies" };

    const url = "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/";
    const headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookiesStr,
        "X-CSRFToken": csrfToken,
        "X-IG-App-ID": "936619743392459",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.instagram.com",
        "Referer": `https://www.instagram.com/direct/t/${threadId}/`
    };

    const payload = new URLSearchParams({
        thread_ids: `[${threadId}]`,
        text: text,
        client_context: uuidv4()
    });

    try {
        // ✅ FIX: maxRedirects: 0 aur validateStatus lagaya hai taaki redirect loop crash na kare
        const response = await axios.post(url, payload.toString(), { 
            headers, 
            timeout: 15000,
            maxRedirects: 0, 
            validateStatus: function (status) {
                return status >= 200 && status < 400; // 200 se 399 tak accept karega
            }
        });

        // Agar Instagram redirect kar raha hai (Matlab login page pe bhej raha hai)
        if (response.status >= 300 && response.status < 400) {
            return { success: false, error: "Blocked! Instagram redirected to Login. (IP Block or Bad Cookies)" };
        }

        if (response.status === 200 && response.data && response.data.status === "ok") {
            return { success: true };
        } else {
            return { success: false, error: `API Error: ${response.data?.message || 'Unknown'}` };
        }
    } catch (err) {
        const errorMsg = err.response?.data?.message || err.message;
        return { success: false, error: errorMsg };
    }
}

async function runWorker(cookies, threadId, timeDelay, senderName, messages) {
    serverState.isRunning = true;
    serverState.totalSent = 0;
    serverState.totalFailed = 0;
    serverState.logs = [];
    stopFlag = false;

    addLog("> 🚀 Server task started...");

    for (let i = 0; i < messages.length; i++) {
        if (stopFlag) {
            addLog("> 🛑 Execution stopped by user.");
            break;
        }

        const fullMessage = `${senderName}\n${messages[i].trim()}`;
        addLog(`> Sending message [${i + 1}/${messages.length}]...`);

        const result = await sendInstaMessage(cookies, threadId, fullMessage);

        if (result.success) {
            serverState.totalSent++;
            addLog("> ✅ Sent successfully!");
        } else {
            serverState.totalFailed++;
            addLog(`> ❌ Failed: ${result.error}`);
            
            // Agar redirect/block error aaya toh thoda ruk kar agla message try karega
            if (result.error.includes("Blocked")) {
                addLog("> ⚠️ Stopping process due to Block/Redirect.");
                break; 
            }
        }

        if (i < messages.length - 1 && !stopFlag) {
            addLog(`> ⏱️ Waiting ${timeDelay} seconds...`);
            for (let s = 0; s < timeDelay; s++) {
                if (stopFlag) break;
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        }
    }

    serverState.isRunning = false;
    addLog("> 🎉 Task finished.");
}

app.post('/api/start', upload.single('message_file'), async (req, res) => {
    if (serverState.isRunning) {
        return res.json({ status: "error", message: "Server is already running!" });
    }

    const { cookies, thread_id, sender_name } = req.body;
    const timeDelay = parseInt(req.body.time_delay || 10);
    const file = req.file;

    if (!cookies || !thread_id || !file || !sender_name) {
        return res.json({ status: "error", message: "All fields are required!" });
    }

    try {
        const fileContent = fs.readFileSync(file.path, 'utf-8');
        fs.unlinkSync(file.path); // Delete temp uploaded file
        const messages = fileContent.split(/\r?\n/).filter(line => line.trim() !== '');

        if (messages.length === 0) {
            return res.json({ status: "error", message: "Message file is empty!" });
        }

        // Run background task
        runWorker(cookies, thread_id, timeDelay, sender_name, messages);

        res.json({ status: "success", message: "Started successfully" });
    } catch (e) {
        serverState.isRunning = false;
        res.json({ status: "error", message: e.message });
    }
});

app.post('/api/stop', (req, res) => {
    stopFlag = true;
    res.json({ status: "success", message: "Stopping..." });
});

app.get('/api/status', (req, res) => {
    res.json(serverState);
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => {
    console.log(`Server is running on http://localhost:${PORT}`);
});
