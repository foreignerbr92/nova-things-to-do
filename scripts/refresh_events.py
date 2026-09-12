#!/usr/bin/env python3
"""
NOVA Things To Do — event collector v5

Free/public-source collector for events within roughly two hours of Fairfax, VA.
No paid API. Drive times are planning estimates, not live traffic.

Strategy
--------
1. Read schema.org Event JSON-LD whenever a site exposes it.
2. Use small source-specific text adapters where JSON-LD is absent.
3. Preserve still-future events already in the feed.
4. Write data/source_status.json so a green workflow is not mistaken for
   "every source worked".
5. Never invent a ticket price. Unknown price stays null ("Check price" in UI).
"""

import hashlib
import html as htmllib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "events.json"
STATUS_OUT = ROOT / "data" / "source_status.json"
UA = "Mozilla/5.0 (compatible; NOVA-Things-To-Do/5.0; public-event-calendar collector)"

SOURCES = {
    # Regional / aggregate
    "fairfax_parks": {
        "name": "Fairfax County Park Authority",
        "url": "https://www.fairfaxcounty.gov/parks/events-calendar",
        "city": "Fairfax County", "venue": "Fairfax County parks", "park": True,
    },
    "artsfairfax": {
        "name": "ArtsFairfax",
        "url": "https://artsfairfax.org/calendar/",
        "city": "Fairfax County", "venue": "Regional arts venue",
    },
    "nova_parks": {
        "name": "NOVA Parks",
        "url": "https://www.novaparks.com/events/event-calendar",
        "city": "Northern Virginia", "venue": "NOVA Parks", "park": True,
    },
    "winchester": {
        "name": "Winchester-Frederick County CVB",
        "url": "https://visitwinchesterva.com/events/",
        "city": "Winchester", "venue": "Winchester-area venue",
    },

    # Priority venues
    "wolf_trap": {
        "name": "Wolf Trap",
        "url": "https://www.wolftrap.org/",
        "city": "Vienna", "venue": "Wolf Trap",
    },
    "nextstop": {
        "name": "NextStop Theatre",
        "url": "https://www.nextstoptheatre.org/2026-27-season",
        "city": "Herndon", "venue": "NextStop Theatre",
    },
    "workhouse": {
        "name": "Workhouse Arts Center",
        "url": "https://www.workhousearts.org/calendar",
        "city": "Lorton", "venue": "Workhouse Arts Center",
    },
    "signature": {
        "name": "Signature Theatre",
        "url": "https://www.sigtheatre.org/events/",
        "city": "Arlington", "venue": "Signature Theatre",
    },
    "gmu_cfa": {
        "name": "GMU Center for the Arts",
        "url": "https://cfa.gmu.edu/events",
        "city": "Fairfax", "venue": "Center for the Arts at George Mason University",
    },
    "hylton": {
        "name": "Hylton Performing Arts Center",
        "url": "https://hyltoncenter.org/calendar",
        "city": "Manassas", "venue": "Hylton Performing Arts Center",
    },
    "first_stage": {
        "name": "1st Stage",
        "url": "https://1ststage.org/",
        "city": "Tysons", "venue": "1st Stage",
    },
    "state_theatre": {
        "name": "The State Theatre",
        "url": "https://www.thestatetheatre.com/events",
        "city": "Falls Church", "venue": "The State Theatre",
    },
    "arlington_drafthouse": {
        "name": "Arlington Cinema & Drafthouse",
        "url": "https://www.arlingtondrafthouse.com/",
        "city": "Arlington", "venue": "Arlington Cinema & Drafthouse",
    },
    "jammin_java": {
        "name": "Jammin Java",
        "url": "https://www.jamminjava.com/",
        "city": "Vienna", "venue": "Jammin Java",
    },
    "birchmere": {
        "name": "The Birchmere",
        "url": "https://www.birchmere.com/calendar/",
        "city": "Alexandria", "venue": "The Birchmere",
    },
    "capital_one_hall": {
        "name": "Capital One Hall",
        "url": "https://www.capitalonehall.com/events",
        "city": "Tysons", "venue": "Capital One Hall",
    },
}

