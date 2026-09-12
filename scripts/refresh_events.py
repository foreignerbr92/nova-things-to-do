#!/usr/bin/env python3
"""
NOVA Things To Do — collector v6
--------------------------------
Multi-strategy, no-paid-API event collector.

For every source:
  1) parse schema.org Event JSON-LD on the calendar page;
  2) discover likely event/show links from that page;
  3) visit a bounded number of official event pages and parse JSON-LD + visible text;
  4) run source-specific calendar/season text parsers where useful;
  5) preserve future records already collected;
  6) write data/source_status.json with per-source diagnostics.

Unknown prices remain null. Drive times are planning estimates from Fairfax,
not live traffic.
"""
import hashlib, html as htmllib, json, re, ssl, sys, time, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/events.json"
STATUS_OUT = ROOT / "data/source_status.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
TODAY = datetime.now(timezone.utc).date()
YEAR = TODAY.year

SOURCES = {
 "fairfax_parks":{"name":"Fairfax County Park Authority","url":"https://www.fairfaxcounty.gov/parks/events-calendar","city":"Fairfax County","venue":"Fairfax County parks","park":True},
 "artsfairfax":{"name":"ArtsFairfax","url":"https://artsfairfax.org/calendar/","city":"Fairfax County","venue":"Fairfax-area venue"},
 "nova_parks":{"name":"NOVA Parks","url":"https://www.novaparks.com/events/event-calendar","city":"Northern Virginia","venue":"NOVA Parks","park":True},
 "winchester":{"name":"Winchester-Frederick County CVB","url":"https://visitwinchesterva.com/events/","city":"Winchester","venue":"Winchester-area venue"},
 "wolf_trap":{"name":"Wolf Trap","url":"https://www.wolftrap.org/","city":"Vienna","venue":"Wolf Trap"},
 "nextstop":{"name":"NextStop Theatre","url":"https://www.nextstoptheatre.org/2026-27-season","city":"Herndon","venue":"NextStop Theatre"},
 "workhouse":{"name":"Workhouse Arts Center","url":"https://www.workhousearts.org/calendar","city":"Lorton","venue":"Workhouse Arts Center"},
 "signature":{"name":"Signature Theatre","url":"https://www.sigtheatre.org/events/","city":"Arlington","venue":"Signature Theatre"},
 "gmu_cfa":{"name":"GMU Center for the Arts","url":"https://cfa.gmu.edu/events/2026-27-season","city":"Fairfax","venue":"Center for the Arts at George Mason University"},
 "hylton":{"name":"Hylton Performing Arts Center","url":"https://hyltoncenter.org/calendar","city":"Manassas","venue":"Hylton Performing Arts Center"},
 "first_stage":{"name":"1st Stage","url":"https://1ststage.org/","city":"Tysons","venue":"1st Stage"},
 "state_theatre":{"name":"The State Theatre","url":"https://www.thestatetheatre.com/events","city":"Falls Church","venue":"The State Theatre"},
 "arlington_drafthouse":{"name":"Arlington Cinema & Drafthouse","url":"https://www.arlingtondrafthouse.com/events","city":"Arlington","venue":"Arlington Cinema & Drafthouse"},
 "jammin_java":{"name":"Jammin Java","url":"https://www.jamminjava.com/","city":"Vienna","venue":"Jammin Java"},
 "birchmere":{"name":"The Birchmere","url":"https://www.birchmere.com/calendar/","city":"Alexandria","venue":"The Birchmere"},
 "capital_one_hall":{"name":"Capital One Hall","url":"https://www.capitalonehall.com/events-tickets","city":"Tysons","venue":"Capital One Hall"},
 # Extra sources requested for broader discovery
 "fauquier_theatre":{"name":"Fauquier Community Theatre","url":"https://www.fctstage.org/","city":"Warrenton","venue":"Fauquier Community Theatre"},
 "low_players":{"name":"Lake of the Woods Players","url":"https://www.lowplayers.org/","city":"Locust Grove","venue":"Lake of the Woods Players"},
 "umw_theatre":{"name":"UMW Theatre","url":"https://cas.umw.edu/theatre/klein-theatre/season/","city":"Fredericksburg","venue":"Klein Theatre"},
 "franklin_park":{"name":"Franklin Park Arts Center","url":"https://www.franklinparkartscenter.org/","city":"Purcellville","venue":"Franklin Park Arts Center"},
}

