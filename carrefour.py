#!/usr/bin/env python3
"""Changuito: scripts para hacer pedidos personales en carrefour.com.ar.

Usa la API pública de VTEX (la plataforma de la tienda). Sin dependencias:
solo la biblioteca estándar de Python. El estado (zona, listas, carrito,
precios vistos) vive en changuito.db; la sesión, en ~/.config/changuito.

Ejemplos:
  ./carrefour.py zona 1425
  ./carrefour.py buscar "leche entera"
  ./carrefour.py lista agregar 144373 2
  ./carrefour.py carrito armar
  ./carrefour.py login clave        # usuario/clave de Mi Carrefour
  ./carrefour.py pedidos
"""

import argparse
import base64
import html
import http.cookiejar
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.carrefour.com.ar"
CUENTA = "carrefourar"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

AQUI = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("CHANGUITO_DB", os.path.join(AQUI, "changuito.db"))
CONFIG = os.path.expanduser(os.environ.get("CHANGUITO_CONFIG", "~/.config/changuito"))
COOKIES = os.path.join(CONFIG, "cookies.txt")
CREDENCIALES = os.path.join(CONFIG, "credenciales")


# ---------------------------------------------------------------- estado

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS productos (
          sku TEXT PRIMARY KEY, nombre TEXT, marca TEXT, seller TEXT,
          link TEXT, unidad TEXT, actualizado INTEGER, multiplicador REAL);
        CREATE TABLE IF NOT EXISTS precios (
          sku TEXT, precio REAL, precio_lista REAL, visto INTEGER,
          PRIMARY KEY (sku, visto));
        CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, cuerpo TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS listas (
          lista TEXT, sku TEXT, cantidad INTEGER, nota TEXT,
          PRIMARY KEY (lista, sku));
        """
    )
    columnas = [r[1] for r in con.execute("PRAGMA table_info(productos)")]
    if "multiplicador" not in columnas:
        con.execute("ALTER TABLE productos ADD COLUMN multiplicador REAL")
    return con


def cfg(clave, defecto=None):
    with db() as con:
        r = con.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    return r["valor"] if r else defecto


def set_cfg(clave, valor):
    with db() as con:
        if valor is None:
            con.execute("DELETE FROM config WHERE clave = ?", (clave,))
        else:
            con.execute(
                "INSERT INTO config VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (clave, str(valor)),
            )


def guardar_producto(p, item, oferta, seller):
    ahora = int(time.time())
    with db() as con:
        con.execute(
            """INSERT INTO productos VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sku) DO UPDATE SET nombre = excluded.nombre, marca = excluded.marca,
                 seller = excluded.seller, link = excluded.link, unidad = excluded.unidad,
                 actualizado = excluded.actualizado, multiplicador = excluded.multiplicador""",
            (item["itemId"], p["productName"], p.get("brand"), seller,
             p.get("link") or f"{BASE}/{p.get('linkText')}/p",
             item.get("measurementUnit"), ahora, item.get("unitMultiplier")),
        )
        ultimo = con.execute(
            "SELECT precio FROM precios WHERE sku = ? ORDER BY visto DESC LIMIT 1", (item["itemId"],)
        ).fetchone()
        if not ultimo or ultimo["precio"] != oferta["Price"]:
            con.execute(
                "INSERT OR IGNORE INTO precios VALUES (?, ?, ?, ?)",
                (item["itemId"], oferta["Price"], oferta["ListPrice"], ahora),
            )


# ---------------------------------------------------------------- http

def jar():
    os.makedirs(CONFIG, mode=0o700, exist_ok=True)
    j = http.cookiejar.MozillaCookieJar(COOKIES)
    if os.path.exists(COOKIES):
        j.load(ignore_discard=True, ignore_expires=True)
    return j


_JAR = None
_OPENER = None


def opener():
    global _JAR, _OPENER
    if _OPENER is None:
        _JAR = jar()
        _OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
    return _OPENER


def guardar_cookies():
    if _JAR is not None:
        _JAR.save(ignore_discard=True, ignore_expires=True)
        os.chmod(COOKIES, 0o600)


# Ser amables con la API: pausa entre pedidos (también entre corridas),
# caché de búsquedas y, si Carrefour limita, frenar un rato sin reintentar.
PAUSA = float(os.environ.get("CHANGUITO_PAUSA", "0.5"))
CACHE_SEG = 6 * 3600
CACHEABLE = ("/api/io/_v/api/intelligent-search/", "/api/checkout/pub/regions")
FRENO_SEG = 15 * 60


def esperar_turno():
    hasta = float(cfg("freno_hasta", 0))
    if time.time() < hasta:
        raise SystemExit(
            "Carrefour nos pidió frenar: espero hasta las "
            + time.strftime("%H:%M", time.localtime(hasta)) + " antes de volver a consultar."
        )
    falta = float(cfg("ultimo_pedido", 0)) + PAUSA + random.uniform(0, 0.3) - time.time()
    if falta > 0:
        time.sleep(falta)
    set_cfg("ultimo_pedido", time.time())


def pedir(url, datos=None, json_body=None, metodo=None, crudo=False):
    if not url.startswith("http"):
        url = BASE + url
    cacheable = not crudo and datos is None and json_body is None and any(c in url for c in CACHEABLE)
    if cacheable:
        with db() as con:
            r = con.execute("SELECT cuerpo, ts FROM cache WHERE url = ?", (url,)).fetchone()
        if r and time.time() - r["ts"] < CACHE_SEG:
            return json.loads(r["cuerpo"])
    headers = {"User-Agent": UA, "Accept": "application/json"}
    cuerpo = None
    if json_body is not None:
        cuerpo = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif datos is not None:
        cuerpo = urllib.parse.urlencode(datos).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=cuerpo, headers=headers, method=metodo)
    esperar_turno()
    try:
        with opener().open(req, timeout=30) as r:
            texto = r.read().decode("utf-8", "replace")
            final = r.geturl()
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        if e.code in (429, 503) or (e.code == 403 and "vtexid" not in url):
            set_cfg("freno_hasta", time.time() + FRENO_SEG)
            raise SystemExit(f"Carrefour respondió {e.code} (nos está limitando). Freno 15 minutos.")
        raise SystemExit(f"Error {e.code} en {url.split('?')[0]}: {texto[:300]}")
    finally:
        guardar_cookies()
    if cacheable:
        with db() as con:
            con.execute(
                "INSERT INTO cache VALUES (?, ?, ?) ON CONFLICT(url) DO UPDATE SET cuerpo = excluded.cuerpo, ts = excluded.ts",
                (url, texto, int(time.time())),
            )
            con.execute("DELETE FROM cache WHERE ts < ?", (int(time.time()) - CACHE_SEG,))
    if crudo:
        return texto, final
    return json.loads(texto) if texto.strip() else None


def cuanto(cantidad, unidad=None, multiplicador=None):
    """6 × 0.1 kg → «600 g»; las unidades quedan «6 ×»."""
    if unidad == "kg" and multiplicador:
        kg = cantidad * multiplicador
        return f"{kg:g} kg" if kg >= 1 else f"{kg * 1000:g} g"
    return f"{cantidad} ×"


def pesos(v):
    if v < 0:
        return "-" + pesos(-v)
    return "$" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")


# ---------------------------------------------------------------- zona

def tiendas_de(cp):
    regiones = pedir(f"/api/checkout/pub/regions?country=ARG&postalCode={cp}&sc=1")
    if not regiones:
        raise SystemExit("No encontré tiendas para ese código postal.")
    return regiones[0]


def region():
    """El id de región que da Carrefour cambia con el tiempo: uno viejo deja
    afuera a las tiendas (y la búsqueda devuelve solo vendedores externos)."""
    cp = cfg("cp")
    if not cp:
        return None
    if time.time() - float(cfg("region_ts", 0)) > 3600:
        set_cfg("region", tiendas_de(cp)["id"])
        set_cfg("region_ts", time.time())
    return cfg("region")


def cmd_zona(a):
    if not a.cp:
        print(f"Código postal: {cfg('cp', '(sin definir)')}")
        return
    r = tiendas_de(a.cp)
    set_cfg("cp", a.cp)
    set_cfg("region", r["id"])
    set_cfg("region_ts", time.time())
    tiendas = [s["name"] for s in r["sellers"]]
    print(f"Zona {a.cp}: {len(tiendas)} tiendas ({', '.join(tiendas[:4])}…)")


# ---------------------------------------------------------------- búsqueda

def ofertas(texto, cantidad=10):
    """Busca y devuelve (producto, item, oferta, seller) con la mejor oferta disponible."""
    q = {"query": texto, "count": cantidad, "hideUnavailableItems": "true", "locale": "es-AR"}
    if region():
        q["regionId"] = region()
    d = pedir("/api/io/_v/api/intelligent-search/product_search/?" + urllib.parse.urlencode(q))
    res = []
    for p in d.get("products", []):
        for item in p["items"]:
            sellers = [
                s for s in item["sellers"]
                if s["commertialOffer"]["AvailableQuantity"] > 0 and s["commertialOffer"]["Price"] > 0
            ]
            if not sellers:
                continue
            s = min(sellers, key=lambda s: s["commertialOffer"]["Price"])
            guardar_producto(p, item, s["commertialOffer"], s["sellerId"])
            res.append((p, item, s["commertialOffer"], s["sellerId"]))
    return res


def promos(o):
    return [t["name"] for t in (o.get("Teasers") or []) + (o.get("PromotionTeasers") or []) if t.get("name")]


def buscar(texto, cantidad=10):
    return [
        {
            "sku": item["itemId"], "nombre": item.get("nameComplete") or p["productName"],
            "marca": p.get("brand"), "precio": o["Price"], "precio_lista": o["ListPrice"],
            "seller": seller, "promos": promos(o),
        }
        for p, item, o, seller in ofertas(texto, cantidad)
    ]


def cmd_buscar(a):
    res = buscar(" ".join(a.texto), a.n)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    if not res:
        print("Sin resultados.")
    for r in res:
        rebaja = f" (antes {pesos(r['precio_lista'])})" if r["precio_lista"] > r["precio"] else ""
        promo = f"  [{'; '.join(r['promos'])}]" if r["promos"] else ""
        print(f"{r['sku']:>8}  {pesos(r['precio']):>11}{rebaja}  {r['nombre']}{promo}")


def cmd_historial(a):
    with db() as con:
        p = con.execute("SELECT * FROM productos WHERE sku = ?", (a.sku,)).fetchone()
        filas = con.execute("SELECT * FROM precios WHERE sku = ? ORDER BY visto", (a.sku,)).fetchall()
    if not p:
        raise SystemExit("No conozco ese SKU: buscalo primero.")
    print(p["nombre"])
    for f in filas:
        print(f"  {time.strftime('%d/%m/%Y %H:%M', time.localtime(f['visto']))}  {pesos(f['precio'])}")


# ---------------------------------------------------------------- listas

def nombre_de(sku):
    with db() as con:
        r = con.execute("SELECT nombre FROM productos WHERE sku = ?", (sku,)).fetchone()
    return r["nombre"] if r else f"SKU {sku}"


def items_lista(lista="habitual"):
    """El precio guardado es por unidad de venta (por kg en lo que va por peso)."""
    with db() as con:
        filas = [dict(f) for f in con.execute(
            """SELECT l.sku, l.cantidad, p.nombre, p.unidad, p.multiplicador,
                 (SELECT precio FROM precios WHERE sku = l.sku ORDER BY visto DESC LIMIT 1) precio
               FROM listas l LEFT JOIN productos p USING (sku) WHERE lista = ? ORDER BY p.nombre""",
            (lista,),
        )]
    for f in filas:
        mult = f["multiplicador"] if f["unidad"] == "kg" and f["multiplicador"] else 1
        f["subtotal"] = (f["precio"] or 0) * f["cantidad"] * mult
        f["precio_txt"] = (pesos(f["precio"]) + ("/kg" if mult != 1 else " c/u")) if f["precio"] else "?"
    return filas


def nombres_listas():
    with db() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT lista FROM listas ORDER BY lista")]


def poner_en_lista(lista, sku, cantidad):
    with db() as con:
        if cantidad <= 0:
            con.execute("DELETE FROM listas WHERE lista = ? AND sku = ?", (lista, sku))
        else:
            con.execute(
                """INSERT INTO listas VALUES (?, ?, ?, NULL) ON CONFLICT(lista, sku)
                   DO UPDATE SET cantidad = excluded.cantidad""",
                (lista, sku, cantidad),
            )


def cmd_lista(a):
    with db() as con:
        if a.accion == "agregar":
            if not a.sku:
                raise SystemExit("Falta el SKU.")
            con.execute(
                """INSERT INTO listas VALUES (?, ?, ?, ?) ON CONFLICT(lista, sku)
                   DO UPDATE SET cantidad = excluded.cantidad""",
                (a.lista, a.sku, a.cantidad or 1, None),
            )
        elif a.accion == "quitar":
            con.execute("DELETE FROM listas WHERE lista = ? AND sku = ?", (a.lista, a.sku))
        elif a.accion == "vaciar":
            con.execute("DELETE FROM listas WHERE lista = ?", (a.lista,))
        elif a.accion == "todas":
            for r in con.execute("SELECT lista, COUNT(*) n FROM listas GROUP BY lista"):
                print(f"{r['lista']}: {r['n']} productos")
            return
    filas = items_lista(a.lista)
    if a.json:
        print(json.dumps(filas, ensure_ascii=False, indent=1))
        return
    print(f"Lista «{a.lista}»" + ("" if filas else " (vacía)"))
    for f in filas:
        print(f"  {cuanto(f['cantidad'], f['unidad'], f['multiplicador']):>6} {f['nombre'] or f['sku']}  ({f['sku']}, {f['precio_txt']})")
    if filas:
        print(f"  Estimado: {pesos(sum(f['subtotal'] for f in filas))} (último precio visto)")


# ---------------------------------------------------------------- carrito

def order_form(nuevo=False):
    of = None if nuevo else cfg("orderform")
    d = pedir(f"/api/checkout/pub/orderForm/{of}" if of else "/api/checkout/pub/orderForm?forceNewCart=true" if nuevo else "/api/checkout/pub/orderForm")
    set_cfg("orderform", d["orderFormId"])
    guardar_precios_vtex(d.get("items", []))
    return d


def con_cp(d):
    """Carga el código postal para que precios y envío sean los de la zona."""
    cp = cfg("cp")
    if not cp or (d.get("shippingData") or {}).get("address"):
        return d
    return pedir(
        f"/api/checkout/pub/orderForm/{d['orderFormId']}/attachments/shippingData",
        json_body={"selectedAddresses": [{"addressType": "residential", "country": "ARG", "postalCode": cp}]},
    )


def totales(d):
    """Sin dirección, VTEX informa envío $0: el costo real se ve al pagar."""
    return [t for t in d.get("totalizers", []) if not (t["id"] == "Shipping" and t["value"] == 0)]


def mostrar_carrito(d):
    if not d["items"]:
        print("Carrito vacío.")
    for i, it in enumerate(d["items"]):
        falta = "" if it.get("availability") == "available" else f"  ⚠ {it.get('availability')}"
        print(f"  [{i}] {it['quantity']:>3} × {it['name']}  {pesos(it['sellingPrice'] / 100)} c/u{falta}")
    for t in totales(d):
        print(f"  {t['name']}: {pesos(t['value'] / 100)}")
    print(f"  TOTAL: {pesos(d['value'] / 100)} (sin envío: se calcula al pagar)")
    for m in d.get("messages", []):
        print(f"  ⓘ {m.get('text')}")
    print(f"\nAbrilo para pagar: {link_checkout(d)}")


def item_vtex(sku, cantidad):
    with db() as con:
        r = con.execute("SELECT seller FROM productos WHERE sku = ?", (sku,)).fetchone()
    return {"id": str(sku), "quantity": int(cantidad), "seller": (r["seller"] if r else None) or "1"}


def parse_pares(pares):
    out = {}
    for p in pares or []:
        sku, _, cant = p.partition(":")
        out[sku] = int(cant or 1)
    return out


def guardar_precios_vtex(items):
    """Precios del carrito o de una simulación (centavos; por unidad de venta)."""
    ahora = int(time.time())
    with db() as con:
        for it in items:
            if it.get("availability", "available") != "available" or not it.get("sellingPrice"):
                continue
            mult = it.get("unitMultiplier") or 1
            precio = round(it["sellingPrice"] / 100 / mult, 2)
            ultimo = con.execute(
                "SELECT precio FROM precios WHERE sku = ? ORDER BY visto DESC LIMIT 1", (it["id"],)
            ).fetchone()
            if not ultimo or ultimo["precio"] != precio:
                con.execute(
                    "INSERT OR IGNORE INTO precios VALUES (?, ?, ?, ?)",
                    (it["id"], precio, (it.get("listPrice") or it["price"]) / 100, ahora),
                )


def armar_carrito(cantidades, nuevo=True):
    d = con_cp(order_form(nuevo=nuevo))
    d = pedir(
        f"/api/checkout/pub/orderForm/{d['orderFormId']}/items",
        json_body={"orderItems": [item_vtex(s, n) for s, n in cantidades.items()]},
    )
    guardar_precios_vtex(d["items"])
    return d


def simular(cantidades):
    """Precios finales (con promos por cantidad) sin tocar el carrito."""
    items = []
    lista = list(cantidades.items())
    for i in range(0, len(lista), 50):
        d = pedir(
            "/api/checkout/pub/orderForms/simulation?sc=1",
            json_body={
                "items": [item_vtex(s, n) for s, n in lista[i:i + 50]],
                "postalCode": cfg("cp"), "country": "ARG",
            },
        )
        items += d.get("items", [])
    guardar_precios_vtex(items)
    return {it["id"]: it for it in items}


def cambiar_cantidad(indice, cantidad):
    """Cantidad 0 saca el item."""
    of = order_form()["orderFormId"]
    return pedir(
        f"/api/checkout/pub/orderForm/{of}/items/update",
        json_body={"orderItems": [{"index": indice, "quantity": cantidad}]},
    )


def link_checkout(d):
    return f"{BASE}/checkout/?orderFormId={d['orderFormId']}#/cart"


def cmd_carrito(a):
    if a.accion == "ver":
        mostrar_carrito(order_form())
        return
    if a.accion == "vaciar":
        d = order_form()
        d = pedir(f"/api/checkout/pub/orderForm/{d['orderFormId']}/items/removeAll", json_body={})
        mostrar_carrito(d)
        return

    # armar: lista (+extras, -sin) → carrito nuevo
    with db() as con:
        filas = con.execute("SELECT sku, cantidad FROM listas WHERE lista = ?", (a.lista,)).fetchall()
    cantidades = {} if a.vacio else {f["sku"]: f["cantidad"] for f in filas}
    for sku, n in parse_pares(a.mas).items():
        cantidades[sku] = cantidades.get(sku, 0) + n
    for sku, n in parse_pares(a.cant).items():
        cantidades[sku] = n
    for sku in a.sin or []:
        cantidades.pop(sku, None)
    cantidades = {k: v for k, v in cantidades.items() if v > 0}
    if not cantidades:
        raise SystemExit("No hay nada para agregar.")

    d = armar_carrito(cantidades, nuevo=not a.sumar)
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
        return
    mostrar_carrito(d)


# ---------------------------------------------------------------- ahorro

UNIDADES = {
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramos": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000),
    "ml": ("ml", 1), "cc": ("ml", 1), "cm3": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
    "u": ("u", 1), "un": ("u", 1), "unid": ("u", 1), "unidades": ("u", 1),
    "paños": ("u", 1), "saquitos": ("u", 1), "rollos": ("u", 1),
}
RE_TAMANO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(UNIDADES, key=len, reverse=True)) + r")\b\.?", re.I)
RE_PACK = re.compile(r"\bx\s*(\d+)\b", re.I)
# Si el original es así, la alternativa también tiene que serlo.
CONDICIONES = ["light", "sin tacc", "sin azucar", "sin azúcar", "sin sal", "descremad", "entera",
               "integral", "diet", "zero", "0%", "vegan", "proteic", "sin lactosa", "deslactosad"]


def tamano(nombre, unidad=None):
    """«Pasta dental 180 grs» → ("g", 180); «x kg» → ("g", 1000) (precio por kg)."""
    if unidad == "kg":
        return ("g", 1000)
    m = RE_TAMANO.search(nombre)
    if not m:
        return None
    base, factor = UNIDADES[m.group(2).lower()]
    total = float(m.group(1).replace(",", ".")) * factor
    for pack in RE_PACK.finditer(nombre):
        if pack.start(1) != m.start(1) and pack.end() <= m.start():
            total *= int(pack.group(1))
            break
    return (base, total)


def sin_acentos(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def consulta_para(nombre, marca):
    t = RE_TAMANO.sub(" ", nombre)
    if marca:
        t = re.sub(re.escape(marca), " ", t, flags=re.I)
    palabras = [w for w in re.findall(r"[\wáéíóúñü%]+", t) if not re.fullmatch(r"x|\d+", w, re.I) and w.lower() not in UNIDADES]
    return " ".join(palabras[:4])


def alternativas(sku, nombre):
    """Productos de la misma categoría con condiciones compatibles.

    Una sola búsqueda «genérica» (sin marca ni tamaño) suele traer al producto
    y a sus alternativas; solo si no aparece buscamos por el nombre completo."""
    with db() as con:
        r = con.execute("SELECT marca FROM productos WHERE sku = ?", (sku,)).fetchone()
    encontrados = ofertas(consulta_para(nombre, r["marca"] if r else None), 40)
    yo = next((r for r in encontrados if r[1]["itemId"] == sku), None)
    if not yo:
        encontrados += ofertas(nombre, 10)
        yo = next((r for r in encontrados if r[1]["itemId"] == sku), None)
    if not yo:
        return None, []
    p, item, _, _ = yo
    t_yo = tamano(item.get("nameComplete") or p["productName"], item.get("measurementUnit"))
    if not t_yo:
        return yo, []
    pide = [c for c in CONDICIONES if c in sin_acentos(p["productName"])]
    candidatos = []
    vistos = {sku}
    for r in encontrados:
        cp, citem = r[0], r[1]
        if citem["itemId"] in vistos or cp.get("categoryId") != p.get("categoryId"):
            continue
        vistos.add(citem["itemId"])
        nombre_c = sin_acentos(cp["productName"])
        if any(c not in nombre_c for c in pide):
            continue
        t = tamano(citem.get("nameComplete") or cp["productName"], citem.get("measurementUnit"))
        if not t or t[0] != t_yo[0] or not (0.5 <= t[1] / t_yo[1] <= 3):
            continue
        candidatos.append((r, t))
    return (yo, t_yo), candidatos


def ahorro(lista="habitual", minimo=0.05, avance=None):
    """Para cada producto de la lista, la alternativa más barata por kg/litro/unidad.

    Los precios salen de una simulación del carrito: incluyen las promos que
    aplica Carrefour según la cantidad (2x1, 2da al 70%…)."""
    items = items_lista(lista)
    analisis = []
    for n, it in enumerate(items, 1):
        if avance:
            avance(n, len(items), it["nombre"] or it["sku"])
        yo, candidatos = alternativas(it["sku"], it["nombre"] or "")
        if yo and candidatos:
            analisis.append((it, yo, candidatos))

    # Una simulación con todo: el original con su cantidad y cada candidato
    # con la cantidad que equivale a lo mismo en kg/litros/unidades.
    pedido = {}
    for it, ((p, item, o, _), t_yo), candidatos in analisis:
        pedido[it["sku"]] = it["cantidad"]
        mult = item.get("unitMultiplier") or 1
        cantidad_base = t_yo[1] * it["cantidad"] * (mult if item.get("measurementUnit") == "kg" else 1)
        baratos = sorted(candidatos, key=lambda c: c[0][2]["Price"] / c[1][1])[:3]
        for (cp, citem, co, _), t in baratos:
            cmult = citem.get("unitMultiplier") or 1
            unidad = t[1] * (cmult if citem.get("measurementUnit") == "kg" else 1)
            pedido.setdefault(citem["itemId"], max(1, round(cantidad_base / unidad)))
    precios = simular(pedido) if pedido else {}

    def costo(sku, base_por_unidad, es_kg, mult):
        sim = precios.get(sku)
        if not sim or sim.get("availability") != "available":
            return None
        cantidad = pedido[sku]
        total = sim["sellingPrice"] / 100 * cantidad
        base = base_por_unidad * cantidad * (mult if es_kg else 1)
        return total, total / base, cantidad

    sugerencias = []
    for it, ((p, item, o, _), t_yo), candidatos in analisis:
        mio = costo(it["sku"], t_yo[1], item.get("measurementUnit") == "kg", item.get("unitMultiplier") or 1)
        if not mio:
            continue
        mejor = None
        for (cp, citem, co, _), t in candidatos:
            if citem["itemId"] not in pedido or citem["itemId"] == it["sku"]:
                continue
            c = costo(citem["itemId"], t[1], citem.get("measurementUnit") == "kg", citem.get("unitMultiplier") or 1)
            if not c:
                continue
            ahorro_total = (mio[1] - c[1]) * t_yo[1] * it["cantidad"] * (
                (item.get("unitMultiplier") or 1) if item.get("measurementUnit") == "kg" else 1)
            if c[1] < mio[1] * (1 - minimo) and (not mejor or ahorro_total > mejor["ahorro"]):
                mejor = {
                    "sku": it["sku"], "nombre": it["nombre"], "cantidad": it["cantidad"],
                    "unidad": item.get("measurementUnit"), "multiplicador": item.get("unitMultiplier"),
                    "total": mio[0], "por_base": mio[1], "base": t_yo[0],
                    "alt_sku": citem["itemId"], "alt_nombre": citem.get("nameComplete") or cp["productName"],
                    "alt_cantidad": c[2], "alt_total": c[0], "alt_por_base": c[1],
                    "alt_promos": promos(co), "ahorro": ahorro_total,
                    "pct": 1 - c[1] / mio[1],
                }
        if mejor:
            sugerencias.append(mejor)
    return sorted(sugerencias, key=lambda s: -s["ahorro"])


def precio_base(v, base):
    if base == "g":
        return f"{pesos(v * 1000)}/kg"
    if base == "ml":
        return f"{pesos(v * 1000)}/l"
    return f"{pesos(v)}/u"


def cmd_ahorrar(a):
    def avance(n, total, nombre):
        print(f"\r  Revisando {n}/{total}: {nombre[:50]:<50}", end="", file=sys.stderr, flush=True)

    sug = ahorro(a.lista, a.minimo / 100, None if a.json else avance)
    if not a.json:
        print("\r" + " " * 80 + "\r", end="", file=sys.stderr)
    if a.json:
        print(json.dumps(sug, ensure_ascii=False, indent=1))
        return
    if not sug:
        print("No encontré alternativas más baratas. 👌")
        return
    for s in sug:
        promo = f"  [{'; '.join(s['alt_promos'])}]" if s["alt_promos"] else ""
        print(f"• {cuanto(s['cantidad'], s['unidad'], s['multiplicador'])} {s['nombre']}  "
              f"{pesos(s['total'])} ({precio_base(s['por_base'], s['base'])})")
        print(f"  → {s['alt_cantidad']} × {s['alt_nombre']}  {pesos(s['alt_total'])} "
              f"({precio_base(s['alt_por_base'], s['base'])}){promo}")
        print(f"    ahorrás ~{pesos(round(s['ahorro']))} ({s['pct']:.0%})   "
              f"./carrefour.py cambiar {s['sku']} {s['alt_sku']}")
    print(f"\nAhorro posible: ~{pesos(round(sum(s['ahorro'] for s in sug)))}")


def cambiar_en_lista(lista, viejo, nuevo, cantidad=None):
    with db() as con:
        r = con.execute("SELECT cantidad FROM listas WHERE lista = ? AND sku = ?", (lista, viejo)).fetchone()
    if not r:
        raise SystemExit(f"El SKU {viejo} no está en «{lista}».")
    poner_en_lista(lista, viejo, 0)
    poner_en_lista(lista, nuevo, cantidad or r["cantidad"])


def cmd_cambiar(a):
    cambiar_en_lista(a.lista, a.viejo, a.nuevo, a.cantidad)
    print(f"En «{a.lista}»: {nombre_de(a.viejo)} → {nombre_de(a.nuevo)}")


# ---------------------------------------------------------------- login

def inicio_auth():
    callback = f"{BASE}/api/vtexid/oauth/finish?popup=false"
    d = pedir(
        "/api/vtexid/pub/authentication/start?"
        + urllib.parse.urlencode({"scope": CUENTA, "locale": "es-AR", "callbackUrl": callback, "returnUrl": "/"})
    )
    return d["authenticationToken"]


def leer_credenciales():
    if not os.path.exists(CREDENCIALES):
        raise SystemExit(
            f"Creá {CREDENCIALES} con dos renglones:\n  usuario=<DNI o email>\n  clave=<tu clave>\n"
            f"y dale permisos 600 (chmod 600 {CREDENCIALES})."
        )
    datos = {}
    for linea in open(CREDENCIALES, encoding="utf-8"):
        k, _, v = linea.strip().partition("=")
        if k:
            datos[k.strip()] = v.strip()
    if not datos.get("usuario") or not datos.get("clave"):
        raise SystemExit(f"Faltan usuario= o clave= en {CREDENCIALES}.")
    return datos


def login_clave():
    """Mi Carrefour (Keycloak): formulario de usuario y clave → cookie de VTEX."""
    cred = leer_credenciales()
    token = inicio_auth()
    pagina, url = pedir(
        "/api/vtexid/pub/authentication/oauth/redirect?"
        + urllib.parse.urlencode({"providerName": "MI CARREFOUR", "authenticationToken": token}),
        crudo=True,
    )
    m = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', pagina)
    if not m:
        raise SystemExit(f"No encontré el formulario de Mi Carrefour (terminé en {url.split('?')[0]}).")
    accion = html.unescape(m.group(1))
    pagina, url = pedir(
        accion, datos={"username": cred["usuario"], "password": cred["clave"], "credentialId": ""}, crudo=True
    )
    if "kc-form-login" in pagina:
        errores = re.findall(r'class="[^"]*(?:error|alert|feedback)[^"]*"[^>]*>(.*?)</', pagina, re.S)
        textos = [html.unescape(re.sub(r"<[^>]+>", "", e)).strip() for e in errores]
        texto = next((t for t in textos if t), "revisá usuario y clave.")
        raise SystemExit(f"Mi Carrefour rechazó el login: {texto}")
    return verificar_sesion()


def login_codigo_enviar(email):
    token = inicio_auth()
    set_cfg("auth_token", token)
    set_cfg("auth_email", email)
    pedir("/api/vtexid/pub/authentication/accesskey/send", datos={"authenticationToken": token, "email": email})
    print(f"Listo: Carrefour te mandó un código a {email}. Después: ./carrefour.py login codigo <CÓDIGO>")


def login_codigo_validar(codigo):
    token, email = cfg("auth_token"), cfg("auth_email")
    if not token:
        raise SystemExit("Primero pedí el código: ./carrefour.py login email <tu email>")
    d = pedir(
        "/api/vtexid/pub/authentication/accesskey/validate",
        datos={"authenticationToken": token, "login": email, "accessKey": codigo},
    )
    if (d or {}).get("authStatus") != "Success":
        raise SystemExit(f"No funcionó: {(d or {}).get('authStatus')}")
    set_cfg("auth_token", None)
    return verificar_sesion()


def sesion():
    d = pedir("/api/vtexid/pub/authenticated/user")
    return d if d and d.get("user") else None


def verificar_sesion():
    s = sesion()
    if not s:
        raise SystemExit("El login no dejó una sesión activa.")
    if not cfg("cp"):
        # Sin zona elegida, usamos la dirección guardada en la cuenta.
        d = pedir("/api/checkout/pub/orderForm")
        cp = (((d.get("shippingData") or {}).get("address")) or {}).get("postalCode")
        if cp:
            cmd_zona(argparse.Namespace(cp=cp))
    vence = next((c.expires for c in _JAR if c.name.startswith("VtexIdclientAutCookie") and c.expires), None)
    hasta = time.strftime(" hasta el %d/%m %H:%M", time.localtime(vence)) if vence else ""
    print(f"Sesión iniciada como {s['user']}{hasta}.")
    # El carrito pasa a estar asociado a la cuenta.
    set_cfg("orderform", None)
    return s


def cmd_login(a):
    if a.modo == "clave":
        login_clave()
    elif a.modo == "email":
        if not a.valor:
            raise SystemExit("Uso: ./carrefour.py login email <tu email>")
        login_codigo_enviar(a.valor)
    elif a.modo == "codigo":
        if not a.valor:
            raise SystemExit("Uso: ./carrefour.py login codigo <CÓDIGO>")
        login_codigo_validar(a.valor)
    elif a.modo == "estado":
        s = sesion()
        print(f"Sesión activa: {s['user']}" if s else "Sin sesión.")
    elif a.modo == "salir":
        if os.path.exists(COOKIES):
            os.remove(COOKIES)
        set_cfg("orderform", None)
        print("Sesión cerrada (borré las cookies).")


# ---------------------------------------------------------------- pedidos

def requiere_sesion():
    if not sesion():
        raise SystemExit("Necesitás iniciar sesión: ./carrefour.py login clave  (o login email …)")


# VTEX devuelve algunos estados en portugués.
ESTADOS = {
    "Faturado": "Facturado", "Cancelado": "Cancelado", "Pronto para o manuseio": "Listo para preparar",
    "Preparando Entrega": "Preparando la entrega", "Aguardando autorização para despachar": "Esperando autorización",
    "Pagamento Aprovado": "Pago aprobado", "Pagamento Pendente": "Pago pendiente", "Entregue": "Entregado",
}


def estado(o):
    e = o.get("statusDescription") or o.get("status") or ""
    return ESTADOS.get(e, e)


def mis_pedidos(n=10):
    d = pedir(f"/api/oms/user/orders?page=1&per_page={n}")
    return d.get("list", d) if isinstance(d, dict) else d


def copiar_pedido_a_lista(items, lista):
    with db() as con:
        for sku, n, nombre, seller in items:
            con.execute(
                "INSERT INTO productos (sku, nombre, seller) VALUES (?, ?, ?) ON CONFLICT(sku) DO NOTHING",
                (sku, nombre, seller),
            )
    for sku, n, _, _ in items:
        poner_en_lista(lista, sku, n)


def cmd_pedidos(a):
    requiere_sesion()
    lista = mis_pedidos(a.n)
    if a.json:
        print(json.dumps(lista, ensure_ascii=False, indent=1))
        return
    for o in lista:
        fecha = (o.get("creationDate") or "")[:10]
        print(f"{o['orderId']}  {fecha}  {pesos(o.get('totalValue', 0) / 100):>12}  {estado(o)}")


def items_de_pedido(order_id):
    d = pedir(f"/api/oms/user/orders/{order_id}")
    items = d.get("items", [])
    with db() as con:
        for it in items:
            con.execute(
                """INSERT INTO productos (sku, nombre, seller, unidad, multiplicador) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(sku) DO UPDATE SET unidad = excluded.unidad, multiplicador = excluded.multiplicador""",
                (it["id"], it["name"], "1", it.get("measurementUnit"), it.get("unitMultiplier")),
            )
    return d, [(it["id"], it["quantity"], it["name"], it.get("seller")) for it in items]


def cmd_pedido(a):
    requiere_sesion()
    d, items = items_de_pedido(a.id)
    print(f"Pedido {a.id}  {d.get('creationDate', '')[:10]}  {estado(d)}")
    unidades = {it["id"]: (it.get("measurementUnit"), it.get("unitMultiplier")) for it in d.get("items", [])}
    for sku, n, nombre, _ in items:
        print(f"  {cuanto(n, *unidades[sku]):>6} {nombre}  ({sku})")
    if a.a_lista:
        copiar_pedido_a_lista(items, a.a_lista)
        print(f"Copiado a la lista «{a.a_lista}».")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Pedidos de Carrefour desde la terminal.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("zona", help="código postal (precios y stock de tu zona)")
    p.add_argument("cp", nargs="?")
    p.set_defaults(f=cmd_zona)

    p = sub.add_parser("buscar", help="buscar productos")
    p.add_argument("texto", nargs="+")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_buscar)

    p = sub.add_parser("ahorrar", help="alternativas más baratas para tu lista")
    p.add_argument("-l", "--lista", default="habitual")
    p.add_argument("--minimo", type=float, default=5, help="ahorro mínimo en %% (5)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_ahorrar)

    p = sub.add_parser("cambiar", help="reemplazar un producto de la lista por otro")
    p.add_argument("viejo")
    p.add_argument("nuevo")
    p.add_argument("cantidad", nargs="?", type=int)
    p.add_argument("-l", "--lista", default="habitual")
    p.set_defaults(f=cmd_cambiar)

    p = sub.add_parser("historial", help="precios vistos de un SKU")
    p.add_argument("sku")
    p.set_defaults(f=cmd_historial)

    p = sub.add_parser("lista", help="listas guardadas (por defecto «habitual»)")
    p.add_argument("accion", nargs="?", default="ver", choices=["ver", "agregar", "quitar", "vaciar", "todas"])
    p.add_argument("sku", nargs="?")
    p.add_argument("cantidad", nargs="?", type=int)
    p.add_argument("-l", "--lista", default="habitual")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_lista)

    p = sub.add_parser("carrito", help="armar / ver / vaciar el carrito")
    p.add_argument("accion", nargs="?", default="ver", choices=["ver", "armar", "vaciar"])
    p.add_argument("-l", "--lista", default="habitual")
    p.add_argument("--vacio", action="store_true", help="no partir de la lista")
    p.add_argument("--mas", nargs="*", metavar="SKU:N", help="sumar unidades")
    p.add_argument("--cant", nargs="*", metavar="SKU:N", help="fijar cantidad")
    p.add_argument("--sin", nargs="*", metavar="SKU", help="sacar productos")
    p.add_argument("--sumar", action="store_true", help="agregar al carrito actual en vez de uno nuevo")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_carrito)

    p = sub.add_parser("login", help="clave | email <email> | codigo <código> | estado | salir")
    p.add_argument("modo", choices=["clave", "email", "codigo", "estado", "salir"])
    p.add_argument("valor", nargs="?")
    p.set_defaults(f=cmd_login)

    p = sub.add_parser("pedidos", help="tus pedidos anteriores (requiere login)")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_pedidos)

    p = sub.add_parser("pedido", help="detalle de un pedido; --a-lista copia sus productos")
    p.add_argument("id")
    p.add_argument("--a-lista", metavar="LISTA")
    p.set_defaults(f=cmd_pedido)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
