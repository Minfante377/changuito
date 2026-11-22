# 🛒 Changuito

Hacé tus compras de [Carrefour Argentina](https://www.carrefour.com.ar) desde la terminal:
buscá productos con los precios de tu zona, guardá tu lista de siempre, armá el carrito
en un segundo y pagá en la web de Carrefour.

- **`tui.py`**: interfaz en la terminal (Buscar · Lista · Carrito · Pedidos).
- **`carrefour.py`**: los mismos comandos sueltos, ideales para scripts o para un asistente
  como Claude («armame el pedido de siempre pero sin yerba y con 2 leches más»).

> ⚠️ **Proyecto personal, no oficial.** No tiene relación con Carrefour. Usa los mismos
> endpoints públicos que usa la web (la tienda corre sobre [VTEX](https://developers.vtex.com/)),
> que pueden cambiar o bloquearse sin aviso. Usalo con moderación y para tus propias compras.

## Requisitos

Python 3.9 o más nuevo. Nada más: no hay dependencias.
La TUI usa `curses`, que viene con Python en Linux y macOS (en Windows: `pip install windows-curses`).

```sh
git clone https://github.com/Minfante377/changuito.git
cd changuito
./tui.py
```

La primera vez te pide el código postal, que define los precios y el stock que ves.

## La TUI

| Pestaña | Teclas |
|---|---|
| todas | `1`–`4` o `Tab` cambian de pestaña · `↑`/`↓` (o `j`/`k`) se mueven · `q` sale |
| **Buscar** | `/` buscar · `a` agregar a la lista · `c` sumar al carrito · `h` historial de precios |
| **Lista** | `+`/`-` cantidad · `d` quitar · `l` cambiar de lista (o crear otra) · `A` armar el carrito con la lista |
| **Carrito** | `+`/`-` cantidad · `d` quitar · `o` abrir en el navegador para pagar · `r` actualizar · `V` vaciar |
| **Pedidos** | `L` iniciar sesión · `Enter` ver productos · `a` copiar un pedido a una lista · `Esc` volver |

## Comandos

```sh
./carrefour.py zona 1425                    # tu código postal
./carrefour.py buscar leche entera          # SKU, precio, rebajas y promos (--json para scripts)
./carrefour.py historial 144373             # precios vistos de un producto

./carrefour.py lista agregar 144373 2       # lista «habitual» (o -l otra)
./carrefour.py lista quitar 144373
./carrefour.py lista                        # ver la lista con el total estimado
./carrefour.py lista todas

./carrefour.py carrito armar                # carrito nuevo con la lista
./carrefour.py carrito armar --sin 25341 --mas 144373:2 --cant 142627:3
./carrefour.py carrito armar --vacio --mas 144373:1   # sin partir de la lista
./carrefour.py carrito                      # ver el carrito y el link para pagar
./carrefour.py carrito vaciar
```

El carrito se arma por API, pero **el pago siempre lo hacés vos en la web de Carrefour**:
el link `https://www.carrefour.com.ar/checkout/?orderFormId=…` abre ese mismo carrito
en el navegador. El envío se calcula ahí, con tu dirección.

## Iniciar sesión (opcional)

Sirve para ver tus pedidos anteriores y copiarlos a una lista. Hay dos formas:

**Código por email**, sin guardar ninguna clave:

```sh
./carrefour.py login email vos@ejemplo.com  # Carrefour te manda un código
./carrefour.py login codigo 123456
```

**Usuario y clave de Mi Carrefour**: creá `~/.config/changuito/credenciales` con

```
usuario=<DNI o email>
clave=<tu clave>
```

y después `chmod 600 ~/.config/changuito/credenciales` y `./carrefour.py login clave`.

Con la sesión iniciada:

```sh
./carrefour.py login estado
./carrefour.py pedidos                          # tus últimos pedidos
./carrefour.py pedido <id> --a-lista habitual   # copiar un pedido a una lista
./carrefour.py login salir                      # borra la sesión
```

## Dónde quedan tus datos

Todo queda en tu compu, nada se manda a otro lado que no sea carrefour.com.ar:

- `changuito.db` (SQLite, al lado de los scripts): zona, listas, carrito actual y precios vistos.
- `~/.config/changuito/` (permisos 600): las cookies de la sesión y, si las creaste, tus credenciales.

Las dos ubicaciones se pueden cambiar con `CHANGUITO_DB` y `CHANGUITO_CONFIG`.

## Cómo funciona

| Qué | Endpoint de VTEX |
|---|---|
| Tiendas de tu zona | `GET /api/checkout/pub/regions?country=ARG&postalCode=…` |
| Búsqueda | `GET /api/io/_v/api/intelligent-search/product_search/?query=…&regionId=…` |
| Carrito | `/api/checkout/pub/orderForm` (`/items`, `/items/update`, `/attachments/shippingData`) |
| Login con código | `/api/vtexid/pub/authentication/accesskey/send` y `/validate` |
| Login con clave | OAuth «MI CARREFOUR» (Keycloak en `id.carrefour.com.ar`) |
| Tus pedidos | `GET /api/oms/user/orders` |
