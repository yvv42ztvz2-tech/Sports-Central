from flask import Flask, jsonify, render_template, request
import requests, xml.etree.ElementTree as ET, time, re
from datetime import datetime, timezone
from functools import lru_cache

app = Flask(__name__, static_folder="static", template_folder="templates")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 SportsCentral/1.0"})
CACHE = {}
CACHE_SECONDS = 75

LEAGUES = {
    "nba": {"name":"NBA","sport":"basketball","path":"basketball/nba"},
    "wnba": {"name":"WNBA","sport":"basketball","path":"basketball/wnba"},
    "nfl": {"name":"NFL","sport":"football","path":"football/nfl"},
    "college-football": {"name":"NCAAF","sport":"football","path":"football/college-football"},
    "mlb": {"name":"MLB","sport":"baseball","path":"baseball/mlb"},
    "nhl": {"name":"NHL","sport":"hockey","path":"hockey/nhl"},
    "mls": {"name":"MLS","sport":"soccer","path":"soccer/usa.1"},
    "premier-league": {"name":"Premier League","sport":"soccer","path":"soccer/eng.1"},
    "uefa": {"name":"Champions League","sport":"soccer","path":"soccer/uefa.champions"},
}

def cached_get(key, url, ttl=CACHE_SECONDS):
    now = time.time()
    if key in CACHE and now - CACHE[key][0] < ttl:
        return CACHE[key][1]
    try:
        r = SESSION.get(url, timeout=12)
        r.raise_for_status()
        data = r.json()
        CACHE[key] = (now, data)
        return data
    except Exception as e:
        if key in CACHE:
            return CACHE[key][1]
        raise e

def clean_event(event, league):
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    teams = []
    for c in competitors:
        team = c.get("team") or {}
        teams.append({
            "id": team.get("id"),
            "name": team.get("displayName") or team.get("name") or "Team",
            "short": team.get("abbreviation") or "",
            "logo": team.get("logo") or (team.get("logos") or [{}])[0].get("href"),
            "score": c.get("score") or "0",
            "homeAway": c.get("homeAway"),
            "winner": c.get("winner", False),
            "record": (c.get("records") or [{}])[0].get("summary","")
        })
    status = comp.get("status") or event.get("status") or {}
    st = status.get("type") or {}
    venue = comp.get("venue") or {}
    return {
        "id": event.get("id"),
        "name": event.get("name") or event.get("shortName") or "Matchup",
        "date": event.get("date"),
        "league": league,
        "status": st.get("shortDetail") or st.get("detail") or "Scheduled",
        "state": st.get("state") or "pre",
        "completed": st.get("completed", False),
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "venue": venue.get("fullName",""),
        "broadcast": ", ".join((b.get("names") or [""])[0] for b in comp.get("broadcasts",[])[:2]),
        "teams": teams[:2]
    }

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/scores")
def scores():
    selected = request.args.get("leagues","nba,nfl,mlb,nhl,wnba,mls,premier-league,uefa,college-football").split(",")
    result, errors = [], []
    for key in selected:
        if key not in LEAGUES: continue
        league = LEAGUES[key]
        url = f"https://site.api.espn.com/apis/site/v2/sports/{league['path']}/scoreboard"
        try:
            data = cached_get("score-"+key, url)
            for event in data.get("events",[]):
                result.append(clean_event(event, league["name"]))
        except Exception as e:
            errors.append({"league":league["name"],"message":str(e)[:140]})
    result.sort(key=lambda x: (x["state"] != "in", x.get("date") or ""))
    return jsonify({"events":result,"updated":datetime.now(timezone.utc).isoformat(),"errors":errors})