CITY_DRIVE = {
    "Fairfax": 10, "Fairfax County": 20, "Northern Virginia": 35,
    "Vienna": 25, "Tysons": 25, "Falls Church": 30, "Herndon": 30,
    "Reston": 30, "Arlington": 35, "Alexandria": 40, "Lorton": 35,
    "Manassas": 35, "Bristow": 40, "Leesburg": 45, "Sterling": 40,
    "Washington, DC": 45, "Washington": 45, "Bethesda": 50,
    "Silver Spring": 55, "Rockville": 60, "Warrenton": 50,
    "Fredericksburg": 65, "Winchester": 75, "Stephens City": 80,
    "Front Royal": 70, "Middleburg": 45, "Purcellville": 50,
    "Woodbridge": 40, "Occoquan": 40, "Burke": 25, "McLean": 25,
}

MONTHS = {m: i for i, m in enumerate(
    ["January","February","March","April","May","June",
     "July","August","September","October","November","December"], 1)}
MONTH_ABBR = {m[:3].lower(): n for m, n in MONTHS.items()}


def fetch(url):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    with urlopen(req, timeout=35) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def strip_html(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def slug_id(source, title, start):
    return hashlib.sha1(f"{source}|{title}|{start}".encode("utf-8")).hexdigest()[:16]


def drive_for(city):
    c = city or ""
    for k, v in CITY_DRIVE.items():
        if k.lower() in c.lower():
            return v
    return None


def category(title, park=False):
    t = (title or "").lower()
    if park:
        return "Park Event"
    if any(x in t for x in ("comedy", "comedian", "improv", "stand-up", "standup")):
        return "Comedy"
    if any(x in t for x in ("theatre", "theater", "musical", "play", "stage")):
        return "Theatre"
    if any(x in t for x in ("concert", "chorale", "choir", "music", "band",
                             "orchestra", "symphony", "jazz", "tribute")):
        return "Music"
    if any(x in t for x in ("festival", "fair", "fest ", "parade")):
        return "Festival"
    if any(x in t for x in ("market", "art sale", "exhibit", "gallery", "art walk")):
        return "Arts & Market"
    if any(x in t for x in ("heritage", "cultural", "history", "museum", "native american")):
        return "Cultural"
    return "Experience"


def price_from_text(text):
    t = strip_html(text)
    lo = t.lower()
    if re.search(r"\bfree\b|no admission|admission is free|free admission", lo):
        return 0, "fixed"
    vals = [float(x.replace(",", "")) for x in
            re.findall(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", t)]
    if vals:
        unique = sorted(set(vals))
        typ = "starting" if len(unique) > 1 or "starting" in lo or "from $" in lo else "fixed"
        return min(unique), typ
    return None, None


def iso_date(value):
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def display_time(value):
    if not value:
        return "See official listing"
    s = str(value)
    m = re.search(r"T(\d{2}):(\d{2})", s)
    if not m:
        return "See official listing"
    hh, mm = map(int, m.groups())
    suffix = "AM" if hh < 12 else "PM"
    h = hh % 12 or 12
    return f"{h}:{mm:02d} {suffix}"


def event_obj(source_name, title, venue, city, start, url, text="",
              park=False, time_text=None, price=None, price_type=None, end=None):
    d = iso_date(start)
    if not d or not title:
        return None
    if price is None:
        price, price_type = price_from_text(text)
    obj = {
        "id": slug_id(source_name, strip_html(title), d),
        "title": strip_html(title),
        "venue": strip_html(venue) or source_name,
        "city": strip_html(city) or "Northern Virginia",
        "date": d,
        "time": time_text or display_time(start),
        "price": price,
        "priceType": price_type,
        "cat": category(title, park),
        "deal": bool(re.search(r"pay.?what.?you.?can|\brush\b|lottery", text or "", re.I)),
        "verified": True,
        "url": url,
        "note": "Automatically discovered from a public event calendar; confirm details on the official/source listing.",
        "park": bool(park),
        "source": source_name,
        "driveMin": drive_for(city or ""),
    }
    if end:
        obj["end"] = iso_date(end) or d
    return obj


def flatten_jsonld(node):
    if isinstance(node, list):
        for x in node:
            yield from flatten_jsonld(x)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from flatten_jsonld(node["@graph"])
        yield node


def jsonld_blocks(raw):
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw, re.I | re.S
    ):
        txt = htmllib.unescape(m.group(1)).strip()
        try:
            yield json.loads(txt)
        except Exception:
            # Some sites include harmless control characters / trailing commas.
            txt2 = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", txt)
            txt2 = re.sub(r",\s*([}\]])", r"\1", txt2)
            try:
                yield json.loads(txt2)
            except Exception:
                continue


def location_parts(loc, default_venue, default_city):
    venue, city = default_venue, default_city
    if isinstance(loc, str):
        venue = loc
    elif isinstance(loc, dict):
        venue = loc.get("name") or venue
        addr = loc.get("address")
        if isinstance(addr, dict):
            city = addr.get("addressLocality") or city
        elif isinstance(addr, str):
            for c in CITY_DRIVE:
                if c.lower() in addr.lower():
                    city = c
                    break
    return venue, city


def offer_price(offers):
    if isinstance(offers, dict):
        offers = [offers]
    vals = []
    if isinstance(offers, list):
        for off in offers:
            if not isinstance(off, dict):
                continue
            p = off.get("lowPrice", off.get("price"))
            try:
                vals.append(float(str(p).replace("$", "").replace(",", "")))
            except Exception:
                pass
    if vals:
        return min(vals), "starting" if len(set(vals)) > 1 else "fixed"
    return None, None


def parse_jsonld(raw, spec):
    out = []
    for block in jsonld_blocks(raw):
        for obj in flatten_jsonld(block):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if not any(str(x).lower() == "event" or str(x).lower().endswith("event") for x in types if x):
                continue
            title = obj.get("name")
            start = obj.get("startDate")
            if not title or not start:
                continue
            venue, city = location_parts(obj.get("location"), spec["venue"], spec["city"])
            url = obj.get("url") or spec["url"]
            desc = strip_html(obj.get("description", ""))
            p, pt = offer_price(obj.get("offers"))
            ev = event_obj(spec["name"], title, venue, city, start, url, desc,
                           spec.get("park", False), price=p, price_type=pt,
                           end=obj.get("endDate"))
            if ev:
                out.append(ev)
    return out


def parse_workhouse(raw, spec):
    text = strip_html(raw)
    out = []
    # Squarespace calendar text: Sep 12 ... Title ... Saturday, September 12, 2026 5:00 PM
    rx = re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4}).{0,100}?([A-Z][^|]{3,90}?)"
        r"(?=\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|$)",
        re.I
    )
    # Prefer explicit weekday/date/title sequence found in accessible page text.
    rx2 = re.compile(
        r"([A-Z][A-Za-z0-9'’&:!?,.\- ]{3,90}?)\s+"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)",
        re.I
    )
    for title, mon, day, yr, tm in rx2.findall(text):
        title = title.strip()
        if title.lower().startswith(("back to all events", "events at the workhouse")):
            continue
        d = f"{int(yr):04d}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        ev = event_obj(spec["name"], title, spec["venue"], spec["city"], d,
                       spec["url"], text, time_text=tm.upper())
        if ev:
            out.append(ev)
    return out


