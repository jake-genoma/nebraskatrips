"""Nebraska Passport data pipeline + trip planner.

What this script now does:
1) Discover stop URLs from past-stop index pages + year pages.
2) Parse stop detail pages into normalized records.
3) Optionally geocode stops (Nominatim).
4) Deduplicate records across years/source pages.
5) Export CSV/JSON/NDJSON and persist to SQLite.
6) Run an interactive trip recommendation flow against SQLite.

Examples:
  python nebraska_passport_scraper.py scrape --outdir output --sqlite output/nebraska_trips.db --geocode
  python nebraska_passport_scraper.py plan --sqlite output/nebraska_trips.db
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://nebraskapassport.com"
INDEX_URLS = [
    f"{BASE}/past-passport-stops",
    f"{BASE}/about/past-passport-stops",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NebraskaPassportResearchBot/0.2; +https://example.local)"
}
TIMEOUT = 25
SLEEP_SECONDS = 0.5
GEOCODER_SLEEP_SECONDS = 1.1  # be polite to Nominatim


@dataclasses.dataclass
class StopRecord:
    name: str = ""
    source_url: str = ""
    source_urls: str = ""
    website_url: str = ""
    address_raw: str = ""
    street: str = ""
    city: str = ""
    state: str = "NE"
    postal_code: str = ""
    phone: str = ""
    description: str = ""
    description_short: str = ""
    hours_text: str = ""
    hours_json: str = ""
    category_raw: str = ""
    category_normalized: str = ""
    tags: str = ""
    passport_years: str = ""
    passport_bonus_stop: bool = False
    family_friendly: Optional[bool] = None
    indoor_outdoor: str = ""
    seasonality: str = ""
    estimated_visit_minutes: Optional[int] = None
    llm_context: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    duplicate_key: str = ""
    scrape_status: str = "ok"
    notes: str = ""


def fetch(url: str, session: requests.Session) -> str:
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    time.sleep(SLEEP_SECONDS)
    return resp.text


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_text(text).lower())


def infer_year_from_url(url: str) -> str:
    parsed = urlparse(url)
    candidates = re.findall(r"(20\d{2})", f"{parsed.path} {parsed.query}")
    if candidates:
        return candidates[0]
    qs = parse_qs(parsed.query)
    for vals in qs.values():
        for v in vals:
            if re.fullmatch(r"20\d{2}", v):
                return v
    return ""


def is_internal_stop_link(href: str) -> bool:
    if not href:
        return False
    parsed = urlparse(href)
    if parsed.netloc and "nebraskapassport.com" not in parsed.netloc:
        return False
    full = urljoin(BASE, href)
    path = urlparse(full).path.lower().rstrip("/")

    excluded_prefixes = (
        "/about",
        "/app",
        "/press-releases",
        "/stories",
        "/trip-idea",
        "/passport-program",
        "/request",
        "/q-a",
        "/taxonomy",
        "/results",
        "/wp-json",
        "/feed",
    )
    if path in {"", "/", "/past-passport-stops", "/about/past-passport-stops"}:
        return False
    if path.startswith(excluded_prefixes):
        return False

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return False
    if any(p.isdigit() and len(p) == 4 for p in parts):
        return False
    return True


def discover_year_pages(session: requests.Session, seed_pages: Iterable[str]) -> list[str]:
    year_pages: set[str] = set()
    for page in seed_pages:
        try:
            html = fetch(page, session)
        except Exception as exc:
            logging.warning("Failed to fetch discovery page %s: %s", page, exc)
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            full = urljoin(BASE, href)
            path = urlparse(full).path.lower()
            if "passport" in path and re.search(r"20\d{2}", f"{path} {urlparse(full).query}"):
                year_pages.add(full)
    return sorted(year_pages)


def discover_stop_urls(session: requests.Session) -> tuple[list[str], dict[str, set[str]]]:
    urls: set[str] = set()
    year_map: dict[str, set[str]] = {}

    pages_to_scan = set(INDEX_URLS)
    pages_to_scan.update(discover_year_pages(session, INDEX_URLS))

    for index_url in sorted(pages_to_scan):
        try:
            html = fetch(index_url, session)
        except Exception as exc:
            logging.warning("Failed to fetch %s: %s", index_url, exc)
            continue

        inferred_year = infer_year_from_url(index_url)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            full = urljoin(BASE, href)
            if is_internal_stop_link(full):
                canon = full.rstrip("/")
                urls.add(canon)
                if inferred_year:
                    year_map.setdefault(canon, set()).add(inferred_year)

    return sorted(urls), year_map


def parse_address_block(lines: list[str]) -> tuple[str, str, str, str]:
    address_raw = ""
    street = ""
    city = ""
    state = "NE"
    postal_code = ""

    if not lines:
        return address_raw, street, city, postal_code

    address_raw = clean_text(" | ".join(lines[:2]))

    if len(lines) >= 1:
        street = clean_text(lines[0])
    if len(lines) >= 2:
        m = re.match(r"^(.*?),\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?$", clean_text(lines[1]))
        if m:
            city = clean_text(m.group(1))
            state = clean_text(m.group(2)) or "NE"
            postal_code = clean_text(m.group(3) or "")
        else:
            city = clean_text(lines[1])
    return address_raw, street, city, postal_code


def infer_category(name: str, description: str) -> str:
    text = f"{name} {description}".lower()
    rules = {
        "museum": ["museum", "history center", "historic site", "memorial", "gallery"],
        "food_drink": ["coffee", "bakery", "cafe", "grill", "restaurant", "brew", "winery", "distill"],
        "outdoor": ["state park", "recreation area", "trail", "outfitters", "wildlife", "forest", "lake"],
        "retail": ["boutique", "market", "candies", "gifts", "store", "shop"],
        "lodging": ["hotel", "inn", "bed and breakfast", "motel", "lodge"],
        "arts": ["art", "studio", "creative district", "theater", "melodramas"],
    }
    for category, keywords in rules.items():
        if any(k in text for k in keywords):
            return category
    return "attraction"


def infer_tags(name: str, description: str, category: str) -> list[str]:
    text = f"{name} {description}".lower()
    tags = {category}

    mapping = {
        "family": ["kids", "family", "children", "zoo", "museum", "park", "waterpark"],
        "sweet_treats": ["candy", "candies", "ice cream", "gelato", "bakery", "dessert", "chocolate"],
        "history": ["historic", "history", "museum", "memorial", "heritage"],
        "outdoor": ["park", "trail", "river", "lake", "wildlife", "forest", "campground"],
        "roadside": ["tower", "carhenge", "landmark", "world's largest"],
        "arts": ["art", "gallery", "studio", "creative district", "theater"],
        "food": ["coffee", "cafe", "grill", "restaurant", "brewery", "winery", "pizza"],
    }
    for tag, keywords in mapping.items():
        if any(k in text for k in keywords):
            tags.add(tag)
    return sorted(tags)


def estimate_visit_minutes(category: str) -> int:
    return {
        "food_drink": 45,
        "retail": 40,
        "museum": 75,
        "arts": 60,
        "outdoor": 90,
        "lodging": 480,
        "attraction": 60,
    }.get(category, 60)


def build_llm_context(record: StopRecord) -> str:
    parts = [
        f"Stop: {record.name}",
        f"Location: {record.city}, {record.state}" if record.city else "",
        f"Address: {record.address_raw}" if record.address_raw else "",
        f"Type: {record.category_normalized}" if record.category_normalized else "",
        f"Tags: {record.tags}" if record.tags else "",
        f"Hours: {record.hours_text}" if record.hours_text else "",
        f"Description: {record.description_short or record.description}" if (record.description_short or record.description) else "",
        f"Estimated visit time: {record.estimated_visit_minutes} minutes" if record.estimated_visit_minutes else "",
    ]
    return " | ".join([p for p in parts if p])


def extract_text_lines_near_title(soup: BeautifulSoup, title: str) -> list[str]:
    lines: list[str] = []
    text_blocks = [clean_text(x.get_text(" ", strip=True)) for x in soup.find_all(["p", "div", "li", "span", "h1", "h2", "h3", "h4"])]
    try:
        idx = text_blocks.index(title)
    except ValueError:
        return lines
    for item in text_blocks[idx + 1 : idx + 10]:
        if item and item not in lines:
            lines.append(item)
    return lines


def parse_hours(soup: BeautifulSoup) -> tuple[str, list[dict]]:
    hours_lines = []
    parsed = []
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for li in soup.find_all("li"):
        text = clean_text(li.get_text(" ", strip=True))
        if any(text.startswith(day) for day in day_names):
            hours_lines.append(text)
            parsed.append({"raw": text})
    return "; ".join(hours_lines), parsed


def parse_stop_page(url: str, session: requests.Session, known_years: Optional[set[str]] = None) -> StopRecord:
    record = StopRecord(source_url=url, source_urls=url)
    try:
        html = fetch(url, session)
    except Exception as exc:
        record.scrape_status = "fetch_error"
        record.notes = str(exc)
        return record

    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("h1")
    record.name = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

    text_lines = extract_text_lines_near_title(soup, record.name)

    address_lines = []
    for line in text_lines[:6]:
        if re.search(r"\d", line):
            address_lines.append(line)
        elif re.search(r",\s*[A-Z]{2}(?:\s+\d{5})?", line):
            address_lines.append(line)
        if len(address_lines) >= 2:
            break

    record.address_raw, record.street, record.city, record.postal_code = parse_address_block(address_lines)

    phone_match = re.search(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}", soup.get_text(" ", strip=True))
    if phone_match:
        record.phone = clean_text(phone_match.group(0))

    hours_text, hours_json = parse_hours(soup)
    record.hours_text = hours_text
    record.hours_json = json.dumps(hours_json, ensure_ascii=False)

    found_years = set(known_years or set())
    for y in re.findall(r"\b(20\d{2})\b", soup.get_text(" ", strip=True)):
        if 2010 <= int(y) <= 2030:
            found_years.add(y)
    record.passport_years = ",".join(sorted(found_years))

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        label = clean_text(a.get_text(" ", strip=True)).lower()
        if "visit website" in label and href:
            record.website_url = urljoin(url, href)
            break

    paragraphs = [clean_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 60 and p != record.name]
    if paragraphs:
        record.description = paragraphs[0]
        record.description_short = paragraphs[0][:240].rstrip() + ("..." if len(paragraphs[0]) > 240 else "")

    record.category_normalized = infer_category(record.name, record.description)
    tag_list = infer_tags(record.name, record.description, record.category_normalized)
    record.tags = ", ".join(tag_list)
    record.estimated_visit_minutes = estimate_visit_minutes(record.category_normalized)

    if any(tag in tag_list for tag in ["family", "sweet_treats", "history", "outdoor", "arts", "food"]):
        record.family_friendly = "family" in tag_list or record.category_normalized in {"museum", "outdoor", "attraction"}

    if record.category_normalized in {"outdoor"}:
        record.indoor_outdoor = "outdoor"
    elif record.category_normalized in {"museum", "food_drink", "retail", "lodging", "arts"}:
        record.indoor_outdoor = "indoor"
    else:
        record.indoor_outdoor = "mixed"

    record.duplicate_key = f"{normalize_token(record.name)}::{normalize_token(record.city)}"
    record.llm_context = build_llm_context(record)
    return record


def deduplicate_records(records: list[StopRecord]) -> list[StopRecord]:
    merged: dict[str, StopRecord] = {}
    for record in records:
        key = record.duplicate_key or f"url::{record.source_url}"
        if key not in merged:
            merged[key] = record
            continue

        current = merged[key]

        merged_urls = sorted(set(filter(None, current.source_urls.split(",") + record.source_urls.split(","))))
        current.source_urls = ",".join(merged_urls)

        merged_years = sorted(set(filter(None, current.passport_years.split(",") + record.passport_years.split(","))))
        current.passport_years = ",".join(merged_years)

        if len(record.description) > len(current.description):
            current.description = record.description
            current.description_short = record.description_short

        if not current.website_url and record.website_url:
            current.website_url = record.website_url
        if not current.phone and record.phone:
            current.phone = record.phone
        if not current.address_raw and record.address_raw:
            current.address_raw = record.address_raw
            current.street = record.street
            current.city = record.city
            current.postal_code = record.postal_code

        tag_set = set(filter(None, [t.strip() for t in current.tags.split(",")])) | set(filter(None, [t.strip() for t in record.tags.split(",")]))
        current.tags = ", ".join(sorted(tag_set))
        current.llm_context = build_llm_context(current)

    return sorted(merged.values(), key=lambda r: (r.city or "", r.name or ""))


def load_seed_urls(seed_path: Path) -> list[str]:
    urls = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def geocode_record(record: StopRecord, session: requests.Session, cache: dict[str, tuple[float, float]]) -> None:
    if record.lat is not None and record.lng is not None:
        return
    query = ", ".join(x for x in [record.street, record.city, record.state, record.postal_code] if x)
    if not query:
        return
    if query in cache:
        record.lat, record.lng = cache[query]
        return

    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = dict(HEADERS)
    headers["User-Agent"] = "NebraskaPassportResearchBot/0.2 (course project geocoder)"
    try:
        resp = session.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            lat = float(rows[0]["lat"])
            lng = float(rows[0]["lon"])
            cache[query] = (lat, lng)
            record.lat, record.lng = lat, lng
    except Exception as exc:
        record.notes = clean_text(f"{record.notes} geocode_error={exc}")
    finally:
        time.sleep(GEOCODER_SLEEP_SECONDS)


def export_records(records: list[StopRecord], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    rows = [dataclasses.asdict(r) for r in records]
    df = pd.DataFrame(rows)
    df.to_csv(outdir / "nebraska_passport_scrape.csv", index=False)
    df.to_json(outdir / "nebraska_passport_scrape.json", orient="records", indent=2, force_ascii=False)

    with open(outdir / "nebraska_passport_scrape.ndjson", "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def init_sqlite(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duplicate_key TEXT,
            address_raw TEXT,
            street TEXT,
            city TEXT,
            state TEXT DEFAULT 'NE',
            postal_code TEXT,
            phone TEXT,
            website_url TEXT,
            source_url TEXT,
            source_urls TEXT,
            source_group TEXT DEFAULT 'nebraska_passport',
            passport_years TEXT,
            passport_bonus_stop INTEGER DEFAULT 0,
            description TEXT,
            description_short TEXT,
            hours_text TEXT,
            category_raw TEXT,
            category_normalized TEXT,
            tags TEXT,
            lat REAL,
            lng REAL,
            seasonality TEXT,
            family_friendly INTEGER,
            indoor_outdoor TEXT,
            estimated_visit_minutes INTEGER,
            llm_context TEXT,
            last_scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(duplicate_key)
        );

        CREATE TABLE IF NOT EXISTS stop_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_id INTEGER NOT NULL,
            day_of_week TEXT,
            open_time TEXT,
            close_time TEXT,
            closed_flag INTEGER DEFAULT 0,
            raw_text TEXT,
            FOREIGN KEY (stop_id) REFERENCES stops(id)
        );

        CREATE TABLE IF NOT EXISTS stop_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (stop_id) REFERENCES stops(id)
        );

        CREATE INDEX IF NOT EXISTS idx_stops_city ON stops(city);
        CREATE INDEX IF NOT EXISTS idx_stops_category ON stops(category_normalized);
        CREATE INDEX IF NOT EXISTS idx_stops_duplicate_key ON stops(duplicate_key);
        """
    )
    conn.commit()


