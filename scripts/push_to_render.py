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


step("gex", lambda: grab("gex-terminal", ["SPY", "QQQ", "DIA", "GLD"], 4))
step("market-drivers", lambda: grab("market-drivers", ["ES", "NQ", "DJI", "XAUUSD"], 4))
step("valuacion", lambda: grab("macro-valuation-data", ["OIL", "BRENT", "DXY", "US02Y", "US10Y", "US30Y"], 4))


def macro():
    for name in ("us-indicators.json", "sectors.json"):
        files[f"{OWNER}/macro-indicators/{name}"] = gh_raw("macro-indicators", f"latest/{name}")
step("macro", macro)

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

bundle = {"files": files, "listings": listings, "history": history, "fx": fx}
body = json.dumps(bundle).encode()
print(f"paquete: {len(files)} archivos, {len(listings)} listados, {len(history)} historiales, {len(body)//1024} KB")
req = urllib.request.Request(URL, data=body, method="POST", headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "inside-push"})
with urllib.request.urlopen(req, timeout=60) as r:
    print("render:", r.status, r.read().decode()[:120])
