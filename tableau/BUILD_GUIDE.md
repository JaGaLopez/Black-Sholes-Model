# Tableau Public build guide

Step-by-step instructions for building the BSM dashboard in **Tableau Public (desktop app)** from the
Google Sheet **BSM Data**. Build the sheets in order: later sheets reuse parameters and calculated
fields from earlier ones.

Conventions used throughout (they match the Python pipeline):

- Rates, vols and yields are **decimals** (0.04 = 4%). Format them as percentages in Tableau; never type 4 for 4%.
- Time is **calendar days / 365**.
- Theta is **per calendar day**, vega **per 1 vol point**, rho **per 1% rate move**.
- `type` values are lowercase: `call` / `put`.

Notation: `[p Something]` is a **parameter**, `[WI ...]`, `[PO ...]`, `[L ...]` are **calculated fields** you create.
Create calculated fields with **Analysis › Create Calculated Field…** and paste the formula exactly.

> **Operator-precedence trap.** In Tableau, unary minus binds *tighter* than `^`, so `-[x]^2` means
> `(-[x])^2 = +x²`. Every formula below writes squares as `[x] * [x]` inside `EXP(-0.5 * ...)` to avoid it.
> Keep that form if you edit them.

---

## 0. Connect the data source

1. Open Tableau Public. In the **Connect** pane choose **To a Server › Google Drive** and sign in with the
   Google account that owns **BSM Data**.
2. Pick **BSM Data**. On the Data Source page you'll see three sheets: `options_latest`, `iv_history`, `run_log`.
3. Drag **options_latest** to the canvas. Then drag **iv_history** next to it. Tableau creates a
   **relationship** (the "noodle"). Click it and set:
   - `ticker` = `ticker`
   - **Add more fields**: `expiry` = `expiry`
   Leave Performance options at their defaults (Many-to-Many, Some records match).
4. Leave `run_log` out of the model (optional: add it as a separate data source for a freshness badge).
5. Check field types in the grid. The pipeline writes ISO dates and plain numbers, so you should see:

   | field | expected type |
   |---|---|
   | `expiry` (both tables), `date` (iv_history) | Date |
   | `snapshot_ts` | String (ISO timestamp, fixed below) |
   | `type`, `ticker`, `contract_symbol` | String |
   | everything else | Number (decimal) |

   If a numeric column shows as String, click the type icon and change it to **Number (decimal)**.
6. Because both tables contain `ticker`, `expiry`, `snapshot_ts`, `days_to_expiry`, `spot`, `r`,
   `realized_vol_30d`, Tableau renames the iv_history copies, e.g. **`ticker (iv_history)`**.
   The formulas below use those names. If your build shows a different suffix, adjust.

### Data-source-level calculated fields

**Snapshot UTC** (turns the ISO string into a datetime):
```
DATEPARSE("yyyy-MM-dd'T'HH:mm:ss'Z'", [snapshot_ts])
```

**OTM** (out-of-the-money side of the chain, gives a clean smile/surface):
```
IIF([type] = "call", [strike] >= [spot], [strike] < [spot])
```

**Moneyness Bucket** (2.5% buckets of K/S; right-click › Convert to Dimension, then Discrete):
```
ROUND([moneyness] / 0.025) * 0.025
```

**Abs Mispricing %**:
```
ABS([mispricing_pct])
```

**Latest Day (iv_history)**:
```
[date] = {FIXED : MAX([date])}
```

**Nearest 30D Expiry (iv_history)** (one constant-maturity-ish row per ticker per day):
```
ABS([days_to_expiry (iv_history)] - 30)
  = {FIXED [ticker (iv_history)], [date] : MIN(ABS([days_to_expiry (iv_history)] - 30))}
```

### The global ticker selector (a parameter, not a filter)

Create the parameter **p Ticker**: Data type **String**, Allowable values **List**,
**When workbook opens › Value from field: ticker** (this keeps the list current as the config changes),
current value `SPY`. Right-click it › **Show Parameter**.

Then create two filter calcs:

**Ticker Match** (for options_latest sheets):
```
[ticker] = [p Ticker]
```
**Ticker Match (iv_history)** (for history sheets):
```
[ticker (iv_history)] = [p Ticker]
```

Why a parameter: with a relationship, a quick filter on an `options_latest` field applied to a sheet that
only uses `iv_history` would drop every history row whose expiry is no longer listed. A parameter works
on both tables without that side effect.