@app.get("/api/news")
def news():
    query = request.args.get("q","sports").strip()[:100] or "sports"
    url = "https://news.google.com/rss/search?q=" + requests.utils.quote(query + " when:7d") + "&hl=en-US&gl=US&ceid=US:en"
    try:
        now=time.time()
        cachekey="news-"+query.lower()
        if cachekey in CACHE and now-CACHE[cachekey][0] < 300:
            return jsonify(CACHE[cachekey][1])
        r=SESSION.get(url,timeout=12); r.raise_for_status()
        root=ET.fromstring(r.content)
        items=[]
        for item in root.findall(".//item")[:18]:
            title=item.findtext("title","")
            link=item.findtext("link","")
            pub=item.findtext("pubDate","")
            source=item.find("source")
            items.append({"title":title,"url":link,"published":pub,"source":source.text if source is not None else "Google News"})
        payload={"items":items,"query":query,"updated":datetime.now(timezone.utc).isoformat()}
        CACHE[cachekey]=(now,payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"items":[],"error":"News feed temporarily unavailable: "+str(e)[:120]})

@app.get("/api/league/<league_key>")
def league_page(league_key):
    if league_key not in LEAGUES: return jsonify({"error":"Unknown league"}),404
    league=LEAGUES[league_key]
    base=f"https://site.api.espn.com/apis/site/v2/sports/{league['path']}"
    try:
        data=cached_get("teams-"+league_key,base+"/teams")
        teams=[]
        sports=data.get("sports",[])
        leagues=sports[0].get("leagues",[]) if sports else []
        for t in (leagues[0].get("teams",[]) if leagues else []):
            team=t.get("team",{})
            teams.append({"id":team.get("id"),"name":team.get("displayName"),"abbr":team.get("abbreviation"),
                          "logo":team.get("logos",[{}])[0].get("href") if team.get("logos") else None,
                          "location":team.get("location"),"nickname":team.get("name")})
        return jsonify({"league":league["name"],"teams":teams})
    except Exception as e:
        return jsonify({"league":league["name"],"teams":[],"error":str(e)[:120]})

@app.get("/api/team/<league_key>/<team_id>")
def team_detail(league_key,team_id):
    if league_key not in LEAGUES:return jsonify({"error":"Unknown league"}),404
    path=LEAGUES[league_key]["path"]
    url=f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team_id}"
    try:
        data=cached_get("team-"+league_key+"-"+team_id,url,180)
        team=data.get("team",{})
        return jsonify({"name":team.get("displayName"),"logo":(team.get("logos") or [{}])[0].get("href"),
                        "record":team.get("record"),"standingSummary":team.get("standingSummary"),
                        "description":team.get("standingSummary") or team.get("displayName","")})
    except Exception as e:return jsonify({"error":"Team details unavailable: "+str(e)[:120]}),502



@app.get("/api/team/<league_key>/<team_id>/roster")
def team_roster(league_key, team_id):
    if league_key not in LEAGUES: return jsonify({"error":"Unknown league"}),404
    path=LEAGUES[league_key]["path"]
    urls=[
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team_id}/roster?limit=1000",
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team_id}?enable=roster"
    ]
    def collect(obj, out):
        if isinstance(obj, dict):
            if obj.get("id") and (obj.get("fullName") or obj.get("displayName")) and (
                "position" in obj or "jersey" in obj or "headshot" in obj):
                out.append(obj)
            for value in obj.values(): collect(value,out)
        elif isinstance(obj,list):
            for value in obj: collect(value,out)
    try:
        data=None
        last_error=None
        for url in urls:
            try:
                data=cached_get("roster-"+league_key+"-"+team_id+"-"+str(urls.index(url)),url,300)
                if data: break
            except Exception as exc: last_error=exc
        athletes=[]
        if data:
            collect(data.get("athletes",data),athletes)
            unique={}
            for a in athletes:
                unique[str(a.get("id"))]={
                    "id":a.get("id"),"name":a.get("fullName") or a.get("displayName"),
                    "position":(a.get("position") or {}).get("abbreviation","") if isinstance(a.get("position"),dict) else str(a.get("position") or ""),
                    "jersey":a.get("jersey"),"age":a.get("age"),"height":a.get("displayHeight"),
                    "weight":a.get("displayWeight"),"headshot":(a.get("headshot") or {}).get("href") if isinstance(a.get("headshot"),dict) else a.get("headshot")}
            athletes=list(unique.values())
        return jsonify({"athletes":athletes,"count":len(athletes),"error":None if athletes else "Roster feed returned no player records"})
    except Exception as e:
        return jsonify({"athletes":[],"count":0,"error":str(e)[:140]})

