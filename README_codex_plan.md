# Nebraska Passport Road Trip App

This package now includes an end-to-end pipeline for your class project:

1. **Expanded discovery** of stop URLs from the Past Stops index + additional year pages.
2. **Stop parsing and normalization** into a single consistent structure.
3. **Geocoding** through Nominatim (optional flag).
4. **Duplicate handling** across year pages and source URLs.
5. **SQLite upsert flow** so the app has a persistent stop catalog.
6. **Interactive route-ranking planner** powered directly by SQLite.

---

## Quickstart

Install dependencies:

```bash
pip install -r requirements.txt
```

Run scrape + dedupe + geocode + SQLite write:

```bash
python nebraska_passport_scraper.py scrape \
  --outdir output \
  --sqlite output/nebraska_trips.db \
  --geocode
```

Run a quick test scrape:

```bash
python nebraska_passport_scraper.py scrape --limit 25 --sqlite output/nebraska_trips.db
```

Run interactive planning flow:

```bash
python nebraska_passport_scraper.py plan --sqlite output/nebraska_trips.db
```

Export SQLite to Excel (plus optional CSV files) so you can open in Excel/Sheets:

```bash
python nebraska_passport_scraper.py export \
  --sqlite output/nebraska_trips.db \
  --xlsx output/nebraska_trips_export.xlsx \
  --csv-dir output/csv
```

---

## How this maps to your desired final app

You asked for the app to feel like an LLM-driven copilot that asks what the traveler wants, then compiles a Nebraska trip recommendation.

The current script now provides exactly that baseline behavior:

- A **question-and-answer trip intake flow** (`plan` command).
- A **route-ranking engine** against the local Nebraska stop dataset.
- A generated **LLM prompt stub** you can pass to Ollama/Dify later for narrative itinerary generation.

This means your infrastructure work (ALB + EC2 + autoscaling + internal APIs) can focus on serving:

- ingestion jobs,
- SQLite/DB-backed retrieval,
- and the LLM itinerary layer.

---

## Suggested next project steps (AWS class alignment)

1. Keep scraper + dedupe + geocode as a batch job.
2. Persist output to SQLite first (Postgres later if needed).
3. Add a lightweight API around `rank_stops`.
4. Put your LLM itinerary generator behind an internal API.
5. Deploy web app + API behind ALB as shown in your architecture diagram.
6. Benchmark one-model vs autoscaled-model latency for itinerary generation.

---

## Files

- `nebraska_passport_scraper.py` — discovery, parsing, geocoding, dedupe, SQLite upsert, and trip planner CLI.
- `schema.sql` — SQLite schema aligned with duplicate handling and route ranking needs.
- `requirements.txt` — Python dependencies.
