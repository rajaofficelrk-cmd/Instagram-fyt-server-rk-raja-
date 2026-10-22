const express = require('express');
const multer = require('multer');
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const app = express();
const upload = multer({ dest: 'uploads/' });

if (!fs.existsSync('uploads')) fs.mkdirSync('uploads');

app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

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

async function runWorker(cookiesStr, threadId, timeDelay, senderName, messages) {
    serverState.isRunning = true;
    serverState.totalSent = 0;
    serverState.totalFailed = 0;
    serverState.logs = [];
    stopFlag = false;

    addLog("> 🚀 Starting Browser Automation...");

    let browser;
    try {
        browser = await puppeteer.launch({
            headless: true, // Background mein chalane ke liye true rakhein
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });

        const page = await browser.newPage();
        
        // Set viewport and user agent
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36');

        // Parse cookies string and set them in browser
        const cookieItems = cookiesStr.split(';').map(item => {
            const parts = item.trim().split('=');
            if (parts.length >= 2) {
                return {
                    name: parts[0].trim(),
                    value: parts.slice(1).join('=').trim(),
                    domain: '.instagram.com'
                };
            }
            return null;
        }).filter(Boolean);

        await page.setCookie(...cookieItems);

        addLog("> 🌐 Opening Instagram Direct Chat...");
        await page.goto(`https://www.instagram.com/direct/t/${threadId}/`, { waitUntil: 'networkidle2', timeout: 60000 });

        // Check if redirected to login
        if (page.url().includes('accounts/login')) {
            addLog("> ❌ Failed: Cookies are invalid or expired (Redirected to login).");
            serverState.isRunning = false;
            await browser.close();
            return;
        }

        // Wait for chat input box to appear
        try {
            await page.waitForSelector('div[contenteditable="true"][role="textbox"]', { timeout: 15000 });
        } catch (e) {
            addLog("> ❌ Failed: Could not find chat input box. Check Thread ID.");
            serverState.isRunning = false;
            await browser.close();
            return;
        }

        addLog("> ✅ Login successful! Starting message loop...");

        for (let i = 0; i < messages.length; i++) {
            if (stopFlag) {
                addLog("> 🛑 Execution stopped by user.");
                break;
            }

            const fullMessage = `${senderName}\n${messages[i].trim()}`;
            addLog(`> Sending message [${i + 1}/${messages.length}]...`);

            try {
                // Click and type message in the textbox
                const textBox = await page.$('div[contenteditable="true"][role="textbox"]');
                await textBox.click();
                
                // Type message line by line (handling newlines properly in puppeteer)
                await page.keyboard.type(fullMessage);
                await page.keyboard.press('Enter');

                serverState.totalSent++;
                addLog("> ✅ Sent successfully!");
            } catch (err) {
                serverState.totalFailed++;
                addLog(`> ❌ Failed: ${err.message}`);
            }

            if (i < messages.length - 1 && !stopFlag) {
                addLog(`> ⏱️ Waiting ${timeDelay} seconds...`);
                for (let s = 0; s < timeDelay; s++) {
                    if (stopFlag) break;
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
            }
        }

        await browser.close();
    } catch (err) {
        addLog(`> ❌ Browser Error: ${err.message}`);
        if (browser) await browser.close();
    }

    serverState.isRunning = false;
    addLog("> 🎉 Task finished.");
}

app.post('/api/start', upload.single('message_file'), async (req, res) => {
    if (serverState.isRunning) {
        if (req.file && fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        return res.json({ status: "error", message: "Server is already running!" });
    }

    const { cookies, thread_id, sender_name } = req.body;
    const timeDelay = parseInt(req.body.time_delay || 10, 10);
    const file = req.file;

    if (!cookies || !thread_id || !file || !sender_name) {
        if (file && fs.existsSync(file.path)) fs.unlinkSync(file.path);
        return res.json({ status: "error", message: "All fields are required!" });
    }

    try {
        const fileContent = fs.readFileSync(file.path, 'utf-8');
        fs.unlinkSync(file.path);
        const messages = fileContent.split(/\r?\n/).filter(line => line.trim() !== '');

        if (messages.length === 0) {
            return res.json({ status: "error", message: "Message file is empty!" });
        }

        // Run worker asynchronously
        runWorker(cookies, thread_id, timeDelay, sender_name, messages);

        res.json({ status: "success", message: "Started successfully" });
    } catch (e) {
        serverState.isRunning = false;
        if (file && fs.existsSync(file.path)) {
            try { fs.unlinkSync(file.path); } catch (err) {}
        }
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