@app.get("/api/team/<league_key>/<team_id>/schedule")
def team_schedule(league_key, team_id):
    if league_key not in LEAGUES: return jsonify({"error":"Unknown league"}),404
    path=LEAGUES[league_key]["path"]
    url=f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team_id}/schedule"
    try:
        data=cached_get("schedule-"+league_key+"-"+team_id,url,180)
        events=[clean_event(ev,LEAGUES[league_key]["name"]) for ev in data.get("events",[])]
        return jsonify({"events":events})
    except Exception as e: return jsonify({"events":[],"error":str(e)[:140]})

@app.get("/api/player/<league_key>/<player_id>")
def player_detail(league_key, player_id):
    if league_key not in LEAGUES: return jsonify({"error":"Unknown league"}),404
    path=LEAGUES[league_key]["path"]
    url=f"https://site.api.espn.com/apis/site/v2/sports/{path}/athletes/{player_id}"
    try:
        data=cached_get("player-"+league_key+"-"+player_id,url,300)
        a=data.get("athlete",data)
        stats=a.get("statistics") or {}
        return jsonify({"id":a.get("id"),"name":a.get("fullName") or a.get("displayName"),
            "position":(a.get("position") or {}).get("displayName",""),"team":(a.get("team") or {}).get("displayName",""),
            "headshot":(a.get("headshot") or {}).get("href"),"jersey":a.get("jersey"),
            "height":a.get("displayHeight"),"weight":a.get("displayWeight"),"age":a.get("age"),
            "stats":stats.get("splits",{}).get("categories",[])})
    except Exception as e: return jsonify({"error":"Player profile unavailable: "+str(e)[:120]}),502

@app.get("/api/game/<league_key>/<event_id>")
def game_detail(league_key,event_id):
    if league_key not in LEAGUES: return jsonify({"error":"Unknown league"}),404
    path=LEAGUES[league_key]["path"]
    url=f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event={event_id}"
    try:
        data=cached_get("game-"+league_key+"-"+event_id,url,90)
        box=data.get("boxscore",{})
        plays=data.get("plays",[])
        return jsonify({"header":data.get("header",{}),"boxscore":box,"plays":plays[:30],
                        "leaders":data.get("leaders",[]),"againstTheSpread":data.get("againstTheSpread",[])})
    except Exception as e: return jsonify({"error":"Game details unavailable: "+str(e)[:120]}),502

@app.get("/api/search")
def search_all():
    q=request.args.get("q","").strip().lower()[:80]
    if len(q)<2: return jsonify({"teams":[],"players":[]})
    teams_out=[]
    for key,league in LEAGUES.items():
        try:
            data=cached_get("teams-"+key,f"https://site.api.espn.com/apis/site/v2/sports/{league['path']}/teams",300)
            leagues=(data.get("sports") or [{}])[0].get("leagues",[])
            for item in (leagues[0].get("teams",[]) if leagues else []):
                t=item.get("team",{})
                name=t.get("displayName","")
                if q in name.lower() or q in t.get("abbreviation","").lower():
                    teams_out.append({"id":t.get("id"),"name":name,"league":key,"leagueName":league["name"],
                        "logo":(t.get("logos") or [{}])[0].get("href")})
        except Exception: pass
    players_out=[]
    for key,league in LEAGUES.items():
        try:
            data=cached_get("athletes-"+key,f"https://site.api.espn.com/apis/site/v2/sports/{league['path']}/athletes?limit=1000",900)
            athletes=data.get("items",[]) or data.get("athletes",[])
            for a in athletes:
                name=a.get("fullName") or a.get("displayName") or ""
                if q in name.lower():
                    players_out.append({"id":a.get("id"),"name":name,"league":key,"leagueName":league["name"],
                        "headshot":(a.get("headshot") or {}).get("href")})
        except Exception: pass
    return jsonify({"teams":teams_out[:20],"players":players_out[:20],
        "leagues":[{"key":k,"name":v["name"]} for k,v in LEAGUES.items() if q in v["name"].lower()]})



@app.get("/api/health")
def health():
    return jsonify({"ok":True,"time":datetime.now(timezone.utc).isoformat(),"data":"ESPN scoreboard API + Google News RSS"})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000,debug=False)