### Other global filters (options sheets only)

On Sheet 1 add quick filters for **expiry** (multiple values, discrete), **type**, and **moneyness**
(range of values, default 0.70 to 1.30). For each: filter card menu › **Apply to Worksheets ›
Selected Worksheets…** and tick Sheets 1–4 only. Do **not** apply them to Sheet 7 (history).

---

## Sheet 1 — Volatility smile

1. New worksheet, rename **Volatility Smile**.
2. Filters: **Ticker Match** = True, **OTM** = True. Also drag `iv_model` to Filters › **Special › Non-null values**.
3. Columns: `strike` (continuous dimension: right-click the pill › **Dimension**, keep it green/continuous).
4. Rows: `iv_model` › **Average**. Format as Percentage (1 decimal).
5. Marks: **Line**. Drag `expiry` to **Color** (right-click › **Exact Date**, discrete).
6. Optional: drag `iv_yahoo` to Rows, make it **Dual Axis**, **Synchronize Axis**, set its mark to **Circle**
   at low opacity to compare our solver with Yahoo's IV.
7. Reference line: Analytics pane › **Reference Line** on the strike axis, value **Average spot**, label "Spot".
8. Tooltip: `contract_symbol`, `type`, `mid`, `iv_model`, `iv_yahoo`, `open_interest`.

## Sheet 2 — IV surface / term structure

**2a. Surface heatmap**

1. New worksheet **IV Surface**. Filters: **Ticker Match** = True, **OTM** = True, `iv_model` non-null.
2. Columns: `expiry` (Exact Date, discrete) — or `days_to_expiry` as a discrete dimension if you prefer DTE labels.
3. Rows: **Moneyness Bucket** (discrete). Sort descending so high strikes are on top.
4. Marks: **Square**. Color: `iv_model` › Average, sequential palette (e.g. Orange-Gold), Percentage format.
5. Label: `iv_model` Average, format 0.0%.

**2b. Term structure (ATM IV by expiry, latest day)**

1. New worksheet **Term Structure**. Filters: **Ticker Match (iv_history)** = True, **Latest Day (iv_history)** = True.
2. Columns: `days_to_expiry (iv_history)` (continuous dimension).
3. Rows: `atm_iv` › Average; add `realized_vol_30d (iv_history)` › Average, Dual Axis, Synchronize.
4. Marks: Line for ATM IV (with circle markers), Line dashed for realized vol.

## Sheet 3 — Greeks by strike (small multiples)

1. New worksheet **Greeks by Strike**. Filters: **Ticker Match** = True, `expiry` = a single expiry
   (show the filter as **Single Value (dropdown)**).
2. Columns: `strike` (continuous dimension).
3. Rows, in order: `delta`, `gamma`, `vega`, `theta`, each set to **Average**. Each gets its own axis,
   which matters because gamma is two orders of magnitude smaller than delta.
4. Marks (All): **Line**. Color: `type`.
5. Reference line on strike: Average `spot`.
6. Axis titles: "Delta", "Gamma", "Vega (per vol pt)", "Theta (per day)".

## Sheet 4 — Mispricing scanner

Mispricing here is **market mid − BSM price at 30-day realized vol**. It is a volatility-premium screen,
not an arbitrage signal: a large positive number means options are priced richer than recent realized
movement would justify.

**4a. Scatter**

1. New worksheet **Mispricing Scatter**. Filters: **Ticker Match** = True, `spread_pct` at most 0.25
   (wide quotes make mid unreliable), `open_interest` at least 50 (optional).
2. Columns: `moneyness` (continuous dimension). Rows: `mispricing_pct` › Average, format Percentage.
3. Marks: **Circle**. Size: `open_interest` › Sum. Color: `type`.
4. Detail (needed for the parameter actions in Sheet 5): `contract_symbol`, `strike`, `spot`,
   `days_to_expiry`, `iv_model`, `r`, `q`, `type`, `expiry`.
5. Reference line on Rows: constant 0.

**4b. Table**

1. New worksheet **Mispricing Table**. Same filters as 4a.
2. Rows (discrete): `contract_symbol`, `type`, `expiry`, `strike`.
3. Measure Values: `mid`, `bs_price_realized`, `mispricing`, `mispricing_pct`, `iv_model`,
   `realized_vol_30d`, `open_interest`, `spread_pct` (all Average except `open_interest` Sum).
