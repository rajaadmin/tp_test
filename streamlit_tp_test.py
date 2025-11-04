import streamlit as st
import pandas as pd
import time
import json
import requests
from bs4 import BeautifulSoup
from collections.abc import Mapping
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ---- CONFIG ----
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}

# ---- Helper Functions ----
def fetch_html(url: str) -> str:
    """Fetch HTML content from a given URL."""
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def extract_jsonld_blocks(html: str):
    """Extract JSON-LD blocks from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = (tag.string or "").strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            try:
                cleaned = raw.replace("\n", "").replace("\t", "").strip()
                blocks.append(json.loads(cleaned))
            except Exception:
                pass
    return blocks

def walk(obj):
    """Recursively yield all nested dicts inside obj."""
    if isinstance(obj, Mapping):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from walk(it)

def is_review(d: dict) -> bool:
    """Check if a dict represents a review."""
    t = d.get("@type")
    if not t:
        return False
    if isinstance(t, str):
        return t.lower() == "review"
    if isinstance(t, list):
        return any(isinstance(x, str) and x.lower() == "review" for x in t)
    return False

def get_reviews_from_jsonld(jsonld_blocks):
    """Extract review nodes from JSON-LD blocks."""
    reviews = []
    for block in jsonld_blocks:
        for node in walk(block):
            if is_review(node):
                reviews.append(node)
    return reviews

def set_page(url: str, page: int) -> str:
    """Update URL with a specific page number."""
    u = urlparse(url)
    q = parse_qs(u.query)
    q["page"] = [str(page)]
    new_query = urlencode(q, doseq=True)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))

def collect_review_sample(base_url: str, sample_size: int, max_pages: int, sleep_secs: float):
    """Collect reviews from multiple pages."""
    collected, seen_ids = [], set()
    page = 1
    while len(collected) < sample_size and page <= max_pages:
        url = base_url if page == 1 else set_page(base_url, page)
        html = fetch_html(url)
        blocks = extract_jsonld_blocks(html)
        reviews = get_reviews_from_jsonld(blocks)

        for r in reviews:
            rid = r.get("@id") or f"{r.get('author',{}).get('name')}|{r.get('datePublished')}"
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            collected.append(r)
            if len(collected) >= sample_size:
                break

        page += 1
        time.sleep(sleep_secs)

    return collected

# ---- Streamlit UI ----
st.title("Trustpilot Review Scraper")

base_url = st.text_input("Enter Trustpilot URL:", "https://dk.trustpilot.com/review/www.ase.dk")
sample_size = st.number_input("Sample size:", min_value=10, max_value=500, value=50)
max_pages = st.number_input("Max pages:", min_value=1, max_value=20, value=3)
sleep_secs = st.slider("Delay between requests (seconds):", 0.5, 5.0, 2.0)

if st.button("Collect Reviews"):
    with st.spinner("Fetching reviews..."):
        sample = collect_review_sample(base_url, sample_size, max_pages, sleep_secs)
        df = pd.json_normalize(sample)

        cols = [
            "@id", "itemReviewed.@id", "author.name", "author.url",
            "datePublished", "headline", "reviewBody",
            "reviewRating.ratingValue", "reviewRating.bestRating",
            "reviewRating.worstRating", "inLanguage",
        ]
        existing = [c for c in cols if c in df.columns]
        df_view = df[existing].sort_values("datePublished", ascending=False, na_position="last")

        st.success(f"Collected {len(df_view)} reviews.")
        st.dataframe(df_view)

        csv = df_view.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv, "trustpilot_reviews.csv", "text/csv")
