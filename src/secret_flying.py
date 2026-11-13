"""Secret Flying RSS and HTML scraper for NYC/Newark mistake fares and flash sales."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.models import DealType, FlightDeal

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PRICE_PATTERN = re.compile(r"[\$€£]\s*(\d+)")
ROUTE_PATTERN = re.compile(
    r"(?:(?:from|flights\s+from)\s+)?([A-Za-z\s\.\,\-]+?)\s+to\s+([A-Za-z\s\.\,\-]+?)(?:\s+for|\s+from|\$|\€|\£)",
    re.IGNORECASE,
)
CLEAN_PREFIX_PATTERN = re.compile(
    r"^(?:(?:non-stop|nonstop|cheap\s+flights|flights|round-trip|roundtrip|error\s+fare[:\-]?|price\s+drop[:\-]?|🚨|🔥|💥)\s+)+",
    re.IGNORECASE,
)


def _clean_location(text: str) -> str:
    """Clean extra words from extracted location names."""
    cleaned = CLEAN_PREFIX_PATTERN.sub("", text).strip(" -:,\t\n")
    cleaned = re.sub(r"^(?:from\s+)", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


class SecretFlyingScanner:
    """Parses Secret Flying for mistake fares and flash sales originating in EWR/JFK/LGA/NYC."""

    SEARCH_URLS = [
        "https://www.secretflying.com/?s=New+York",
        "https://www.secretflying.com/?s=Newark",
        "https://www.secretflying.com/usa-deals/",
    ]

    def __init__(self, keywords: list[str] | None = None):
        self.keywords = [
            k.lower()
            for k in (keywords or ["ewr", "jfk", "lga", "new york", "newark", "nyc"])
        ]

    def is_nyc_match(self, text: str) -> bool:
        """Check if deal text mentions NYC metro airports or keywords."""
        t_lower = text.lower()
        return any(k in t_lower for k in self.keywords)

    def parse_deal_page(self, url: str) -> dict[str, str]:
        """Fetch article page to extract airline, date, and booking info."""
        details: dict[str, str] = {}
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=8)
            if resp.status_code != 200:
                return details
            soup = BeautifulSoup(resp.text, "html.parser")
            body_text = soup.get_text("\n")

            # Extract airline
            airline_match = re.search(r"AIRLINES?:\s*\n*([^\n]+)", body_text, re.IGNORECASE)
            if airline_match:
                details["airline"] = airline_match.group(1).strip()

            # Extract dates
            dates_match = re.search(
                r"DATES?:\s*\n*(?:Availability\s+(?:in|from)\s*\n*)?([A-Za-z0-9\s,\-]+?)(?:\n|Example|STOPS|AIRLINES)",
                body_text,
                re.IGNORECASE,
            )
            if dates_match:
                clean_d = " ".join(dates_match.group(1).split()).strip(" -:,")
                if clean_d and len(clean_d) > 2:
                    details["dates"] = clean_d

        except Exception as e:
            logger.debug(f"Could not parse Secret Flying subpage {url}: {e}")

        return details

    def scan(self) -> list[FlightDeal]:
        """Scrape deals matching NYC/Newark."""
        deals: list[FlightDeal] = []
        seen_urls: set[str] = set()

        for search_url in self.SEARCH_URLS:
            try:
                resp = requests.get(search_url, headers=REQUEST_HEADERS, timeout=10)
                if resp.status_code != 200:
                    logger.debug(f"Secret Flying {search_url} returned HTTP {resp.status_code}")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                articles = soup.find_all("article")
                if not articles:
                    # Fallback to search entries if article tags are absent
                    articles = soup.find_all("div", class_=re.compile(r"post|entry"))

                for art in articles:
                    a_tag = art.find("a", href=re.compile(r"/posts/"))
                    if not a_tag:
                        continue

                    post_url = a_tag.get("href", "")
                    if not post_url or post_url in seen_urls:
                        continue

                    # Extract title and link text
                    title = a_tag.get_text(strip=True)
                    if not title:
                        title_el = art.find(["h2", "h3", "h1"])
                        if title_el:
                            title = title_el.get_text(strip=True)

                    if not title or len(title) < 10:
                        continue

                    # Check if relevant to NYC
                    if not self.is_nyc_match(title) and not self.is_nyc_match(art.get_text()):
                        continue

                    seen_urls.add(post_url)

                    # Extract price - skip articles that have no price mentioned (e.g. news stories)
                    price_match = PRICE_PATTERN.search(title)
                    if not price_match:
                        # Must contain price or fare keywords to be a valid deal
                        if not re.search(r"\b(?:round-?trip|one-?way|fare|deal)\b", title, re.IGNORECASE):
                            continue
                        price = 0.0
                    else:
                        price = float(price_match.group(1))

                    # Must look like a flight route ("to", "flights", etc.)
                    if " to " not in title.lower() and " flights " not in title.lower():
                        continue

                    # Extract route
                    route_match = ROUTE_PATTERN.search(title)
                    raw_origin = route_match.group(1) if route_match else "New York"
                    raw_dest = route_match.group(2) if route_match else "International"

                    origin = _clean_location(raw_origin)
                    destination = _clean_location(raw_dest)

                    # Check for mistake fare keywords
                    is_mistake = bool(
                        re.search(
                            r"error\s*fare|mistake\s*fare|glitch|crazy\s*deal",
                            title,
                            re.IGNORECASE,
                        )
                    )

                    # Determine specific airport code if mentioned
                    origin_code = "EWR" if "newark" in title.lower() or "ewr" in title.lower() else "JFK"

                    # Optional detail enrichment
                    subpage_info = self.parse_deal_page(post_url)
                    airline = subpage_info.get("airline", "Various Airlines")
                    dates_str = subpage_info.get("dates", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

                    deal = FlightDeal(
                        origin=origin_code,
                        destination=destination[:20].strip(),
                        destination_name=destination,
                        departure_date=dates_str[:25],
                        return_date="Flexible",
                        price=price,
                        currency="USD",
                        deal_type=DealType.MISTAKE_FARE if is_mistake else DealType.SECRET_FLYING,
                        airline=airline,
                        stops=0 if "non-stop" in title.lower() or "nonstop" in title.lower() else 1,
                        deep_link=post_url,
                        notes=f"Spotted via Secret Flying: {title}",
                        source="secret_flying",
                    )
                    deals.append(deal)

            except Exception as e:
                logger.warning(f"Error scraping Secret Flying {search_url}: {e}")

        logger.info(f"Secret Flying scan complete: found {len(deals)} candidate deals.")
        return deals
