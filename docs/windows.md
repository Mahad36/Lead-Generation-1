# Run on Windows (no Docker)

Everything runs on your PC: the app, its database (built in, nothing to install), the automatic audit poller and
Claude Code on your Claude subscription. These steps use `E:\Lead-Generation`; any folder works.

## 1. Get the code into E:\

Either:
- **ZIP:** while logged in to GitHub, download
  https://github.com/Mahad36/Lead-Generation/archive/refs/heads/claude/adoring-noether-tolrwm.zip, unzip it into
  `E:\`, and rename the folder to `Lead-Generation`; or
- **Git:** in PowerShell
  ```
  cd E:\
  git clone -b claude/adoring-noether-tolrwm https://github.com/Mahad36/Lead-Generation.git
  ```

## 2. One-time setup

Double-click **`E:\Lead-Generation\setup-windows.bat`**. It installs Python 3.12, Node.js, Git, Claude Code, the
screenshot browser and the app's packages (Windows may ask for permission a few times), then runs
`claude setup-token`: approve in the browser, paste the token back into the window, and it is saved to `.env`.

## 3. Start the app

Double-click **`E:\Lead-Generation\start-windows.bat`**. The first start builds the web app (a few minutes); later
starts take about 20 seconds. Your browser opens http://localhost:3000. Log in with `demo@leadgen.local` /
`DemoPass123!`.

Keep that window open: it runs the app **and the automatic audit poller**. Close it (or Ctrl+C) to stop.

## 4. Set up once in the app

1. **AI & settings → AI setup:** provider **Claude Code (your Claude subscription, no API key)**, model `opus`
   → **Save settings** → **Test AI connection**.
2. **AI & settings → Google Maps (Apify):** paste your Apify token (for scraping leads).
3. **Email setup:** connect Gmail (SMTP with a Gmail App Password) and click **Default** on it.
4. **Website audits → Automatic audits:** set **Max audits per day**, tick **Run automatically** and
   **Send emails automatically**.

New leads with a website and an email are then audited and emailed every 5 minutes while the window is open.

## Good to know

- Your data is in `E:\Lead-Generation\.localdata` (database, logs). Don't delete it; it isn't uploaded anywhere.
- Logs: `E:\Lead-Generation\.localdata\logs` (`api.log` has the audits and the poller).
- The poller pauses when the PC sleeps. To keep it running, set Windows **Settings → System → Power → Sleep** to
  *Never* while plugged in.
- Start automatically when you log in: press `Win+R`, type `shell:startup`, and put a shortcut to
  `start-windows.bat` in that folder.
- Update the code later: `git pull` in the folder (or download the ZIP again), then run `setup-windows.bat` once more.
