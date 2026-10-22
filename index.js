
const express = require("express");
const multer = require("multer");
const fs = require("fs");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 5000;

const uploadDir = path.join(__dirname, "uploads");
const publicDir = path.join(__dirname, "public");

fs.mkdirSync(uploadDir, { recursive: true });
fs.mkdirSync(publicDir, { recursive: true });

const upload = multer({
  dest: uploadDir,
  limits: {
    fileSize: 1024 * 1024,
    files: 1
  },
  fileFilter: (req, file, cb) => {
    if (path.extname(file.originalname).toLowerCase() !== ".txt") {
      return cb(new Error("Only .txt files are allowed"));
    }
    cb(null, true);
  }
});

app.use(express.urlencoded({ extended: true, limit: "20kb" }));
app.use(express.json({ limit: "20kb" }));
app.use(express.static(publicDir));

let state = {
  isRunning: false,
  totalSent: 0,
  totalFailed: 0,
  logs: []
};

let stopRequested = false;

function addLog(message) {
  const entry = `[${new Date().toISOString()}] ${message}`;
  state.logs.push(entry);
  if (state.logs.length > 150) state.logs.shift();
  console.log(entry);
}

app.get("/", (req, res) => {
  res.send("RK RAJA server is online ❤️");
});

app.get("/api/status", (req, res) => {
  res.json(state);
});

app.post("/api/start", upload.single("message_file"), (req, res) => {
  const file = req.file;

  if (state.isRunning) {
    if (file) fs.promises.unlink(file.path).catch(() => {});
    return res.status(409).json({
      status: "error",
      message: "A task is already running."
    });
  }

  const senderName = String(req.body.sender_name || "").trim();
  const delay = Number(req.body.time_delay ?? 10);

  if (!senderName || !file) {
    if (file) fs.promises.unlink(file.path).catch(() => {});
    return res.status(400).json({
      status: "error",
      message: "Sender name and a TXT file are required."
    });
  }

  if (!Number.isInteger(delay) || delay < 1 || delay > 3600) {
    fs.promises.unlink(file.path).catch(() => {});
    return res.status(400).json({
      status: "error",
      message: "Delay must be between 1 and 3600 seconds."
    });
  }

  let messages;

  try {
    messages = fs.readFileSync(file.path, "utf8")
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean);
  } catch (error) {
    fs.promises.unlink(file.path).catch(() => {});
    return res.status(500).json({
      status: "error",
      message: "Could not read the uploaded file."
    });
  }

  fs.promises.unlink(file.path).catch(() => {});

  if (messages.length === 0) {
    return res.status(400).json({
      status: "error",
      message: "The TXT file is empty."
    });
  }

  // Validation and status endpoint test only.
  // No messages are sent to Instagram.
  state = {
    isRunning: false,
    totalSent: 0,
    totalFailed: 0,
    logs: []
  };

  stopRequested = false;
  addLog(`Validated ${messages.length} messages for ${senderName}.`);

  return res.json({
    status: "success",
    message: "File validated successfully.",
    messageCount: messages.length
  });
});

app.post("/api/stop", (req, res) => {
  stopRequested = true;
  res.json({
    status: "success",
    message: "Stop flag set."
  });
});

app.use((err, req, res, next) => {
  if (req.file?.path) {
    fs.promises.unlink(req.file.path).catch(() => {});
  }

  const status = err instanceof multer.MulterError ? 400 : 400;

  res.status(status).json({
    status: "error",
    message: err.message || "Request failed."
  });
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`RK RAJA server listening on port ${PORT}`);
});
