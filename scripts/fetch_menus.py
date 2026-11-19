#!/usr/bin/env python3
"""Nightly refresh for the Love Elementary lunch/breakfast menus.

Reads the district's webmenus2 data as structured JSON -- no PDF
parsing, no HTML scraping:

  1. For each menu type (lunch, breakfast), list that type's menus
     via the discovery endpoint and find the current + next month.
  2. Fetch each month's items via the GraphQL API.
  3. Convert to the repo's data/<month>.json shape and write only
     when the result passes validation.
  4. Link each month's original PDF in data/months.json. The
     viewer's "Calendar Without Images" PDF filename is
     sha256("<menu-id>-grayscale-no-images-en"); the file is
     rendered on demand via the viewer backend when missing.
     Best-effort: a missing PDF never fails the run.

Self-check: every run first re-fetches November 2026 lunch and
breakfast and diffs the generated days against the saved reference
files (ignoring note text). Any mismatch aborts the run before
anything is written -- a changed API or a bad transform must fail
loudly, never publish a wrong menu.

Month IDs are cached in data/menu-ids.json: discovery is best-effort
(the listing call is the flakiest one), so a failed listing only
warns and the run proceeds with the IDs it already knows. A menu
that exists but has no items is treated as not posted yet, never as
a month with no school.

The district site has refused cloud connections before (HTTP 403
from cloud IPs), so a run may fail at the fetch step. That is also
loud: exit 1, nothing written, the site keeps serving its last
good data.

Usage:
  python3 scripts/fetch_menus.py [--data-dir data] [--dry-run]

Exit codes: 0 = ok (possibly no changes), 1 = validation/API
failure. With --dry-run nothing is written.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PDF_BASE = "https://docs.isitesoftware.com/snaf-assets/webmenus2-data/pdfs/"
PDF_API = "https://www.schoolnutritionandfitness.com/webmenus2/api/menuController.php"
PDF_SUFFIX = "grayscale-no-images"  # the viewer's "Calendar Without Images" PDF
# The viewer names a menu's PDF by sha256("<menu-id>-grayscale-no-images-en").
# Verified against both November 2026 PDFs before relying on it.
PT = ZoneInfo("America/Los_Angeles")
DISCOVERY_URL = (
    "https://www.schoolnutritionandfitness.com"
    "/webmenus2/api/menutypeController.php/show"
)
GRAPHQL_URL = "https://api.schoolnutritionandfitness.com/graphql"

LUNCH_TYPE_ID = "5dba1029534a138b548b456c"
BREAKFAST_TYPE_ID = "6a67d6de037dec741d5250b7"  # found 2026-11-12 via GraphQL menuType lookup
NOV_LUNCH_MENU_ID = "6ad0f76e0d331267b8e842a6"
NOV_BREAKFAST_MENU_ID = "6ae0bf3c1f295ab747f00158"
DEC_LUNCH_MENU_ID = "6af6329b1f295ab747e98a1b"
DEC_BREAKFAST_MENU_ID = "6afcc4b4cd25a53f4ca4381c"
# Month IDs the script already knows, so a failed discovery call never
# blocks a run. Discovery only adds to this; data/menu-ids.json is the
# persisted copy.
DEFAULT_IDS = {
    "lunch": {"2026-11": NOV_LUNCH_MENU_ID, "2026-12": DEC_LUNCH_MENU_ID},
    "breakfast": {"2026-11": NOV_BREAKFAST_MENU_ID, "2026-12": DEC_BREAKFAST_MENU_ID},
}

UA = {"User-Agent": "school-lunch-menu-refresh/1.0 (+https://robinkraft.github.io/school-lunch-menu/)"}

MENU_ITEMS_QUERY = """{menu(id:"%s"){id month year weekLength menuType{id name} items{day hidden product{id name category}}}}"""
def http_json(method, url, body=None, attempts=5):
    """Fetch JSON with retries on transient failures (5xx, dropped
    connections). A 4xx (including the site's cloud 403) fails fast.
    The district API is flaky and seems to rate-limit bursts: retries
    back off 5s/15s/30s/60s, and calls are spaced >= 2s apart."""
    import http.client
    import time

    global _LAST_CALL
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA)
    headers["Accept-Encoding"] = "gzip"
    if data is not None:
        headers["Content-Type"] = "application/json"
    last_error = None
    for attempt in range(attempts):
        now = time.monotonic()
        gap = now - _LAST_CALL
        if gap < 2.0:
            time.sleep(2.0 - gap)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                _LAST_CALL = time.monotonic()
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip

                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise RuntimeError(f"{method} {url} -> HTTP {e.code}") from e
            last_error = RuntimeError(f"{method} {url} -> HTTP {e.code}")
        except (urllib.error.URLError, http.client.HTTPException) as e:
            last_error = RuntimeError(f"{method} {url} -> {e}")
        if attempt < attempts - 1:
            time.sleep((5, 15, 30, 60)[attempt])
    raise last_error


_LAST_CALL = 0.0
def discover_menus(type_id):
    """List a menu type's months. Returns { (year, month0) : menu_id }."""
    url = DISCOVERY_URL + "?" + urllib.parse.urlencode({"_id": type_id})
    payload = http_json("GET", url)
    entries = None
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        for key in ("data", "menus", "result", "items"):
            if isinstance(payload.get(key), list):
                entries = payload[key]
                break
        if entries is None:
            # Fall back: any list value in the object.
            for value in payload.values():
                if isinstance(value, list):
                    entries = value
                    break
    if entries is None:
        raise RuntimeError(f"discovery for {type_id}: unexpected shape: {str(payload)[:200]}")
    out = {}
    for entry in entries:
        raw_id = entry.get("_id")
        menu_id = raw_id.get("$id") if isinstance(raw_id, dict) else raw_id
        if menu_id and "year" in entry and "month" in entry:
            out[(entry["year"], entry["month"])] = menu_id
    return out


def fetch_menu(menu_id):
    query = MENU_ITEMS_QUERY % menu_id
    payload = http_json("POST", GRAPHQL_URL, {"query": query})
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors for {menu_id}: {payload['errors']}")
    menu = (payload.get("data") or {}).get("menu")
    if not menu:
        raise RuntimeError(f"GraphQL returned no menu for {menu_id}: {str(payload)[:200]}")
    return menu


def pdf_url(menu_id):
    """The viewer's 'Calendar Without Images' PDF for a menu."""
    import hashlib

    digest = hashlib.sha256(f"{menu_id}-{PDF_SUFFIX}-en".encode()).hexdigest()
    return PDF_BASE + digest + ".pdf"


def _url_is_pdf(url):
    req = urllib.request.Request(url, method="HEAD", headers=dict(UA))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200 and "pdf" in (resp.headers.get("Content-Type") or "")
    except Exception:
        return False


def ensure_pdf(menu_id, dry_run=False):
    """Return the menu's PDF URL if the file exists (or can be
    generated on the district's side), else None. Best-effort:
    never raises -- a missing PDF must not fail a menu refresh.
    In dry-run mode only checks existence, never triggers a render."""
    import time

    url = pdf_url(menu_id)
    try:
        if _url_is_pdf(url):
            return url
        if dry_run:
            return None
        # Not generated yet: ask the viewer backend to render it
        # (the same POST the viewer makes on PDF/Print), then poll.
        job = http_json(
            "POST",
            PDF_API + "/pdf",
            {"id": menu_id, "suffix": PDF_SUFFIX, "language": "en"},
        )
        handle = (job or {}).get("jobHandle")
        if not handle:
            return None
        for _ in range(20):
            time.sleep(3)
            status = http_json("POST", PDF_API + "/status", {"id": handle})
            state = (status or {}).get("status")
            if state == "success":
                return url if _url_is_pdf(url) else None
            if state == "error":
                return None
        return None
    except Exception as e:
        print(f"  WARNING: PDF check for {menu_id} failed ({e}) -- no PDF link.")
        return None


def weekdays_in_month(year, month):
    """All Mon-Fri dates in the month as YYYY-MM-DD strings."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    days = []
    d = first
    while d < nxt:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def build_days(menu, year, month):
    """GraphQL menu -> {date: {items} | {no_school}} plus categories seen.

    The API returns dishes as fragments, in day order: an 'Entrees'
    item starts a dish; following fragments ("w/ Honey Biscuit",
    "& Dinner Roll", "on whole wheat bun") -- category 'Sides', null,
    or '' -- attach to that dish. A "Salad Bar Featured Item: X"
    line (any category) is the day's salad bar, not a dish. A row
    with no product name is a separator.
    """
    by_day = {}
    categories = set()
    for item in menu.get("items") or []:
        if item.get("hidden"):
            continue
        product = item.get("product") or {}
        name = (product.get("name") or "").strip()
        category = (product.get("category") or "").strip()
        categories.add(category)
        slot = by_day.setdefault(item["day"], {"dishes": [], "salad": None})
        if not name:
            continue
        if name.startswith("Salad Bar Featured Item:"):
            slot["salad"] = name.split(":", 1)[1].strip()
        elif category == "Entrees":
            slot["dishes"].append(name)
        elif slot["dishes"]:
            slot["dishes"][-1] += " " + name
        else:
            slot["dishes"].append(name)

    days = {}
    for datestr in weekdays_in_month(year, month):
        day_no = int(datestr[-2:])
        slot = by_day.get(day_no)
        if not slot or not slot["dishes"]:
            days[datestr] = {"no_school": True}
            continue
        day = {"items": slot["dishes"]}
        if slot["salad"]:
            day["salad_bar"] = slot["salad"]
        days[datestr] = day
    return days, sorted(categories)


def norm_name(s):
    """Compare names loosely: the API and the PDF differ cosmetically
    in casing ("ChiK'n" vs "Chik'n") and apostrophes ("Sukhis" vs
    "Sukhi's"). Structure is what the check guards, not punctuation."""
    return " ".join(str(s).casefold().replace("'", "").replace("\u2019", "").split())


def comparable(days):
    """Days reduced to the fields the self-check compares (notes ignored)."""
    out = {}
    for datestr, day in days.items():
        out[datestr] = {
            "items": [norm_name(x) for x in day.get("items", [])],
            "salad_bar": norm_name(day["salad_bar"]) if day.get("salad_bar") else None,
            "no_school": bool(day.get("no_school")),
        }
    return out


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")


def month_label(year, month):
    import calendar

    return f"{calendar.month_name[month]} {year}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.rstrip("/")

    now_pt = datetime.now(PT).date()
    targets = []
    for offset in (0, 1):
        y = now_pt.year + (now_pt.month - 1 + offset) // 12
        m = (now_pt.month - 1 + offset) % 12 + 1
        targets.append((y, m))

    # --- Self-check: November 2026 must reproduce the saved reference. ---
    print("Self-check: November 2026 lunch + breakfast vs saved reference...")
    for meal, menu_id, ref_file in (
        ("lunch", NOV_LUNCH_MENU_ID, f"{data_dir}/november-2026.json"),
        ("breakfast", NOV_BREAKFAST_MENU_ID, f"{data_dir}/november-2026-breakfast.json"),
    ):
        menu = fetch_menu(menu_id)
        days, categories = build_days(menu, 2026, 11)
        print(f"  {meal}: categories seen: {categories}")
        ref = load_json(ref_file)
        got, want = comparable(days), comparable(ref["days"])
        if got != want:
            for datestr in sorted(set(got) | set(want)):
                if got.get(datestr) != want.get(datestr):
                    print(f"  MISMATCH {meal} {datestr}: got {got.get(datestr)} want {want.get(datestr)}")
                    break
            print("Self-check FAILED -- aborting, nothing written.")
            return 1
        print(f"  {meal}: matches saved November reference.")
    print("Self-check passed.")

    # --- Month IDs: cached list + best-effort discovery. ---
    # Discovery is the flakiest call; a failed listing only warns and
    # the run proceeds with the IDs it already knows.
    id_cache_path = f"{data_dir}/menu-ids.json"
    try:
        id_cache = load_json(id_cache_path)
    except FileNotFoundError:
        id_cache = json.loads(json.dumps(DEFAULT_IDS))
    cache_changed = False
    for meal, type_id in (("lunch", LUNCH_TYPE_ID), ("breakfast", BREAKFAST_TYPE_ID)):
        try:
            menus = discover_menus(type_id)
        except RuntimeError as e:
            print(f"  WARNING: {meal} discovery failed ({e}) -- using cached IDs.")
            continue
        print(f"  {meal}: discovery listed {len(menus)} months.")
        for (year, month0), menu_id in menus.items():
            key = f"{year}-{month0 + 1:02d}"
            if id_cache.setdefault(meal, {}).get(key) != menu_id:
                id_cache[meal][key] = menu_id
                cache_changed = True
    if cache_changed and not args.dry_run:
        dump_json(id_cache_path, id_cache)
        print("  Menu ID cache updated.")

    manifest_path = f"{data_dir}/months.json"
    manifest = load_json(manifest_path)
    manifest_months = {m["id"]: m for m in manifest["months"]}
    changed = False

    for meal in ("lunch", "breakfast"):
        for (year, month) in targets:
            month_id = f"{year}-{month:02d}"
            menu_id = id_cache.get(meal, {}).get(month_id)
            entry = manifest_months.setdefault(
                month_id,
                {
                    "id": month_id,
                    "label": month_label(year, month),
                    "lunch": None,
                    "breakfast": None,
                    "lunch_pdf": None,
                    "breakfast_pdf": None,
                },
            )
            if not menu_id:
                print(f"  {meal} {month_id}: no menu ID known -- not posted yet or not discovered.")
                continue
            menu = fetch_menu(menu_id)
            named = [
                it
                for it in menu.get("items") or []
                if not it.get("hidden") and (it.get("product") or {}).get("name")
            ]
            if not named:
                print(f"  {meal} {month_id}: menu exists but has no items -- not posted yet.")
                continue
            days, _ = build_days(menu, year, month)
            pdf_link = ensure_pdf(menu_id, dry_run=args.dry_run)
            # File naming matches the existing November files:
            # november-2026.json (lunch), november-2026-breakfast.json.
            month_name = month_label(year, month).split()[0].lower()
            if meal == "lunch":
                rel_path = f"{data_dir}/{month_name}-{year}.json"
            else:
                rel_path = f"{data_dir}/{month_name}-{year}-breakfast.json"
            existing = None
            try:
                existing = load_json(rel_path)
            except FileNotFoundError:
                pass
            if existing is not None and comparable(existing["days"]) == comparable(days):
                print(f"  {meal} {month_id}: unchanged.")
                entry[meal] = rel_path
                if pdf_link:
                    if existing.get("source_pdf") != pdf_link:
                        existing["source_pdf"] = pdf_link
                        if not args.dry_run:
                            dump_json(rel_path, existing)
                        print(f"  {meal} {month_id}: PDF link added.")
                        changed = True
                    if entry.get(meal + "_pdf") != pdf_link:
                        entry[meal + "_pdf"] = pdf_link
                        changed = True
                continue
            print(f"  {meal} {month_id}: new/changed -- writing {rel_path}.")
            if existing is not None:
                # Preserve curated top-level fields and per-day notes.
                merged_days = {}
                for datestr, day in days.items():
                    old = existing["days"].get(datestr) or {}
                    if "note" in old:
                        day = dict(day, note=old["note"])
                    merged_days[datestr] = day
                existing["days"] = merged_days
                obj = existing
            else:
                obj = {
                    "days": days,
                    "footer": "Meals are served with seasonal fruits and vegetables and 1% or non-fat flavored milk. Menu subject to change without notice.",
                    "month": month_id,
                    "school": "Love Elementary (AUSD Elementary Lunch Menu — district-wide, same for all AUSD elementary schools)"
                    if meal == "lunch"
                    else "Love Elementary (AUSD Elementary Breakfast Menu — district-wide, same for all AUSD elementary schools)",
                    "source": f"AUSD Elementary {meal.capitalize()} Menu, {month_label(year, month)}, via webmenus2 GraphQL API",
                    "source_pdf": None,
                    "verified": f"{now_pt.isoformat()}, nightly refresh from the district API (self-check passed against saved November reference)",
                }
            if pdf_link:
                obj["source_pdf"] = pdf_link
                entry[meal + "_pdf"] = pdf_link
            if not args.dry_run:
                dump_json(rel_path, obj)
            entry[meal] = rel_path
            changed = True

    # Keep the manifest chronological and write it when anything changed.
    manifest["months"] = [manifest_months[k] for k in sorted(manifest_months)]
    if changed and not args.dry_run:
        dump_json(manifest_path, manifest)
        print("Manifest updated.")
    elif changed:
        print("Dry run: changes found, nothing written.")
    else:
        print("No changes.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
