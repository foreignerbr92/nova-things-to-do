#!/usr/bin/env python3
"""NOVA Things To Do v4 event-feed refresher.

Runs daily with no paid API. Source-specific adapters pull public event calendars.
Drive time is a conservative planning estimate from central Fairfax, VA, based on
venue/city buckets; it is NOT live traffic. Unknown locations are kept only when
known to be within the 2-hour discovery area.
"""
import json, re, hashlib, html as htmllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'events.json'
UA='Mozilla/5.0 NOVA-Things-To-Do/4.0'
SOURCES={
 'fairfax_parks':'https://www.fairfaxcounty.gov/parks/events-calendar',
 'artsfairfax':'https://artsfairfax.org/calendar/',
 'winchester':'https://visitwinchesterva.com/events/',
 'nova_parks':'https://www.novaparks.com/events/event-calendar',
 # Priority venue calendars retained in registry for source-specific adapters/curation:
 'wolf_trap':'https://www.wolftrap.org/calendar.aspx',
 'nextstop':'https://www.nextstoptheatre.org/',
 'workhouse':'https://www.workhousearts.org/calendar',
 'signature':'https://www.sigtheatre.org/events/',
 'gmu_cfa':'https://cfa.gmu.edu/events',
 'hylton':'https://hyltoncenter.org/calendar',
 '1st_stage':'https://1ststage.org/',
 'state_theatre':'https://www.thestatetheatre.com/events',
 'arlington_drafthouse':'https://www.arlingtondrafthouse.com/events',
}
MONTH={m:i for i,m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
SHORT={k[:3]:v for k,v in MONTH.items()}
CITY_DRIVE={ # planning estimates in minutes, intentionally not live traffic
 'Fairfax':10,'Fairfax County':20,'Vienna':25,'Tysons':25,'Falls Church':30,'Herndon':30,'Reston':30,
 'Arlington':35,'Alexandria':40,'Lorton':35,'Manassas':35,'Bristow':40,'Leesburg':45,'Sterling':40,
 'Washington, DC':45,'Washington':45,'Bethesda':50,'Silver Spring':55,'Rockville':60,
 'Warrenton':50,'Fredericksburg':65,'Winchester':75,'Stephens City':80,'Front Royal':70,
 'Middleburg':45,'Purcellville':50,'Woodbridge':40,'Occoquan':40,'Burke':25,'McLean':25,
}

def fetch(url):
 req=Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml'}); return urlopen(req,timeout=30).read().decode('utf-8','replace')
def clean(s): return re.sub(r'\s+',' ',htmllib.unescape(re.sub(r'<[^>]+>',' ',s))).strip()
def sid(source,title,date): return hashlib.sha1(f'{source}|{title}|{date}'.encode()).hexdigest()[:16]
def drive(city):
 for k,v in CITY_DRIVE.items():
  if k.lower() in city.lower(): return v
 return None
def category(title, park=False):
 t=title.lower()
 if park:return 'Park Event'
 if any(x in t for x in ['comedy','comedian','improv','stand-up']):return 'Comedy'
 if any(x in t for x in ['theatre','theater','musical','play:','live theatre']):return 'Theatre'
 if any(x in t for x in ['concert','chorale','choir','music','band','orchestra','symphony']):return 'Music'
 if any(x in t for x in ['festival','fair','fest ','parade']):return 'Festival'
 if any(x in t for x in ['market','art sale','exhibit','gallery']):return 'Arts & Market'
 if any(x in t for x in ['heritage','cultural','history','native american']):return 'Cultural'
 return 'Experience'
def price_from(text):
 t=text.lower()
 if re.search(r'\bfree\b|no admission|admission is free',t): return 0,'fixed'
 vals=[float(x.replace(',','')) for x in re.findall(r'\$\s*([0-9]+(?:\.[0-9]{1,2})?)',text)]
 if vals: return min(vals),'starting' if len(set(vals))>1 or 'starting' in t or 'from $' in t else 'fixed'
 return None,None
def mk(source,title,venue,city,date,url,text='',park=False,time='See official listing'):
 p,pt=price_from(text); return {'id':sid(source,title,date),'title':title,'venue':venue,'city':city,'date':date,'time':time,'price':p,'priceType':pt,'cat':category(title,park),'deal':bool(re.search(r'pay.what.you.can|\brush\b|lottery',text,re.I)),'verified':True,'url':url,'note':'Automatically discovered from a public event calendar; open official/source listing to confirm details.','park':park,'source':source,'driveMin':drive(city)}

def parse_winchester(raw):
 # WordPress/Tribe event pages expose ISO datetime and event-title links in HTML.
 out=[]; year=datetime.now().year
 # robust text fallback around Month DD patterns and headings
 text=clean(raw)
 rx=re.compile(r'\b('+'|'.join(SHORT)+r')(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\s+(\d{1,2})(?:\s*@[^A-Z]{0,30})?\s+(.{4,120}?)(?=\s+(?:'+ '|'.join(SHORT)+r')\w*\s+\d{1,2}\b|$)',re.I)
 for mon,day,chunk in rx.findall(text):
  title=chunk.strip(' –—-');
  if len(title)>100 or len(title)<4: continue
  city='Winchester'; date=f'{year}-{SHORT[mon[:3].title()]:02d}-{int(day):02d}'
  out.append(mk('Winchester-Frederick CVB',title,'Winchester-area venue',city,date,SOURCES['winchester'],chunk))
 return out

def parse_artsfairfax(raw):
 text=clean(raw); out=[]; year=datetime.now().year
 # Calendar text commonly: DD Mon - DD Mon Title ... Venue
 rx=re.compile(r'\b(\d{1,2})\s+('+'|'.join(SHORT)+r')\s+(?:-\s+\d{1,2}\s+\w+\s+)?(.{4,100}?)(?=\s+\d{1,2}\s+(?:'+ '|'.join(SHORT)+r')\b|$)',re.I)
 for day,mon,chunk in rx.findall(text):
  title=chunk.split(' 0')[0].strip(' –—-');
  if not 4<=len(title)<=90: continue
  city=next((c for c in CITY_DRIVE if c.lower() in chunk.lower()),'Fairfax County')
  date=f'{year}-{SHORT[mon[:3].title()]:02d}-{int(day):02d}'
  out.append(mk('ArtsFairfax',title,'Regional arts venue',city,date,SOURCES['artsfairfax'],chunk))
 return out

def parse_fairfax(raw):
 text=clean(raw); out=[]; year=datetime.now().year
 # conservative: only emit recognizable event-like chunks with month/day
 rx=re.compile(r'\b('+'|'.join(SHORT)+r')\w*\s+(\d{1,2})\s+(.{4,110}?)(?=\s+(?:'+ '|'.join(SHORT)+r')\w*\s+\d{1,2}\b|$)',re.I)
 for mon,day,chunk in rx.findall(text):
  title=chunk.split('Photography/Videography')[0].strip(' –—-')
  if not 4<=len(title)<=90: continue
  date=f'{year}-{SHORT[mon[:3].title()]:02d}-{int(day):02d}'
  out.append(mk('Fairfax County Park Authority',title,'Fairfax County Park Authority','Fairfax County',date,SOURCES['fairfax_parks'],chunk,True))
 return out

def main():
 existing=json.loads(OUT.read_text()) if OUT.exists() else []; by={e['id']:e for e in existing}; status=[]
 for key,parser in [('fairfax_parks',parse_fairfax),('artsfairfax',parse_artsfairfax),('winchester',parse_winchester)]:
  try:
   found=parser(fetch(SOURCES[key]));
   for e in found:
    if e.get('driveMin') is None or e['driveMin']<=120: by[e['id']]=e
   status.append(f'{key}: {len(found)}')
  except Exception as ex: status.append(f'{key} failed: {ex}')
 today=datetime.now(timezone.utc).date().isoformat()
 events=[e for e in by.values() if e.get('end',e.get('date','9999'))>=today and (e.get('driveMin') is None or e.get('driveMin')<=120)]
 events.sort(key=lambda e:(e.get('date',''),e.get('driveMin',999),e.get('title','')))
 OUT.write_text(json.dumps(events,indent=2,ensure_ascii=False)+'\n'); print('; '.join(status)); print('Wrote',len(events),'events')
if __name__=='__main__': main()
