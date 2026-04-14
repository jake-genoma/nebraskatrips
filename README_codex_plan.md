# Nebraska Passport Road Trip App

This package gives you a Codex-ready starting point for turning Nebraska Passport stop pages into a structured database that can power a Nebraska road trip planner with Ollama.

## Why this fits your project

Your `scenicview` repo is currently a lightweight static web app built with HTML, CSS, and JavaScript, designed to plan routes with time constraints and stop prioritization. That makes it a good conceptual base for a Nebraska-specific version that swaps broad live POI discovery for a curated, state-specific stop catalog.

Instead of discovering stops on the fly from all of North America, this Nebraska version should:

1. Ingest Nebraska Passport stops into a structured database.
2. Let the user choose trip goals such as food, art, museums, lodging, parks, quirky stops, family stops, and sweet treats.
3. Use the stop database plus route logic to generate a Nebraska road trip.
4. Feed stop summaries and metadata into Ollama so the app can explain *why* each stop was chosen.

## Recommended app architecture

### Phase 1. Data ingestion

Use the included scraper to collect stop data from Nebraska Passport pages.

Output formats:
- CSV for quick inspection
- JSON for API seeding
- Optional SQLite for local testing

### Phase 2. Normalize and enrich

Transform raw stop pages into a clean schema with:
- canonical stop name
- street address
- city
- state
- ZIP code when present
- latitude and longitude if you geocode later
- category tags
- phone
- hours
- website URL
- passport source URL
- short description snippet
- longer context text for LLM retrieval

Optional enrichment:
- derive region from city/county
- derive stop type from keywords
- geocode with Nominatim or Mapbox
- add tourism themes like `sweet_treats`, `museum`, `roadside`, `outdoor`, `lodging`, `family`, `date_night`

### Phase 3. Database

For your class project, keep it simple:
- **SQLite** for local development and quick demos
- **PostgreSQL** later if you want a more production-like stack

Suggested tables:
- `stops`
- `stop_tags`
- `stop_hours`
- `trip_runs`
- `trip_run_stops`
- `embeddings` or vector index metadata if you add semantic retrieval

### Phase 4. Ollama context layer

Create one compact retrieval document per stop using:
- stop name
- city and region
- category/type
- description snippet
- hours
- family-friendly or seasonal notes
- route relevance tags

This should be what Ollama sees during itinerary generation.

### Phase 5. Road trip generation

A simple first version:
1. user chooses origin city in Nebraska or nearby
2. user chooses trip duration or target driving time
3. app filters candidate stops by radius and categories
4. app ranks stops by theme match plus route distance
5. Ollama turns top-ranked stops into a human itinerary

## How this maps to Deliverable 3

This dataset and app idea gives you a concrete workload for the LLM infrastructure assignment.

### Good fit for benchmarking

You can benchmark two scenarios:
- **Baseline:** one Ollama instance summarizes or classifies 100 Nebraska stops
- **Scaled:** ALB + ASG backed Ollama fleet performs the same batch

### Suggested benchmark tasks

- sentiment or tone classification of stop descriptions
- theme labeling like `family`, `history`, `food`, `outdoor`, `quirky`
- short itinerary generation from a candidate stop list
- description compression into `one sentence` travel blurbs

### Suggested comparison dataset

Use 100 stop descriptions from the Nebraska Passport crawl.

For each record, measure:
- total batch runtime
- per-request latency
- successful responses
- any timeouts or retries

## Recommended database schema

### `stops`

- `id`
- `name`
- `slug`
- `address_raw`
- `street`
- `city`
- `state`
- `postal_code`
- `phone`
- `website_url`
- `source_url`
- `source_group`
- `passport_years`
- `passport_bonus_stop`
- `description`
- `description_short`
- `hours_text`
- `category_raw`
- `category_normalized`
- `tags`
- `lat`
- `lng`
- `seasonality`
- `family_friendly`
- `indoor_outdoor`
- `estimated_visit_minutes`
- `llm_context`
- `last_scraped_at`

### `stop_hours`

- `id`
- `stop_id`
- `day_of_week`
- `open_time`
- `close_time`
- `closed_flag`
- `raw_text`

### `stop_tags`

- `id`
- `stop_id`
- `tag`

## Codex implementation prompts

You can feed Codex prompts like these:

### Prompt 1

Build a Python scraper that crawls `https://nebraskapassport.com/past-passport-stops`, extracts stop page URLs, follows each stop page, and saves normalized stop records to CSV and JSON.

### Prompt 2

Refactor the scraper into a small package with modules for discovery, parsing, normalization, export, and retry logic. Use `requests`, `beautifulsoup4`, and `pandas`.

### Prompt 3

Create a SQLite database and import the scraped Nebraska Passport stop data. Add a full text search field and a compact `llm_context` column for each stop.

### Prompt 4

Build a small API endpoint that takes origin, destination, trip length, and preferred tags, then returns the best Nebraska stops ordered by route relevance.

### Prompt 5

Add Ollama integration that turns top candidate stops into a travel itinerary with one paragraph per stop plus a practical schedule.

## Suggested next coding steps

1. Run the scraper in this package.
2. Review the CSV for messy edge cases.
3. Add geocoding.
4. Create a SQLite database.
5. Build a tiny route planner API.
6. Swap your current broad POI logic for Nebraska-specific stop selection.
7. Use the stop descriptions as the benchmark batch for Deliverable 3.

## Notes on scraping

- Some stop pages live under paths like `/greenwood/bakers-candies`.
- The Past Stops page is a useful master index, but not every line has identical formatting.
- Some entries have direct external links in the index, while others need discovery from related taxonomy or stop pages.
- Plan for duplicates across years.
- Normalize apostrophes, punctuation, spacing, and city names.

## Files in this package

- `nebraska_passport_scraper.py` — starter scraper
- `schema.sql` — starter relational schema
- `requirements.txt` — Python dependencies
- `README_codex_plan.md` — this file
