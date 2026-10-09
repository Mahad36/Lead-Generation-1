# Test the app in GitHub Codespaces (no installs)

Codespaces runs the whole app (database, background worker, web app and a test inbox) on a cloud computer from
GitHub. You only need a browser and your GitHub account.

## 1. Create the codespace

1. Open the repository on GitHub and switch to the branch you want to test.
2. Click **Code** → **Codespaces** tab → **Create codespace on <branch>**.
3. Wait while it sets up. The first time takes about 5–8 minutes (it installs everything). You'll see the log in the
   terminal at the bottom; it ends with a box that says **Lead Generator is running**.

## 2. Open the app

- Click the **Ports** tab (next to *Terminal*). Hover **App (3000)** and click the globe icon, or use the link printed
  in the terminal box.
- Log in with `demo@leadgen.local` / `DemoPass123!`.

The links are private: only you, signed in to GitHub, can open them.

## 3. Test the complete workflow

1. Connect Claude Code to your Claude subscription (one time, no API key). In the codespace terminal:
   ```
   claude setup-token
   ```
   Open the link it shows, approve, and copy the token it prints. Then (replace `paste-token-here`):
   ```
   echo "CLAUDE_CODE_OAUTH_TOKEN=paste-token-here" >> .env
   bash .devcontainer/start.sh
   ```
   The start box should say **Claude Code: ready**.
   In the app: **AI & settings → AI setup** → provider **Claude Code (your Claude subscription, no API key)** →
   model `opus` → **Save settings** → **Test AI connection**.
2. **Website audits** → enter a business name, its website URL and a customer email → **Run website audit**.
   The app takes desktop and phone screenshots, and Claude reviews the site like a CRO / design consultant
   (2–5 minutes).
3. Review the score and findings, click **Open PDF**, edit the email if you like → **Send email with PDF**.
4. In the **Ports** tab open **Test inbox (Mailpit) (8025)**: the email is there exactly as the customer would see it,
   with the PDF attached.

Emails go to that test inbox, so nothing reaches real people. To send for real, connect Gmail or SMTP in
**Email setup** and make it the default account.

## If something goes wrong

- **"HTTP ERROR 502" on the app link** means the app isn't running yet. The link exists as soon as the codespace
  opens, but the app needs a few minutes on first start. In the terminal run `bash .devcontainer/start.sh`, wait for the
  **Lead Generator is running** box, then reload the page. If it stops with a `!!` message instead, that message and the
  log lines under it say what failed.
- Restart everything: in the terminal run `bash .devcontainer/start.sh`.
- Logs: `ls /tmp/leadgen-logs` then e.g. `tail -50 /tmp/leadgen-logs/api.log` (also `worker.log`, `web.log`).
- Codespaces stop after 30 minutes of inactivity. Reopen it from **Code → Codespaces**; the app starts again
  automatically. Your data is kept until you delete the codespace.
- Free GitHub accounts include a monthly allowance of Codespaces hours; stop or delete the codespace when you're done
  (**Code → Codespaces → … → Stop / Delete**).
