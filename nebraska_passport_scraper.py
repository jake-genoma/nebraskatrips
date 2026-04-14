"""Nebraska Passport scraper starter.

Purpose:
- Discover stop pages from Nebraska Passport's Past Stops index
- Parse stop detail pages
- Normalize records for CSV / JSON / SQLite import

Usage:
    python nebraska_passport_scraper.py --limit 25
    python nebraska_passport_scraper.py --outdir ./output
    python nebraska_passport_scraper.py --seed-urls urls.txt

This starter is intentionally conservative and human-readable so you can hand it
into Codex and iterate from here.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import logging
import re
import time
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://nebraskapassport.com"
INDEX_URLS = [
    f"{BASE}/past-passport-stops",
    f"{BASE}/about/past-passport-stops",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NebraskaPassportResearchBot/0.1; +https://example.local)"
}
TIMEOUT = 25
SLEEP_SECONDS = 0.5


@dataclasses.dataclass
class StopRecord:
    name: str = ""
    source_url: str = ""
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
    scrape_status: str = "ok"
    notes: str = ""


def fetch(url: str, session: requests.Session) -> str:
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    time.sleep(SLEEP_SECONDS)
    return resp.text


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_internal_stop_link(href: str) -> bool:
    if not href:
        return False
    parsed = urlparse(href)
    if parsed.netloc and "nebraskapassport.com" not in parsed.netloc:
        return False
    full = urljoin(BASE, href)
    path = urlparse(full).path.lower()

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
    )
    if path in {"/", "/past-passport-stops", "/about/past-passport-stops"}:
        return False
    if path.startswith(excluded_prefixes):
        return False

    # detail pages usually have at least two path parts like /greenwood/bakers-candies
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


def discover_stop_urls(session: requests.Session) -> list[str]:
    urls: set[str] = set()
    for index_url in INDEX_URLS:
        try:
            html = fetch(index_url, session)
        except Exception as exc:
            logging.warning("Failed to fetch %s: %s", index_url, exc)
            continue

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            full = urljoin(BASE, href)
            if is_internal_stop_link(full):
                urls.add(full)

    return sorted(urls)


def parse_address_block(lines: list[str]) -> tuple[str, str, str, str]:
    address_raw = ""
    street = ""
    city = ""
    state = "NE"
    postal_code = ""

    if not lines:
        return address_raw, street, city, postal_code

    address_raw = clean_text(" | ".join(lines[:2]))

    # common pattern: first line street, second line "City, NE 12345"
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


def parse_stop_page(url: str, session: requests.Session) -> StopRecord:
    record = StopRecord(source_url=url)
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
    for line in text_lines[:5]:
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

    # External website links often use text like Visit Website
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        label = clean_text(a.get_text(" ", strip=True)).lower()
        if "visit website" in label and href:
            record.website_url = urljoin(url, href)
            break

    # Description: prefer the first substantial paragraph after title/address block
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

    record.llm_context = build_llm_context(record)
    return record


def load_seed_urls(seed_path: Path) -> list[str]:
    urls = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def export_records(records: list[StopRecord], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    rows = [dataclasses.asdict(r) for r in records]
    df = pd.DataFrame(rows)
    df.to_csv(outdir / "nebraska_passport_scrape.csv", index=False)
    df.to_json(outdir / "nebraska_passport_scrape.json", orient="records", indent=2, force_ascii=False)

    with open(outdir / "nebraska_passport_scrape.ndjson", "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="output", help="Output directory")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for test runs")
    parser.add_argument("--seed-urls", type=str, default="", help="Optional text file with explicit stop URLs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    outdir = Path(args.outdir)

    session = requests.Session()

    if args.seed_urls:
        urls = load_seed_urls(Path(args.seed_urls))
    else:
        urls = discover_stop_urls(session)

    if args.limit and args.limit > 0:
        urls = urls[: args.limit]

    logging.info("Discovered %s stop URLs", len(urls))

    records: list[StopRecord] = []
    for idx, url in enumerate(urls, start=1):
        logging.info("[%s/%s] %s", idx, len(urls), url)
        record = parse_stop_page(url, session)
        records.append(record)

    export_records(records, outdir)
    logging.info("Wrote output files to %s", outdir.resolve())


if __name__ == "__main__":
    main()
