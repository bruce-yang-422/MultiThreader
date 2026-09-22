const {defineConfig} = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:5099', channel: 'chrome', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: '.venv\\Scripts\\python.exe tests/ui_server.py', url: 'http://127.0.0.1:5099/login', reuseExistingServer: false, timeout: 30000 }
});
