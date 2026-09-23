# MultiThreader 多脆客

Flask + SQLite 的 Threads 多帳號圖文工具。Insights 為本機獨立參考庫（不納入 Git），執行程式位於專案根目錄。

首頁模板位於根目錄 `index.html`，由 Flask 的 `/` 與 `/compose` 載入；其他頁面與共用版型位於 `templates/`。

## 已實作

- 同篇圖文 → A／B／C，一次提交。
- 不同圖文 → 各自指定帳號，整批提交。
- OAuth 帳號確認、加密 token 保存、更新及重新連結。
- 內部登入、同事帳號與操作權限、草稿、圖片上傳及預覽。
- 管理者可新增、刪除同事帳號及重設密碼；刪除與重設會使舊登入失效。刪除後保留歷史發文紀錄並停止該同事的待發布工作，不移除 Threads 帳號。
- 逐項狀態、貼文連結、只重試失敗項目、逾時先查核、防重複提交。
- 模擬與正式資料庫分離；每篇支援文字、最多 20 張圖片輪播或一支影片，以及選填的第一則文字回覆。

## 開始使用

在 PowerShell 執行：

```powershell
cd D:\Tools\MultiThreader
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

第一次安裝：複製 `.env.example` 為 `.env`（已有設定時不要覆蓋），保持 `THREADS_MODE=demo`。

```powershell
python manage.py init
```

輸入自訂的內部管理者帳號與至少 12 字元密碼。這與 Threads 帳密無關。啟動兩個終端：

```powershell
# 終端一
python app.py
# 終端二
python worker.py
```

未啟用 HTTPS 時，開啟 http://127.0.0.1:18473 。使用 HTTPS 時，改開 `instance/https-url.txt` 中的網址。日常可使用桌面「MultiThreader 控制台」，或執行 `./Start.ps1` 啟動背景網頁與 worker；關閉控制台或終端不會停止服務，請用 `./Stop.ps1` 安全停止。

本次工作若已產生初始帳號，登入資料在 `instance/initial-login.txt`；登入後點右上角帳號名稱更改密碼，並刪除初始登入檔。不要將該檔、`instance/`、`.env` 或 `帳號密碼.md` 上傳到 Git。

本次若服務已在背景啟動，可直接使用 `instance/https-url.txt` 的網址。控制台只停止由新共用模組記錄且身分相符的程序。舊版腳本或自行在終端啟動的程序，請先確認沒有發布工作，再由原啟動方式結束；控制台不會接管或強制結束未知程序。

## 桌面控制台

完成 `.env`、Python 套件與 `python manage.py init` 後，執行 `./Install-ControlPanel.ps1` 建立桌面捷徑。日常雙擊「MultiThreader 控制台」即可啟動、停止、重啟、開啟網站或查看不含憑證的操作紀錄與詳細狀態。控制台固定檢查 `127.0.0.1:18473` 與 `https://multithreader.stack-base.com`，要求 `HOST=127.0.0.1`、`PORT=18473`（未填則使用預設）。首次初始化不會在日常啟動時要求輸入帳密。

停止時暫停新發布及重試，worker 完成已領取的發布／回覆後自行退出；預設等待 120 秒，逾時保留程序與停止狀態。可按「取消停止」，再按「全部啟動」補起已退出的 worker。命令列相同操作為 `./Stop.ps1 -Cancel` 與 `./Start.ps1`。遇到先前中斷、仍標示處理中的工作，保留服務供查核，不強制結束。資料庫、草稿、token 與待發布工作均保留。

**Cloudflared 預設僅監看，排除於啟停範圍。** 管理者確認 `Cloudflared` 的 Tunnel `f0c5c608-6c46-4903-b75b-7f9fb3f4dd4e` 只服務本工具後，才執行 `./Install-ControlPanel.ps1 -DedicatedTunnelConfirmed`。安裝程式驗證服務 token 中的 Tunnel ID，只保存命令雜湊，不保存 token；無法驗證則拒絕啟用。日常使用者須已獲授該服務的啟停權限，否則顯示首次設定提示，不提升整個控制台、網頁或 worker 的權限。遠端 Tunnel 路由變更後，管理者應重新確認是否仍專用。

