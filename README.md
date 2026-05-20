# Intelapp

Two-stage Playwright pipeline that extracts current rental listings from
realestate.com.au and looks up each property's owner postal address from
RP Data (CoreLogic), producing a single CSV.

Default target: postcodes **4551** + **4575**, rent **over $800/week**,
ordered newest first (realestate.com.au's default rental sort).

> RP Data and realestate.com.au both restrict automated access in their
> terms of service. Use this tool only against accounts you control and
> for personal/authorised purposes.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

## Usage

```bash
# Stage 1 only (quick smoke test, no RP Data needed)
intelapp rentals --postcodes 4551,4575 --min-rent 800 --limit 3 --skip-rpdata

# Full pipeline
#  - First run opens a browser; log in to RP Data manually.
#  - Session is cached in .auth/rpdata/ so subsequent runs are headless.
intelapp rentals --postcodes 4551,4575 --min-rent 800
```

Output: `output/rentals_<YYYY-MM-DD>.csv`. Re-running on the same day
appends only new listings (existing `listing_id`s are skipped).

## CSV columns

`listing_id, listing_url, property_address, suburb, postcode, weekly_rent,
bedrooms, bathrooms, parking, date_listed, owner_name, owner_postal_address,
owner_address_matches_property, lookup_status, rpdata_url`

Filter `owner_address_matches_property = false` in Excel to get your
investor-owned / rental shortlist with off-site owner postal addresses.

## Notes

- RP Data's DOM differs across property templates. The owner-scrape uses
  label-text matching ("Owner Postal Address", "Registered Owner", etc.)
  rather than CSS selectors, so it should be reasonably resilient — but if
  it returns empty fields, open the `rpdata_url` for that row and check the
  label wording, then update `_POSTAL_LABELS` / `_NAME_LABELS` in
  `src/intelapp/rpdata.py`.
- Throttling between RP Data lookups is 1.5–3s jittered. Don't reduce it.