4. Sort `contract_symbol` by **Abs Mispricing %**, descending (Sort › Field › Abs Mispricing % › Average).
5. Filter **Abs Mispricing %** › Top 25 by field if you want a short list.
6. Add the same Detail fields as 4a so the table can drive the pricer too.

## Sheet 5 — What-if pricer (parameters + BSM in calculated fields)

Everything here is computed from parameters, so it recalculates instantly in the browser on Tableau Public,
with no Python round-trip.

### Why there's no `NORM.DIST`

Tableau's function list (Number functions) has no normal CDF or `erf`. The standard normal CDF is
implemented below with **Abramowitz & Stegun 26.2.17**, whose maximum absolute error is **7.5e-8**.
Checked against the Python model (scipy `ndtr`) over x ∈ [−8, 8], the largest error was 7.45e-8, and
prices agree with the Python model to about 1e-5.

### Parameters

Create each with **right-click in the Data pane › Create Parameter…**, then **Show Parameter**.

| name | data type | current value | allowable values | display format |
|---|---|---|---|---|
| p Spot | Float | 100 | All | Number, 2 dp |
| p Strike | Float | 105 | All | Number, 2 dp |
| p Vol | Float | 0.30 | Range 0.01 – 2.00, step 0.01 | Percentage, 1 dp |
| p Rate | Float | 0.04 | Range 0.00 – 0.10, step 0.0025 | Percentage, 2 dp |
| p Div Yield | Float | 0.01 | Range 0.00 – 0.08, step 0.0025 | Percentage, 2 dp |
| p Days | Integer | 30 | Range 1 – 730, step 1 | Number, 0 dp |
| p Type | String | call | List: `call` (display "Call"), `put` (display "Put") | — |

`p Days` has a minimum of 1 on purpose: at T = 0 the formulas divide by zero (Tableau returns NULL).

### Calculated fields (create in this order)

**WI T**
```
[p Days] / 365
```

**WI sqrtT**
```
SQRT([WI T])
```

**WI d1**
```
(LN([p Spot] / [p Strike]) + ([p Rate] - [p Div Yield] + 0.5 * [p Vol] * [p Vol]) * [WI T])
  / ([p Vol] * [WI sqrtT])
```

**WI d2**
```
[WI d1] - [p Vol] * [WI sqrtT]
```

**WI pdf d1** (standard normal density at d1)
```
EXP(-0.5 * [WI d1] * [WI d1]) / SQRT(2 * PI())
```

**WI pdf d2**
```
EXP(-0.5 * [WI d2] * [WI d2]) / SQRT(2 * PI())
```

**WI t d1**
```
1 / (1 + 0.2316419 * ABS([WI d1]))
```

**WI t d2**
```
1 / (1 + 0.2316419 * ABS([WI d2]))
```

**WI tail d1** (upper-tail probability Q(|d1|), Abramowitz & Stegun 26.2.17)
```
[WI pdf d1] * [WI t d1] * (0.319381530 + [WI t d1] * (-0.356563782 + [WI t d1] * (1.781477937
  + [WI t d1] * (-1.821255978 + [WI t d1] * 1.330274429))))
```

**WI tail d2**
```
[WI pdf d2] * [WI t d2] * (0.319381530 + [WI t d2] * (-0.356563782 + [WI t d2] * (1.781477937
  + [WI t d2] * (-1.821255978 + [WI t d2] * 1.330274429))))
```

**WI N d1** (standard normal CDF at d1)
```
IIF([WI d1] >= 0, 1 - [WI tail d1], [WI tail d1])
```

**WI N d2**
```
IIF([WI d2] >= 0, 1 - [WI tail d2], [WI tail d2])
```

**WI Call**
```
[p Spot] * EXP(-[p Div Yield] * [WI T]) * [WI N d1]
  - [p Strike] * EXP(-[p Rate] * [WI T]) * [WI N d2]
```

**WI Put**
```
[p Strike] * EXP(-[p Rate] * [WI T]) * (1 - [WI N d2])
  - [p Spot] * EXP(-[p Div Yield] * [WI T]) * (1 - [WI N d1])
```

**WI Price**
```
IIF([p Type] = "call", [WI Call], [WI Put])
```

**WI Delta**
```
IIF([p Type] = "call",
    EXP(-[p Div Yield] * [WI T]) * [WI N d1],
    EXP(-[p Div Yield] * [WI T]) * ([WI N d1] - 1))
```

