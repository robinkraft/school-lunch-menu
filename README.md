# Love Elementary Lunch Menu — portable source

A plain static site for the Love Elementary (Alameda Unified) lunch and
breakfast menus — vanilla HTML/CSS/JS, **no build step, no Muse runtime**.
Same shape as the Soccer Sub Manager portable app. The menu data is
public district data; there are no real names anywhere in this repo.

## Files

- `index.html` — shell; loads `styles.css`, `supabase-config.js`, `app.js`
- `app.js` — the whole app: hero (next lunch), Breakfast/Lunch tabs,
  forward-only month lists, kids' picks, PDF links, "Check now"
- `styles.css` — phone-first styling
- `supabase-config.js` — Supabase URL + anon key (left empty and inert;
  picks stay on-device — Robin dropped the Supabase plan, so there is
  no backend)
- `scripts/fetch_menus.py` — nightly refresh: reads the district's
  webmenus2 API as structured JSON and updates `data/` (see below)
- `.github/workflows/nightly-menu-refresh.yml` — the nightly Action
  that runs the script (push this file from a laptop, never through
  the Muse GitHub connection)
- `data/months.json` — manifest: months, data file paths, raw PDF links
- `data/november-2026.json` — November lunch (verified 2026-11-07
  against the district PDF; Nov 2–6 genuinely have no salad bar)
- `data/november-2026-breakfast.json` — November breakfast (verified
  2026-11-07 from the breakfast calendar view)

## Behaviour (Robin's rulings)

- Breakfast tab comes first, but Lunch is selected by default.
- Forward-only: past days are hidden; no previous-month button. From a
  later month, "Back to this month" jumps to the current month.
- Tapping an entrée marks it as a kids' pick (circle check + label),
  saved separately per meal (lunch and breakfast picks never collide).
- Each month shows a "View original PDF" link to the district's raw
  monthly PDF for the meal being viewed.
- A month with no posted menu shows an honest "isn't posted yet" state
  with a Check now button (re-fetches the manifest, so a newly pushed
  month appears without a reload).

## Run locally

```sh
cd school-lunch-menu
python3 -m http.server 8000
# open http://localhost:8000/
```

## Host on GitHub Pages

1. Create a repo (e.g. `school-lunch-menu`) and push these
   files to its default branch. Workflow-free push only — never include
   a `.github/workflows/` file in a push through the Muse GitHub
   connection (it 403s).
2. Repo → Settings → Pages → Source: **Deploy from a branch**, branch:
   default, folder: `/ (root)`.
3. Open the Pages URL on your phone. Anyone with the link can open it;
   picks are per-device in Phase 1.

## Nightly refresh (GitHub Action)

`scripts/fetch_menus.py` reads the district's webmenus2 data as
structured JSON — no PDF parsing, no HTML scraping:

- Discovery: `GET .../webmenus2/api/menutypeController.php/show?_id=<menu-type-id>`
  lists a menu type's months and their IDs (lunch type id
  `5dba1029534a138b548b456c`; breakfast's is looked up from the known
  November breakfast menu). December 2026's lunch menu id is already
  known: `6af6329b1f295ab747e98a1b`.
- Items: `POST https://api.schoolnutritionandfitness.com/graphql`
  with `menu(id:"..."){id month year weekLength menuType{id name}
  items{day hidden product{id name category}}}`.

The script converts the API's fragment-style items into the repo's
`days` shape (an `Entrees` item starts a dish; following fragments
like "w/ Honey Biscuit" or "& Dinner Roll" attach to it; a "Salad
Bar Featured Item: X" line is the day's salad bar), then writes a
month file only when it changed.

Safety: every run first re-fetches November 2026 (lunch + breakfast)
and diffs it against the saved reference files. Any mismatch, or any
API failure, aborts the run before anything is written — the site
keeps serving its last good data and the failure shows up in the
Actions tab. The comparison forgives cosmetic differences between
the API and the PDF (casing, apostrophes: "ChiK'n" vs "Chik'n",
"Sukhis" vs "Sukhi's"); structure is what's checked. Month IDs are
cached in `data/menu-ids.json` and discovery is best-effort, so a
failed listing only warns and the run proceeds with the IDs it
already knows; a menu with no items is treated as not posted yet,
never as a no-school month.

API quirks worth knowing:

- Plain (uncompressed) responses truncate — the script requests
  `Accept-Encoding: gzip` and the gzipped responses complete fine.
- The API is flaky and seems to rate-limit bursts: the script
  retries with backoff (5s/15s/30s/60s) and spaces calls ≥ 2s apart.
  If the Action starts failing consistently, the fallback is a
  manual update (see below) — the site never goes stale silently,
  it just stops refreshing.
- The API carries no PDF URLs. New months get their menus
  automatically; the "View original PDF" link for a new month is
  added when the PDF is verified (the field stays null until then
  and the app hides the link).

The Action (`.github/workflows/nightly-menu-refresh.yml`) runs daily
at 02:00 PT and on manual dispatch (with a dry-run option), commits
any `data/` changes, and Pages redeploys on push. **Push the workflow
file from a laptop checkout** — the Muse GitHub connection cannot
push anything under `.github/workflows/` (it 403s).

## Adding a new month (manual fallback)

Normally the nightly Action does this. By hand:

1. Get the month's lunch and breakfast PDFs from the district menus
   page: https://www.schoolnutritionandfitness.com/index.php?sid=1571761233982&page=menus
   (keep the `sid` — it selects Alameda Unified). In the webmenus2
   viewer use PDF/Print → "Calendar Without Images" for the readable
   PDF. The in-viewer calendar may render empty and its Accessible
   View may wrongly say "No menus published" — trust the PDF.
2. Transcribe each month into `data/<month>-<year>.json` in the same
   shape as the November files (`days` keyed by `YYYY-MM-DD`, each
   with `items`, optional `salad_bar`, or `no_school` + `note`).
3. Add the month to `data/months.json` with its data paths and the two
   raw PDF URLs, and push. The site and the "Check now" button pick
   it up from the manifest.

## No Supabase

An earlier plan synced kids' picks through Supabase with a family
code. Robin dropped it: picks stay per-device in localStorage (the
shared page is only ever looked at the night before) and the
`school-lunch-menu` Supabase project he created goes unused. The
`supabase-config.js` in this repo is inert.
