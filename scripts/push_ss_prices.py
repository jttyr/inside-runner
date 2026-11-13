"""Precarga los precios de Stock Seeker en la terminal (Render).

Corre en el runner de GitHub (siempre tiene internet). Para las empresas mas importantes y las del radar baja de Yahoo
el historial de 1 año (diario) y 5 dias (5 min, con pre y post mercado), arma las mismas series que la terminal armaria
(Dia, Semana, Mes, YTD, 1 año) y las envia a POST /ingest como {"ss_px": {...}}. Asi la grafica sale al instante,
sin esperar a Yahoo desde Render.

Nunca falla por culpa de la red: lo que no alcance se completa en la siguiente corrida (5 min). No imprime secretos."""
import json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

GH = os.environ["RUNNER_TOKEN"]
URL = os.environ.get("RENDER_INGEST_URL", "https://terminal.insidetrades.app/ingest")
TOK = os.environ["INGEST_TOKEN"]
MAX_TICKERS = int(os.environ.get("SS_PX_MAX", "120"))
BUDGET = 150                                     # segundos para bajar de Yahoo; lo que falte queda para la siguiente corrida
NY = ZoneInfo("America/New_York")
T0 = time.time()


def get(url, headers, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def chart(sym, rng, interval, prepost=False):
    last = None
    for host in ("query1", "query2"):
        u = f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range={rng}&interval={interval}" + ("&includePrePost=true" if prepost else "")
        try:
            res = json.loads(get(u, {"User-Agent": "Mozilla/5.0"}, 12))["chart"]["result"][0]
            ts = res.get("timestamp") or []
            cl = res["indicators"]["quote"][0]["close"]
            return [(t, float(c)) for t, c in zip(ts, cl) if c is not None], res.get("meta") or {}
        except Exception as e:
            last = e
            time.sleep(0.4)
    raise last


def build(sym):
    """Mismo formato que /ss-prices en la terminal."""
    y1, my = chart(sym, "1y", "1d")
    if len(y1) < 20:
        raise ValueError("pocos datos")
    try:
        dx, _ = chart(sym, "5d", "5m", True)
    except Exception:
        dx = []
    off = my.get("gmtoffset", 0) or 0
    iso = lambda t: datetime.utcfromtimestamp(t + off).strftime("%Y-%m-%d")
    d1, d5, base_d = [], [], y1[-2][1] if len(y1) > 1 else None
    if dx:
        days = sorted({datetime.fromtimestamp(t, NY).date() for t, _ in dx})
        if len(days) > 1:
            p = days[-2]
            t0 = datetime(p.year, p.month, p.day, 18, 0, tzinfo=NY).timestamp()
            d1 = [q for q in dx if q[0] >= t0]
        last_day = days[-1].strftime("%Y-%m-%d")
        base_d = y1[-2][1] if iso(y1[-1][0]) == last_day and len(y1) > 1 else y1[-1][1]      # antes de abrir hoy, el "ayer" es la ultima barra diaria
        for t, c in dx:                                                                       # semana: sesion regular cada 30 min
            lt = datetime.fromtimestamp(t, NY)
            if (lt.hour, lt.minute) >= (9, 30) and lt.hour < 16 and lt.minute % 30 == 0:
                d5.append((t, c))
    if not d1:
        d1 = y1[-2:]
    if not d5:
        d5 = y1[-6:]
    mes = y1[-23:]
    yr = iso(y1[-1][0])[:4]
    ytd_i = next((i for i, (t, _) in enumerate(y1) if iso(t)[:4] == yr), 0)
    ytd = y1[ytd_i:]
    pack = lambda pts, base: {"t": [p[0] for p in pts], "c": [round(p[1], 4) for p in pts], "base": base}
    return {
        "sym": sym, "off": off, "live": my.get("regularMarketPrice"),
        "D": pack(d1, base_d),
        "S": pack(d5, y1[-6][1] if len(y1) > 6 else None),
        "M": pack(mes[1:], mes[0][1]),
        "Y": pack(ytd, y1[ytd_i - 1][1] if ytd_i > 0 else ytd[0][1]),
        "A": pack(y1, y1[0][1]),
    }


def pick():
    raw = get("https://api.github.com/repos/jttyr/macro-indicators/contents/latest/stock-seeker.json?ref=data-stock",
              {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github.raw+json", "User-Agent": "inside-push"}, 60)
    rows = json.loads(raw).get("empresas") or []
    radar = [c["s"] for c in rows if c.get("radar")]
    big = [c["s"] for c in sorted(rows, key=lambda c: -(c.get("cap_b") or 0))]
    return list(dict.fromkeys(radar + big))[:MAX_TICKERS]


def post(chunk):
    body = json.dumps({"ss_px": chunk}).encode()
    for i, wait in enumerate((10, 25, 0)):
        try:
            req = urllib.request.Request(URL, data=body, method="POST", headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "inside-push"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 413):
                print(f"::error::Render rechazo los precios (HTTP {e.code})")
                sys.exit(1)
        except Exception:
            pass
        if wait:
            time.sleep(wait)
    return None


def main():
    try:
        syms = pick()
    except Exception as e:
        print("::warning::no se pudo leer el radar:", type(e).__name__)
        return
    out = {}

    def one(s):
        if time.time() - T0 > BUDGET:
            return
        try:
            out[s] = build(s)
        except Exception as e:
            print("omitido", s, type(e).__name__)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, syms))
    print(f"precios listos: {len(out)} de {len(syms)}")
    items = list(out.items())
    sent = 0
    for i in range(0, len(items), 30):
        st = post(dict(items[i:i + 30]))
        if st:
            sent += len(items[i:i + 30])
        else:
            print("::warning::Render no respondio al enviar precios; se reintenta en el siguiente ciclo")
            break
    print(f"enviados: {sent}")


main()
