"""Empuja a la terminal (Render) los datos que ella normalmente iria a buscar.

Corre en el runner de GitHub (que siempre tiene internet): baja los JSON de los
repos de datos, el historial de precios y el tipo de cambio, y los envia en un
solo paquete a POST /ingest. Asi la pagina sigue funcionando aunque la instancia
de Render pierda su salida a internet. No imprime secretos."""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta

GH = os.environ["RUNNER_TOKEN"]
URL = os.environ.get("RENDER_INGEST_URL", "https://terminal.insidetrades.app/ingest")
TOK = os.environ["INGEST_TOKEN"]
OWNER = "jttyr"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gh_raw(repo, path):
    return json.loads(get(f"https://api.github.com/repos/{OWNER}/{repo}/contents/{path}?ref=data",
                          {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github.raw+json", "User-Agent": "inside-push"}))


def gh_list(repo, folder="latest"):
    j = json.loads(get(f"https://api.github.com/repos/{OWNER}/{repo}/contents/{folder}?ref=data",
                       {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github+json", "User-Agent": "inside-push"}))
    return [i["name"] for i in j]


files, listings, history = {}, {}, {}


def grab(repo, prefixes, keep):
    names = gh_list(repo)
    listings[f"{OWNER}/{repo}/latest@data"] = names
    for pre in prefixes:
        dates = sorted({n[len(pre) + 1:-5] for n in names if n.startswith(pre + "_") and n.endswith(".json") and DATE.match(n[len(pre) + 1:-5])}, reverse=True)[:keep]
        for d in dates:
            try:
                files[f"{OWNER}/{repo}/{pre}_{d}.json"] = gh_raw(repo, f"latest/{pre}_{d}.json")
            except Exception as e:
                print("omitido", repo, pre, d, type(e).__name__)


def step(name, fn):
    try:
        fn(); print("ok", name)
    except Exception as e:
        print("fallo", name, type(e).__name__, str(e)[:80])


step("gex", lambda: grab("gex-terminal", ["SPY", "QQQ", "DIA", "GLD"], 7))
step("market-drivers", lambda: grab("market-drivers", ["ES", "NQ", "DJI", "XAUUSD"], 7))
step("valuacion", lambda: grab("macro-valuation-data", ["OIL", "BRENT", "DXY", "US02Y", "US10Y", "US30Y"], 4))


def macro():
    for name in ("us-indicators.json", "sectors.json"):
        files[f"{OWNER}/macro-indicators/{name}"] = gh_raw("macro-indicators", f"latest/{name}")
step("macro", macro)

def stock_seeker():
    # radar de acciones (1.4 MB): se empuja para que la terminal no dependa de bajarlo ella misma desde Render
    files[f"{OWNER}/macro-indicators/stock-seeker.json"] = json.loads(get(
        f"https://api.github.com/repos/{OWNER}/macro-indicators/contents/latest/stock-seeker.json?ref=data-stock",
        {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github.raw+json", "User-Agent": "inside-push"}, timeout=60))
step("stock-seeker", stock_seeker)

ASSETS = [("ES", "ES=F"), ("NQ", "NQ=F"), ("DJI", "^DJI"), ("XAUUSD", "GC=F"), ("WTI", "CL=F"), ("BRENT", "BZ=F"),
          ("DXY", "DX-Y.NYB"), ("US02Y", "FRED:DGS2"), ("US10Y", "^TNX"), ("US30Y", "^TYX")]


def yahoo(sym):
    j = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=1y&interval=1d"))["chart"]["result"][0]
    off = (j.get("meta") or {}).get("gmtoffset", 0) or 0
    out = [(datetime.utcfromtimestamp(t + off).strftime("%Y-%m-%d"), float(c)) for t, c in zip(j.get("timestamp") or [], j["indicators"]["quote"][0]["close"]) if c is not None]
    live = (j.get("meta") or {}).get("regularMarketPrice")
    if out and isinstance(live, (int, float)):
        out[-1] = (out[-1][0], float(live))
    return out


def fred(sid):
    since = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d")
    rows = None
    for intento in range(2):                     # FRED a veces tarda; un reintento
        try:
            rows = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={since}", timeout=75).decode().strip().splitlines()[1:]
            break
        except Exception:
            if intento:
                raise
    out = []
    for ln in rows:
        d, _, v = ln.partition(",")
        try:
            out.append((d, float(v)))
        except ValueError:
            pass
    return out


def hist():
    for label, sym in ASSETS:
        try:
            pts = fred(sym[5:]) if sym.startswith("FRED:") else yahoo(sym)
            if len(pts) >= 30:
                history[label] = {"dates": [d for d, _ in pts], "closes": [round(c, 4) for _, c in pts]}
        except Exception as e:
            print("historial omitido", label, type(e).__name__)
step("historial", hist)

fx = None
def fxrate():
    global fx
    j = json.loads(get("https://api.frankfurter.app/latest?from=USD&to=MXN,EUR"))
    mxn = float(j["rates"]["MXN"]); eur = float(j["rates"]["EUR"])
    fx = {"ok": True, "mxn_per_usd": mxn, "mxn_per_eur": mxn / eur, "date": j.get("date")}
step("tipo de cambio", fxrate)

# ---- noticias en vivo: se bajan aqui (el runner siempre tiene internet) y se empujan ya filtradas y en espanol ----
live = None
FEEDS = [("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,%5EDJI,CL=F,DX-Y.NYB,%5ETNX&region=US&lang=en-US"),
         ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"), ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
         ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories")]
BLOCK = re.compile(r"penny stocks?|value stocks?|dividend stocks?|growth stocks?|\bROE\b|in-office|^\d+ [\w ,&-]*\bstocks\b", re.I)
RELEV = re.compile(
    r"\b(stocks?|shares|markets?|wall street|s&p|nasdaq|dow|futures|fed|fomc|powell|warsh|rates?|yields?|treasur\w*|bonds?|"
    r"inflation|cpi|pce|jobs|payrolls?|unemployment|gdp|economy|economic|tariffs?|oil|crude|brent|wti|opec|gold|dollar|"
    r"currency|earnings|revenue|guidance|ipo|merger|acquir\w*|deal|bank\w*|credit|debt|chips?|semiconductors?|ai|nvidia|"
    r"apple|microsoft|amazon|alphabet|google|meta|tesla|oracle|amd|intel|iran|china|russia|ukraine|israel|sanctions?)\b", re.I)


def clean(t, n):
    t = re.sub(r"<[^>]+>", " ", t or ""); t = re.sub(r"&[a-z]+;|&#\d+;", " ", t); t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0].rstrip(",.;: ") + "\u2026"


def noticias():
    global live
    import time, xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    now, items = time.time(), []
    for src, url in FEEDS:
        try:
            root = ET.fromstring(get(url, {"User-Agent": "Mozilla/5.0 (inside-market-terminal)"}, timeout=15))
        except Exception as e:
            print("feed omitido", src, type(e).__name__); continue
        for it in root.iter("item"):
            title, link = clean(it.findtext("title"), 160), (it.findtext("link") or "").strip()
            if not title or not link.startswith("http"):
                continue
            try:
                ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
            except Exception:
                continue
            items.append({"t": title, "s": src, "d": clean(it.findtext("description"), 110), "u": link, "ts": ts})
    seen, out = set(), []
    for it in sorted(items, key=lambda i: -i["ts"]):
        key = re.sub(r"\W+", "", it["t"].lower())[:60]
        if key in seen or it["ts"] < now - 36 * 3600 or not RELEV.search(it["t"]) or BLOCK.search(it["t"]):
            continue
        seen.add(key); out.append(it)
    out = out[:40]
    for it in out:                                   # al espanol (traductor gratuito); si falla, queda en ingles
        try:
            q = urllib.request.quote(it["t"])
            j = json.loads(get("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=es&dt=t&q=" + q, {"User-Agent": "Mozilla/5.0"}, timeout=10))
            es = "".join(seg[0] for seg in j[0] if seg and seg[0]).strip()
            if es:
                it["t"] = es
        except Exception:
            pass
    if not out:
        raise RuntimeError("sin titulares")
    live = {"items": out, "updated": int(now)}
step("noticias", noticias)

bundle = {"files": files, "listings": listings, "history": history, "fx": fx, "live": live}
body = json.dumps(bundle).encode()
print(f"paquete: {len(files)} archivos, {len(listings)} listados, {len(history)} historiales, {len(body)//1024} KB")
import time, urllib.error


def enviar():
    """POST con reintentos. Un servidor caido o reiniciandose (5xx, tiempo agotado, sin conexion) NO es un error del
    runner: se reintenta con espera y, si sigue sin responder, se avisa con una anotacion y el proximo envio (5 min)
    lo corrige solo, sin mandar un correo de fallo cada vez. Un 401/403 (token malo) si falla de verdad."""
    espera = (15, 30, 60)
    ultimo = ""
    for intento in range(len(espera) + 1):
        try:
            req = urllib.request.Request(URL, data=body, method="POST", headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "inside-push"})
            with urllib.request.urlopen(req, timeout=45) as r:
                print("render:", r.status, r.read().decode()[:120])
                return True
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 413):
                print(f"::error::Render rechazo el paquete (HTTP {e.code}): revisa INGEST_TOKEN / la ruta /ingest")
                sys.exit(1)
            ultimo = f"HTTP {e.code}"
        except Exception as e:
            ultimo = type(e).__name__
        print(f"intento {intento + 1}/{len(espera) + 1} sin exito ({ultimo})")
        if intento < len(espera):
            time.sleep(espera[intento])
    return False


if not enviar():
    print(f"::warning::Render no respondio (servidor caido o reiniciando); se reintenta en el siguiente ciclo")