`instance/control.log` 為安全操作摘要，超過 1 MB 保留上一份；網頁及 worker 的輸出於每次啟動保留上一份。問題紀錄視窗只顯示操作摘要與必要狀態，不直接展示可能含敏感資訊的原始程序輸出。`instance/control-processes.json` 保存 PID、建立時間、執行路徑及命令列；啟停以互斥鎖與檔案鎖防止重複操作。健康檢查 `/healthz` 不回傳帳號或設定。

控制台驗證：`powershell.exe -NoProfile -STA -File ./ControlPanel.ps1 -SmokeTest`；隔離 demo 啟停驗收：`powershell.exe -NoProfile -File tests/control_lifecycle.ps1`（使用暫存資料與 19473，不操作正式 Cloudflared）。

## 模擬驗收

模擬模式不會向 Meta 發出請求，A／B／C 是測試帳號。

1. 同篇模式輸入文字、上傳圖片，勾 A／B／C，預覽後發布。
2. 不同篇模式新增兩張卡片，甲選 A、乙選 B，整批發布。
3. 文字加入 `[模擬失敗]`，B 第一次失敗；按「只重試此項目」後成功，A／C 不重發。
4. 文字加入 `[模擬逾時]`，顯示待確認；按「查核結果」確認模擬成功。
5. 重新登入或重啟服務，帳號與紀錄仍保留。重新建立 demo 授權可執行 `python manage.py seed-demo`。

## Meta 與 HTTPS

目前已切換 `THREADS_MODE=live`，入口為 https://multithreader.stack-base.com 。已沿用原多脆客管理者登入，沒有複製模擬社群帳號或 token。下一步登入「帳號管理」，連結 petstar_5566 並完成官方授權，先驗證純文字發布。

詳見 [設定指南.md](設定指南.md)。填妥 App 設定、切換 `THREADS_MODE=live` 並重啟後，才能授權真實帳號。正式與模擬模式使用不同資料庫，第一次進入 live 需再執行 `python manage.py init` 建立內部管理者。

## 驗證與前端編譯

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
npm.cmd install
npm.cmd run build:css
npx.cmd playwright test
```

CSS 已編譯為 `static/app.css`，一般啟動不需要 Node.js。修改 HTML 或樣式後才需要重建 CSS。瀏覽器測試使用電腦已安裝的 Chrome，以及獨立的臨時資料庫，不使用真實帳密。

`requirements.txt` 保留無版本號格式；不要用 `pip freeze > requirements.txt` 覆蓋。

## 執行與維護

- `app.py`：Waitress 網頁服務，預設只綁定 127.0.0.1:18473。
- `worker.py`：持續領取已提交工作、檢查授權到期及更新 token。關掉此程序會停止發布。
- `instance/demo.sqlite3`、`instance/live.sqlite3`：帳號、草稿與發布工作。
- `instance/keys.json`：持久化 session 與憑證加密金鑰；可改由環境變數提供。遺失金鑰就無法解密原授權。
- 停止網頁與 worker 後備份資料庫與金鑰，金鑰備份另行保管。啟用 WAL 時不可在執行中只複製 `.sqlite3` 單一檔。
- 中斷的處理中工作在 10 分鐘後標為待確認。查核若確認尚未呼叫發布 API 才開放重試；無法確認的項目不自動重送。
- 上傳 live 圖片會存到你的 Cloudinary 帳戶。取消草稿不會自動刪除雲端素材，請依保存政策定期清理。
- 目前一次處理一項工作；一次提交多帳號不代表同秒發布。

第一階段的本機流程與模擬測試已建立，真實 Meta OAuth／文字／圖片發布仍需以翔志管理的授權帳號驗收。Facebook、Instagram、定時排程與 RSS／劇集處理尚未實作。