**WI Gamma**
```
EXP(-[p Div Yield] * [WI T]) * [WI pdf d1] / ([p Spot] * [p Vol] * [WI sqrtT])
```

**WI Vega** (per 1 vol point)
```
[p Spot] * EXP(-[p Div Yield] * [WI T]) * [WI pdf d1] * [WI sqrtT] / 100
```

**WI Theta** (per calendar day)
```
( -[p Spot] * EXP(-[p Div Yield] * [WI T]) * [WI pdf d1] * [p Vol] / (2 * [WI sqrtT])
  + IIF([p Type] = "call",
        -[p Rate] * [p Strike] * EXP(-[p Rate] * [WI T]) * [WI N d2]
          + [p Div Yield] * [p Spot] * EXP(-[p Div Yield] * [WI T]) * [WI N d1],
         [p Rate] * [p Strike] * EXP(-[p Rate] * [WI T]) * (1 - [WI N d2])
          - [p Div Yield] * [p Spot] * EXP(-[p Div Yield] * [WI T]) * (1 - [WI N d1]))
) / 365
```

**WI Rho** (per 1% rate move)
```
IIF([p Type] = "call",
     [p Strike] * [WI T] * EXP(-[p Rate] * [WI T]) * [WI N d2],
    -[p Strike] * [WI T] * EXP(-[p Rate] * [WI T]) * (1 - [WI N d2])) / 100
```

**WI Intrinsic**
```
IIF([p Type] = "call", MAX([p Spot] - [p Strike], 0), MAX([p Strike] - [p Spot], 0))
```

**WI Time Value**
```
[WI Price] - [WI Intrinsic]
```

**WI Breakeven** (underlying price at expiry where a long position breaks even)
```
IIF([p Type] = "call", [p Strike] + [WI Price], [p Strike] - [WI Price])
```

### Build the sheet

1. New worksheet **What-If Pricer**.
2. Drag **Measure Names** to Rows and **Measure Values** to Text.
3. In the Measure Values card keep only: WI Price, WI Delta, WI Gamma, WI Vega, WI Theta, WI Rho,
   WI Intrinsic, WI Time Value, WI Breakeven.
4. Each value is the same on every row of the data, so the aggregation must not sum them. If a pill reads
   `SUM(WI Price)`, right-click › **Measure › Minimum**. If it reads `AGG(...)` it is already correct.
5. Format: Price/Intrinsic/Time Value/Breakeven as currency (2 dp); Greeks 4 dp.
6. Show all seven parameter controls; use sliders for Vol, Rate, Div Yield, Days.

### Check your formulas

Set the parameters to Spot 100, Strike 105, Vol 30%, Rate 4%, Div Yield 1%, Days 30.
You should see (Python model values, rounded):

| | Price | Delta | Gamma | Vega | Theta | Rho |
|---|---|---|---|---|---|---|
| call | 1.6364 | 0.3098 | 0.0410 | 0.1011 | −0.0529 | 0.0241 |
| put | 6.3739 | −0.6893 | 0.0410 | 0.1011 | −0.0442 | −0.0619 |

The A&S approximation may move the 4th decimal of the price by at most 1. Anything larger means a typo.

### Load a real contract into the pricer (parameter actions)

On the dashboard (section 8), add **Dashboard › Actions › Add Action › Change Parameter** once per row
below. Source sheets: **Mispricing Scatter** and **Mispricing Table**. Run action on: **Select**.
Aggregation: **Average** (or **None** where offered).

| action name | target parameter | source field |
|---|---|---|
| Load spot | p Spot | spot |
| Load strike | p Strike | strike |
| Load vol | p Vol | iv_model |
| Load rate | p Rate | r |
| Load div | p Div Yield | q |
| Load days | p Days | days_to_expiry |
| Load type | p Type | type |
| Load contract | p Contract | contract_symbol (see Sheet 6) |

Clicking a contract then prices it at its own implied vol, which should reproduce its market mid.
From there, drag the sliders to ask "what if vol drops 5 points" or "what if we're 10 days closer".
If a contract has no `iv_model` (solver returned blank, usually deep in-the-money), the Vol parameter keeps
its previous value.

### Optional: model vs market across strikes at your vol

A row-level variant of the same math that uses each contract's strike and expiry with your **p Vol**,
**p Rate**, **p Div Yield**, and **p Spot**:

**L T**
```
[days_to_expiry] / 365
```
**L d1**
```
(LN([p Spot] / [strike]) + ([p Rate] - [p Div Yield] + 0.5 * [p Vol] * [p Vol]) * [L T])
  / ([p Vol] * SQRT([L T]))
```
**L d2**
```
[L d1] - [p Vol] * SQRT([L T])
```
**L tail d1**
```
EXP(-0.5 * [L d1] * [L d1]) / SQRT(2 * PI())
  * (1 / (1 + 0.2316419 * ABS([L d1])))
  * (0.319381530 + (1 / (1 + 0.2316419 * ABS([L d1]))) * (-0.356563782
  + (1 / (1 + 0.2316419 * ABS([L d1]))) * (1.781477937
  + (1 / (1 + 0.2316419 * ABS([L d1]))) * (-1.821255978
  + (1 / (1 + 0.2316419 * ABS([L d1]))) * 1.330274429))))
```
**L tail d2**
```
EXP(-0.5 * [L d2] * [L d2]) / SQRT(2 * PI())
  * (1 / (1 + 0.2316419 * ABS([L d2])))
  * (0.319381530 + (1 / (1 + 0.2316419 * ABS([L d2]))) * (-0.356563782
  + (1 / (1 + 0.2316419 * ABS([L d2]))) * (1.781477937
  + (1 / (1 + 0.2316419 * ABS([L d2]))) * (-1.821255978
  + (1 / (1 + 0.2316419 * ABS([L d2]))) * 1.330274429))))
```
**L N d1**
```
IIF([L d1] >= 0, 1 - [L tail d1], [L tail d1])
```
**L N d2**
```
IIF([L d2] >= 0, 1 - [L tail d2], [L tail d2])
```
**L Price**
```
IIF([type] = "call",
    [p Spot] * EXP(-[p Div Yield] * [L T]) * [L N d1] - [strike] * EXP(-[p Rate] * [L T]) * [L N d2],
    [strike] * EXP(-[p Rate] * [L T]) * (1 - [L N d2]) - [p Spot] * EXP(-[p Div Yield] * [L T]) * (1 - [L N d1]))
```

Sheet **Model vs Market**: filters Ticker Match = True, one `expiry`, one `type`. Columns `strike`
(continuous). Rows `mid` (Average, circles) and `L Price` (Average, line), Dual Axis, Synchronize.

## Sheet 6 — Payoff diagram

P&L at expiry versus the underlying price for one selected contract. The x-axis reuses the strikes of the
selected ticker as a price grid (it spans the ±30% moneyness band the pipeline keeps).

### Parameters

| name | data type | current value | allowable values |
|---|---|---|---|
| p Contract | String | any symbol, e.g. from the Mispricing Table | List › **When workbook opens › Value from field: contract_symbol** |
| p Position | String | long | List: `long` (display "Long"), `short` (display "Short") |
| p Contracts | Integer | 1 | Range 1 – 100, step 1 |

### Calculated fields

**Sel Ticker**
```
{FIXED : MAX(IF [contract_symbol] = [p Contract] THEN [ticker] END)}
```
**Sel Type**
```
{FIXED : MAX(IF [contract_symbol] = [p Contract] THEN [type] END)}
```
**Sel Strike**
```
{FIXED : MAX(IF [contract_symbol] = [p Contract] THEN [strike] END)}
```
**Sel Premium** (market mid per share)
```
{FIXED : MAX(IF [contract_symbol] = [p Contract] THEN [mid] END)}
```
**Sel Spot**
```
{FIXED : MAX(IF [contract_symbol] = [p Contract] THEN [spot] END)}
```
**PO In Grid** (rows of the same underlying, used as the price grid)
```
[ticker] = [Sel Ticker]
```
**PO Payoff per Share** (intrinsic value at expiry if the underlying finishes at this grid price)
```
IIF([Sel Type] = "call", MAX([strike] - [Sel Strike], 0), MAX([Sel Strike] - [strike], 0))
```
**PO P&L** (per position: 100 shares per contract)
```
([PO Payoff per Share] - [Sel Premium])
  * IIF([p Position] = "long", 1, -1) * 100 * [p Contracts]
```
**PO Breakeven**
```
IIF([Sel Type] = "call", [Sel Strike] + [Sel Premium], [Sel Strike] - [Sel Premium])
```
**PO Title**
```
[p Position] + " " + STR([p Contracts]) + " x " + [p Contract]
  + "  premium " + STR(ROUND([Sel Premium], 2))
```

### Build the sheet