def parse_drafthouse(raw, spec):
    text = strip_html(raw)
    out = []
    # Example: Drew Lynch Friday, Sep 18 – 7:00pm
    rx = re.compile(
        r"([A-Z0-9$][A-Za-z0-9À-ÿ'’&:!?,.$()\- /]{2,90}?)\s+"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})"
        r"\s+[–—-]\s+(\d{1,2}:\d{2}\s*[ap]m)",
        re.I
    )
    today = datetime.now(timezone.utc).date()
    for title, mon, day, tm in rx.findall(text):
        month = MONTH_ABBR[mon.lower()]
        yr = today.year + (1 if month < today.month - 6 else 0)
        d = f"{yr:04d}-{month:02d}-{int(day):02d}"
        ev = event_obj(spec["name"], title, spec["venue"], spec["city"], d,
                       spec["url"], text, time_text=tm.upper().replace("PM"," PM").replace("AM"," AM"))
        if ev:
            out.append(ev)
    return out


def parse_wolftrap(raw, spec):
    text = strip_html(raw)
    out = []
    # Homepage/calendar cards commonly contain: Title ... SAT, SEPTEMBER 12 - 8 PM
    rx = re.compile(
        r"([A-Z0-9][A-Za-z0-9À-ÿ'’&:!?,.+\- /]{2,100}?)\s+"
        r"(?:MON|TUE|WED|THU|FRI|SAT|SUN),\s+"
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)"
        r"\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}(?::\d{2})?\s*[AP]M)",
        re.I
    )
    yr = datetime.now(timezone.utc).year
    for title, mon, day, tm in rx.findall(text):
        d = f"{yr:04d}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        ev = event_obj(spec["name"], title, spec["venue"], spec["city"], d,
                       spec["url"], text, time_text=tm.upper())
        if ev:
            out.append(ev)
    return out