CITY_DRIVE={"Fairfax":10,"Fairfax County":20,"Northern Virginia":35,"Vienna":25,"Tysons":25,
"Falls Church":30,"Herndon":30,"Reston":30,"Arlington":35,"Alexandria":40,"Lorton":35,
"Manassas":35,"Bristow":40,"Leesburg":45,"Sterling":40,"Washington":45,"Bethesda":50,
"Silver Spring":55,"Rockville":60,"Warrenton":50,"Fredericksburg":65,"Winchester":75,
"Stephens City":80,"Front Royal":70,"Middleburg":45,"Purcellville":50,"Woodbridge":40,
"Occoquan":40,"Burke":25,"McLean":25,"Locust Grove":70}

MONTHS={m:i for i,m in enumerate(["January","February","March","April","May","June",
"July","August","September","October","November","December"],1)}
MON={m[:3].lower():i for m,i in MONTHS.items()}

def fetch(url, timeout=25):
    req=Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        with urlopen(req,timeout=timeout) as r:
            return r.read().decode("utf-8","replace"),r.geturl()
    except Exception as e:
        # Some small venue sites have broken cert chains; retry only certificate failures.
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
            with urlopen(req,timeout=timeout,context=ctx) as r:
                return r.read().decode("utf-8","replace"),r.geturl()
        raise

def clean(x):
    return re.sub(r"\s+"," ",htmllib.unescape(re.sub(r"<[^>]+>"," ",str(x or "")))).strip()

def drive(city):
    for k,v in CITY_DRIVE.items():
        if k.lower() in (city or "").lower(): return v
    return None

def cat(title,park=False):
    t=(title or "").lower()
    if park:return "Park Event"
    if re.search(r"comedy|comedian|improv|stand.?up",t):return "Comedy"
    if re.search(r"theatre|theater|musical|\bplay\b|broadway|opera",t):return "Theatre"
    if re.search(r"concert|music|band|orchestra|symphony|jazz|tribute|choir|choral",t):return "Music"
    if re.search(r"festival|\bfair\b|\bfest\b|parade",t):return "Festival"
    if re.search(r"market|gallery|exhibit|art walk|craft",t):return "Arts & Market"
    if re.search(r"heritage|cultural|history|museum|native american",t):return "Cultural"
    return "Experience"

def iso_date(x):
    if not x:return None
    s=str(x).strip()
    m=re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b",s)
    if m:return m.group(0)
    for fmt in ("%B %d, %Y","%b %d, %Y","%m/%d/%Y"):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except:pass
    return None

def clock(x):
    s=str(x or "")
    m=re.search(r"T(\d\d):(\d\d)",s)
    if not m:return "See official listing"
    h,mi=map(int,m.groups()); return f"{h%12 or 12}:{mi:02d} {'AM' if h<12 else 'PM'}"

def price_text(text, allow_free=True):
    t=clean(text); lo=t.lower()
    # "Free" is high-risk on whole web pages (free parking, free newsletter,
    # free admission to a museum after a paid tour, etc.). Only callers with
    # event-local text should allow it.
    if allow_free and re.search(r"\b(?:free admission|admission is free|free event|tickets?\s*:?\s*free|\bfree\b)",lo):
        return 0,"fixed"
    vals=[]
    for x in re.findall(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)",t):
        try: vals.append(float(x))
        except: pass
    if vals:
        vals=[v for v in vals if 0 <= v <= 10000]
        if vals:return min(vals),"starting" if len(set(vals))>1 or "starting" in lo or "from $" in lo else "fixed"
    return None,None

def eid(source,title,d):
    return hashlib.sha1(f"{source}|{title}|{d}".encode()).hexdigest()[:16]

