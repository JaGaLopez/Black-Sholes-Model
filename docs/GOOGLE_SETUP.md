# Google Sheets setup (do this yourself: about 10 minutes)

Tableau reads from your Google Drive through your own login. The Python pipeline, though, needs its own
credential so it can write to the sheet unattended (including from GitHub Actions). That credential is a **service account**.

1. **Create a project** at https://console.cloud.google.com, e.g. named `bsm-tool`.
2. **Enable the APIs**: APIs & Services → Library → enable **Google Sheets API** and **Google Drive API**.
3. **Create a service account**: IAM & Admin → Service Accounts → Create. No roles are needed.
4. **Create a key**: open the service account → Keys → Add key → JSON. Save the downloaded file as
   `secrets/service_account.json` in this repo. It's already gitignored, so **never commit it**.
5. **Create the sheet**: in Google Drive, make a blank spreadsheet named exactly **BSM Data**.
6. **Share the sheet** with the service account's email (`...@bsm-tool.iam.gserviceaccount.com`, found in the JSON under `client_email`) as **Editor**.
7. **Configure the environment**: `cp .env.example .env`. The default path already points at `secrets/service_account.json`.
8. **For GitHub Actions**: repo Settings → Secrets and variables → Actions → New secret `GOOGLE_SERVICE_ACCOUNT_JSON`, and paste the full JSON contents.

Quick check once the key is in place:
```bash
./.venv/bin/python -c "import gspread; print(gspread.service_account('secrets/service_account.json').open('BSM Data').title)"
```
