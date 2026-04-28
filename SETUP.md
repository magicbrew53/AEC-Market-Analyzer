# RevWin Market Analysis — Complete Deployment Guide

## What you're deploying

| Service | What it does | Cost |
|---|---|---|
| **GitHub** | Source control | Free |
| **Neon** | PostgreSQL database (job tracking) | Free tier |
| **Vercel** | Next.js frontend + Blob file storage | Free tier |
| **Railway** | Python FastAPI backend (runs the report pipeline) | ~$5/mo |

---

## Before you start — collect these files

You'll need to copy files from your existing Python project into `backend/`:

```
From: C:\dev\Market Analysis\revwin_market_analysis\
To:   C:\dev\revwin-web\backend\

Copy these folders/files:
  lib\ingest.py       → backend\lib\ingest.py
  lib\resolve.py      → backend\lib\resolve.py
  lib\compute.py      → backend\lib\compute.py
  lib\charts.py       → backend\lib\charts.py
  lib\narrative.py    → backend\lib\narrative.py
  lib\research.py     → backend\lib\research.py
  lib\forecast.py     → backend\lib\forecast.py
  lib\docx_render.py  → backend\lib\docx_render.py
  data\enr\           → backend\data\enr\        (all 21 .xlsx files)
  data\cci.xlsx       → backend\data\cci.xlsx
  data\fmi_forecast.json → backend\data\fmi_forecast.json
  data\research\      → backend\data\research\   (any firm .json files)
```

---

## Step 1 — Neon (database)

1. Go to **neon.tech** → Sign up / Log in
2. **Create a new project** → name it `revwin-market-analysis`
3. On the project dashboard, click **Connection Details**
4. Copy the **Connection string** — it looks like:
   `postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`
5. Save it somewhere — you'll use it in Steps 3 and 5

---

## Step 2 — Push the repo to GitHub

Open a terminal in `C:\dev\revwin-web\` and run:

```bash
git add .
git commit -m "Initial web app"
git push -u origin main
```

---

## Step 3 — Create the database table

From `C:\dev\revwin-web\frontend\` in a terminal:

```bash
npm install
```

Create a file `frontend\.env.local` with this content (replace the string):
```
DATABASE_URL=postgresql://user:password@ep-xxx.neon.tech/neondb?sslmode=require
```

Then run:
```bash
npx prisma db push
```

You should see: `Your database is now in sync with your Prisma schema.`

---

## Step 4 — Vercel (frontend + blob storage)

### 4a. Deploy the Next.js frontend

1. Go to **vercel.com** → Log in with GitHub
2. Click **Add New → Project**
3. Import the `AEC-Market-Analyzer` GitHub repo
4. **IMPORTANT:** Set **Root Directory** to `frontend`
5. Vercel will auto-detect **Next.js** — click **Deploy**
   (It will fail the first time — that's fine, we need to add env vars next)
6. After deploy attempt, go to **Project Settings → Environment Variables**
7. Add these variables:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Your Neon connection string from Step 1 |
   | `BACKEND_URL` | Leave blank for now (fill in after Step 5) |
   | `BACKEND_API_SECRET` | Make up a random password, e.g. `MySecret2025!` |

8. Go to **Deployments** → click the three dots on the latest → **Redeploy**

### 4b. Create Blob storage

1. In Vercel, go to your project → **Storage** tab
2. Click **Create** → select **Blob**
3. Name it `revwin-reports` → Create
4. You'll see a `BLOB_READ_WRITE_TOKEN` variable — copy it
5. Also go to **Settings → Environment Variables** and verify
   `BLOB_READ_WRITE_TOKEN` was automatically added

---

## Step 5 — Upload data files to Vercel Blob

This is a one-time step. Run from `C:\dev\revwin-web\backend\`:

```bash
pip install -r requirements.txt
```

Create `backend\.env` with this content:
```
BLOB_READ_WRITE_TOKEN=vercel_blob_rw_xxx...   ← from Step 4b
DATABASE_URL=postgresql://...                  ← from Step 1
ANTHROPIC_API_KEY=sk-ant-...
BACKEND_API_SECRET=MySecret2025!              ← same as Step 4a
```

Then upload all the data files to Vercel Blob:
```bash
cd backend
python upload_data.py --data-dir ./data
```

You should see each file print `OK`. This uploads the 21 ENR xlsx files and
the cci/fmi files. Railway will download them automatically on startup.

---

## Step 6 — Railway (Python backend)

1. Go to **railway.app** → Log in with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Select `AEC-Market-Analyzer`
4. Railway will ask for the root directory — set it to `backend`
5. It will detect Python and start building
6. Go to **Service → Variables** and add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon connection string |
   | `ANTHROPIC_API_KEY` | Your Anthropic key |
   | `BLOB_READ_WRITE_TOKEN` | From Step 4b |
   | `BACKEND_API_SECRET` | Same secret as Step 4a |
   | `ALLOWED_ORIGINS` | Your Vercel app URL (e.g. `https://aec-market-analyzer.vercel.app`) |

7. After the deploy finishes, go to **Service → Settings → Networking**
8. Click **Generate Domain** — copy the URL (e.g. `https://xxx.railway.app`)

---

## Step 7 — Connect frontend to backend

1. Go back to **Vercel → Project Settings → Environment Variables**
2. Edit `BACKEND_URL` → paste the Railway URL from Step 6
3. Go to **Deployments** → Redeploy

---

## Step 8 — Test it

1. Open your Vercel URL (e.g. `https://aec-market-analyzer.vercel.app`)
2. Type `HDR` in the firm name box
3. Check **Skip AI narratives** to run a fast test
4. Click **Generate Report**
5. Watch the progress bar — it should complete in 1–2 minutes
6. Download the `.docx` when done

---

## Updating data files in the future

When you get new ENR files or update `fmi_forecast.json`:

```bash
cd backend
python upload_data.py --data-dir ./data
```

Railway will pick up the new files on the next report generation (etag-based caching).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Firm not found" | Firm name doesn't match ENR data — try the full name or a common abbreviation |
| Job stuck at 0% | Check Railway logs — likely a startup error or missing env var |
| Download link 404 | Vercel Blob token may be wrong — verify `BLOB_READ_WRITE_TOKEN` in Railway vars |
| Narratives fail | Check `ANTHROPIC_API_KEY` in Railway vars |
| DB errors | Run `npx prisma db push` again from `frontend/` |
