from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import quote

import requests

HEADERS = {
    "User-Agent": "TrinityAI/0.3 (local desktop assistant; research tool)",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
}


def web_search(query: str, mode: str = "web") -> str:
    query = query.strip()
    if not query:
        return "Search query was empty."
    mode = (mode or "web").lower()
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        if mode == "news":
            rows = list(ddgs.news(query, max_results=5) or [])
            if not rows:
                return f"No news results for {query}."
            lines = []
            for row in rows:
                title = row.get("title") or row.get("source") or "Untitled"
                url = row.get("url") or row.get("link") or ""
                date = row.get("date") or ""
                body = row.get("body") or row.get("excerpt") or ""
                lines.append(f"- {title} ({date})\n  {url}\n  {body}")
            return "\n".join(lines)
        rows = list(ddgs.text(query, max_results=6) or [])
        if not rows:
            return _duck_html_fallback(query)
        return _format_text_results(rows)
    except Exception:
        return _duck_html_fallback(query)


def _format_text_results(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        title = row.get("title") or "Untitled"
        href = row.get("href") or row.get("link") or row.get("url") or ""
        body = row.get("body") or row.get("snippet") or ""
        lines.append(f"- {title}\n  {href}\n  {body}")
    return "\n".join(lines) if lines else "No results."


def _duck_html_fallback(query: str) -> str:
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Web search failed: {exc}"
    html = response.text
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.I | re.S)
    links = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html, re.I)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', html, re.I | re.S)
    if not titles:
        # lite layout
        titles = re.findall(r'uddg=[^"]+"[^>]*>(.*?)</a>', html, re.I | re.S)[:6]
    cleaned = []
    for i, title in enumerate(titles[:6]):
        href = links[i] if i < len(links) else ""
        snippet = snippets[i] if i < len(snippets) else ""
        cleaned.append(
            f"- {_strip_tags(title)}\n  {href}\n  {_strip_tags(snippet)}"
        )
    return "\n".join(cleaned) if cleaned else f"No web results for {query}."


def deep_research(topic: str, depth: int = 3) -> str:
    """Search, then actually read the top sources and hand back notes with links."""
    topic = topic.strip()
    if not topic:
        return "No research topic given."
    depth = max(1, min(int(depth or 3), 4))
    results = web_search(topic)
    if results.startswith(("Web search failed", "No web results", "No results")):
        return results
    urls = _extract_urls(results)[:depth]
    if not urls:
        return f"Search notes for {topic}:\n{results}"

    sections = [f"Research notes on {topic}", "", "Search summary:", results, ""]
    for index, url in enumerate(urls, 1):
        page = read_url(url)
        body = page.split("\n", 1)[1] if "\n" in page else page
        sections.append(f"Source {index}: {url}")
        sections.append(_condense(body, 1400))
        sections.append("")
    sections.append(
        "Synthesize these sources into an answer, note where they disagree, and cite the "
        "source numbers you relied on."
    )
    return "\n".join(sections)


def _extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"https?://[^\s)\]}>\"']+", text):
        url = match.group(0).rstrip(".,")
        if url not in found and "duckduckgo.com" not in url:
            found.append(url)
    return found


def _condense(text: str, limit: int) -> str:
    clean = re.sub(r"\n{2,}", "\n", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def read_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "No URL given."
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Could not read {url}: {exc}"
    ctype = response.headers.get("content-type", "")
    if "json" in ctype:
        text = response.text[:8000]
        return f"JSON from {url}:\n{text}"
    return f"Content from {url}:\n{_html_to_text(response.text)[:7000]}"


def wikipedia(topic: str) -> str:
    topic = topic.strip()
    if not topic:
        return "No topic given."
    api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(topic)}"
    try:
        response = requests.get(api, headers=HEADERS, timeout=15)
        if response.status_code == 404:
            search = requests.get(
                "https://en.wikipedia.org/w/rest.php/v1/search/title",
                params={"q": topic, "limit": 1},
                headers=HEADERS,
                timeout=15,
            )
            search.raise_for_status()
            pages = (search.json() or {}).get("pages") or []
            if not pages:
                return f"No Wikipedia page for {topic}."
            key = pages[0].get("key") or pages[0].get("title")
            response = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(str(key))}",
                headers=HEADERS,
                timeout=15,
            )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return f"Wikipedia lookup failed: {exc}"
    title = data.get("title") or topic
    extract = data.get("extract") or ""
    page_url = (data.get("content_urls") or {}).get("desktop", {}).get("page") or ""
    return f"{title}\n{extract}\n{page_url}".strip()


def weather(location: str) -> str:
    location = location.strip()
    if not location:
        return "No location given."
    try:
        results = _geocode(location)
        head = location.split(",")[0].strip()
        if not results and head and head != location:
            results = _geocode(head)
        if not results:
            return f"Could not geocode {location}."
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        label = ", ".join(
            p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p
        )
        wx = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            headers=HEADERS,
            timeout=15,
        )
        wx.raise_for_status()
        cur = (wx.json() or {}).get("current") or {}
        return (
            f"Weather for {label}: {describe_weather_code(cur.get('weather_code'))}, "
            f"{cur.get('temperature_2m')}°F "
            f"(feels {cur.get('apparent_temperature')}°F), "
            f"humidity {cur.get('relative_humidity_2m')}%, "
            f"wind {cur.get('wind_speed_10m')} mph."
        )
    except requests.RequestException as exc:
        return f"Weather lookup failed: {exc}"


WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "light freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "light snow showers",
    86: "snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with heavy hail",
}


def describe_weather_code(code: Any) -> str:
    try:
        return WEATHER_CODES.get(int(code), "unsettled conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def _geocode(name: str) -> list[dict[str, Any]]:
    response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 1},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return (response.json() or {}).get("results") or []


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p>", "\n\n", html)
    html = re.sub(r"(?is)</h[1-6]>", "\n", html)
    text = _strip_tags(html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_tags(html: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
