CREATE TABLE IF NOT EXISTS stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    duplicate_key TEXT,
    slug TEXT,
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
