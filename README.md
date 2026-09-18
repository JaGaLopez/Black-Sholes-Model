# Black-Scholes Model

An interactive Black-Scholes options analysis tool. It pulls option chains from Yahoo Finance daily, prices each contract and computes Greeks and implied volatility, flags mispricing, and pushes the results to Google Sheets. A Tableau Public dashboard (synced daily) visualizes them and includes a what-if pricer.

**Status:** scaffolded, and the model isn't implemented yet. See `docs/SPEC.md` for the design, `docs/GOOGLE_SETUP.md` for credentials, and `docs/FABLE_KICKOFF.md` for the build prompt.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m bsm.pipeline        # once implemented
```