def make(spec,title,start,url,venue=None,city=None,text="",end=None,time_text=None,price=None,ptype=None,allow_free=True):
    d=iso_date(start)
    title=clean(title)
    if not d or not title or len(title)<2:return None
    city=clean(city) or spec["city"]; venue=clean(venue) or spec["venue"]
    if price is None:price,ptype=price_text(text,allow_free=allow_free)
    return {"id":eid(spec["name"],title,d),"title":title,"venue":venue,"city":city,"date":d,
      "time":time_text or clock(start),"price":price,"priceType":ptype,"cat":cat(title,spec.get("park",False)),
      "deal":bool(re.search(r"pay.?what.?you.?can|\brush\b|lottery",text or "",re.I)),"verified":True,
      "url":url,"note":"Automatically discovered from an official/public event listing; confirm details before going.",
      "park":bool(spec.get("park",False)),"source":spec["name"],"driveMin":drive(city),
      **({"end":iso_date(end)} if iso_date(end) else {})}

def jsonld(raw):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',raw,re.I|re.S):
        s=htmllib.unescape(m.group(1)).strip()
        for candidate in (s,re.sub(r",\s*([}\]])",r"\1",re.sub(r"[\x00-\x1f]","",s))):
            try: yield json.loads(candidate); break
            except: pass

def walk(x):
    if isinstance(x,list):
        for y in x:yield from walk(y)
    elif isinstance(x,dict):
        if "@graph" in x:yield from walk(x["@graph"])
        yield x

def locparts(loc,spec):
    venue,city=spec["venue"],spec["city"]
    if isinstance(loc,str):venue=loc
    elif isinstance(loc,dict):
        venue=loc.get("name") or venue; a=loc.get("address")
        if isinstance(a,dict):city=a.get("addressLocality") or city
        elif isinstance(a,str):
            for c in CITY_DRIVE:
                if c.lower() in a.lower():city=c;break
    return venue,city

def offer(offers):
    if isinstance(offers,dict):offers=[offers]
    vals=[]
    for o in offers if isinstance(offers,list) else []:
        if isinstance(o,dict):
            try:vals.append(float(str(o.get("lowPrice",o.get("price"))).replace("$","").replace(",","")))
            except:pass
    return (min(vals),"starting" if len(set(vals))>1 else "fixed") if vals else (None,None)

def parse_jsonld(raw,spec,base):
    out=[]
    for b in jsonld(raw):
        for o in walk(b):
            ty=o.get("@type",[])
            ty=ty if isinstance(ty,list) else [ty]
            if not any(str(z).lower().endswith("event") for z in ty):continue
            p,pt=offer(o.get("offers")); v,c=locparts(o.get("location"),spec)
            e=make(spec,o.get("name"),o.get("startDate"),urljoin(base,o.get("url") or base),
                   v,c,clean(o.get("description","")),o.get("endDate"),price=p,ptype=pt)
            if e:out.append(e)
    return out

def meta(raw,key):
    patterns=[
      rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
      rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']']
    for p in patterns:
        m=re.search(p,raw,re.I)
        if m:return clean(m.group(1))
    return None

def parse_event_page(raw,spec,url):
    out=parse_jsonld(raw,spec,url)
    if out:return out
    text=clean(raw)
    title=meta(raw,"og:title") or (re.search(r"<h1[^>]*>(.*?)</h1>",raw,re.I|re.S).group(1)
                                  if re.search(r"<h1[^>]*>(.*?)</h1>",raw,re.I|re.S) else None)
    # Full written date is deliberately conservative to avoid article/news dates.
    dates=re.findall(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",text,re.I)
    if title and dates:
        mon,day,yr=dates[0]; d=f"{int(yr):04d}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        tm=re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b",text,re.I)
        # Price comes from a bounded event-content window. This prevents
        # "FREE" in navigation/footer text from contaminating paid events.
        pos=text.lower().find(clean(title).lower())
        local=text[pos:pos+3500] if pos>=0 else text[:3500]
        e=make(spec,title,d,url,text=local,time_text=tm.group(1).upper() if tm else None,allow_free=True)
        if e:out.append(e)
    return out

def links(raw,base):
    host=urlparse(base).netloc.lower().replace("www.","")
    scored=[]
    for href,label in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',raw,re.I|re.S):
        u=urljoin(base,htmllib.unescape(href))
        if urlparse(u).scheme not in ("http","https"):continue
        if host not in urlparse(u).netloc.lower().replace("www.",""):continue
        s=(u+" "+clean(label)).lower()
        score=sum(x in s for x in ("/event","/events/","/show","calendar/event","performance","tickets"))
        if score and not re.search(r"login|privacy|terms|donate|support|membership",s):
            scored.append((score,u.split("#")[0]))
    return [u for _,u in sorted(set(scored),reverse=True)]

