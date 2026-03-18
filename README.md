# Beautiful Feet — Evangelism Heatmap

A full-stack web app for the Beautiful Feet Lagos evangelism group to log encounters, track follow-ups, and visualise visit density on a Leaflet heatmap — synced bidirectionally with Google Sheets.

---

## 1. Google Cloud Service Account Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create or select a project.
2. Enable these APIs:
   - **Google Sheets API**
   - **Google Drive API**
3. Navigate to **IAM & Admin → Service Accounts** → **Create Service Account**.
4. Give it a name (e.g. `bfeet-sync`) and click **Create and Continue**.
5. Skip role assignment, click **Done**.
6. Click the service account → **Keys** → **Add Key → Create new key → JSON**.
7. Download the `.json` file and save it as `credentials.json` in the project root.

---

## 2. Share the Sheet with the Service Account

1. Open the service account JSON file and copy the `client_email` value (looks like `bfeet-sync@your-project.iam.gserviceaccount.com`).
2. Open your Google Sheet.
3. Click **Share** → paste the service account email → set role to **Editor** → **Send**.

---

## 3. PostgreSQL + PostGIS Setup

```bash
# Ubuntu / Debian
sudo apt install postgresql postgresql-contrib postgis

# macOS (Homebrew)
brew install postgresql postgis

# Create database
psql -U postgres -c "CREATE DATABASE bfeet;"
psql -U postgres -d bfeet -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

Or use a managed service with PostGIS support (see Deployment section).

---

## 4. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:yourpassword@localhost/bfeet
GOOGLE_CREDENTIALS_PATH=./credentials.json
GOOGLE_SHEET_ID=<your_sheet_id_from_url>
```

The Sheet ID is the long string in the Google Sheets URL:
`https://docs.google.com/spreadsheets/d/**<SHEET_ID>**/edit`

---

## 5. Running Locally

```bash
cd evangelism-heatmap

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

API docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## 6. Importing Existing Sheet Data

If you already have rows in the Google Sheet, import them into the database:

```bash
curl -X POST http://localhost:8000/api/sync/import
```

This reads all rows from the sheet and upserts them into the database. Rows without latitude/longitude will have `NULL` geometry. Rows without a `record_id` (column N) will get a generated UUID written back to the sheet on next sync.

---

## 7. Retrying Failed Syncs

If visits were logged while offline or during a Sheets API outage:

```bash
curl -X POST http://localhost:8000/api/sync/retry
```

---

## 8. Deployment

### Backend — Railway (recommended, has PostGIS addon)

1. Create a new Railway project.
2. Add a **PostgreSQL** service → in its settings, enable the **PostGIS** plugin/extension.
3. Deploy the backend from this directory (or a GitHub repo).
4. Set environment variables in Railway dashboard:
   - `DATABASE_URL` (Railway provides this automatically as `${{Postgres.DATABASE_URL}}`)
   - `GOOGLE_CREDENTIALS_PATH` — upload `credentials.json` to the project and reference it
   - `GOOGLE_SHEET_ID`
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Frontend — Netlify (static)

Since the frontend is a single `index.html` file served by FastAPI, deploying the whole app together on Railway is simplest.

For a separate Netlify deploy:
1. Change `const API_BASE = ''` in `frontend/index.html` to your Railway backend URL.
2. Deploy the `frontend/` folder to Netlify.

---

## Column Mapping (Google Sheet)

| Col | Field |
|-----|-------|
| A | person_name |
| B | prayer_level |
| C | evangelisers |
| D | status |
| E | date_of_evangelism (DD/MM/YYYY) |
| F | date_of_accepting_christ |
| G | *(empty)* |
| H | notes |
| I | phone_numbers |
| J | location_area |
| K | latitude |
| L | longitude |
| M | follow_up_status |
| N | record_id (UUID — do not edit) |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/visits` | Log a new visit |
| GET | `/api/visits` | List visits (paginated, filterable) |
| PUT | `/api/visits/{record_id}` | Update status/notes |
| GET | `/api/heatmap` | Heatmap coordinate density |
| GET | `/api/visits/geojson` | GeoJSON FeatureCollection |
| GET | `/api/stats` | Summary statistics |
| GET | `/api/teams` | Distinct team names |
| GET | `/api/evangelisers` | Distinct evangeliser names |
| POST | `/api/sync/retry` | Retry failed sheet syncs |
| POST | `/api/sync/import` | Import all sheet rows to DB |
