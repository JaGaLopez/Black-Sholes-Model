# Tableau

**Data source:** Tableau Public › Connect › Google Drive › **BSM Data** (tabs `options_latest`, `iv_history`),
related on `ticker` and `expiry`. When publishing, tick the **keep data in sync** checkbox so the published viz
refreshes daily.

- **[BUILD_GUIDE.md](BUILD_GUIDE.md)** has step-by-step instructions for all seven sheets, the dashboard,
  and publishing. It includes the exact calculated-field formulas for the what-if pricer (verified against
  the Python model).
- Keep a downloaded `bsm_dashboard.twbx` of the published workbook here for version control.
