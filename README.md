# 🛒 Changuito

Shop at [Carrefour Argentina](https://www.carrefour.com.ar) from your terminal:
search products with your area's prices, keep your usual shopping list, build the cart
in seconds and pay on Carrefour's website.

- **`tui.py`**: terminal UI (Search · List · Cart · Orders).
- **`carrefour.py`**: the same features as standalone commands, handy for scripts or for an
  assistant like Claude ("build my usual order, but without yerba and with 2 more milks").

> ⚠️ **Unofficial personal project.** Not affiliated with Carrefour. It uses the same public
> endpoints the website uses (the store runs on [VTEX](https://developers.vtex.com/)), which
> may change or be blocked without notice. Use it sparingly and for your own shopping.

The interface is in Spanish (Argentina).

## Requirements

Python 3.9 or newer. That's it: no dependencies.
The TUI uses `curses`, which ships with Python on Linux and macOS (on Windows: `pip install windows-curses`).

```sh
git clone https://github.com/Minfante377/changuito.git
cd changuito
./tui.py
```

On first run it asks for your postal code, which determines the prices and stock you see.

## The TUI

| Tab | Keys |
|---|---|
| all | `1`–`4` or `Tab` switch tabs · `↑`/`↓` (or `j`/`k`) move · `q` quits |
| **Buscar** (search) | `/` search · `a` add to list · `c` add to cart · `h` price history |
| **Lista** (list) | `+`/`-` quantity · `d` remove · `l` switch list (or create one) · `A` build the cart from the list |
| **Carrito** (cart) | `+`/`-` quantity · `d` remove · `o` open in the browser to pay · `r` refresh · `V` empty |
| **Pedidos** (orders) | `L` log in · `Enter` show products · `a` copy an order to a list · `Esc` back |

## Commands

```sh
./carrefour.py zona 1425                    # your postal code
./carrefour.py buscar leche entera          # SKU, price, discounts and promos (--json for scripts)
./carrefour.py historial 144373             # prices seen for a product

./carrefour.py lista agregar 144373 2       # the "habitual" list (or -l another)
./carrefour.py lista quitar 144373
./carrefour.py lista                        # show the list with an estimated total
./carrefour.py lista todas                  # all lists

./carrefour.py carrito armar                # new cart from the list
./carrefour.py carrito armar --sin 25341 --mas 144373:2 --cant 142627:3
./carrefour.py carrito armar --vacio --mas 144373:1   # without starting from the list
./carrefour.py carrito                      # show the cart and the payment link
./carrefour.py carrito vaciar
```

`--sin` removes products, `--mas` adds units and `--cant` sets the quantity.

The cart is built through the API, but **you always pay on Carrefour's website**: the link
`https://www.carrefour.com.ar/checkout/?orderFormId=…` opens that same cart in your browser.
Shipping is calculated there, with your address.

## Logging in (optional)

Lets you see your past orders and copy them into a list. Two ways:

**Email code**, no password stored:

```sh
./carrefour.py login email you@example.com  # Carrefour emails you a code
./carrefour.py login codigo 123456
```

**Mi Carrefour username and password**: create `~/.config/changuito/credenciales` with

```
usuario=<DNI or email>
clave=<your password>
```

then run `chmod 600 ~/.config/changuito/credenciales` and `./carrefour.py login clave`.

Once logged in:

```sh
./carrefour.py login estado
./carrefour.py pedidos                          # your latest orders
./carrefour.py pedido <id> --a-lista habitual   # copy an order into a list
./carrefour.py login salir                      # log out
```
