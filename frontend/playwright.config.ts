import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: { baseURL: "http://127.0.0.1:8765", browserName: "chromium", channel: "msedge", headless: true },
  webServer: {
    command: '"../.venv/Scripts/python.exe" ../backend/manage.py runserver 127.0.0.1:8765 --noreload',
    url: "http://127.0.0.1:8765/admin/login/",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
