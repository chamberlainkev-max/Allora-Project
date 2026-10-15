// ============================================================
// PM2 Process Manager Configuration
// Deploy with: pm2 start ecosystem.config.js
// Each system runs as its own process — pause one with `pm2 stop <name>`
// without touching the other.
// ============================================================

const fs = require("fs");
const path = require("path");

// Use the project's venv Python when present (packages are installed there)
const venvPython = path.join(__dirname, "venv", "bin", "python");

const common = {
  interpreter: fs.existsSync(venvPython) ? venvPython : "python3",
  cwd: __dirname,
  instances: 1,
  autorestart: true,
  watch: false,
  max_memory_restart: "512M",
  restart_delay: 3000,
  env: {
    ENV: "production",
    PYTHONUNBUFFERED: "1",
  },
  log_date_format: "YYYY-MM-DD HH:mm:ss.SSS",
  merge_logs: true,
};

module.exports = {
  apps: [
    {
      // ---- Elora: meme marketing (no trading, no token creation) ----
      ...common,
      name: "elora-main",
      script: "main.py",
      log_file: "logs/main-combined.log",
      out_file: "logs/main-out.log",
      error_file: "logs/main-err.log",
    },
    {
      // ---- Pre-Scope & Terminal Sniper (sniper wallet) ----
      ...common,
      name: "sniper",
      script: "scripts/run_prescope_sniper.py",
      log_file: "logs/sniper-combined.log",
      out_file: "logs/sniper-out.log",
      error_file: "logs/sniper-err.log",
    },
  ],
};