def write_sqlite(records: list[StopRecord], sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        init_sqlite(conn)
        for r in records:
            conn.execute(
                """
                INSERT INTO stops (
                    name, duplicate_key, address_raw, street, city, state, postal_code, phone, website_url,
                    source_url, source_urls, passport_years, passport_bonus_stop, description, description_short,
                    hours_text, category_raw, category_normalized, tags, lat, lng, seasonality,
                    family_friendly, indoor_outdoor, estimated_visit_minutes, llm_context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(duplicate_key) DO UPDATE SET
                    source_urls = excluded.source_urls,
                    passport_years = excluded.passport_years,
                    description = excluded.description,
                    description_short = excluded.description_short,
                    tags = excluded.tags,
                    lat = COALESCE(excluded.lat, stops.lat),
                    lng = COALESCE(excluded.lng, stops.lng),
                    llm_context = excluded.llm_context,
                    last_scraped_at = CURRENT_TIMESTAMP
                """,
                (
                    r.name,
                    r.duplicate_key,
                    r.address_raw,
                    r.street,
                    r.city,
                    r.state,
                    r.postal_code,
                    r.phone,
                    r.website_url,
                    r.source_url,
                    r.source_urls,
                    r.passport_years,
                    int(r.passport_bonus_stop),
                    r.description,
                    r.description_short,
                    r.hours_text,
                    r.category_raw,
                    r.category_normalized,
                    r.tags,
                    r.lat,
                    r.lng,
                    r.seasonality,
                    int(r.family_friendly) if r.family_friendly is not None else None,
                    r.indoor_outdoor,
                    r.estimated_visit_minutes,
                    r.llm_context,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode_free_text(location: str) -> tuple[Optional[float], Optional[float]]:
    if not location.strip():
        return None, None
    params = {"q": location, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = dict(HEADERS)
    headers["User-Agent"] = "NebraskaPassportResearchBot/0.2 (trip planner geocoder)"
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/search", params=params, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return float(rows[0]["lat"]), float(rows[0]["lon"])
    except Exception:
        return None, None
    return None, None


def rank_stops(conn: sqlite3.Connection, origin: str, preferred_tags: list[str], max_drive_miles: int, trip_hours: int, limit: int = 8) -> list[dict]:
    origin_lat, origin_lng = geocode_free_text(origin)
    rows = conn.execute(
        """
        SELECT id, name, city, state, tags, category_normalized, description_short, llm_context,
               estimated_visit_minutes, lat, lng, website_url
        FROM stops
        WHERE lat IS NOT NULL AND lng IS NOT NULL
        """
    ).fetchall()

    scored: list[dict] = []
    for row in rows:
        (
            stop_id,
            name,
            city,
            state,
            tags,
            category,
            description_short,
            llm_context,
            estimated_visit_minutes,
            lat,
            lng,
            website_url,
        ) = row

        tag_set = {t.strip().lower() for t in (tags or "").split(",") if t.strip()}
        tag_score = sum(1 for t in preferred_tags if t.lower() in tag_set)

        dist = None
        dist_score = 0.5
        if origin_lat is not None and origin_lng is not None:
            dist = haversine_miles(origin_lat, origin_lng, lat, lng)
            if dist > max_drive_miles:
                continue
            dist_score = max(0.0, 1.0 - (dist / max_drive_miles))

        time_cost = (estimated_visit_minutes or 60) / 60
        time_score = max(0.0, 1.0 - (time_cost / max(1, trip_hours)))

        total = 0.55 * tag_score + 0.30 * dist_score + 0.15 * time_score

        scored.append(
            {
                "id": stop_id,
                "name": name,
                "city": city,
                "state": state,
                "tags": sorted(tag_set),
                "category": category,
                "description_short": description_short,
                "llm_context": llm_context,
                "estimated_visit_minutes": estimated_visit_minutes,
                "distance_miles": dist,
                "website_url": website_url,
                "score": round(total, 4),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    picked: list[dict] = []
    budget_minutes = trip_hours * 60
    used = 0
    for item in scored:
        visit = item["estimated_visit_minutes"] or 60
        if used + visit > budget_minutes and picked:
            continue
        picked.append(item)
        used += visit
        if len(picked) >= limit:
            break
    return picked


def interactive_plan(sqlite_path: Path) -> None:
    conn = sqlite3.connect(sqlite_path)
    try:
        print("Nebraska Trip Copilot")
        print("I can ask a few questions and build a route-ranked recommendation from your stop database.\n")

        origin = input("Where are you starting from? (city/address): ").strip() or "Lincoln, NE"
        hours_raw = input("How many total hours do you want for this trip day? (default 8): ").strip()
        trip_hours = int(hours_raw) if hours_raw.isdigit() else 8
        miles_raw = input("Max one-way distance from origin in miles? (default 180): ").strip()
        max_miles = int(miles_raw) if miles_raw.isdigit() else 180
        tags_raw = input(
            "What themes do you care about? (comma list, e.g. history, food, family, outdoor, arts, sweet_treats): "
        ).strip()
        preferred_tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()] or ["history", "food"]

        picks = rank_stops(conn, origin, preferred_tags, max_miles, trip_hours)

        print("\n--- Recommended Nebraska road trip ---")
        print(f"Origin: {origin}")
        print(f"Trip time budget: {trip_hours} hours")
        print(f"Tag priorities: {', '.join(preferred_tags)}\n")

        if not picks:
            print("No ranked stops matched your constraints. Try increasing distance or using broader tags.")
            return

        for idx, stop in enumerate(picks, start=1):
            dist_label = f"{stop['distance_miles']:.1f} mi" if stop["distance_miles"] is not None else "distance n/a"
            print(f"{idx}. {stop['name']} ({stop['city']}, {stop['state']})")
            print(f"   Why it fits: tags={', '.join(stop['tags']) or 'n/a'} | category={stop['category']} | score={stop['score']} | {dist_label}")
            if stop["description_short"]:
                print(f"   Summary: {stop['description_short']}")
            if stop["website_url"]:
                print(f"   Website: {stop['website_url']}")
            print()

        print("Prompt stub for downstream LLM:")
        print("""\
You are a Nebraska road-trip planner. Build a practical itinerary from these ranked stops.
Include driving order, time estimates, lunch timing, and why each stop matches user preferences.
""")
    finally:
        conn.close()


def run_scrape(args: argparse.Namespace) -> None:
    outdir = Path(args.outdir)
    session = requests.Session()

    if args.seed_urls:
        urls = load_seed_urls(Path(args.seed_urls))
        year_map: dict[str, set[str]] = {}
    else:
        urls, year_map = discover_stop_urls(session)

    if args.limit and args.limit > 0:
        urls = urls[: args.limit]

    logging.info("Discovered %s stop URLs", len(urls))

    records: list[StopRecord] = []
    for idx, url in enumerate(urls, start=1):
        logging.info("[%s/%s] %s", idx, len(urls), url)
        record = parse_stop_page(url, session, year_map.get(url.rstrip("/"), set()))
        records.append(record)

    deduped = deduplicate_records(records)
    logging.info("Deduplicated %s raw records down to %s unique stops", len(records), len(deduped))

    if args.geocode:
        cache: dict[str, tuple[float, float]] = {}
        for idx, record in enumerate(deduped, start=1):
            logging.info("Geocoding [%s/%s] %s", idx, len(deduped), record.name)
            geocode_record(record, session, cache)

    export_records(deduped, outdir)
    logging.info("Wrote output files to %s", outdir.resolve())

    if args.sqlite:
        sqlite_path = Path(args.sqlite)
        write_sqlite(deduped, sqlite_path)
        logging.info("Upserted %s records to %s", len(deduped), sqlite_path.resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    scrape = sub.add_parser("scrape", help="Discover, scrape, dedupe, geocode, and export stops")
    scrape.add_argument("--outdir", default="output", help="Output directory")
    scrape.add_argument("--limit", type=int, default=0, help="Optional limit for test runs")
    scrape.add_argument("--seed-urls", type=str, default="", help="Optional text file with explicit stop URLs")
    scrape.add_argument("--sqlite", type=str, default="", help="Optional SQLite path to upsert records")
    scrape.add_argument("--geocode", action="store_true", help="Enable Nominatim geocoding")

    plan = sub.add_parser("plan", help="Run interactive route-ranking trip planner")
    plan.add_argument("--sqlite", type=str, required=True, help="SQLite path containing stops table")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command in (None, "scrape"):
        run_scrape(args)
    elif args.command == "plan":
        interactive_plan(Path(args.sqlite))


if __name__ == "__main__":
    main()