1. New worksheet **Payoff**. Filter: **PO In Grid** = True.
2. Columns: `strike` (continuous dimension), axis title "Underlying price at expiry".
3. Rows: **PO P&L** › Average (every row at a given grid price has the same P&L).
4. Marks: **Line** (or Area). Color by sign: create **PO Profit?** = `AVG([PO P&L]) >= 0` and put it on Color.
5. Reference lines on the x-axis: **Sel Strike** (label "Strike"), **Sel Spot** (label "Spot"),
   **PO Breakeven** (label "Breakeven"), all as Average. On the y-axis: constant 0.
6. Title: insert **PO Title** (Attribute) into the sheet title.

## Sheet 7 — IV vs realized vol over time (volatility risk premium)

Built entirely from `iv_history`, which gains about 30 rows per weekday. The chart will show a single day at
first and fills in as the pipeline runs.

1. New worksheet **IV vs RV**. Filters: **Ticker Match (iv_history)** = True,
   **Nearest 30D Expiry (iv_history)** = True.
2. Columns: `date` › **Exact Date**, continuous (green).
3. Rows: `atm_iv` › Average and `realized_vol_30d (iv_history)` › Average. Dual Axis, Synchronize, Percentage format.
4. Marks: Line for both. Colors: IV in a strong color, RV dashed grey.
5. Optional third row: `vol_risk_premium` › Average as bars (positive = IV rich vs realized).
6. Optional: `skew_25d` › Average on a separate row to track put skew over time.

---

## 8. Dashboard

1. **Dashboard › New Dashboard**, size **Automatic** (or Fixed 1400 × 1000).
2. Suggested layout:
   - Top bar: title, **p Ticker** control, expiry/type/moneyness filters, and a text object
     "Data as of <MAX(Snapshot UTC)>" (build it as a tiny sheet with `MAX([Snapshot UTC])` on Text).
   - Row 1: Volatility Smile | IV Surface | Term Structure.
   - Row 2: Mispricing Scatter | Mispricing Table.
   - Row 3: What-If Pricer (with its parameter controls) | Payoff | Greeks by Strike.
   - Row 4: IV vs RV.
3. Add the **parameter actions** from Sheet 5, so clicking a contract in the scanner loads the pricer and payoff.
4. Add a **Text** object with the model caveat:

   > Prices use Black-Scholes-Merton, which assumes European exercise. Most US equity options are American,
   > so values are approximations, reasonably close for calls on non-dividend payers and less so for deep
   > in-the-money puts. Quotes are Yahoo Finance end-of-day, delayed roughly 15 minutes, and refreshed once
   > per weekday. "Mispricing" compares the market mid with a BSM price at 30-day realized volatility.
   > It is a volatility-premium screen, not a trade signal.

## 9. Publish with daily sync

1. **File › Save to Tableau Public As…**, name it e.g. "Black-Scholes Options Explorer".
2. In the save dialog tick the checkbox to **keep the data in sync with the Google Sheet and embed
   credentials**. It only appears for Google Sheets sources. Without it, the published viz is a frozen extract.
3. Tableau Public refreshes synced Google Sheets once a day. The pipeline writes after the close
   (21:30 UTC), so the viz shows fresh data by the next day. On the viz's settings page you can
   **Request Update** to pull immediately after a manual run.
4. Tableau Public workbooks are public. That's fine here, since the sheet holds market data only.

## 10. Version control

Tableau Public saves to the web. After publishing, download the workbook from your profile
(**Download › Tableau Workbook**) and save it as `tableau/bsm_dashboard.twbx`, then commit it.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| Pricer values are thousands of times too big | Aggregation is `SUM` over every row; change to Minimum (Sheet 5, step 4). |
| Pricer shows blank | `p Days` is 0 or `p Vol` is 0, or a calc name has a typo. |
| Price is right but put Greeks are wrong | `p Type` value is `Put` instead of `put`. Values must be lowercase; set the display label to "Put". |
| Smile has gaps | Contracts where the IV solver returned blank (mid below intrinsic). They're excluded by the non-null filter. |
| History chart empty | Sheet 7 is receiving an `options_latest` quick filter. Set that filter to apply only to Sheets 1–4. |
| Headers changed / fields missing | The pipeline keeps headers stable; if you edited the sheet by hand, delete the tab and re-run `python -m bsm.pipeline`. |
| Viz not updating | Check the sync checkbox (step 9.2) and the `run_log` tab for the last successful run. |
