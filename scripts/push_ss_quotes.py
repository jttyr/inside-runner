"""Cotizaciones de TODO el universo del Stock Seeker, cada ~5 min, hacia la terminal (Render).

El radar completo (fundamentales, puntaje, categorias) se genera pocas veces al dia, pero el precio no puede esperar: aqui se baja de Yahoo el
ultimo precio de cada empresa (incluye pre y post mercado) y se envia a POST /ingest como {"ss_quotes": {"t": ..., "q": {SIM: [precio, hora]}}}.
La pagina las aplica sola cada minuto, asi el precio de la ficha coincide con el del grafico y con el del mercado.

Corre en el runner de GitHub (siempre tiene internet). Nunca falla por la red: lo que no alcance se completa en el siguiente ciclo.
No imprime secretos."""
import json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

GH = os.environ["RUNNER_TOKEN"]
URL = os.environ.get("RENDER_INGEST_URL", "https://terminal.insidetrades.app/ingest")
TOK = os.environ["INGEST_TOKEN"]
BUDGET = 200                                    # segundos para bajar de Yahoo; lo que falte queda para el siguiente ciclo
T0 = time.time()


def get(url, headers, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def quote_of(sym):
    """Ultimo precio con hora: la ultima barra de 5 min (con pre y post mercado); si no hay, el precio regular del meta."""
    last = None
    for host in ("query1", "query2"):
        u = f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=1d&interval=5m&includePrePost=true"
        try:
            res = json.loads(get(u, {"User-Agent": "Mozilla/5.0"}, 12))["chart"]["result"][0]
            ts = res.get("timestamp") or []
            cl = (res["indicators"]["quote"][0].get("close")) or []
            pts = [(t, c) for t, c in zip(ts, cl) if c is not None]
            meta = res.get("meta") or {}
            if pts:
                t, c = pts[-1]
                return [round(float(c), 4), int(t)]
            reg = meta.get("regularMarketPrice")
            if isinstance(reg, (int, float)):
                return [round(float(reg), 4), int(meta.get("regularMarketTime") or time.time())]
            return None
        except Exception as e:
            last = e
            time.sleep(0.3)
    raise last


def universe():
    raw = get("https://api.github.com/repos/jttyr/macro-indicators/contents/latest/stock-seeker.json?ref=data-stock",
              {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github.raw+json", "User-Agent": "inside-push"}, 60)
    rows = json.loads(raw).get("empresas") or []
    # primero el radar y las mayores (lo que mas se mira), despues el resto
    rows.sort(key=lambda c: (not c.get("radar"), -(c.get("cap_b") or 0)))
    return [c["s"] for c in rows]


def post(body_obj):
    body = json.dumps(body_obj).encode()
    for wait in (10, 25, 0):
        try:
            req = urllib.request.Request(URL, data=body, method="POST", headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "inside-push"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 413):
                print(f"::error::Render rechazo las cotizaciones (HTTP {e.code})")
                sys.exit(1)
        except Exception:
            pass
        if wait:
            time.sleep(wait)
    return None


def main():
    try:
        syms = universe()
    except Exception as e:
        print("::warning::no se pudo leer el universo:", type(e).__name__)
        return
    q = {}

    def one(s):
        if time.time() - T0 > BUDGET:
            return
        try:
            v = quote_of(s)
            if v:
                q[s] = v
        except Exception:
            pass
    with ThreadPoolExecutor(14) as ex:
        list(ex.map(one, syms))
    print(f"cotizaciones: {len(q)} de {len(syms)}")
    if len(q) < 20:
        print("::warning::muy pocas cotizaciones; no se envian")
        return
    st = post({"ss_quotes": {"t": int(time.time()), "q": q}})
    print("enviadas" if st else "::warning::Render no respondio; se reintenta en el siguiente ciclo")


main()