def generic_date_cards(raw,spec,base):
    """Conservative fallback for calendar pages with visible title + full date."""
    text=clean(raw); out=[]
    rx=re.compile(r"([A-Z][A-Za-z0-9À-ÿ'’“”&:!?,.+()\- /]{2,100}?)\s+"
      r"(January|February|March|April|May|June|July|August|September|October|November|December)"
      r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})",re.I)
    for title,mon,day,yr in rx.findall(text):
        if len(title)>100 or re.search(r"copyright|calendar|subscribe|privacy|updated",title,re.I):continue
        d=f"{int(yr):04d}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        e=make(spec,title,d,base,text="",allow_free=False)
        if e:out.append(e)
    return out

def nextstop_season(raw,spec,base):
    text=clean(raw);out=[]
    rx=re.compile(r"([A-Z][A-Za-z0-9'’‘“”:&!?.,\- ]{3,90}?)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*[-–—]\s*(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?(\d{1,2}),\s+(20\d{2})",re.I)
    for title,sm,sd,em,ed,yr in rx.findall(text):
        # trim author/director residue
        title=re.split(r"\b(?:Directed by|By)\b",title,flags=re.I)[0].strip()
        start=f"{yr}-{MONTHS[sm.title()]:02d}-{int(sd):02d}"; em=em or sm
        end=f"{yr}-{MONTHS[em.title()]:02d}-{int(ed):02d}"
        e=make(spec,title,start,base,text=text,end=end)
        if e:out.append(e)
    return out

def wolf_home(raw,spec,base):
    text=clean(raw);out=[]
    # Cards currently use "SAT, SEPTEMBER 12 - 8 PM"
    rx=re.compile(r"([A-Z][A-Za-z0-9À-ÿ'’&:!?,.+\- /]{2,100}?)\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN),\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}(?::\d{2})?\s*[AP]M)",re.I)
    for title,mon,day,tm in rx.findall(text):
        d=f"{YEAR}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        # Card regex can swallow navigation prose; keep the last meaningful
        # title-like segment before the date.
        title=re.split(r"(?:Get Tickets|View Calendar|Tickets On Sale Now!)",title,flags=re.I)[-1].strip()
        e=make(spec,title,d,base,text="",time_text=tm.upper(),allow_free=False)
        if e:out.append(e)
    return out

SPECIAL={"nextstop":nextstop_season,"wolf_trap":wolf_home}

def capital_one_cards(raw,spec,base):
    text=clean(raw); out=[]
    rx=re.compile(
      r"(September|October|November|December|January|February|March|April|May|June|July|August)\s+"
      r"(\d{1,2}),\s+(20\d{2})(?:\s+Doors:\s*[^#]{0,30})?\s+"
      r"(?:Presented By:\s*[^#]{0,80}\s+|Good Beats presents\s+|Gate 52 presents\s+)?"
      r"([A-Z][A-Za-z0-9À-ÿ'’“”&:!?,.+()\- /]{2,110}?)\s+"
      r"(?:Main Theater|The Vault)", re.I)
    for mon,day,yr,title in rx.findall(text):
        d=f"{yr}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        # Look locally for the start time.
        pos=text.lower().find(title.lower())
        chunk=text[pos:pos+220] if pos>=0 else ""
        tm=re.search(r"Event Starts\s+(\d{1,2}(?::\d{2})?\s*[AP]M)",chunk,re.I)
        e=make(spec,title,d,base,text=chunk,time_text=tm.group(1).upper() if tm else None)
        if e:out.append(e)
    return out

def arlington_cards(raw,spec,base):
    text=clean(raw); out=[]
    rx=re.compile(
      r"([A-Z0-9$][A-Za-z0-9À-ÿ'’“”&:!?,.$()\- /]{2,90}?)\s+"
      r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
      r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s*[–—-]\s*"
      r"(\d{1,2}:\d{2}\s*[ap]m)",re.I)
    for title,mon,day,tm in rx.findall(text):
        month=MON[mon.lower()]
        yr=YEAR + (1 if month < TODAY.month-6 else 0)
        d=f"{yr:04d}-{month:02d}-{int(day):02d}"
        e=make(spec,title,d,base,text=text,time_text=tm.upper())
        if e:out.append(e)
    return out

