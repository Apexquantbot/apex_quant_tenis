import os
import io
import csv
import json
import math
import time
import random
import re
import difflib
import itertools
import unicodedata
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

VERSION = "1.0"

# ==============================================================================
# APEX QUANT TENIS
# Misma arquitectura que Apex Quant fútbol y baloncesto: calendario y resultados
# gratuitos (ESPN), cuotas reales (The Odds API) frente al consenso del mercado y
# un modelo estadístico propio:
#   1) Elo de cada jugador/a, general y por superficie (pista dura, tierra, hierba),
#      sembrado con el histórico público de Jeff Sackmann (GitHub) y actualizado
#      con cada partido terminado.
#   2) La probabilidad del Elo se convierte en % de puntos ganados al saque y se
#      simulan 10.000 partidos juego a juego -> ganador, total de juegos y hándicap.
# Circuitos: ATP y WTA (individuales).
# ==============================================================================
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
APP_URL = os.getenv("APP_URL", "https://apexquantbot.github.io/apex_quant_tenis/")

TZ_MADRID = ZoneInfo("Europe/Madrid")

TOURS = {
    "ATP": {"name": "ATP", "key": "atp", "flag": "👨", "espn": "atp", "odds_prefijo": "tennis_atp",
            "saque_base": 0.630, "sackmann": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{y}.csv"},
    "WTA": {"name": "WTA", "key": "wta", "flag": "👩", "espn": "wta", "odds_prefijo": "tennis_wta",
            "saque_base": 0.555, "sackmann": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{y}.csv"},
}
AJUSTE_SUPERFICIE = {"Hard": 0.0, "Clay": -0.015, "Grass": 0.015}
NOMBRE_SUPERFICIE = {"Hard": "Pista dura", "Clay": "Tierra batida", "Grass": "Hierba"}
GRAND_SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")

# ==============================================================================
# CUOTAS REALES (THE ODDS API) - MEJOR CASA vs CONSENSO DEL MERCADO
# En tenis la API tiene una clave por torneo (tennis_atp_wimbledon, ...). La lista de
# torneos activos y sus partidos se consulta gratis; solo cuesta crédito pedir cuotas.
# Si no hay cuotas (torneo no cubierto o créditos agotados) el bot da el pronóstico
# del modelo con CUOTA MÍNIMA, solo para favoritos claros.
# ==============================================================================
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
BOOKMAKERS_OBJETIVO = [b.strip() for b in os.getenv(
    "ODDS_BOOKMAKERS",
    "winamax_fr,winamax_de,betclic_fr,unibet_eu,williamhill,betsson,marathonbet,sport888,coolbet,tipico_de,nordicbet,leovegas"
).split(",") if b.strip()]
PESOS_CASAS = {"pinnacle": 3.0, "matchbook": 2.0, "betfair_ex_eu": 2.0, "smarkets": 2.0}
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "eu")
MIN_CASAS_CONSENSO = int(os.getenv("ODDS_MIN_CASAS", "4"))
MIN_CASAS_NICHO = int(os.getenv("ODDS_MIN_CASAS_NICHO", "3"))
CREDITOS_RESERVA = float(os.getenv("ODDS_MIN_CREDITS", "20"))
MAX_CONSULTAS_CICLO = int(os.getenv("ODDS_MAX_CALLS", "10"))
EV_MINIMO_PCT = float(os.getenv("EV_MINIMO_PCT", "3.0"))
EV_MAXIMO_PCT = 20.0
CUOTA_MAXIMA = float(os.getenv("ODDS_MAX_CUOTA", "3.0"))
PROB_MINIMA = float(os.getenv("ODDS_MIN_PROB", "0.40"))
PERFILES_MERCADOS = {"reducido": "h2h,spreads,totals", "basico": "h2h", "medio": "h2h,totals", "completo": "h2h,spreads,totals"}
ODDS_PERFIL = os.getenv("ODDS_PERFIL", "reducido")
ODDS_MARKETS_FORZADOS = os.getenv("ODDS_MARKETS", "")
MERCADOS_ACTIVOS = {m.strip().upper() for m in os.getenv("MERCADOS_ACTIVOS", "ML,HC,TOT").split(",") if m.strip()}
ODDS_DIA_RESET = int(os.getenv("ODDS_DIA_RESET", "1"))
ODDS_FACTOR_PRESUPUESTO = float(os.getenv("ODDS_FACTOR_PRESUPUESTO", "1.25"))

COMBI_ACTIVA = os.getenv("COMBI_ACTIVA", "1") == "1"
COMBI_MAX_PATAS = 3
COMBI_EV_PATA_MIN = float(os.getenv("COMBI_EV_PATA_MIN", "1.0"))
COMBI_PROB_PATA_MIN = float(os.getenv("COMBI_PROB_PATA_MIN", "0.50"))
COMBI_CUOTA_PATA = (1.25, 2.30)
COMBI_PROB_MIN = float(os.getenv("COMBI_PROB_MIN", "0.30"))
COMBI_EV_MIN = float(os.getenv("COMBI_EV_MIN", "6.0"))

VENTANA_MIN = 15
VENTANA_MAX = 90
MODELO_SOLO_ACTIVO = os.getenv("MODELO_SOLO_ACTIVO", "1") == "1"
MODELO_SOLO_PROB_MIN = float(os.getenv("MODELO_SOLO_PROB_MIN", "0.70"))
MODELO_SOLO_MARGEN = 1.05

STATS_PESO_MERCADO = float(os.getenv("STATS_PESO_MERCADO", "0.65"))
STATS_OBLIGATORIAS = os.getenv("STATS_OBLIGATORIAS", "1") == "1"
STATS_MIN_PARTIDOS = int(os.getenv("STATS_MIN_PARTIDOS", "8"))
N_SIMULACIONES = int(os.getenv("N_SIMULACIONES", "10000"))

ODDS_ESTADO = {"restantes": None, "consultas": 0}


# ==============================================================================
# NOMBRES DE JUGADORES
# ==============================================================================
def _tokens(nombre):
    t = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode("ascii").lower()
    for ch in ["-", ".", "'", "(", ")", ",", "/"]:
        t = t.replace(ch, " ")
    return [x for x in t.split() if x]


def clave_jugador(nombre):
    return "_".join(_tokens(nombre))


def nombres_equivalentes(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb or set(ta) == set(tb):
        return True
    if ta[-1] == tb[-1] and ta[0][0] == tb[0][0]:
        return True
    sa, sb = set(ta), set(tb)
    if len(sa & sb) / len(sa | sb) >= 0.6 and ta[0][0] == tb[0][0]:
        return True
    return difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.88


def normalizar_texto(texto):
    return " ".join(sorted(_tokens(texto)))


# ==============================================================================
# THE ODDS API
# ==============================================================================
def odds_get(path, params):
    if not THE_ODDS_API_KEY:
        return None, None
    q = dict(params)
    q["apiKey"] = THE_ODDS_API_KEY
    try:
        res = requests.get(f"{ODDS_API_BASE}{path}", params=q, timeout=15)
    except Exception as e:
        print(f"⚠️ Error The Odds API ({path}): {e}")
        return None, None
    rest = res.headers.get("x-requests-remaining")
    if rest is not None:
        try:
            ODDS_ESTADO["restantes"] = float(rest)
        except Exception:
            pass
    if res.status_code == 200:
        return res.json(), 200
    print(f"⚠️ The Odds API {res.status_code} en {path}: {res.text[:160]}")
    return None, res.status_code


def claves_tenis_activas(lista_sports, tour):
    pref = TOURS[tour]["odds_prefijo"]
    return [s["key"] for s in (lista_sports or []) if isinstance(s, dict) and s.get("active")
            and str(s.get("key", "")).startswith(pref) and "doubles" not in str(s.get("key", ""))]


def buscar_evento_odds(tour, j1, j2, inicio_utc, cache):
    """Busca el partido en los torneos activos del circuito. Devuelve (sport_key, evento, invertido)."""
    for sk in cache.get("_claves", {}).get(tour, []):
        if sk not in cache:
            data, _ = odds_get(f"/sports/{sk}/events", {"dateFormat": "iso"})   # gratis
            cache[sk] = data if isinstance(data, list) else []
        for ev in cache[sk]:
            h, a = ev.get("home_team"), ev.get("away_team")
            directo = nombres_equivalentes(h, j1) and nombres_equivalentes(a, j2)
            invertido = nombres_equivalentes(h, j2) and nombres_equivalentes(a, j1)
            if not (directo or invertido):
                continue
            if inicio_utc is not None:
                try:
                    t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                    if abs((t - inicio_utc).total_seconds()) > 14 * 3600:
                        continue
                except Exception:
                    pass
            return sk, ev, invertido
    return None, None, False


def mercados_a_consultar():
    if ODDS_MARKETS_FORZADOS:
        return ODDS_MARKETS_FORZADOS
    perfil = ODDS_PERFIL
    if perfil == "auto":
        r = ODDS_ESTADO["restantes"]
        perfil = "basico" if r is None else ("completo" if r >= 1500 else ("medio" if r >= 600 else "basico"))
    ODDS_ESTADO["perfil"] = perfil
    return PERFILES_MERCADOS.get(perfil, PERFILES_MERCADOS["reducido"])


def consultar_cuotas_evento(sport_key, event_id):
    base = mercados_a_consultar()
    vistos = []
    ODDS_ESTADO["consultas"] += 1
    for mk in [base, "h2h,totals", "h2h"]:
        if mk in vistos:
            continue
        vistos.append(mk)
        params = {"regions": ODDS_REGIONS, "markets": mk, "oddsFormat": "decimal", "dateFormat": "iso"}
        data, status = odds_get(f"/sports/{sport_key}/events/{event_id}/odds", params)
        if data:
            return data
        if status != 422:
            break
    return None


def parsear_bookmaker(bk, j1, j2):
    """Grupos {clave: {selección: cuota}} con los lados referidos a NUESTRO jugador 1 y 2.
    ("ML",) -> {"1","2"} · ("TOT", línea) -> {"O","U"} · ("HC", línea_del_j1) -> {"H","A"}"""
    g = {}

    def lado(nombre):
        if nombres_equivalentes(nombre, j1):
            return "1"
        if nombres_equivalentes(nombre, j2):
            return "2"
        return None

    for m in bk.get("markets", []):
        key = m.get("key") or ""
        for o in m.get("outcomes", []):
            precio = o.get("price")
            if not precio or precio <= 1.0:
                continue
            nombre = (o.get("name") or "").strip()
            punto = o.get("point")
            if key == "h2h":
                s = lado(nombre)
                if s:
                    g.setdefault(("ML",), {}).setdefault(s, precio)
            elif key == "totals" and punto is not None:
                low = nombre.lower()
                if low in ("over", "under"):
                    g.setdefault(("TOT", float(punto)), {}).setdefault("O" if low == "over" else "U", precio)
            elif key == "spreads" and punto is not None:
                s = lado(nombre)
                if s == "1":
                    g.setdefault(("HC", float(punto)), {}).setdefault("H", precio)
                elif s == "2":
                    g.setdefault(("HC", -float(punto)), {}).setdefault("A", precio)
    return g


def _quitar_margen(precios):
    inv = {k: 1.0 / v for k, v in precios.items() if v and v > 1.0}
    if len(inv) != len(precios) or not inv:
        return None
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else None


def _consenso(otros, extractor, minimo):
    acum, peso_total, n = {}, 0.0, 0
    for key, b in otros:
        precios = extractor(b)
        if not precios:
            continue
        pr = _quitar_margen(precios)
        if not pr:
            continue
        w = PESOS_CASAS.get(key, 1.0)
        n += 1
        peso_total += w
        for k, v in pr.items():
            acum[k] = acum.get(k, 0.0) + v * w
    if n < minimo or peso_total <= 0:
        return None, n
    return {k: v / peso_total for k, v in acum.items()}, n


def ev_y_stake(cuota, prob, push=0.0):
    ev = prob * cuota + push - 1.0
    if ev <= 0:
        return round(ev * 100, 1), 0
    b = cuota - 1.0
    q = max(0.0, 1.0 - prob - push)
    kelly = (b * prob - q) / b
    stake = min(10, max(1, round(max(0.0, kelly * 0.25) * 40)))
    return round(ev * 100, 1), stake


def dias_hasta_reinicio(hoy):
    reset = hoy.replace(day=min(ODDS_DIA_RESET, 28))
    if hoy.day >= reset.day:
        mes = hoy.month % 12 + 1
        anio = hoy.year + (1 if hoy.month == 12 else 0)
        reset = hoy.replace(year=anio, month=mes, day=min(ODDS_DIA_RESET, 28))
    return max(1, (reset - hoy).days)


def preparar_presupuesto(datos_previos, ahora_madrid):
    r = ODDS_ESTADO["restantes"]
    hoy_str = ahora_madrid.strftime("%Y-%m-%d")
    dia = dict(datos_previos.get("creditos_dia") or {})
    if r is None:
        ODDS_ESTADO["dia"], ODDS_ESTADO["presupuesto_dia"] = dia or None, None
        return
    if dia.get("fecha") != hoy_str or r > dia.get("restante_inicio", r):
        dia = {"fecha": hoy_str, "restante_inicio": r}
    ODDS_ESTADO["dia"] = dia
    ODDS_ESTADO["presupuesto_dia"] = dia["restante_inicio"] / dias_hasta_reinicio(ahora_madrid.date())


def presupuesto_agotado():
    dia, pres, r = ODDS_ESTADO.get("dia"), ODDS_ESTADO.get("presupuesto_dia"), ODDS_ESTADO.get("restantes")
    if not dia or pres is None or r is None:
        return False
    gastado = dia["restante_inicio"] - r
    coste = len(mercados_a_consultar().split(","))
    return gastado > 0 and (gastado + coste) > pres * ODDS_FACTOR_PRESUPUESTO


# ==============================================================================
# FUENTES GRATUITAS: ESPN (calendario, directo, resultados) y Sackmann (histórico)
# Formato interno de un partido:
# m = {"id", "utcDate", "status", "detalle", "tour", "torneo", "ronda", "superficie", "bo",
#      "p1": {"name", "flag"}, "p2": {...}, "sets": [[6, 4], [3, 6]], "ganador": 1|2|None, "retirada": bool}
# status: TIMED | IN_PLAY | FINISHED | POSTPONED | CANCELLED
# ==============================================================================
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (ApexQuantTenis; +https://apexquantbot.github.io/apex_quant_tenis/)",
                "Accept": "application/json"}
DIAG_FUENTES = {"espn_ok": 0, "espn_error": 0, "por_tour": {}, "sackmann": {}, "muestras": []}
INICIO_CICLO = time.time()
TIEMPO_MAX_DESCARGAS = float(os.getenv("TIEMPO_MAX_DESCARGAS", "540"))
RELLENO_DIAS_CICLO = int(os.getenv("RELLENO_DIAS_CICLO", "40"))
ESTADOS_CERRADOS = ("FINISHED", "CANCELLED", "POSTPONED")

TIERRA = ("roland garros", "french open", "monte carlo", "monte-carlo", "madrid", "internazionali", "italian open", "rome",
          "barcelona", "hamburg", "rio open", "buenos aires", "estoril", "munich", "bmw open", "geneva", "lyon", "bastad",
          "gstaad", "umag", "kitzbuhel", "santiago", "houston", "marrakech", "bucharest", "cordoba", "charleston",
          "rabat", "palermo", "prague", "strasbourg", "bogota", "iasi", "parma", "portoroz", "hamburg", "budapest",
          "lausanne", "bad gastein", "warsaw", "rouen", "istanbul", "oeiras")
HIERBA = ("wimbledon", "queen", "halle", "hertogenbosch", "libema", "eastbourne", "mallorca", "newport", "birmingham",
          "nottingham", "berlin", "bad homburg", "stuttgart open", "boss open")


def tiempo_usado():
    return time.time() - INICIO_CICLO


def http_json(url, params=None, intentos=2):
    for intento in range(intentos):
        try:
            res = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=(5, 15))
            if res.status_code == 200:
                try:
                    return res.json(), 200
                except Exception:
                    return None, "200-no-json"
            if res.status_code == 429 and intento + 1 < intentos:
                time.sleep(3)
                continue
            return None, res.status_code
        except Exception as e:
            print(f"   Aviso red ({url.split('?')[0]}): {e}")
            if intento + 1 < intentos:
                time.sleep(1)
    return None, "sin-respuesta"


def superficie_torneo(nombre, mapa_sackmann=None):
    n = " ".join(_tokens(nombre))
    if mapa_sackmann:
        if n in mapa_sackmann:
            return mapa_sackmann[n]
        for k, v in mapa_sackmann.items():
            if len(k) >= 6 and k in n:
                return v
    if any(x in n for x in HIERBA):
        return "Grass"
    if any(x in n for x in TIERRA):
        return "Clay"
    return "Hard"


def _int_o_none(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _estado_espn(st_obj):
    st = (st_obj or {}).get("type") or {}
    nombre = (st.get("name") or "").upper()
    state = st.get("state")
    detalle = st.get("shortDetail") or st.get("detail") or st.get("description") or ""
    det_up = detalle.upper()
    if "POSTPONED" in nombre or "SUSPENDED" in nombre:
        return "POSTPONED", detalle, False
    if "WALKOVER" in nombre or "W/O" in det_up or "WALKOVER" in det_up:
        return "CANCELLED", "Walkover", False
    if "CANCEL" in nombre:
        return "CANCELLED", detalle, False
    if "RETIRE" in nombre or re.search(r"\bRET\b|RETIRED|ABANDON", det_up) or "ABANDON" in nombre:
        return "FINISHED", "Retirada", True
    if state == "post" or st.get("completed") or "FINAL" in nombre:
        return "FINISHED", detalle, False
    if state == "in" or "PROGRESS" in nombre:
        return "IN_PLAY", detalle, False
    return "TIMED", detalle, False


def _competidor(c):
    ath = c.get("athlete") or {}
    nombre = ath.get("displayName") or ath.get("fullName") or ath.get("shortName")
    if not nombre:
        roster = c.get("roster") or {}
        nombre = roster.get("displayName") or c.get("displayName") or "Jugador"
    flag = (ath.get("flag") or {}).get("href") or ""
    sets = []
    for ls in c.get("linescores") or []:
        v = _int_o_none(ls.get("value"))
        if v is not None:
            sets.append(v)
    return {"name": nombre, "flag": flag, "sets": sets, "winner": bool(c.get("winner")),
            "order": _int_o_none(c.get("order")) or 9}


def espn_partidos_dia(espn_tour, fecha_yyyymmdd, mapa_sup):
    """Partidos individuales de un día. ESPN agrupa por torneo y, dentro, por cuadro (individual/dobles)."""
    if tiempo_usado() > TIEMPO_MAX_DESCARGAS:
        return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{espn_tour}/scoreboard"
    data, st = http_json(url, {"dates": fecha_yyyymmdd, "limit": 500})
    if len(DIAG_FUENTES["muestras"]) < 6:
        DIAG_FUENTES["muestras"].append({"o": espn_tour, "d": fecha_yyyymmdd, "st": st,
                                         "ev": len((data or {}).get("events") or []) if data else None})
    if data is None:
        DIAG_FUENTES["espn_error"] += 1
        print(f"   ⚠️ ESPN {espn_tour} {fecha_yyyymmdd}: HTTP {st}")
        return []
    DIAG_FUENTES["espn_ok"] += 1
    salida = []
    for ev in data.get("events", []) or []:
        torneo = ev.get("name") or ev.get("shortName") or "Torneo"
        grupos = ev.get("groupings")
        if not grupos:
            grupos = [{"grouping": {"slug": "singles", "displayName": ""}, "competitions": ev.get("competitions") or []}]
        for gr in grupos:
            g = gr.get("grouping") or {}
            slug = (g.get("slug") or g.get("displayName") or "").lower()
            if "double" in slug or "mixed" in slug:
                continue
            if "women" in slug or "womens" in slug:
                tour = "WTA"
            elif "men" in slug:
                tour = "ATP"
            else:
                tour = "WTA" if espn_tour == "wta" else "ATP"
            for comp in gr.get("competitions") or []:
                try:
                    cs = [_competidor(c) for c in comp.get("competitors") or []]
                    if len(cs) != 2:
                        continue
                    cs.sort(key=lambda c: c["order"])
                    if any("/" in c["name"] for c in cs):
                        continue   # dobles
                    status, detalle, ret = _estado_espn(comp.get("status") or ev.get("status"))
                    n = min(len(cs[0]["sets"]), len(cs[1]["sets"]))
                    sets = [[cs[0]["sets"][i], cs[1]["sets"][i]] for i in range(n)]
                    if status == "TIMED":
                        sets = []
                    ganador = 1 if cs[0]["winner"] else (2 if cs[1]["winner"] else None)
                    if status == "FINISHED" and ganador is None and sets and not ret:
                        s1 = sum(1 for a, b in sets if a > b)
                        s2 = sum(1 for a, b in sets if b > a)
                        ganador = 1 if s1 > s2 else (2 if s2 > s1 else None)
                    nt = " ".join(_tokens(torneo))
                    bo = 5 if (tour == "ATP" and any(x in nt for x in GRAND_SLAMS)) else 3
                    per = ((comp.get("format") or {}).get("regulation") or {}).get("periods")
                    if _int_o_none(per) in (3, 5):
                        bo = _int_o_none(per)
                    salida.append({
                        "id": f"te{comp.get('id')}",
                        "utcDate": comp.get("date") or comp.get("startDate") or ev.get("date") or "",
                        "status": status, "detalle": detalle, "tour": tour, "torneo": torneo,
                        "ronda": ((comp.get("round") or {}).get("displayName") or (comp.get("type") or {}).get("text") or ""),
                        "superficie": superficie_torneo(torneo, mapa_sup), "bo": bo,
                        "p1": {"name": cs[0]["name"], "flag": cs[0]["flag"]},
                        "p2": {"name": cs[1]["name"], "flag": cs[1]["flag"]},
                        "sets": sets, "ganador": ganador, "retirada": ret,
                    })
                except Exception as e:
                    print(f"   Aviso partido ESPN: {e}")
    return salida


def _fecha_utc(s):
    try:
        t = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except Exception:
        return None


def obtener_partidos(lunes_semana_str, partidos_previos, mapa_sup):
    ahora_utc = datetime.now(timezone.utc)
    hoy = ahora_utc.astimezone(TZ_MADRID).date()
    lunes = datetime.fromisoformat(lunes_semana_str).date()
    desde = lunes if not partidos_previos else max(lunes, hoy - timedelta(days=1))
    hasta = hoy + timedelta(days=2)
    matches = {}
    for espn_tour in ("atp", "wta"):
        d = desde - timedelta(days=1)
        while d <= hasta:
            for m in espn_partidos_dia(espn_tour, d.strftime("%Y%m%d"), mapa_sup):
                t = _fecha_utc(m["utcDate"])
                if t and lunes - timedelta(days=1) <= t.astimezone(TZ_MADRID).date() <= hasta:
                    matches[m["id"]] = m
            d += timedelta(days=1)
    for tour in TOURS:
        DIAG_FUENTES["por_tour"][tour] = sum(1 for m in matches.values() if m["tour"] == tour)
    print(f"📡 [ESPN] ATP {DIAG_FUENTES['por_tour'].get('ATP', 0)} · WTA {DIAG_FUENTES['por_tour'].get('WTA', 0)} partidos ({desde} a {hasta})")
    return list(matches.values())


# ==============================================================================
# ELO POR SUPERFICIE (archivo elo.json, pequeño: una ficha por jugador/a)
# ==============================================================================
ARCHIVO_ELO = "elo.json"
ELO_INICIAL = 1500.0


def cargar_elo():
    if os.path.exists(ARCHIVO_ELO):
        try:
            with open(ARCHIVO_ELO, "r", encoding="utf-8") as f:
                d = json.load(f)
                for k, v in (("jugadores", {}), ("procesados", {}), ("superficies", {}), ("estado", {})):
                    d.setdefault(k, v)
                return d
        except Exception as e:
            print(f"Aviso leyendo {ARCHIVO_ELO}: {e}")
    return {"version": 1, "jugadores": {}, "procesados": {}, "superficies": {}, "estado": {}}


def guardar_elo(elo, hoy):
    limite = (hoy - timedelta(days=120)).isoformat()
    elo["procesados"] = {k: v for k, v in elo["procesados"].items() if v >= limite}
    try:
        with open(ARCHIVO_ELO, "w", encoding="utf-8") as f:
            json.dump(elo, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"Aviso guardando {ARCHIVO_ELO}: {e}")


def _k_factor(n):
    return 250.0 / ((n + 5) ** 0.4)   # fórmula de FiveThirtyEight para tenis


def _ficha(elo, nombre, tour):
    k = clave_jugador(nombre)
    j = elo["jugadores"].get(k)
    if j is None:
        j = {"n": nombre, "t": tour, "e": ELO_INICIAL, "c": 0, "s": {}, "f": "", "u": ""}
        elo["jugadores"][k] = j
    return j


def buscar_ficha(elo, nombre):
    k = clave_jugador(nombre)
    if k in elo["jugadores"]:
        return elo["jugadores"][k]
    tk = _tokens(nombre)
    if not tk:
        return None
    candidatos = [j for kk, j in elo["jugadores"].items()
                  if kk.split("_")[-1] == tk[-1] and kk[:1] == tk[0][:1]]
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        return max(candidatos, key=lambda j: j.get("c", 0))
    return None


def rating(j, superficie):
    s = j["s"].get(superficie)
    if s and s[1] >= 1:
        return 0.5 * j["e"] + 0.5 * s[0]
    return j["e"]


def prob_elo(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def aplicar_partido_elo(elo, ganador, perdedor, superficie, tour, fecha):
    w, l = _ficha(elo, ganador, tour), _ficha(elo, perdedor, tour)
    p = prob_elo(w["e"], l["e"])
    kw, kl = _k_factor(w["c"]), _k_factor(l["c"])
    w["e"] += kw * (1 - p)
    l["e"] -= kl * (1 - p)
    w["c"] += 1
    l["c"] += 1
    sw = w["s"].setdefault(superficie, [ELO_INICIAL, 0])
    sl = l["s"].setdefault(superficie, [ELO_INICIAL, 0])
    ps = prob_elo(sw[0], sl[0])
    sw[0] += _k_factor(sw[1]) * (1 - ps)
    sl[0] -= _k_factor(sl[1]) * (1 - ps)
    sw[1] += 1
    sl[1] += 1
    for j, r in ((w, "W"), (l, "L")):
        j["f"] = (j.get("f", "") + r)[-5:]
        j["u"] = max(j.get("u", ""), fecha)
        j["e"] = round(j["e"], 1)
        j["s"][superficie][0] = round(j["s"][superficie][0], 1)
        j["t"] = tour


def sembrar_con_sackmann(elo, hoy):
    """Primera vez: descarga 2-3 temporadas del histórico público de Jeff Sackmann (CSV en GitHub)."""
    est = elo["estado"]
    if est.get("semilla") == "hecha":
        return
    est["intentos_semilla"] = est.get("intentos_semilla", 0) + 1
    filas = []
    for tour, cfg in TOURS.items():
        for y in (hoy.year - 2, hoy.year - 1, hoy.year):
            url = cfg["sackmann"].format(y=y)
            try:
                res = requests.get(url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]}, timeout=(5, 40))
            except Exception as e:
                DIAG_FUENTES["sackmann"][f"{tour}{y}"] = f"error {e.__class__.__name__}"
                continue
            DIAG_FUENTES["sackmann"][f"{tour}{y}"] = res.status_code
            if res.status_code != 200:
                continue
            for r in csv.DictReader(io.StringIO(res.text)):
                try:
                    score = (r.get("score") or "").upper()
                    if not r.get("winner_name") or not r.get("loser_name") or "W/O" in score or "WALK" in score or "RET" in score or "DEF" in score:
                        continue
                    d = r.get("tourney_date") or ""
                    fecha = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                    sup = r.get("surface") or "Hard"
                    if sup not in AJUSTE_SUPERFICIE:
                        sup = "Hard"   # Carpet -> pista dura
                    filas.append((fecha, _int_o_none(r.get("match_num")) or 0, tour, r["winner_name"], r["loser_name"], sup))
                    nt = " ".join(_tokens(r.get("tourney_name") or ""))
                    if nt and "davis" not in nt and "cup" not in nt:
                        elo["superficies"][nt] = sup
                except Exception:
                    continue
            print(f"   📚 Sackmann {tour} {y}: descargado")
    if not filas:
        print("   ⚠️ No se pudo descargar el histórico de Sackmann; se reintenta en el siguiente ciclo")
        if est["intentos_semilla"] >= 3:
            est["semilla"] = "hecha"
            est["inicio_espn"] = (hoy - timedelta(days=365)).isoformat()
            print("   ➡️ Tras 3 intentos se construye el Elo solo con ESPN (último año)")
        return
    filas.sort()
    for fecha, _, tour, w, l, sup in filas:
        aplicar_partido_elo(elo, w, l, sup, tour, fecha)
    ultima = max(f[0] for f in filas)
    est["semilla"] = "hecha"
    est["semilla_hasta"] = ultima
    est["inicio_espn"] = (datetime.fromisoformat(ultima).date() + timedelta(days=15)).isoformat()
    print(f"   ✅ Elo sembrado con {len(filas)} partidos (hasta {ultima}); {len(elo['jugadores'])} jugadores")


def registrar_en_elo(elo, m):
    """Aplica al Elo un partido terminado de ESPN (una sola vez)."""
    if m.get("status") != "FINISHED" or m.get("retirada") or not m.get("ganador") or not m.get("sets"):
        return False
    if elo["estado"].get("semilla") != "hecha":
        return False
    fecha = (m.get("utcDate") or "")[:10]
    if fecha < elo["estado"].get("inicio_espn", "9999"):
        return False
    if m["id"] in elo["procesados"]:
        return False
    w, l = (m["p1"], m["p2"]) if m["ganador"] == 1 else (m["p2"], m["p1"])
    aplicar_partido_elo(elo, w["name"], l["name"], m.get("superficie") or "Hard", m["tour"], fecha)
    elo["procesados"][m["id"]] = fecha
    return True


def rellenar_con_espn(elo, hoy):
    """Recorre con ESPN los días que faltan entre el final del histórico y ayer (unos días por ciclo)."""
    est = elo["estado"]
    if est.get("semilla") != "hecha":
        return 0
    cursor = datetime.fromisoformat(est.get("cursor_espn") or est.get("inicio_espn") or hoy.isoformat()).date()
    cursor = max(cursor, hoy - timedelta(days=400))
    nuevos, dias = 0, 0
    while cursor < hoy and dias < RELLENO_DIAS_CICLO and tiempo_usado() < TIEMPO_MAX_DESCARGAS * 0.6:
        lote = []
        for espn_tour in ("atp", "wta"):
            lote.extend(espn_partidos_dia(espn_tour, cursor.strftime("%Y%m%d"), elo["superficies"]))
        lote.sort(key=lambda m: m.get("utcDate") or "")
        nuevos += sum(1 for m in lote if registrar_en_elo(elo, m))
        cursor += timedelta(days=1)
        dias += 1
    est["cursor_espn"] = cursor.isoformat()
    if dias:
        print(f"   📚 Relleno ESPN: {dias} días revisados (+{nuevos} partidos) · al día hasta {cursor}")
    return nuevos


# ==============================================================================
# MODELO: Elo -> % de puntos al saque -> simulación juego a juego
# ==============================================================================
def p_juego(p):
    q = 1.0 - p
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * (p ** 3) * (q ** 3) * (p * p) / (1 - 2 * p * q)


def p_tiebreak(pa, pb):
    """A saca el primer punto. Primero a 7 con 2 de diferencia."""
    memo = {}

    def f(i, j):
        if i >= 7 and i - j >= 2:
            return 1.0
        if j >= 7 and j - i >= 2:
            return 0.0
        if i == j and i >= 6:
            a = pa * (1 - pb)
            b = (1 - pa) * pb
            return a / (a + b) if a + b > 0 else 0.5
        if (i, j) in memo:
            return memo[(i, j)]
        k = i + j + 1
        saca_a = ((k // 2) % 2) == 0
        p = pa if saca_a else 1 - pb
        r = p * f(i + 1, j) + (1 - p) * f(i, j + 1)
        memo[(i, j)] = r
        return r
    return f(0, 0)


def p_set(ga, gb, tb):
    """A saca el primer juego (y por tanto también el primer punto del desempate a 6-6).
    ga/gb = prob. de ganar el juego al saque; tb = prob. de A de ganar el desempate."""
    memo = {}

    def f(i, j):
        if (i >= 6 and i - j >= 2) or i == 7:
            return 1.0
        if (j >= 6 and j - i >= 2) or j == 7:
            return 0.0
        if i == 6 and j == 6:
            return tb
        if (i, j) in memo:
            return memo[(i, j)]
        p = ga if (i + j) % 2 == 0 else 1 - gb
        r = p * f(i + 1, j) + (1 - p) * f(i, j + 1)
        memo[(i, j)] = r
        return r
    return f(0, 0)


def prob_partido_desde_saque(pa, pb, bo):
    ga, gb = p_juego(pa), p_juego(pb)
    tb_a = p_tiebreak(pa, pb)
    ps = 0.5 * (p_set(ga, gb, tb_a) + _p_set_b(ga, gb, pa, pb))
    if bo == 5:
        return ps ** 3 * (1 + 3 * (1 - ps) + 6 * (1 - ps) ** 2)
    return ps ** 2 * (1 + 2 * (1 - ps))


def _p_set_b(ga, gb, pa, pb):
    """Probabilidad de que A gane el set cuando saca primero B."""
    tb_b = p_tiebreak(pb, pa)   # B saca primero en el desempate
    return 1 - p_set(gb, ga, tb_b)


def calibrar_saque(prob_objetivo, base, bo):
    lo, hi = -0.22, 0.22
    for _ in range(40):
        d = (lo + hi) / 2
        if prob_partido_desde_saque(base + d, base - d, bo) < prob_objetivo:
            lo = d
        else:
            hi = d
    d = (lo + hi) / 2
    return base + d, base - d


def simular(pa, pb, bo, n, semilla):
    """Muestras (juegos_j1, juegos_j2, sets_j1, sets_j2) juego a juego, con desempate a 6-6."""
    rng = random.Random(semilla)
    ga, gb = p_juego(pa), p_juego(pb)
    tba, tbb = p_tiebreak(pa, pb), p_tiebreak(pb, pa)
    necesarios = bo // 2 + 1
    muestras = []
    for _ in range(n):
        s1 = s2 = g1 = g2 = 0
        saca_a = rng.random() < 0.5
        while s1 < necesarios and s2 < necesarios:
            i = j = 0
            while True:
                if i == 6 and j == 6:
                    gana_a = rng.random() < (tba if saca_a else 1 - tbb)
                    if gana_a:
                        i += 1
                    else:
                        j += 1
                    saca_a = not saca_a
                    break
                gana_a = rng.random() < (ga if saca_a else 1 - gb)
                if gana_a:
                    i += 1
                else:
                    j += 1
                saca_a = not saca_a
                if (i >= 6 and i - j >= 2) or (j >= 6 and j - i >= 2):
                    break
            g1 += i
            g2 += j
            if i > j:
                s1 += 1
            else:
                s2 += 1
        muestras.append((g1, g2, s1, s2))
    return muestras


def calcular_modelo(m, elo, cache):
    """Modelo del partido o None si alguno de los dos tiene pocos partidos registrados."""
    clave = m["id"]
    if clave in cache:
        return cache[clave]
    a, b = buscar_ficha(elo, m["p1"]["name"]), buscar_ficha(elo, m["p2"]["name"])
    if not a or not b or a.get("c", 0) < STATS_MIN_PARTIDOS or b.get("c", 0) < STATS_MIN_PARTIDOS:
        cache[clave] = None
        return None
    sup = m.get("superficie") or "Hard"
    ra, rb = rating(a, sup), rating(b, sup)
    p = prob_elo(ra, rb)
    base = TOURS[m["tour"]]["saque_base"] + AJUSTE_SUPERFICIE.get(sup, 0.0)
    pa, pb = calibrar_saque(p, base, m.get("bo", 3))
    M = simular(pa, pb, m.get("bo", 3), N_SIMULACIONES, f"{m['id']}|{round(ra)}|{round(rb)}")
    n = len(M)
    juegos = sum(x[0] + x[1] for x in M) / n
    dif = sum(x[0] - x[1] for x in M) / n
    p1 = sum(1 for x in M if x[2] > x[3]) / n
    modelo = {"muestras": M, "elo_1": round(ra), "elo_2": round(rb), "prob_1": p1,
              "juegos": round(juegos, 1), "dif_juegos": round(dif, 1),
              "saque_1": round(pa * 100, 1), "saque_2": round(pb * 100, 1)}
    cache[clave] = modelo
    return modelo


# ==============================================================================
# SELECCIONES: spec = {"t": "ML"|"HC"|"TOT", "s": lado, "l": línea, "team": "L"|"V"}
# "L" = jugador 1 (primero en la tarjeta), "V" = jugador 2.
# Muestra / resultado: r = (juegos_j1, juegos_j2, sets_j1, sets_j2)
# ==============================================================================
def pred_win(spec):
    t, s, l = spec["t"], spec.get("s"), spec.get("l")
    if t == "ML":
        return (lambda r: r[2] > r[3]) if s == "1" else (lambda r: r[3] > r[2])
    if t == "TOT":
        return (lambda r: r[0] + r[1] > l) if s == "O" else (lambda r: r[0] + r[1] < l)
    if t == "HC":
        if spec["team"] == "L":
            return lambda r: (r[0] + l - r[1]) > 0
        return lambda r: (r[1] + l - r[0]) > 0
    return lambda r: False


def pred_push(spec):
    t, l = spec["t"], spec.get("l")
    if l is None or not float(l).is_integer():
        return None
    if t == "HC":
        if spec["team"] == "L":
            return lambda r: (r[0] + l - r[1]) == 0
        return lambda r: (r[1] + l - r[0]) == 0
    if t == "TOT":
        return lambda r: (r[0] + r[1]) == l
    return None


def prob_modelo_sel(modelo, spec):
    if not modelo:
        return None, None
    M = modelo["muestras"]
    pw, pp = pred_win(spec), pred_push(spec)
    win = sum(1 for r in M if pw(r)) / len(M)
    push = (sum(1 for r in M if pp(r)) / len(M)) if pp else 0.0
    return win, push


def describir_sel(spec, j1, j2):
    t, s, l = spec["t"], spec.get("s"), spec.get("l")
    if t == "ML":
        return f"Victoria {j1 if s == '1' else j2} (Ganador)"
    if t == "TOT":
        return f"Más de {l:g} juegos (Over {l:g})" if s == "O" else f"Menos de {l:g} juegos (Under {l:g})"
    if t == "HC":
        return f"Hándicap juegos {j1 if spec['team'] == 'L' else j2} {l:+g}"
    return str(spec)


def tipo_y_categoria(spec):
    return {"ML": ("GANADOR", "ganador"), "HC": ("HANDICAP", "handicap"), "TOT": ("JUEGOS", "juegos")}.get(spec["t"], ("GANADOR", "ganador"))


def jugador_de_sel(spec, j1, j2):
    if spec["t"] == "ML":
        return j1 if spec["s"] == "1" else j2
    if spec["t"] == "HC":
        return j1 if spec["team"] == "L" else j2
    return None


def resultado_tupla(info):
    sets = info.get("sets") or []
    if not sets:
        return None
    return (sum(a for a, _ in sets), sum(b for _, b in sets),
            sum(1 for a, b in sets if a > b), sum(1 for a, b in sets if b > a))


def resolver_seleccion(spec, info):
    """True / False / 'PUSH' / None. Si hay retirada o abandono, la apuesta se da por anulada (devuelta)."""
    if info.get("estado") == "CANCELLED":
        return "PUSH"   # walkover o partido cancelado: apuesta anulada
    if info.get("estado") != "FINISHED":
        return None
    if info.get("retirada"):
        return "PUSH"
    r = resultado_tupla(info)
    if r is None or not spec:
        return None
    if pred_win(spec)(r):
        return True
    pp = pred_push(spec)
    if pp and pp(r):
        return "PUSH"
    return False


# ==============================================================================
# ANÁLISIS DE UN PARTIDO
# ==============================================================================
ESPERADOS_GRUPO = {"ML": 2, "TOT": 2, "HC": 2}


def _spec_desde_grupo(gk, sel):
    tg = gk[0]
    if tg not in MERCADOS_ACTIVOS:
        return None
    if tg == "ML":
        return {"t": "ML", "s": sel}
    if tg == "TOT":
        return {"t": "TOT", "s": sel, "l": gk[1]}
    if tg == "HC":
        lh = gk[1]
        return {"t": "HC", "team": "L" if sel == "H" else "V", "l": lh if sel == "H" else -lh}
    return None


def generar_candidatos(ev_odds, j1, j2, modelo):
    books, titulos = {}, {}
    for bk in ev_odds.get("bookmakers", []):
        books[bk["key"]] = parsear_bookmaker(bk, j1, j2)
        titulos[bk["key"]] = bk.get("title") or bk["key"]
    print(f"   Casas disponibles en {j1} vs {j2}: {', '.join(sorted(books)) or 'ninguna'}")
    objetivos = [k for k in BOOKMAKERS_OBJETIVO if k in books]
    if not objetivos:
        return [], 0, "Ninguna casa objetivo ofrece cuotas de este partido", None
    mejores, patas_por_casa, n_max = {}, {}, 0
    for tk in objetivos:
        obj, casa = books[tk], titulos[tk]
        grupo = tk.split("_")[0]
        otros = [(k, b) for k, b in books.items() if k.split("_")[0] != grupo]
        cands_casa = []
        for gk, precios in obj.items():
            esperado = ESPERADOS_GRUPO[gk[0]]
            minimo = MIN_CASAS_CONSENSO if gk[0] == "ML" else MIN_CASAS_NICHO
            cons, n = _consenso(otros, lambda b, g=gk, e=esperado: b.get(g) if len(b.get(g, {})) == e else None, minimo)
            if not cons:
                continue
            n_max = max(n_max, n)
            for sel, cuota in precios.items():
                spec = _spec_desde_grupo(gk, sel)
                if not spec or sel not in cons:
                    continue
                p_merc = cons[sel]
                p_mod, pp = prob_modelo_sel(modelo, spec)
                push = 0.0
                if pred_push(spec) is not None:
                    if p_mod is None:
                        continue
                    push = pp
                    p_merc = p_merc * (1.0 - push)
                if p_mod is not None:
                    prob = STATS_PESO_MERCADO * p_merc + (1 - STATS_PESO_MERCADO) * p_mod
                    conf = (p_mod * cuota + push) > 1.0
                else:
                    prob, conf = p_merc, False
                ev, stake = ev_y_stake(cuota, prob, push)
                if ev <= 0 or ev > EV_MAXIMO_PCT or cuota > CUOTA_MAXIMA or prob < PROB_MINIMA:
                    continue
                tipo, cat = tipo_y_categoria(spec)
                texto = describir_sel(spec, j1, j2)
                cand = {"mercado": texto, "tipo_mercado": tipo, "categoria_visual": cat, "sel": spec,
                        "equipo_pronosticado": jugador_de_sel(spec, j1, j2), "cuota": round(cuota, 2),
                        "prob_modelo": f"{round(prob * 100, 1)}%", "ev": ev, "stake": stake,
                        "prob_mercado": round(p_merc * 100, 1),
                        "prob_estadistica": round(p_mod * 100, 1) if p_mod is not None else None,
                        "confirmado_stats": bool(conf), "n_casas": n, "casa": casa,
                        "prob_final": round(prob, 4), "score": round(ev * math.sqrt(prob), 3)}
                cands_casa.append(cand)
                previo = mejores.get(texto)
                if previo is None or (cand["confirmado_stats"], cand["ev"]) > (previo["confirmado_stats"], previo["ev"]):
                    mejores[texto] = cand
        legs = [c for c in cands_casa if c["confirmado_stats"] and c["prob_final"] >= COMBI_PROB_PATA_MIN
                and COMBI_CUOTA_PATA[0] <= c["cuota"] <= COMBI_CUOTA_PATA[1] and c["ev"] >= COMBI_EV_PATA_MIN
                and pred_push(c["sel"]) is None]
        legs = sorted(legs, key=lambda c: c["score"], reverse=True)[:2]
        if legs:
            patas_por_casa[casa] = [{"mercado": c["mercado"], "tipo": c["tipo_mercado"], "sel": c["sel"],
                                     "equipo": c["equipo_pronosticado"], "cuota": c["cuota"], "prob": c["prob_final"],
                                     "ev": c["ev"], "confirmado": True} for c in legs]
    fav = {"por_casa": patas_por_casa} if patas_por_casa else None
    cands = list(mejores.values())
    if not cands:
        if n_max < MIN_CASAS_CONSENSO:
            return [], n_max, f"Pocas casas para el consenso ({n_max}/{MIN_CASAS_CONSENSO})", fav
        return [], n_max, "Sin valor: ninguna casa supera al consenso del mercado", fav
    return cands, n_max, None, fav


def es_alertable(c):
    if c.get("cuota_minima"):
        return True
    return c["ev"] >= EV_MINIMO_PCT and (c.get("confirmado_stats") or not STATS_OBLIGATORIAS)


def _extras_modelo(modelo):
    if not modelo:
        return {"xg_local": None, "xg_visitante": None}
    return {"xg_local": modelo["elo_1"], "xg_visitante": modelo["elo_2"]}


def analizar_solo_modelo(m, modelo, motivo):
    j1, j2 = m["p1"]["name"], m["p2"]["name"]
    if not modelo:
        return None, True, "Sin datos suficientes de algún jugador/a para el modelo"
    p1 = modelo["prob_1"]
    s = "1" if p1 >= 0.5 else "2"
    p = p1 if s == "1" else 1 - p1
    nota = (f"Modelo: {round(p1 * 100)}% - {round((1 - p1) * 100)}% · {modelo['juegos']:g} juegos esperados "
            f"(Elo {modelo['elo_1']} vs {modelo['elo_2']})")
    if not MODELO_SOLO_ACTIVO or p < MODELO_SOLO_PROB_MIN:
        return None, True, f"{motivo}. Sin favorito claro para el modelo ({round(p * 100)}%). {nota}"
    spec = {"t": "ML", "s": s}
    tipo, cat = tipo_y_categoria(spec)
    a = {"mercado": describir_sel(spec, j1, j2), "tipo_mercado": tipo, "categoria_visual": cat, "sel": spec,
         "equipo_pronosticado": jugador_de_sel(spec, j1, j2), "cuota": round(MODELO_SOLO_MARGEN / p, 2),
         "cuota_ref": None, "cuota_minima": True, "solo_modelo": True, "prob_modelo": f"{round(p * 100, 1)}%",
         "ev": round((MODELO_SOLO_MARGEN - 1) * 100, 1), "stake": 1, "prob_mercado": None,
         "prob_estadistica": round(p * 100, 1), "confirmado_stats": True, "n_casas": None, "casa": None,
         "nota": f"{motivo}. {nota}", **_extras_modelo(modelo)}
    return a, True, a["nota"]


def analizar_partido(m, inicio_utc, elo, cache_modelos, cache_odds):
    """(analisis|None, definitivo, nota, fav). definitivo=False -> se reintenta en el siguiente ciclo."""
    modelo = calcular_modelo(m, elo, cache_modelos)
    j1, j2 = m["p1"]["name"], m["p2"]["name"]
    if not THE_ODDS_API_KEY:
        a, d, n = analizar_solo_modelo(m, modelo, "Sin clave de cuotas")
        return a, d, n, None
    if ODDS_ESTADO["restantes"] is not None and ODDS_ESTADO["restantes"] <= CREDITOS_RESERVA:
        a, d, n = analizar_solo_modelo(m, modelo, "Créditos de cuotas en reserva")
        return a, d, n, None
    sk, ev, _ = buscar_evento_odds(m["tour"], j1, j2, inicio_utc, cache_odds)
    if not ev:
        a, d, n = analizar_solo_modelo(m, modelo, "Partido sin cuotas en The Odds API")
        return a, d, n, None
    if ODDS_ESTADO["consultas"] >= MAX_CONSULTAS_CICLO:
        return None, False, "Límite de consultas por ciclo", None
    if presupuesto_agotado():
        a, d, n = analizar_solo_modelo(m, modelo, "Presupuesto diario de créditos agotado")
        return a, d, n, None
    datos = consultar_cuotas_evento(sk, ev.get("id"))
    if not datos:
        return None, False, "Sin respuesta de cuotas", None
    cands, n_casas, nota, fav = generar_candidatos(datos, j1, j2, modelo)
    alertables = [c for c in cands if es_alertable(c)]
    if alertables:
        mejor = max(alertables, key=lambda c: c["score"])
    elif cands:
        mejor = max(cands, key=lambda c: (c["confirmado_stats"], c["score"]))
    else:
        return None, True, nota, fav
    mejor.update(_extras_modelo(modelo))
    if modelo is None:
        mejor["nota"] = "Sin datos estadísticos suficientes para confirmar"
    elif not es_alertable(mejor):
        mejor["nota"] = "El modelo estadístico no respalda la apuesta" if not mejor["confirmado_stats"] else "Ventaja inferior al mínimo"
    return mejor, True, mejor.get("nota"), fav


CLAVES_ANALISIS = ("mercado", "tipo_mercado", "categoria_visual", "equipo_pronosticado", "cuota", "prob_modelo", "stake",
                   "n_casas", "casa", "prob_mercado", "prob_estadistica", "confirmado_stats", "xg_local", "xg_visitante",
                   "sel", "cuota_ref", "cuota_minima", "solo_modelo")


def reconstruir_analisis(prev):
    if not prev or not prev.get("tiene_pronostico") or not prev.get("mercado"):
        return None
    a = {k: prev.get(k) for k in CLAVES_ANALISIS}
    a["ev"] = prev.get("ev_numero")
    return a


# ==============================================================================
# TELEGRAM
# ==============================================================================
AVISO_JUEGO = "🔞 <i>+18 • Juega con responsabilidad • Información estadística, no es asesoramiento • jugarbien.es</i>"


def enviar_telegram(mensaje, url_accion=None, texto_boton="📱 Abrir en Web App"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"{mensaje}\n\n{AVISO_JUEGO}", "parse_mode": "HTML",
               "disable_web_page_preview": True}
    destino = url_accion or APP_URL
    if destino:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": texto_boton, "url": destino}]]}
    for intento in range(3):
        try:
            res = requests.post(url, json=payload, timeout=12)
            if res.status_code == 200:
                return True
            print(f"⚠️ Telegram rechazó el mensaje (HTTP {res.status_code}): {res.text[:200]}")
            if res.status_code in (400, 401, 403, 404):
                return False
            time.sleep(1.5)
        except Exception as e:
            print(f"Error Telegram intento {intento + 1}: {e}")
            time.sleep(2)
    return False


# ==============================================================================
# SEMANAS, HISTÓRICOS Y RESÚMENES (igual que fútbol y baloncesto)
# ==============================================================================
def cargar_datos_existentes():
    if os.path.exists("datos.json"):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Aviso leyendo datos existentes: {e}")
    return {}


def obtener_identificador_semana(fecha_madrid):
    anio, semana, _ = fecha_madrid.isocalendar()
    return f"{anio}-W{semana:02d}"


def obtener_fechas_limite_semana(fecha_madrid):
    lunes_dt = fecha_madrid - timedelta(days=fecha_madrid.weekday())
    return lunes_dt.strftime("%Y-%m-%d"), (lunes_dt + timedelta(days=6)).strftime("%Y-%m-%d")


def archivar_historico_mensual(alertas_viejas, mes_id):
    archivo = "historico_mensual.json"
    historico = {}
    if os.path.exists(archivo):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except Exception:
            historico = {}
    historico.setdefault(mes_id, [])
    ids = {a.get("id_alerta") for a in historico[mes_id]}
    for a in alertas_viejas:
        if a.get("id_alerta") not in ids:
            historico[mes_id].append(a)
    try:
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Aviso guardando histórico mensual: {e}")


def _a_float(txt, defecto):
    try:
        return float(str(txt).split("/")[0].replace("@", "").replace("U", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return defecto


def calcular_resumen_financiero(alertas_lista):
    apostadas, neto, acertadas, finalizadas = 0.0, 0.0, 0, 0
    for a in alertas_lista:
        if a.get("finalizado") and not a.get("push"):
            finalizadas += 1
            stk = _a_float(a.get("stake", "1"), 1.0)
            cuota = _a_float(a.get("cuota", "@2.00"), 2.0)
            apostadas += stk
            if a.get("acertado") is True:
                acertadas += 1
                neto += stk * (cuota - 1.0)
            else:
                neto -= stk
    roi = round((neto / apostadas) * 100, 1) if apostadas > 0 else 0.0
    return {"finalizadas": finalizadas, "acertadas": acertadas, "falladas": finalizadas - acertadas,
            "unidades_apostadas": round(apostadas, 1), "unidades_netas": round(neto, 2), "roi_pct": roi}


def formatear_fecha_corta(fecha_str):
    p = (fecha_str or "").split("-")
    return f"{p[2]}/{p[1]}" if len(p) == 3 else (fecha_str or "")


def texto_marcador(sets):
    return " ".join(f"{a}-{b}" for a, b in sets) if sets else None


# ---------------------------- COMBINADAS DE PARTIDOS ---------------------------
def buscar_mejor_combinada(partidos, alertas):
    usados = set()
    for a in alertas:
        if a.get("es_combinada"):
            usados.update(str(l.get("id")) for l in a.get("legs", []))
    por_casa = {}
    for p in partidos:
        fav = p.get("fav")
        if not fav or p.get("estado") != "TIMED" or p.get("minutos_restantes", 0) < 10:
            continue
        if str(p.get("id")) in usados or p.get("telegram_enviado"):
            continue
        for casa, patas in fav.get("por_casa", {}).items():
            mejor_leg = None
            for d in patas:
                if not (COMBI_CUOTA_PATA[0] <= d["cuota"] <= COMBI_CUOTA_PATA[1] and d["prob"] >= COMBI_PROB_PATA_MIN
                        and d["ev"] >= COMBI_EV_PATA_MIN and d.get("confirmado")):
                    continue
                score = d["ev"] * math.sqrt(d["prob"])
                if mejor_leg is None or score > mejor_leg["score"]:
                    mejor_leg = {"id": p.get("id"), "partido": f"{p.get('local')} vs {p.get('visitante')}",
                                 "equipo": d.get("equipo"), "mercado": d["mercado"], "tipo": d.get("tipo"),
                                 "sel": d.get("sel"), "cuota": d["cuota"], "prob": d["prob"], "ev": d["ev"],
                                 "score": score, "timestamp_ms": p.get("timestamp_ms", 0), "fecha": p.get("fecha"),
                                 "fecha_hora_display": p.get("fecha_hora_display")}
            if mejor_leg:
                por_casa.setdefault(casa, []).append(mejor_leg)
    mejor = None
    for casa, legs in por_casa.items():
        legs = sorted(legs, key=lambda l: l["ev"], reverse=True)[:8]
        for k in range(2, COMBI_MAX_PATAS + 1):
            for combo in itertools.combinations(legs, k):
                cuota = math.prod(l["cuota"] for l in combo)
                prob = math.prod(l["prob"] for l in combo)
                ev = (prob * cuota - 1.0) * 100
                if prob < COMBI_PROB_MIN or ev < COMBI_EV_MIN:
                    continue
                score = ev * math.sqrt(prob)
                if mejor is None or score > mejor["score"]:
                    mejor = {"casa": casa, "legs": list(combo), "cuota": cuota, "prob": prob, "ev": ev, "score": score}
    return mejor


def crear_alerta_combinada(c, semana_id):
    legs = sorted(c["legs"], key=lambda l: l["timestamp_ms"])
    primero = legs[0]
    txt = primero.get("fecha_hora_display") or ""
    return {
        "id_partido": "", "id_alerta": f"COMBI_{semana_id}_{'-'.join(str(l['id']) for l in legs)}",
        "partido": f"Combinada de partidos ×{len(legs)}", "mercado": " + ".join(l["mercado"] for l in legs),
        "tipo_mercado": "COMBINADA", "categoria_visual": "combinada", "es_combinada": True,
        "legs": [{"id": l["id"], "partido": l["partido"], "equipo": l["equipo"], "mercado": l["mercado"], "tipo": l["tipo"],
                  "sel": l["sel"], "cuota": l["cuota"], "fecha_hora_display": l["fecha_hora_display"],
                  "res": None, "marcador": None} for l in legs],
        "casa": c["casa"], "cuota": f"@{round(c['cuota'], 2)}", "ev": f"+{round(c['ev'], 1)}%",
        "prob_combinada": round(c["prob"] * 100, 1), "stake": "1/10",
        "hora": txt.split("•")[-1].strip() if "•" in txt else txt, "fecha": primero["fecha"],
        "fecha_hora_display": txt, "semana_id": semana_id, "es_ht": False, "es_semana_actual": True,
        "estado_partido": "TIMED", "marcador": None, "marcador_final": None, "finalizado": False,
        "acertado": None, "liquidada_telegram": False, "cuotas_reales": True,
    }


def mensaje_combinada(a):
    lineas = [f"{i}️⃣ <b>{l['mercado']}</b> — {l['partido']} ({l['fecha_hora_display']}) @{l['cuota']}"
              for i, l in enumerate(a["legs"], 1)]
    return (f"🎯 <b>COMBINADA DE PARTIDOS (+EV) ×{len(a['legs'])}</b>\n\n" + "\n".join(lineas) + "\n\n"
            f"🏦 <b>Casa:</b> {a['casa']} (todas las selecciones en la misma casa)\n"
            f"💰 <b>Cuota total:</b> {a['cuota']}\n"
            f"📈 <b>Ventaja combinada (+EV):</b> {a['ev']}\n"
            f"🎲 <b>Probabilidad de acierto:</b> {a['prob_combinada']}% (deben acertar todas)\n"
            f"💡 <b>Stake Sugerido:</b> {a['stake']} unidades\n\n"
            f"⚡ <i>Apex Quant Engine • Combinada con respaldo estadístico</i>")


def liquidar_combinadas(alertas, mapa_por_id):
    for a in alertas:
        if not a.get("es_combinada") or a.get("finalizado"):
            continue
        legs = a.get("legs", [])
        aciertos, pendientes, fallo, en_juego, nulas = 0, 0, False, False, 0
        for leg in legs:
            info = mapa_por_id.get(str(leg.get("id")))
            if not info:
                pendientes += 1
                continue
            res = resolver_seleccion(leg.get("sel"), info)
            if res is None:
                pendientes += 1
                en_juego = en_juego or info["estado"] == "IN_PLAY"
                continue
            leg["marcador"] = info.get("marcador")
            if res == "PUSH":
                leg["res"] = "NULA"
                nulas += 1
            elif res:
                leg["res"] = True
                aciertos += 1
            else:
                leg["res"] = False
                fallo = True
        if fallo or pendientes == 0:
            a["finalizado"] = True
            a["acertado"] = (not fallo) and pendientes == 0
            a["marcador_final"] = f"{aciertos}/{len(legs)} aciertos" + (f" ({nulas} nula)" if nulas else "")
            a["estado_partido"] = "FINISHED"
            if not a.get("liquidada_telegram"):
                detalle = "\n".join(
                    f"{'✅' if l.get('res') is True else ('❌' if l.get('res') is False else ('➖' if l.get('res') == 'NULA' else '⏳'))} "
                    f"{l.get('mercado')} ({l['partido']}) {l.get('marcador') or ''}".rstrip() for l in legs)
                titulo = "✅ <b>Resultado:</b> ACERTADA" if a["acertado"] else "❌ <b>Resultado:</b> PERDIDA"
                enviar_telegram(f"🎯 <b>COMBINADA DE PARTIDOS ×{len(legs)}</b>\n{detalle}\n\n{titulo}\n"
                                f"💰 <b>Cuota:</b> {a['cuota']} • 💡 <b>Stake:</b> {a['stake']}\n\n⚡ <i>Apex Quant Engine</i>",
                                url_accion=APP_URL, texto_boton="📱 Ver en la Web App")
                a["liquidada_telegram"] = True
        elif en_juego or aciertos > 0:
            a["estado_partido"] = "IN_PLAY"


ARCHIVO_LEDGER = "historico_picks.json"


def actualizar_ledger(alertas, partidos):
    ledger = {"version": 1, "picks": []}
    if os.path.exists(ARCHIVO_LEDGER):
        try:
            with open(ARCHIVO_LEDGER, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except Exception:
            pass
    picks = ledger.get("picks", [])
    ids = {p.get("id") for p in picks}
    nuevas = 0
    for a in alertas:
        if not (a.get("finalizado") and a.get("acertado") is not None and a.get("cuotas_reales") and not a.get("push")):
            continue
        es_combi = bool(a.get("es_combinada"))
        pid = str(a.get("id_alerta")) if es_combi else f"TG_{a.get('id_alerta')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "COMBI" if es_combi else "TG", "fecha": a.get("fecha"),
                      "mes": (a.get("fecha") or "")[:7], "semana_id": a.get("semana_id"), "partido": a.get("partido"),
                      "mercado": a.get("mercado"), "casa": a.get("casa"), "cuota": _a_float(a.get("cuota"), 2.0),
                      "stake": _a_float(a.get("stake"), 1.0), "ev": _a_float(a.get("ev"), 0.0),
                      "confirmado_stats": a.get("confirmado_stats"), "acertado": bool(a.get("acertado")),
                      "marcador": a.get("marcador_final")})
        ids.add(pid)
        nuevas += 1
    for p in partidos:
        if not (p.get("estado") == "FINISHED" and p.get("acertado") is not None and p.get("tiene_pronostico")
                and p.get("analizado") and not p.get("telegram_enviado") and not p.get("solo_modelo")):
            continue
        pid = f"CTRL_{p.get('id')}_{p.get('tipo_mercado')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "CONTROL", "fecha": p.get("fecha"), "mes": (p.get("fecha") or "")[:7],
                      "semana_id": p.get("semana_id"), "partido": f"{p.get('local')} vs {p.get('visitante')}",
                      "mercado": p.get("mercado"), "casa": p.get("casa"), "cuota": _a_float(p.get("cuota"), 2.0),
                      "stake": _a_float(p.get("stake"), 1.0), "ev": _a_float(p.get("ev_numero"), 0.0),
                      "confirmado_stats": p.get("confirmado_stats"), "acertado": bool(p.get("acertado")),
                      "marcador": p.get("marcador")})
        ids.add(pid)
        nuevas += 1
    if nuevas:
        ledger["picks"] = picks
        try:
            with open(ARCHIVO_LEDGER, "w", encoding="utf-8") as f:
                json.dump(ledger, f, ensure_ascii=False, separators=(",", ":"))
            print(f"📒 Libro mayor: +{nuevas} picks (total {len(picks)})")
        except Exception as e:
            print(f"Aviso guardando libro mayor: {e}")


def construir_form_info(elo, j1, j2):
    a, b = buscar_ficha(elo, j1), buscar_ficha(elo, j2)
    rl = "-".join(a["f"]) if a and a.get("f") else ""
    rv = "-".join(b["f"]) if b and b.get("f") else ""
    if len(rl.split("-")) < 3 or len(rv.split("-")) < 3:
        return {"racha_local": rl, "racha_visitante": rv, "tendencia": "", "resumen": "Sin datos de forma suficientes", "real": False}
    vl, vv = rl.count("W"), rv.count("W")
    dif = vl - vv
    if dif >= 3:
        tendencia = f"🔥 Mejor racha: {j1}"
    elif dif <= -3:
        tendencia = f"🔥 Mejor racha: {j2}"
    elif dif != 0:
        tendencia = f"⚖️ Ligera ventaja de forma: {j1 if dif > 0 else j2}"
    else:
        tendencia = "⚖️ Forma equilibrada"
    return {"racha_local": rl, "racha_visitante": rv, "tendencia": tendencia,
            "resumen": f"{j1}: {rl} ({vl}V) vs {j2}: {rv} ({vv}V)", "real": True}


# ==============================================================================
# CICLO PRINCIPAL
# ==============================================================================
def ejecutar_ciclo():
    ahora_utc = datetime.now(timezone.utc)
    ahora_madrid = ahora_utc.astimezone(TZ_MADRID)
    hora_madrid_str = ahora_madrid.strftime("%H:%M")
    hoy_madrid_str = ahora_madrid.strftime("%Y-%m-%d")
    semana_actual_id = obtener_identificador_semana(ahora_madrid)
    mes_actual_id = ahora_madrid.strftime("%Y-%m")
    dia_semana, hora_int = ahora_madrid.weekday(), ahora_madrid.hour
    lunes_semana_str, domingo_semana_str = obtener_fechas_limite_semana(ahora_madrid)
    print(f"[{hora_madrid_str}] Iniciando ciclo Apex Quant Tenis v{VERSION} (Semana {semana_actual_id})...")

    estado_api_cuotas = "sin_clave"
    cache_odds = {"_claves": {}}
    if THE_ODDS_API_KEY:
        sports, st = odds_get("/sports", {})   # no gasta créditos
        if sports is not None:
            estado_api_cuotas = "ok"
            for tour in TOURS:
                cache_odds["_claves"][tour] = claves_tenis_activas(sports, tour)
            print(f"🔑 The Odds API OK | créditos: {ODDS_ESTADO['restantes']} | torneos con cuotas: "
                  f"{', '.join(cache_odds['_claves']['ATP'] + cache_odds['_claves']['WTA']) or 'ninguno'}")
        else:
            estado_api_cuotas = f"error_{st}"
            print(f"🔑 The Odds API ERROR (HTTP {st})")
    else:
        print("🔑 THE_ODDS_API_KEY no configurada: solo pronósticos del modelo con cuota mínima")

    datos_previos = cargar_datos_existentes()
    preparar_presupuesto(datos_previos, ahora_madrid)
    alertas = datos_previos.get("alertas", [])
    partidos_previos = datos_previos.get("partidos", [])
    semana_guardada = datos_previos.get("semana_activa", semana_actual_id)
    resumen_semanal_enviado = datos_previos.get("resumen_semanal_enviado_para", "")
    resumen_mensual_enviado = datos_previos.get("resumen_mensual_enviado_para", "")

    telegram_ok = bool(datos_previos.get("telegram_ok"))
    if not telegram_ok and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_ok = enviar_telegram(
            "🎾 <b>APEX QUANT TENIS</b> ya está en marcha.\n\n"
            "Aquí recibirás las alertas con valor (+EV) de los circuitos <b>ATP</b> y <b>WTA</b> (individuales), "
            "unos 15-90 minutos antes de cada partido, con su resultado al terminar y un resumen semanal.",
            None, "📱 Abrir Apex Quant Tenis")
        print(f"📨 Bienvenida a Telegram: {'enviada ✅' if telegram_ok else 'FALLO ❌'}")

    if semana_actual_id != semana_guardada:
        print(f"🔄 Nueva semana ({semana_guardada} -> {semana_actual_id})")
        archivar_historico_mensual([a for a in alertas if not a.get("es_semana_actual", True)], mes_actual_id)
        for a in alertas:
            a["es_semana_actual"] = False
        partidos_previos = []
        semana_guardada = semana_actual_id

    # MODELO: Elo (semilla con Sackmann la primera vez + relleno con ESPN)
    elo = cargar_elo()
    sembrar_con_sackmann(elo, ahora_madrid.date())
    partidos_raw = obtener_partidos(lunes_semana_str, partidos_previos, elo["superficies"])
    rellenar_con_espn(elo, ahora_madrid.date())
    nuevos_elo = 0
    if (elo["estado"].get("cursor_espn") or "") >= (ahora_madrid.date() - timedelta(days=1)).isoformat():
        # solo cuando el relleno ya está al día, para aplicar los partidos en orden cronológico
        nuevos_elo = sum(1 for m in sorted(partidos_raw, key=lambda x: x.get("utcDate") or "") if registrar_en_elo(elo, m))
    guardar_elo(elo, ahora_madrid.date())
    print(f"📚 Elo: {len(elo['jugadores'])} jugadores (+{nuevos_elo} partidos nuevos)")

    partidos_web, mapa_por_id = [], {}
    previos_map = {str(x.get("id")): x for x in partidos_previos}
    cache_modelos = {}

    for m in partidos_raw:
        tour = m["tour"]
        utc = _fecha_utc(m.get("utcDate"))
        if utc:
            mad = utc.astimezone(TZ_MADRID)
            hora_p, fecha_p = mad.strftime("%H:%M"), mad.strftime("%Y-%m-%d")
            ts_ms = int(utc.timestamp() * 1000)
            minutos_restantes = round((utc - ahora_utc).total_seconds() / 60.0)
        else:
            hora_p, fecha_p, ts_ms, minutos_restantes = "TBD", hoy_madrid_str, 0, 999
        fecha_hora_display = f"{formatear_fecha_corta(fecha_p)} • {hora_p}"
        j1, j2 = m["p1"]["name"], m["p2"]["name"]
        partido_nombre = f"{j1} vs {j2}"
        estado = m["status"]
        if estado == "TIMED" and minutos_restantes < -240:
            estado = "PENDING_RESULT"
        sets = m.get("sets") or []
        marcador = texto_marcador(sets)
        if estado == "IN_PLAY" and not marcador:
            marcador = "0-0"
        s1 = sum(1 for a, b in sets if a > b) if sets else None
        s2 = sum(1 for a, b in sets if b > a) if sets else None

        info = {"id": m["id"], "partido": partido_nombre, "local": j1, "visitante": j2, "estado": estado,
                "sets": sets, "retirada": m.get("retirada"), "marcador": marcador, "goles_local": s1, "goles_visitante": s2}
        mapa_por_id[str(m["id"])] = info
        if fecha_p < lunes_semana_str:
            continue

        form_info = construir_form_info(elo, j1, j2)
        prev = previos_map.get(str(m["id"]))
        analizado = bool(prev and prev.get("analizado"))
        nota_cuotas = prev.get("nota_cuotas") if prev else None
        fav_info = prev.get("fav") if analizado else None
        analisis, analisis_nuevo = None, False

        prevision = prev.get("prevision") if prev else None
        if estado == "TIMED":
            try:
                mod = calcular_modelo(m, elo, cache_modelos)
                if mod:
                    prevision = {"prob_local": round(mod["prob_1"] * 100, 1), "prob_visitante": round(100 - round(mod["prob_1"] * 100, 1), 1),
                                 "juegos": mod["juegos"], "dif_juegos": mod["dif_juegos"],
                                 "elo_local": mod["elo_1"], "elo_visitante": mod["elo_2"],
                                 "saque_local": mod["saque_1"], "saque_visitante": mod["saque_2"]}
            except Exception as e:
                print(f"   Aviso previsión {partido_nombre}: {e}")

        if analizado:
            analisis = reconstruir_analisis(prev)
        elif estado == "TIMED" and VENTANA_MIN <= minutos_restantes <= VENTANA_MAX:
            analisis, definitivo, nota_cuotas, fav_nuevo = analizar_partido(m, utc, elo, cache_modelos, cache_odds)
            if definitivo:
                analizado = True
                analisis_nuevo = analisis is not None
                fav_info = fav_nuevo
            else:
                analisis = None

        acertado_p, push_p, ya_registrada, id_alerta = None, False, False, None
        if analisis:
            res = resolver_seleccion(analisis.get("sel"), info)
            if res == "PUSH":
                push_p = True
            elif res is True or res is False:
                acertado_p = res
            id_alerta = f"{m['id']}_{analisis['tipo_mercado']}"
            ya_registrada = any(a.get("id_alerta") == id_alerta for a in alertas)

        enviado = False
        if analisis_nuevo and es_alertable(analisis) and not ya_registrada:
            enviado = True
            sup_txt = NOMBRE_SUPERFICIE.get(m.get("superficie"), "")
            if analisis.get("solo_modelo"):
                l_cuota = f"💰 <b>Cuota mínima para tener valor:</b> @{analisis['cuota']}. Juégala solo si tu casa paga esa cuota o más.\n"
                l_ev = f"📈 <b>Margen exigido sobre la cuota justa:</b> +{analisis['ev']}%\n"
                l_prob = f"🧮 <b>Probabilidad según el modelo:</b> {analisis['prob_modelo']}\n"
                l_resp = f"📐 <b>Elo ({sup_txt.lower()}):</b> {analisis.get('xg_local')} vs {analisis.get('xg_visitante')} (sin cuotas de mercado)\n"
                pie = "⚡ <i>Apex Quant Engine • Pronóstico del modelo estadístico</i>"
            else:
                l_cuota = f"💰 <b>Cuota {analisis.get('casa', '')}:</b> @{analisis['cuota']}\n"
                l_ev = f"📈 <b>Ventaja (+EV vs consenso):</b> +{analisis['ev']}%\n"
                l_prob = (f"🧮 <b>Prob. combinada:</b> {analisis['prob_modelo']} (mercado {analisis.get('prob_mercado') or '—'}% · "
                          f"modelo {analisis.get('prob_estadistica') if analisis.get('prob_estadistica') is not None else '—'}%)\n")
                l_resp = (f"📐 <b>Respaldo estadístico:</b> {'✅ sí' if analisis.get('confirmado_stats') else '—'} • "
                          f"Elo {analisis.get('xg_local')} vs {analisis.get('xg_visitante')} • consenso de {analisis.get('n_casas', '?')} casas\n")
                pie = "⚡ <i>Apex Quant Engine • Cuotas reales de mercado</i>"
            enviar_telegram(
                f"🎯 <b>ALERTA CUANTITATIVA (+EV) — [PRE-PARTIDO]</b>\n\n"
                f"🏆 <b>Torneo:</b> {TOURS[tour]['name']} · {m['torneo']}{' · ' + m['ronda'] if m.get('ronda') else ''}\n"
                f"🟫 <b>Superficie:</b> {sup_txt}\n"
                f"🎾 <b>Partido:</b> {partido_nombre}\n"
                f"⏰ <b>Inicio previsto:</b> {fecha_hora_display} (en {minutos_restantes} min)\n"
                f"🛡️ <b>Mercado:</b> {analisis['mercado']}\n"
                f"{l_cuota}{l_ev}"
                f"💡 <b>Stake Sugerido:</b> {analisis['stake']}/10 unidades\n"
                f"{l_prob}{l_resp}"
                f"📊 <b>Forma:</b> {form_info['resumen']}\n\n{pie}",
                url_accion=APP_URL)
            alertas.append({
                "id_partido": str(m["id"]), "id_alerta": id_alerta, "partido": partido_nombre, "local": j1, "visitante": j2,
                "escudo_local": m["p1"]["flag"], "escudo_visitante": m["p2"]["flag"], "mercado": analisis["mercado"],
                "tipo_mercado": analisis["tipo_mercado"], "categoria_visual": analisis.get("categoria_visual"),
                "equipo_pronosticado": analisis.get("equipo_pronosticado"), "casa": analisis.get("casa"),
                "sel": analisis.get("sel"), "cuota_ref": analisis.get("cuota_ref"), "cuota_minima": analisis.get("cuota_minima"),
                "prob_modelo": analisis.get("prob_modelo"), "cuotas_reales": not analisis.get("solo_modelo"),
                "solo_modelo": bool(analisis.get("solo_modelo")), "liga": f"{TOURS[tour]['name']} · {m['torneo']}",
                "liga_code": tour, "confirmado_stats": analisis.get("confirmado_stats"),
                "cuota": f"@{analisis['cuota']}", "ev": f"+{analisis['ev']}%", "stake": f"{analisis['stake']}/10",
                "hora": hora_p, "fecha": fecha_p, "fecha_hora_display": fecha_hora_display, "semana_id": semana_actual_id,
                "es_ht": False, "es_semana_actual": True, "estado_partido": estado, "marcador": marcador,
                "marcador_final": None, "finalizado": False, "acertado": None, "liquidada_telegram": False})

        tp = analisis is not None
        g = (lambda k: analisis.get(k)) if tp else (lambda k: None)
        partidos_web.append({
            "id": m["id"], "local": j1, "visitante": j2, "escudo_local": m["p1"]["flag"], "escudo_visitante": m["p2"]["flag"],
            "liga": m["torneo"], "liga_key": TOURS[tour]["key"], "liga_code": tour, "circuito": tour,
            "ronda": m.get("ronda"), "superficie": m.get("superficie"), "mejor_de": m.get("bo"),
            "fecha": fecha_p, "hora": hora_p, "fecha_hora_display": fecha_hora_display, "hora_utc": m.get("utcDate"),
            "utcDate": m.get("utcDate"), "timestamp_ms": ts_ms, "minutos_restantes": minutos_restantes,
            "estado": estado, "minuto_juego": (m.get("detalle") or "En juego") if estado == "IN_PLAY" else (m.get("detalle") if m.get("retirada") else None),
            "marcador": marcador, "goles_local": s1, "goles_visitante": s2, "sets": sets, "retirada": m.get("retirada"),
            "ganador": m.get("ganador"),
            "mercado": g("mercado"), "tipo_mercado": g("tipo_mercado"), "categoria_visual": g("categoria_visual"),
            "equipo_pronosticado": g("equipo_pronosticado"), "cuota": g("cuota"), "prob_modelo": g("prob_modelo"),
            "ev": f"+{analisis['ev']}%" if tp else None, "ev_numero": g("ev"), "stake": g("stake"),
            "n_casas": g("n_casas"), "casa": g("casa"), "sel": g("sel"), "cuota_ref": g("cuota_ref"),
            "cuota_minima": g("cuota_minima"), "solo_modelo": g("solo_modelo"), "push": push_p,
            "prob_mercado": g("prob_mercado"), "prob_estadistica": g("prob_estadistica"),
            "confirmado_stats": g("confirmado_stats"), "xg_local": g("xg_local"), "xg_visitante": g("xg_visitante"),
            "tiene_pronostico": tp, "analizado": analizado, "nota_cuotas": nota_cuotas, "fav": fav_info,
            "semana_id": semana_actual_id,
            "telegram_enviado": bool(enviado or ya_registrada or (prev and prev.get("telegram_enviado"))),
            "acertado": acertado_p, "form_info": form_info, "prevision": prevision})

    # Un partido terminado no "retrocede" por un dato atrasado de la fuente
    for i, it in enumerate(partidos_web):
        pv = previos_map.get(str(it.get("id")))
        if pv and pv.get("estado") == "FINISHED" and it.get("estado") != "FINISHED" and pv.get("marcador"):
            r = dict(pv)
            r["minutos_restantes"] = it.get("minutos_restantes")
            r["telegram_enviado"] = bool(pv.get("telegram_enviado") or it.get("telegram_enviado"))
            partidos_web[i] = r
            mapa_por_id[str(r["id"])] = {"id": r["id"], "partido": f"{r['local']} vs {r['visitante']}", "local": r["local"],
                                         "visitante": r["visitante"], "estado": "FINISHED", "sets": r.get("sets") or [],
                                         "retirada": r.get("retirada"), "marcador": r.get("marcador"),
                                         "goles_local": r.get("goles_local"), "goles_visitante": r.get("goles_visitante")}

    if COMBI_ACTIVA:
        mc = buscar_mejor_combinada(partidos_web, alertas)
        if mc:
            ac = crear_alerta_combinada(mc, semana_actual_id)
            if not any(a.get("id_alerta") == ac["id_alerta"] for a in alertas):
                enviar_telegram(mensaje_combinada(ac), url_accion=APP_URL)
                alertas.append(ac)
                print(f"🎯 Combinada creada: {ac['mercado']} {ac['cuota']}")

    # LIQUIDACIÓN
    liquidar_combinadas(alertas, mapa_por_id)
    for a in alertas:
        if a.get("es_combinada") or a.get("finalizado"):
            continue
        info = mapa_por_id.get(str(a.get("id_partido", "")))
        if not info:
            continue
        a["estado_partido"] = info["estado"]
        if info.get("marcador"):
            a["marcador"] = info["marcador"]
        res = resolver_seleccion(a.get("sel"), info)
        if res is None:
            continue
        es_push = res == "PUSH"
        a.update({"finalizado": True, "marcador_final": info.get("marcador") + (" (retirada)" if info.get("retirada") else "") if info.get("marcador") else ("Retirada" if info.get("retirada") else None),
                  "goles_local": info.get("goles_local"), "goles_visitante": info.get("goles_visitante"),
                  "acertado": None if es_push else bool(res), "push": es_push})
        if not a.get("liquidada_telegram"):
            cab = f"🎾 <b>{a['partido']}</b>\n📌 <b>Pronóstico:</b> {a['mercado']}\n"
            if es_push:
                cuerpo = f"↩️ <b>Resultado:</b> ANULADA / DEVUELTA{' (retirada)' if info.get('retirada') else ''}\n🏁 <b>Marcador:</b> {a['marcador_final'] or 'Final'}\n\n⚡ <i>Apex Quant Engine</i>"
            elif res:
                cuerpo = (f"✅ <b>Resultado:</b> ACERTADO\n🏁 <b>Marcador Final:</b> {a['marcador_final'] or 'Final'}\n"
                          f"💰 <b>{'Cuota mínima' if a.get('cuota_minima') else 'Cuota Cobrada'}:</b> {a['cuota']}\n💡 <b>Stake:</b> {a['stake']}\n\n"
                          f"🎉 <i>¡Verificado y sumado a la Efectividad Oficial en la App!</i>\n⚡ <i>Apex Quant Engine</i>")
            else:
                cuerpo = (f"❌ <b>Resultado:</b> PERDIDO\n🏁 <b>Marcador Final:</b> {a['marcador_final'] or 'Final'}\n"
                          f"💡 <b>Stake:</b> {a['stake']} (Control estricto de banca)\n\n⚡ <i>Apex Quant Engine • Disciplina a Largo Plazo</i>")
            enviar_telegram(cab + cuerpo, url_accion=APP_URL, texto_boton="📱 Ver en la Web App")
            a["liquidada_telegram"] = True

    # RESUMEN DOMINICAL
    if dia_semana == 6 and hora_int >= 22 and resumen_semanal_enviado != semana_actual_id:
        pendientes = [p for p in partidos_web if p["fecha"] == hoy_madrid_str and p["estado"] not in ("FINISHED", "CANCELLED", "POSTPONED")]
        if not pendientes or (hora_int == 23 and ahora_madrid.minute >= 45):
            r = calcular_resumen_financiero([a for a in alertas if a.get("semana_id") == semana_actual_id and a.get("finalizado")])
            if r["finalizadas"] > 0:
                pct = round(r["acertadas"] / r["finalizadas"] * 100, 1)
                s = "+" if r["unidades_netas"] >= 0 else ""
                enviar_telegram(
                    f"📊 <b>CIERRE SEMANAL OFICIAL — APEX QUANT TENIS</b> 🏆\n\n"
                    f"📅 <b>Semana:</b> {semana_actual_id} (Finalizada)\n"
                    f"📈 <b>Total Pronósticos Oficiales:</b> <b>{r['finalizadas']}</b>\n\n"
                    f"✅ <b>Acertadas:</b> <b>{r['acertadas']}</b> ({pct}%)\n❌ <b>Falladas:</b> <b>{r['falladas']}</b>\n\n"
                    f"💰 <b>Rendimiento Neto:</b> <b>{s}{r['unidades_netas']} Unidades</b>\n"
                    f"📈 <b>Yield / ROI Semanal:</b> <b>{s}{r['roi_pct']}%</b>\n\n"
                    f"🔄 <i>Las estadísticas se reinician a las 00:00 del lunes.</i>\n⚡ <i>Apex Quant Engine</i>",
                    url_accion=APP_URL, texto_boton="📱 Abrir Balance Completo en la App")
            resumen_semanal_enviado = semana_actual_id

    # RESUMEN MENSUAL
    if (ahora_madrid + timedelta(days=1)).month != ahora_madrid.month and hora_int >= 22 and resumen_mensual_enviado != mes_actual_id:
        todas = []
        if os.path.exists("historico_mensual.json"):
            try:
                with open("historico_mensual.json", "r", encoding="utf-8") as f:
                    todas.extend(json.load(f).get(mes_actual_id, []))
            except Exception:
                pass
        vistos = {a.get("id_alerta") for a in todas}
        todas += [a for a in alertas if (a.get("fecha") or "").startswith(mes_actual_id) and a.get("finalizado") and a.get("id_alerta") not in vistos]
        r = calcular_resumen_financiero(todas)
        if r["finalizadas"] > 0:
            meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
            pct = round(r["acertadas"] / r["finalizadas"] * 100, 1)
            s = "+" if r["unidades_netas"] >= 0 else ""
            enviar_telegram(
                f"🏆 <b>CIERRE MENSUAL OFICIAL — APEX QUANT TENIS</b> 📊\n\n"
                f"📅 <b>Mes:</b> <b>{meses[ahora_madrid.month - 1]} {ahora_madrid.year}</b>\n"
                f"📈 <b>Total Pronósticos:</b> <b>{r['finalizadas']}</b>\n"
                f"✅ <b>Acertadas:</b> <b>{r['acertadas']}</b> ({pct}%) · ❌ <b>{r['falladas']}</b>\n\n"
                f"💰 <b>Rendimiento Neto:</b> <b>{s}{r['unidades_netas']} U.</b> · ROI {s}{r['roi_pct']}%\n\n⚡ <i>Apex Quant Engine</i>",
                url_accion=APP_URL, texto_boton="📱 Abrir Balance Completo en la App")
        resumen_mensual_enviado = mes_actual_id

    # FUSIÓN Y GUARDADO
    guardar = list(partidos_web)
    ids = {p["id"] for p in guardar}
    for pp in partidos_previos:
        if pp.get("id") not in ids and pp.get("fecha", "") >= lunes_semana_str:
            guardar.append(pp)
            ids.add(pp.get("id"))
    datos = {
        "version_bot": VERSION, "ultima_actualizacion": hora_madrid_str,
        "ultima_actualizacion_utc": ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "huso_horario": "Europe/Madrid",
        "semana_activa": semana_guardada, "lunes_semana": lunes_semana_str, "domingo_semana": domingo_semana_str,
        "resumen_semanal_enviado_para": resumen_semanal_enviado, "resumen_mensual_enviado_para": resumen_mensual_enviado,
        "metricas_semana": calcular_resumen_financiero([a for a in alertas if a.get("semana_id") == semana_actual_id]),
        "total_partidos_hoy": len(guardar), "partidos": guardar,
        "creditos_odds_restantes": ODDS_ESTADO["restantes"], "api_cuotas_estado": estado_api_cuotas,
        "creditos_dia": ODDS_ESTADO.get("dia"), "telegram_ok": telegram_ok,
        "diagnostico": {"perfil_mercados": ODDS_ESTADO.get("perfil"), "fuentes": DIAG_FUENTES,
                        "elo_jugadores": len(elo["jugadores"]), "elo_estado": elo["estado"],
                        "torneos_cuotas": cache_odds.get("_claves")},
        "alertas": alertas[-70:],
    }
    actualizar_ledger(alertas, guardar)
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    print(f"💳 The Odds API: {ODDS_ESTADO['consultas']} consultas | créditos restantes: {ODDS_ESTADO['restantes']}")
    print(f"📁 datos.json guardado. Partidos: {len(guardar)} | Alertas: {len(alertas)}")


if __name__ == "__main__":
    ejecutar_ciclo()
