#!/usr/bin/env python3
"""Changuito en la terminal: buscar, armar listas, el carrito y tus pedidos.

Teclas generales: 1-4 o Tab cambian de pestaña, ↑/↓ se mueven, q sale.
"""

import curses
import locale
import webbrowser

import carrefour as cf

PESTANAS = ["Buscar", "Lista", "Carrito", "Pedidos", "Ahorro"]

AYUDA = {
    "Buscar": "/ buscar   a agregar a la lista   c sumar al carrito   h historial de precios",
    "Lista": "+/- cantidad   d quitar   l cambiar de lista   A armar carrito con la lista",
    "Carrito": "+/- cantidad   d quitar   o abrir para pagar   r actualizar   V vaciar",
    "Pedidos": "Enter ver productos   a copiar a la lista   r actualizar   L iniciar sesión",
    "Ahorro": "r buscar alternativas para la lista   Enter cambiar en la lista   d descartar",
}


class App:
    def __init__(self, scr):
        self.scr = scr
        self.tab = 0
        self.sel = {t: 0 for t in PESTANAS}
        self.top = {t: 0 for t in PESTANAS}
        self.msg = ""
        self.lista = "habitual"
        self.resultados = []
        self.consulta = ""
        self.carrito = None
        self.pedidos = None
        self.detalle = None  # (pedido, items) abierto en Pedidos
        self.usuario = None
        self.sesion_vista = False
        self.sugerencias = None

    # ------------------------------------------------------------ helpers

    def intentar(self, f, *args, espera="Cargando…"):
        """Corre una llamada a la API; los errores van a la línea de estado."""
        self.msg = espera
        self.dibujar()
        try:
            r = f(*args)
            self.msg = ""
            return r
        except SystemExit as e:
            self.msg = str(e)[:300]
        except Exception as e:  # red caída, JSON raro…
            self.msg = f"Error: {e}"
        return None

    def preguntar(self, etiqueta, inicial=""):
        h, w = self.scr.getmaxyx()
        curses.curs_set(1)
        texto = inicial
        while True:
            self.scr.move(h - 1, 0)
            self.scr.clrtoeol()
            self.scr.addnstr(h - 1, 0, f"{etiqueta}{texto}", w - 1, curses.A_BOLD)
            self.scr.refresh()
            c = self.scr.get_wch()
            if c in ("\n", "\r", curses.KEY_ENTER):
                break
            if c == "\x1b":
                texto = None
                break
            if c in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                texto = texto[:-1]
            elif isinstance(c, str) and c.isprintable():
                texto += c
        curses.curs_set(0)
        return texto.strip() if texto is not None else None

    def preguntar_cantidad(self, etiqueta, inicial=1):
        t = self.preguntar(f"{etiqueta} cantidad: ", str(inicial))
        if t is None:
            return None
        try:
            return max(0, int(t))
        except ValueError:
            self.msg = "Cantidad inválida."
            return None

    def filas(self):
        t = PESTANAS[self.tab]
        if t == "Buscar":
            return self.resultados
        if t == "Lista":
            return cf.items_lista(self.lista)
        if t == "Carrito":
            return (self.carrito or {}).get("items", [])
        if t == "Ahorro":
            return self.sugerencias or []
        if self.detalle:
            return self.detalle[1]
        return self.pedidos or []

    # ------------------------------------------------------------ dibujo

    def dibujar(self):
        s = self.scr
        s.erase()
        h, w = s.getmaxyx()
        if h < 8 or w < 40:
            s.addnstr(0, 0, "Agrandá la terminal", w - 1)
            s.refresh()
            return

        # Encabezado: pestañas + zona + sesión
        x = 0
        for i, nombre in enumerate(PESTANAS):
            etiqueta = f" {i + 1} {nombre} "
            s.addnstr(0, x, etiqueta, max(0, w - x - 1), curses.A_REVERSE if i == self.tab else curses.A_DIM)
            x += len(etiqueta) + 1
        estado = f"CP {cf.cfg('cp', '—')} · {self.usuario or 'sin sesión'}"
        if x + len(estado) + 2 < w:
            s.addnstr(0, w - len(estado) - 1, estado, len(estado), curses.color_pair(2))
        s.hline(1, 0, curses.ACS_HLINE, w)

        t = PESTANAS[self.tab]
        renglones = self.renglones(t, w)
        alto = h - 5
        sel = min(self.sel[t], max(0, len(self.filas()) - 1))
        self.sel[t] = sel
        if sel < self.top[t]:
            self.top[t] = sel
        if sel >= self.top[t] + alto:
            self.top[t] = sel - alto + 1

        titulo, cuerpo, pie = renglones
        s.addnstr(2, 0, titulo, w - 1, curses.A_BOLD)
        for fila in range(alto):
            i = self.top[t] + fila
            if i >= len(cuerpo):
                break
            texto, attr = cuerpo[i]
            if i == sel:
                attr |= curses.A_REVERSE
            s.addnstr(3 + fila, 0, texto.ljust(w - 1), w - 1, attr)
        if pie:
            s.addnstr(h - 3, 0, pie, w - 1, curses.A_BOLD)

        s.addnstr(h - 2, 0, AYUDA[t] + "   q salir", w - 1, curses.A_DIM)
        if self.msg:
            s.addnstr(h - 1, 0, self.msg, w - 1, curses.color_pair(1))
        s.refresh()

    def renglones(self, t, w):
        normal = curses.A_NORMAL
        if t == "Buscar":
            titulo = f"Resultados para «{self.consulta}»" if self.consulta else "Apretá / para buscar"
            cuerpo = []
            for r in self.resultados:
                rebaja = f" (antes {cf.pesos(r['precio_lista'])})" if r["precio_lista"] > r["precio"] else ""
                promo = f" [{'; '.join(r['promos'])}]" if r["promos"] else ""
                attr = curses.color_pair(3) if rebaja or promo else normal
                cuerpo.append((f"{cf.pesos(r['precio']):>11}{rebaja}  {r['nombre']}{promo}", attr))
            return titulo, cuerpo, ""

        if t == "Lista":
            items = cf.items_lista(self.lista)
            otras = [n for n in cf.nombres_listas() if n != self.lista]
            titulo = f"Lista «{self.lista}»" + (f"   (otras: {', '.join(otras)})" if otras else "")
            total = sum(i["subtotal"] for i in items)
            cuerpo = [
                (f"{cf.cuanto(i['cantidad'], i['unidad'], i['multiplicador']):>6} {i['nombre'] or i['sku']}"
                 f"  {i['precio_txt']}", normal)
                for i in items
            ]
            if not items:
                cuerpo = [("Vacía: buscá productos y agregalos con a", curses.A_DIM)]
            return titulo, cuerpo, f"Estimado: {cf.pesos(total)} (último precio visto)" if items else ""

        if t == "Carrito":
            d = self.carrito
            if not d:
                return "Carrito", [("r para cargarlo, o A en la Lista para armarlo", curses.A_DIM)], ""
            cuerpo = []
            for it in d["items"]:
                falta = it.get("availability") != "available"
                attr = curses.color_pair(1) if falta else normal
                aviso = f"  ⚠ {it.get('availability')}" if falta else ""
                cuerpo.append(
                    (f"{it['quantity']:>3} × {it['name']}  {cf.pesos(it['sellingPrice'] / 100)} c/u{aviso}", attr)
                )
            if not cuerpo:
                cuerpo = [("Carrito vacío.", curses.A_DIM)]
            partes = [f"{z['name']}: {cf.pesos(z['value'] / 100)}" for z in cf.totales(d)]
            pie = " · ".join(partes + [f"TOTAL {cf.pesos(d['value'] / 100)} + envío"])
            return "Carrito", cuerpo, pie

        if t == "Ahorro":
            if self.sugerencias is None:
                return (f"Ahorro en «{self.lista}»",
                        [("r para buscar productos parecidos más baratos (tarda: va despacio para no saturar a Carrefour)",
                          curses.A_DIM)], "")
            cuerpo = []
            for x in self.sugerencias:
                promo = f" [{'; '.join(x['alt_promos'])}]" if x["alt_promos"] else ""
                cuerpo.append((
                    f"-{cf.pesos(round(x['ahorro'])):>8} ({x['pct']:.0%})  {x['nombre'][:38]} → {x['alt_nombre']}"
                    f"  {cf.precio_base(x['alt_por_base'], x['base'])}{promo}",
                    curses.color_pair(3),
                ))
            total = sum(x["ahorro"] for x in self.sugerencias)
            return (f"Ahorro en «{self.lista}»", cuerpo or [("No encontré nada más barato. 👌", curses.A_DIM)],
                    f"Ahorro posible: ~{cf.pesos(round(total))}" if cuerpo else "")

        # Pedidos
        if self.detalle:
            pedido, items = self.detalle
            cuerpo = [(f"{n:>3} × {nombre}", normal) for _, n, nombre, _ in items]
            return f"Pedido {pedido}  (Esc vuelve)", cuerpo, ""
        if self.pedidos is None:
            msg = "r para cargar tus pedidos" if self.usuario else "Iniciá sesión con L para ver tus pedidos"
            return "Pedidos", [(msg, curses.A_DIM)], ""
        cuerpo = [
            (f"{(o.get('creationDate') or '')[:10]}  {cf.pesos(o.get('totalValue', 0) / 100):>12}"
             f"  {cf.estado(o)}  {o['orderId']}", normal)
            for o in self.pedidos
        ]
        return "Pedidos", cuerpo or [("No hay pedidos.", curses.A_DIM)], ""

    # ------------------------------------------------------------ acciones

    def actual(self):
        filas = self.filas()
        i = self.sel[PESTANAS[self.tab]]
        return filas[i] if 0 <= i < len(filas) else None

    def tecla(self, c):
        t = PESTANAS[self.tab]
        filas = self.filas()
        if c in ("KEY_DOWN", "j"):
            self.sel[t] = min(self.sel[t] + 1, max(0, len(filas) - 1))
        elif c in ("KEY_UP", "k"):
            self.sel[t] = max(self.sel[t] - 1, 0)
        elif c == "KEY_NPAGE":
            self.sel[t] = min(self.sel[t] + 10, max(0, len(filas) - 1))
        elif c == "KEY_PPAGE":
            self.sel[t] = max(self.sel[t] - 10, 0)
        elif c in "12345" and len(c) == 1:
            self.ir(int(c) - 1)
        elif c == "\t":
            self.ir((self.tab + 1) % len(PESTANAS))
        else:
            getattr(self, "tecla_" + t.lower())(c)

    def ir(self, tab):
        self.tab = tab
        self.msg = ""
        if PESTANAS[tab] == "Carrito" and self.carrito is None:
            self.carrito = self.intentar(cf.order_form)
        if PESTANAS[tab] == "Pedidos" and self.pedidos is None and self.usuario:
            self.pedidos = self.intentar(cf.mis_pedidos, 20)

    def tecla_buscar(self, c):
        r = self.actual()
        if c == "/":
            q = self.preguntar("Buscar: ", self.consulta)
            if q:
                self.consulta = q
                self.resultados = self.intentar(cf.buscar, q, 30, espera=f"Buscando «{q}»…") or []
                self.sel["Buscar"] = self.top["Buscar"] = 0
                if not self.resultados and not self.msg:
                    self.msg = "Sin resultados."
        elif c == "a" and r:
            n = self.preguntar_cantidad(r["nombre"][:40])
            if n:
                cf.poner_en_lista(self.lista, r["sku"], n)
                self.msg = f"Agregado a «{self.lista}»: {n} × {r['nombre']}"
        elif c == "c" and r:
            n = self.preguntar_cantidad(r["nombre"][:40])
            if n:
                d = self.intentar(cf.armar_carrito, {r["sku"]: n}, False)
                if d:
                    self.carrito = d
                    self.msg = f"Al carrito: {n} × {r['nombre']}"
        elif c == "h" and r:
            with cf.db() as con:
                precios = con.execute(
                    "SELECT precio, visto FROM precios WHERE sku = ? ORDER BY visto", (r["sku"],)
                ).fetchall()
            import time
            self.msg = "Precios vistos: " + "  ".join(
                f"{time.strftime('%d/%m', time.localtime(p['visto']))} {cf.pesos(p['precio'])}" for p in precios
            )

    def tecla_lista(self, c):
        r = self.actual()
        if c in ("+", "=") and r:
            cf.poner_en_lista(self.lista, r["sku"], r["cantidad"] + 1)
        elif c == "-" and r:
            cf.poner_en_lista(self.lista, r["sku"], r["cantidad"] - 1)
        elif c == "d" and r:
            cf.poner_en_lista(self.lista, r["sku"], 0)
            self.msg = f"Quitado: {r['nombre']}"
        elif c == "l":
            nombre = self.preguntar(f"Lista ({', '.join(cf.nombres_listas()) or 'nueva'}): ", self.lista)
            if nombre:
                self.lista = nombre
                self.sel["Lista"] = self.top["Lista"] = 0
        elif c == "A":
            items = cf.items_lista(self.lista)
            if not items:
                self.msg = "La lista está vacía."
                return
            d = self.intentar(
                cf.armar_carrito, {i["sku"]: i["cantidad"] for i in items}, True, espera="Armando el carrito…"
            )
            if d:
                self.carrito = d
                self.tab = PESTANAS.index("Carrito")
                faltan = [i["name"] for i in d["items"] if i.get("availability") != "available"]
                self.msg = f"Carrito armado. Sin stock: {', '.join(faltan)}" if faltan else "Carrito armado: o para pagar."

    def tecla_carrito(self, c):
        r = self.actual()
        i = self.sel["Carrito"]
        if c == "r":
            self.carrito = self.intentar(cf.order_form) or self.carrito
        elif c in ("+", "=") and r:
            self.carrito = self.intentar(cf.cambiar_cantidad, i, r["quantity"] + 1) or self.carrito
        elif c == "-" and r:
            self.carrito = self.intentar(cf.cambiar_cantidad, i, r["quantity"] - 1) or self.carrito
        elif c == "d" and r:
            self.carrito = self.intentar(cf.cambiar_cantidad, i, 0) or self.carrito
        elif c == "V":
            if self.preguntar("¿Vaciar el carrito? (s/n) ") == "s":
                of = cf.order_form()["orderFormId"]
                self.carrito = self.intentar(
                    cf.pedir, f"/api/checkout/pub/orderForm/{of}/items/removeAll", None, {}
                ) or self.carrito
        elif c == "o" and self.carrito:
            link = cf.link_checkout(self.carrito)
            webbrowser.open(link)
            self.msg = f"Abierto en el navegador: {link}"

    def tecla_pedidos(self, c):
        r = self.actual()
        if c == "\x1b" and self.detalle:
            self.detalle = None
        elif c == "L":
            self.login()
        elif c == "r":
            if self.usuario:
                self.pedidos = self.intentar(cf.mis_pedidos, 20)
                self.detalle = None
        elif c in ("\n", "\r", "KEY_ENTER") and r and not self.detalle:
            res = self.intentar(cf.items_de_pedido, r["orderId"])
            if res:
                self.detalle = (r["orderId"], res[1])
                self.sel["Pedidos"] = 0
        elif c == "a" and (self.detalle or r):
            if not self.detalle:
                res = self.intentar(cf.items_de_pedido, r["orderId"])
                if not res:
                    return
                pedido, items = r["orderId"], res[1]
            else:
                pedido, items = self.detalle
            nombre = self.preguntar("Copiar a la lista: ", self.lista)
            if nombre:
                cf.copiar_pedido_a_lista(items, nombre)
                self.lista = nombre
                self.msg = f"{len(items)} productos del pedido {pedido} copiados a «{nombre}»."

    def tecla_ahorro(self, c):
        r = self.actual()
        if c == "r":
            def avance(n, total, nombre):
                self.msg = f"Revisando {n}/{total}: {nombre}"
                self.dibujar()
            self.sugerencias = self.intentar(cf.ahorro, self.lista, 0.05, avance, espera="Buscando alternativas…")
            self.sel["Ahorro"] = 0
        elif c in ("\n", "\r", "KEY_ENTER") and r:
            cf.cambiar_en_lista(self.lista, r["sku"], r["alt_sku"], r["alt_cantidad"])
            self.sugerencias.remove(r)
            self.msg = f"Cambiado en «{self.lista}»: {r['alt_nombre']}"
        elif c == "d" and r:
            self.sugerencias.remove(r)

    def login(self):
        modo = self.preguntar("Login: [c]lave de Mi Carrefour o código por [e]mail? ")
        if modo == "c":
            s = self.intentar(cf.login_clave, espera="Iniciando sesión…")
        elif modo == "e":
            email = self.preguntar("Email: ")
            if not email or self.intentar(cf.login_codigo_enviar, email) is None and self.msg:
                return
            codigo = self.preguntar("Código que te llegó por mail: ")
            s = self.intentar(cf.login_codigo_validar, codigo) if codigo else None
        else:
            return
        if s:
            self.usuario = s["user"]
            self.carrito = None
            self.pedidos = self.intentar(cf.mis_pedidos, 20)
            self.msg = f"Sesión iniciada como {self.usuario}."

    # ------------------------------------------------------------ loop

    def correr(self):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        s = self.intentar(cf.sesion, espera="Revisando la sesión…")
        self.usuario = s["user"] if s else None
        if not cf.cfg("cp"):
            cp = self.preguntar("Tu código postal (para precios y stock de tu zona): ")
            if cp:
                self.intentar(lambda: cf.cmd_zona(type("A", (), {"cp": cp})))
        while True:
            self.dibujar()
            try:
                c = self.scr.get_wch()
            except KeyboardInterrupt:
                return
            if c == curses.KEY_RESIZE:
                continue
            if isinstance(c, int):
                c = curses.keyname(c).decode()
            if c == "q":
                return
            self.tecla(c)


def main():
    locale.setlocale(locale.LC_ALL, "")
    # cmd_zona imprime; en la TUI no queremos ensuciar la pantalla.
    import builtins
    builtins.print = lambda *a, **k: None
    curses.wrapper(lambda scr: App(scr).correr())


if __name__ == "__main__":
    main()
