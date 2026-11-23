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
          link TEXT, unidad TEXT, actualizado INTEGER);
        CREATE TABLE IF NOT EXISTS precios (
          sku TEXT, precio REAL, precio_lista REAL, visto INTEGER,
          PRIMARY KEY (sku, visto));
        CREATE TABLE IF NOT EXISTS listas (
          lista TEXT, sku TEXT, cantidad INTEGER, nota TEXT,
          PRIMARY KEY (lista, sku));
        """
    )
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
            """INSERT INTO productos VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sku) DO UPDATE SET nombre = excluded.nombre, marca = excluded.marca,
                 seller = excluded.seller, link = excluded.link, unidad = excluded.unidad,
                 actualizado = excluded.actualizado""",
            (item["itemId"], p["productName"], p.get("brand"), seller,
             p.get("link") or f"{BASE}/{p.get('linkText')}/p",
             item.get("measurementUnit"), ahora),
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


def pedir(url, datos=None, json_body=None, metodo=None, crudo=False):
    if not url.startswith("http"):
        url = BASE + url
    headers = {"User-Agent": UA, "Accept": "application/json"}
    cuerpo = None
    if json_body is not None:
        cuerpo = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif datos is not None:
        cuerpo = urllib.parse.urlencode(datos).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=cuerpo, headers=headers, method=metodo)
    try:
        with opener().open(req, timeout=30) as r:
            texto = r.read().decode("utf-8", "replace")
            final = r.geturl()
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "replace")
        guardar_cookies()
        raise SystemExit(f"Error {e.code} en {url.split('?')[0]}: {texto[:300]}")
    finally:
        guardar_cookies()
    if crudo:
        return texto, final
    return json.loads(texto) if texto.strip() else None


def pesos(v):
    if v < 0:
        return "-" + pesos(-v)
    return "$" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")


# ---------------------------------------------------------------- zona

def cmd_zona(a):
    if not a.cp:
        print(f"Código postal: {cfg('cp', '(sin definir)')}  región: {cfg('region', '-')}")
        return
    regiones = pedir(f"/api/checkout/pub/regions?country=ARG&postalCode={a.cp}&sc=1")
    if not regiones:
        raise SystemExit("No encontré tiendas para ese código postal.")
    set_cfg("cp", a.cp)
    set_cfg("region", regiones[0]["id"])
    tiendas = [s["name"] for s in regiones[0]["sellers"]]
    print(f"Zona {a.cp}: {len(tiendas)} tiendas ({', '.join(tiendas[:4])}…)")


# ---------------------------------------------------------------- búsqueda

def buscar(texto, cantidad=10):
    q = {"query": texto, "count": cantidad, "hideUnavailableItems": "true", "locale": "es-AR"}
    if cfg("region"):
        q["regionId"] = cfg("region")
    d = pedir("/api/io/_v/api/intelligent-search/product_search/?" + urllib.parse.urlencode(q))
    res = []
    for p in d.get("products", []):
        for item in p["items"]:
            sellers = [s for s in item["sellers"] if s["commertialOffer"]["AvailableQuantity"] > 0]
            if not sellers:
                continue
            s = min(sellers, key=lambda s: s["commertialOffer"]["Price"])
            o = s["commertialOffer"]
            guardar_producto(p, item, o, s["sellerId"])
            res.append({
                "sku": item["itemId"], "nombre": item.get("nameComplete") or p["productName"],
                "marca": p.get("brand"), "precio": o["Price"], "precio_lista": o["ListPrice"],
                "seller": s["sellerId"], "promos": [t["name"] for t in o.get("Teasers", [])],
            })
    return res


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
    with db() as con:
        return [dict(f) for f in con.execute(
            """SELECT l.sku, l.cantidad, p.nombre,
                 (SELECT precio FROM precios WHERE sku = l.sku ORDER BY visto DESC LIMIT 1) precio
               FROM listas l LEFT JOIN productos p USING (sku) WHERE lista = ? ORDER BY p.nombre""",
            (lista,),
        )]


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
    total = 0
    for f in filas:
        total += (f["precio"] or 0) * f["cantidad"]
        precio = pesos(f["precio"]) if f["precio"] else "?"
        print(f"  {f['cantidad']:>3} × {f['nombre'] or f['sku']}  ({f['sku']}, {precio} c/u)")
    if filas:
        print(f"  Estimado: {pesos(total)} (último precio visto)")


# ---------------------------------------------------------------- carrito

def order_form(nuevo=False):
    of = None if nuevo else cfg("orderform")
    d = pedir(f"/api/checkout/pub/orderForm/{of}" if of else "/api/checkout/pub/orderForm?forceNewCart=true" if nuevo else "/api/checkout/pub/orderForm")
    set_cfg("orderform", d["orderFormId"])
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


def armar_carrito(cantidades, nuevo=True):
    d = con_cp(order_form(nuevo=nuevo))
    return pedir(
        f"/api/checkout/pub/orderForm/{d['orderFormId']}/items",
        json_body={"orderItems": [item_vtex(s, n) for s, n in cantidades.items()]},
    )


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
    return d, [(it["id"], it["quantity"], it["name"], it.get("seller")) for it in d.get("items", [])]


def cmd_pedido(a):
    requiere_sesion()
    d, items = items_de_pedido(a.id)
    print(f"Pedido {a.id}  {d.get('creationDate', '')[:10]}  {estado(d)}")
    for sku, n, nombre, _ in items:
        print(f"  {n:>3} × {nombre}  ({sku})")
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