def parse_nextstop(raw, spec):
    text = strip_html(raw)
    out = []
    # Season page: Title ... September 17-October 11, 2026
    rx = re.compile(
        r"([A-Z][A-Za-z0-9'’“”:&!?.,\- ]{3,90}?)\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2})\s*[-–—]\s*"
        r"(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?"
        r"(\d{1,2}),\s+(\d{4})",
        re.I
    )
    for title, sm, sd, em, ed, yr in rx.findall(text):
        title = title.strip()
        if len(title) > 90 or title.lower().startswith(("directed by", "by ")):
            continue
        start = f"{int(yr):04d}-{MONTHS[sm.title()]:02d}-{int(sd):02d}"
        endmon = em.title() if em else sm.title()
        end = f"{int(yr):04d}-{MONTHS[endmon]:02d}-{int(ed):02d}"
        ev = event_obj(spec["name"], title, spec["venue"], spec["city"], start,
                       spec["url"], text, end=end)
        if ev:
            out.append(ev)
    return out


SPECIAL = {
    "workhouse": parse_workhouse,
    "arlington_drafthouse": parse_drafthouse,
    "wolf_trap": parse_wolftrap,
    "nextstop": parse_nextstop,
}


def dedupe(events):
    best = {}
    for e in events:
        # Dedupe same title/date/source even if JSON-LD and fallback both saw it.
        key = (e.get("source","").lower(), e.get("title","").lower(), e.get("date",""))
        old = best.get(key)
        if old is None:
            best[key] = e
        else:
            # Prefer richer record.
            score = lambda x: sum([
                x.get("price") is not None,
                x.get("time") not in (None, "See official listing"),
                bool(x.get("url")),
                x.get("venue") not in (None, x.get("source")),
            ])
            if score(e) > score(old):
                best[key] = e
    return list(best.values())


def main():
    existing = []
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass

    discovered = []
    statuses = []
    successful_sources = 0

    for key, spec in SOURCES.items():
        try:
            raw, final_url = fetch(spec["url"])
            spec = dict(spec)
            spec["url"] = final_url

            found = parse_jsonld(raw, spec)
            fallback = SPECIAL.get(key)
            if fallback:
                found.extend(fallback(raw, spec))

            found = dedupe(found)
            found = [e for e in found if e.get("driveMin") is None or e["driveMin"] <= 120]
            discovered.extend(found)
            successful_sources += 1
            statuses.append({
                "key": key, "source": spec["name"], "ok": True,
                "events_found": len(found), "url": final_url
            })
            print(f"[OK] {key}: {len(found)} event(s)")
        except Exception as ex:
            statuses.append({
                "key": key, "source": spec["name"], "ok": False,
                "events_found": 0, "url": spec["url"], "error": str(ex)[:300]
            })
            print(f"[FAIL] {key}: {ex}", file=sys.stderr)

    today = datetime.now(timezone.utc).date().isoformat()

    # Keep future seed/previous records as a safety net, then overlay fresh records.
    combined = {}
    for e in existing:
        if e.get("end", e.get("date", "9999-99-99")) >= today:
            combined[e.get("id") or slug_id(e.get("source","legacy"), e.get("title",""), e.get("date",""))] = e
    for e in dedupe(discovered):
        combined[e["id"]] = e

    events = [
        e for e in combined.values()
        if e.get("end", e.get("date", "9999-99-99")) >= today
        and (e.get("driveMin") is None or e.get("driveMin") <= 120)
    ]
    events.sort(key=lambda e: (e.get("date",""), e.get("driveMin",999), e.get("title","")))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    status_doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources_attempted": len(SOURCES),
        "sources_reached": successful_sources,
        "fresh_events_discovered": len(dedupe(discovered)),
        "events_in_feed": len(events),
        "sources": statuses,
    }
    STATUS_OUT.write_text(json.dumps(status_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nFresh discovered: {status_doc['fresh_events_discovered']}")
    print(f"Final feed:       {len(events)}")
    print(f"Sources reached:  {successful_sources}/{len(SOURCES)}")

    # Only fail hard when the collector could not reach ANY source.
    # A source returning 0 events is visible in source_status.json and Actions logs.
    if successful_sources == 0:
        raise SystemExit("No event source could be reached; refusing silent success.")


if __name__ == "__main__":
    main()