def umw_season(raw,spec,base):
    text=clean(raw); out=[]
    # Main productions: Title + Month d – d, yyyy
    rx=re.compile(
      r"([A-Z][A-Za-z0-9'’&:!?.,\- ]{2,70}?)\s+"
      r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
      r"(\d{1,2})\s*[–—-]\s*(\d{1,2}),\s+(20\d{2})",re.I)
    for title,mon,sd,ed,yr in rx.findall(text):
        title=title.strip()
        if re.search(r"season|box office|opens|exchange",title,re.I):continue
        start=f"{yr}-{MONTHS[mon.title()]:02d}-{int(sd):02d}"
        end=f"{yr}-{MONTHS[mon.title()]:02d}-{int(ed):02d}"
        e=make(spec,title,start,base,text=text,end=end,time_text="See official listing")
        if e:out.append(e)
    return out

def gmu_season(raw,spec,base):
    text=clean(raw); out=[]
    # The season page is editorial; crawl finds event links when present.
    # This fallback catches explicit performance statements such as
    # "Music From The Sole ... September 26".
    rx=re.compile(
      r"([A-Z][A-Za-z0-9À-ÿ'’“”&:!?,.+()\- /]{3,100}?)\s+(?:on\s+)?"
      r"(September|October|November|December|January|February|March|April|May|June|July|August)\s+"
      r"(\d{1,2})(?:,\s*(20\d{2}))?",re.I)
    for title,mon,day,yr in rx.findall(text):
        y=int(yr) if yr else (YEAR if MONTHS[mon.title()]>=TODAY.month else YEAR+1)
        d=f"{y:04d}-{MONTHS[mon.title()]:02d}-{int(day):02d}"
        if len(title)>100:continue
        e=make(spec,title,d,base,text="",allow_free=False)
        if e:out.append(e)
    return out

SPECIAL.update({
    "capital_one_hall": capital_one_cards,
    "arlington_drafthouse": arlington_cards,
    "umw_theatre": umw_season,
    "gmu_cfa": gmu_season,
})

FALLBACK_URLS={
  # Wolf Trap's main page can return 403 to GitHub-hosted runners. These are
  # official Wolf Trap pages that expose the same current-program information.
  "wolf_trap":[
    "https://www.wolftrap.org/shows/venues/filene-center/",
    "https://www.wolftrap.org/support/special-events",
    "https://www.wolftrap.org/centerlines/",
  ],
  # Arlington's ticketing calendar is public and often easier for server-side
  # collectors than the JS-heavy front end.
  "arlington_drafthouse":[
    "https://www-arlingtondrafthouse-com.seatengine.com/events"
  ],
  "capital_one_hall":[
    "https://www.capitalonehall.com/",
    "https://www.capitalonehall.com/events-tickets"
  ],
  "gmu_cfa":[
    "https://cfa.gmu.edu/",
    "https://cfa.gmu.edu/events/2026-27-season"
  ],
}

def canonical_url(url):
    if not url: return ""
    try:
        p=urlsplit(url)
        return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),"",""))
    except:
        return url

def norm_words(x):
    x=unicodedata.normalize("NFKD",clean(x)).encode("ascii","ignore").decode().lower()
    x=x.replace("&"," and ")
    x=re.sub(r"[^a-z0-9]+"," ",x)
    return " ".join(x.split())

def clean_title(title, event_date=None):
    """Remove calendar metadata accidentally swallowed into event titles."""
    t=clean(title)
    if not t:return t

    # Remove leading date metadata: Sep 12, September 12, 09/12, Sep 12 2026.
    months=r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    t=re.sub(rf"^\s*{months}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?\s*[-–—:|]*\s*","",t,flags=re.I)
    t=re.sub(r"^\s*\d{1,2}/\d{1,2}(?:/20\d{2})?\s*[-–—:|]*\s*","",t)

    # Some calendar cards append the weekday after the event title.
    t=re.sub(r"[\s,|–—-]+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*$","",t,flags=re.I)

    # Remove a duplicated date at the end.
    t=re.sub(rf"[\s,|–—-]+{months}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?\s*$","",t,flags=re.I)

    # Common parser/card prefixes.
    t=re.sub(r"^(?:event|events|calendar event)\s*[:\-–—]\s*","",t,flags=re.I)
    return re.sub(r"\s+"," ",t).strip(" ,-|–—")

