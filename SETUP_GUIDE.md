# Setup guide — getting your live daily MLB board online

This walks you from zero to a public URL that rebuilds itself every morning.
No prior GitHub experience needed. Budget about 15–20 minutes. You'll do all
of it in a web browser — nothing to install on your computer.

There are two things to understand up front:

1. **GitHub** hosts your code and runs the daily job for free.
2. **GitHub Pages** turns the output into a public web page at a URL like
   `https://YOURNAME.github.io/mlb-board/`.

The daily job (a "GitHub Action") runs the Python script on GitHub's computers,
builds the dashboard, and publishes it to that URL. You never have to run
anything yourself after setup.

---

## Step 1 — Make a GitHub account

If you don't have one: go to https://github.com and sign up (free). Verify your
email. That's it.

---

## Step 2 — Create a new repository

A "repository" (repo) is just a folder for your project.

1. Click the **+** in the top-right of GitHub → **New repository**.
2. **Repository name:** `mlb-board` (this becomes part of your URL).
3. Set it to **Public**. (Pages is free for public repos. Your API key will
   NOT be public — it goes in a separate secret vault, see Step 4.)
4. Leave everything else default. Click **Create repository**.

You'll land on an empty repo page with setup instructions. Ignore those.

---

## Step 3 — Upload the project files

The easiest way, no command line:

1. On your new repo page, click **uploading an existing file** (it's a link in
   the "Quick setup" box), or go to the **Add file** button → **Upload files**.
2. Drag in **all** the files and folders from this project:
   - `generate_dashboard.py`
   - `mlb_data.py`
   - `mlb_model.py`
   - `mlb_odds.py`
   - `mlb_props.py`
   - `requirements.txt`
   - `README.md`
   - `.gitignore`
   - the `.github` folder (contains the daily-job workflow)

   **Important:** the `.github` folder must come along — it holds the automation.
   If drag-and-drop won't take the folder, see the note at the bottom.
3. Scroll down, click **Commit changes**.

---

## Step 4 — Add your API key as a secret

Your balldontlie key must never be in the code. GitHub has a secure vault for it.

1. In your repo, click **Settings** (top menu).
2. Left sidebar: **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. **Name:** `BALLDONTLIE_API_KEY` (exactly this — capitals and underscores).
5. **Secret:** paste your key.
6. Click **Add secret**.

> Reminder: since your key was shared in a chat earlier, rotate it in your
> balldontlie account first, then paste the new one here.

---

## Step 5 — Turn on GitHub Pages

1. Still in **Settings**, click **Pages** in the left sidebar.
2. Under **Build and deployment** → **Source**, choose **GitHub Actions**.
   (Not "Deploy from a branch" — pick **GitHub Actions**.)
3. That's all — no save button; it saves automatically.

---

## Step 6 — Run it for the first time

The job runs automatically each morning, but let's trigger it now so you don't
have to wait.

1. Click the **Actions** tab (top menu).
2. If GitHub asks you to enable Actions, click the green **enable** button.
3. On the left, click the workflow named **Build and deploy MLB board**.
4. On the right, click **Run workflow** → in the little dropdown, **Run workflow**
   again (the green button).
5. Wait ~1–2 minutes. Refresh the page. You'll see a run appear with a spinning
   yellow dot that turns into a green check when it's done.

---

## Step 7 — Open your live URL

1. Go back to **Settings → Pages**.
2. At the top you'll see: **"Your site is live at https://YOURNAME.github.io/mlb-board/"**.
3. Click it. That's your board. Bookmark it.

From now on it rebuilds every morning automatically. You can also hit
**Run workflow** anytime (Step 6) to refresh it on demand — useful when
probable pitchers or odds update closer to game time.

---

## What "daily" means here

The schedule is set to **15:00 UTC** (about 11 AM Eastern). MLB posts probable
pitchers and opening odds through the morning, so this timing catches most of
them. Some late games may not have a probable pitcher or props until an hour or
two before first pitch — re-run the workflow later in the day to pick those up.

To change the time: open `.github/workflows/deploy.yml`, find the `cron:` line,
and change the hour. Cron is in UTC, so subtract 4 (daylight) or 5 (standard)
to get Eastern. For example, `"0 16 * * *"` is noon ET in summer.

---

## Troubleshooting

**The Actions run failed (red X).**
Click the failed run → click the `build` job → read the red step. Most common
causes:
- API key secret missing or misnamed — recheck Step 4 (must be exactly
  `BALLDONTLIE_API_KEY`).
- balldontlie rate limit or outage — just re-run it in a few minutes.

**The page loads but says no games / no odds.**
Either there are genuinely no games that day, or you ran it before odds were
posted. Re-run later. The script is built to degrade gracefully: if odds are
missing it still shows the games with straight-winner picks.

**The `.github` folder wouldn't upload by drag-and-drop.**
Some browsers hide dotfiles. Instead: on your repo, **Add file → Create new file**,
and in the filename box type `.github/workflows/deploy.yml` (typing the slashes
creates the folders). Then paste the contents of the `deploy.yml` from this
project and commit.

**I want it private.**
Free Pages requires a public repo. Keeping it private needs GitHub Pro or a
different host with a login — more setup. The repo being public exposes your
picks page, but never your API key (that stays in the secret vault).

---

## A last word on the picks

This is a research tool, not a tipsheet. The game model is simple and its
backtest only checks winner accuracy, not profit against the closing line.
The prop leans are even lighter — a plain season-rate estimate with no backtest
at all. A green "value" tag means the model disagrees with the market, not that
it's right. Bet only what you can afford to lose. If it stops being fun or you're
chasing losses, stop. **National Problem Gambling Helpline: 1-800-522-4700.**