def title_signature(title, event_date=None):
    t=clean_title(title,event_date)
    n=norm_words(t)
    # Remove weak calendar words only at the edges. Do NOT remove meaningful
    # words such as "family", "festival", "tour", etc.
    toks=n.split()
    weak={"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}
    while toks and toks[0] in weak:toks.pop(0)
    while toks and toks[-1] in weak:toks.pop()
    return " ".join(toks)

def token_similarity(a,b):
    A=set(a.split()); B=set(b.split())
    if not A or not B:return 0.0
    jac=len(A&B)/len(A|B)
    containment=len(A&B)/min(len(A),len(B))
    # Containment is useful for "Historic Campus Walking Tour" vs
    # "Lucy Burns Museum Historic Campus Walking Tour".
    return max(jac, containment*0.96)

def same_venue(a,b):
    va=norm_words(a.get("venue","")); vb=norm_words(b.get("venue",""))
    if not va or not vb:return True
    if va==vb or va in vb or vb in va:return True
    A=set(va.split());B=set(vb.split())
    return len(A&B)/max(1,min(len(A),len(B)))>=0.75

def same_session(a,b):
    if a.get("date")!=b.get("date"):return False
    if not same_venue(a,b):return False

    ta=title_signature(a.get("title",""),a.get("date"))
    tb=title_signature(b.get("title",""),b.get("date"))
    if ta==tb:return True
    if token_similarity(ta,tb)>=0.90:return True

    # Exact official URL is strong evidence when the date is also identical.
    ua=canonical_url(a.get("url","")); ub=canonical_url(b.get("url",""))
    if ua and ub and ua==ub and ta and tb:return token_similarity(ta,tb)>=0.72
    return False

def real_time(e):
    t=(e.get("time") or "").strip().lower()
    return bool(t and "official" not in t and "see " not in t and
                re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",t,re.I))

def source_priority(e):
    # Prefer the venue's own listing over an aggregator.
    src=norm_words(e.get("source",""))
    if "artsfairfax" in src or "winchester frederick" in src:return 1
    return 3

def completeness(e):
    return (source_priority(e)*20
            + (8 if real_time(e) else 0)
            + (8 if e.get("price") is not None else 0)
            + (5 if e.get("url") else 0)
            + (3 if e.get("venue") else 0)
            + (2 if e.get("city") else 0))

def merge_event(a,b):
    """Merge two records known to represent the same performance/session."""
    # Use the stronger record as the base, but always use the cleanest title.
    base,other=(a,b) if completeness(a)>=completeness(b) else (b,a)
    out=dict(base)

    ca=clean_title(a.get("title",""),a.get("date"))
    cb=clean_title(b.get("title",""),b.get("date"))
    # Prefer the cleaner title that is not suspiciously truncated.
    candidates=[x for x in (ca,cb) if x]
    if candidates:
        candidates.sort(key=lambda x:(len(title_signature(x).split()),len(x)),reverse=True)
        out["title"]=candidates[0]

    if not real_time(out) and real_time(other):out["time"]=other["time"]
    if out.get("price") is None and other.get("price") is not None:
        out["price"]=other.get("price"); out["priceType"]=other.get("priceType")
    if not out.get("end") and other.get("end"):out["end"]=other["end"]
    if not out.get("driveMin") and other.get("driveMin") is not None:out["driveMin"]=other["driveMin"]
    out["deal"]=bool(a.get("deal") or b.get("deal"))
    out["park"]=bool(a.get("park") or b.get("park"))
    out["verified"]=bool(a.get("verified") or b.get("verified"))

    # Preserve provenance without cluttering the card.
    sources=[]
    for e in (a,b):
        for x in e.get("sources",[e.get("source")]):
            if x and x not in sources:sources.append(x)
    out["sources"]=sources
    # Keep the preferred source label and its URL.
    out["id"]=eid(out.get("source",""),out["title"],out.get("date",""))
    return out

def dedupe(es):
    """Fuzzy, session-aware deduplication.

    Same show on different dates stays separate. Same show/date at the same
    venue merges even when one calendar added a date/weekday to the title.
    """
    cleaned=[]
    for original in es:
        e=dict(original)
        e["title"]=clean_title(e.get("title",""),e.get("date"))
        if not e["title"]:continue
        e["id"]=eid(e.get("source","legacy"),e["title"],e.get("date",""))
        cleaned.append(e)

    # Compare only inside date buckets: both safer and faster.
    buckets={}
    for e in cleaned:buckets.setdefault(e.get("date",""),[]).append(e)

    out=[]
    for date,items in buckets.items():
        merged=[]
        # Most complete records first improves which URL/source survives.
        for e in sorted(items,key=completeness,reverse=True):
            hit=None
            for i,x in enumerate(merged):
                if same_session(x,e):
                    hit=i;break
            if hit is None:merged.append(e)
            else:merged[hit]=merge_event(merged[hit],e)
        out.extend(merged)
    return out

def main():
    existing=[]
    try:existing=json.loads(OUT.read_text(encoding="utf-8"))
    except:pass
    fresh=[];statuses=[]
    for key,spec0 in SOURCES.items():
        spec=dict(spec0); found=[]; err=None; pages=0
        try:
            attempts=[spec["url"]]+FALLBACK_URLS.get(key,[])
            seen_urls=set()
            raws=[]
            last_ex=None
            for start_url in attempts:
                if start_url in seen_urls: continue
                seen_urls.add(start_url)
                try:
                    raw,base=fetch(start_url); raws.append((raw,base))
                except Exception as ex:
                    last_ex=ex
                    continue
            if not raws:
                raise last_ex or RuntimeError("No source page could be fetched")

            for raw,base in raws:
                found+=parse_jsonld(raw,spec,base)
                found+=generic_date_cards(raw,spec,base)
                if key in SPECIAL: found+=SPECIAL[key](raw,spec,base)

                # Crawl a bounded number of official event/show pages.
                cand=links(raw,base)[:45]
                for u in cand:
                    try:
                        r,final=fetch(u,18); pages+=1
                        found+=parse_event_page(r,spec,final)
                        time.sleep(.03)
                    except Exception:
                        continue
        except Exception as ex:
            err=str(ex)[:260]

        found=[e for e in dedupe(found)
               if e.get("end",e["date"])>=TODAY.isoformat()
               and (e.get("driveMin") is None or e["driveMin"]<=120)]
        fresh+=found
        statuses.append({"key":key,"source":spec["name"],"ok":err is None,
                         "events_found":len(found),"event_pages_checked":pages,
                         "url":spec0["url"],**({"error":err} if err else {})})
        print(f"[{'OK' if err is None else 'FAIL'}] {key}: {len(found)} event(s), {pages} event page(s) checked"
              + (f" | {err}" if err else ""))

    # Preserve future records, then deduplicate ACROSS existing + fresh data.
    # This prevents an old duplicate from surviving merely because its source/id
    # differs from the newly collected official record.
    candidates=[]
    for olde in existing:
        if olde.get("end",olde.get("date",""))<TODAY.isoformat():continue
        e=dict(olde)
        # v6-v8 could infer FREE from unrelated Workhouse page text.
        # Clear that legacy value; v9 fresh event-page parsing can restore a
        # verified price (e.g. Historic Campus Walking Tour = $10 adult).
        if norm_words(e.get("source",""))=="workhouse arts center" and e.get("price")==0:
            e["price"]=None; e["priceType"]=None
        candidates.append(e)
    candidates.extend(fresh)
    events=[e for e in dedupe(candidates)
            if e.get("end",e.get("date",""))>=TODAY.isoformat()]
    events.sort(key=lambda e:(e.get("date",""),e.get("driveMin") if e.get("driveMin") is not None else 999,e.get("title","")))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(events,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    status={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"sources_attempted":len(SOURCES),
            "sources_reached":sum(x["ok"] for x in statuses),"fresh_events_discovered":len(dedupe(fresh)),
            "events_in_feed":len(events),"sources":statuses}
    STATUS_OUT.write_text(json.dumps(status,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(f"\nFresh discovered: {status['fresh_events_discovered']}")
    print(f"Final feed: {len(events)}")
    print(f"Sources reached: {status['sources_reached']}/{len(SOURCES)}")
    if status["sources_reached"]==0:raise SystemExit("No source could be reached.")

if __name__=="__main__":main()
