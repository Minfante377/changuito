# Changuito

Pedidos personales de [carrefour.com.ar](https://www.carrefour.com.ar) desde la terminal.
Usa la API pública de VTEX (la plataforma de la tienda); **no es oficial** y puede cambiar.
Solo Python 3 (biblioteca estándar), sin instalar nada.

- `./tui.py` — interfaz en la terminal (Buscar · Lista · Carrito · Pedidos).
- `./carrefour.py …` — los mismos comandos sueltos (para scripts o para Claude).

El carrito se arma por API y **se paga en la web de Carrefour**: el link
`/checkout/?orderFormId=…` abre ese carrito en el navegador.

## Estado

- `changuito.db` (SQLite, al lado de los scripts): zona, listas, carrito actual y precios vistos.
- `~/.config/changuito/` (permisos 600): cookies de sesión y, si querés, `credenciales`.

## Comandos

```
./carrefour.py zona 1425                    # precios y stock de tu zona
./carrefour.py buscar leche entera          # SKU, precio, rebajas y promos
./carrefour.py historial 144373             # precios vistos de un producto
./carrefour.py lista agregar 144373 2       # lista «habitual» (-l otra)
./carrefour.py lista                        # ver la lista con el estimado
./carrefour.py carrito armar                # carrito nuevo con la lista
./carrefour.py carrito armar --sin 25341 --mas 144373:2 --cant 142627:3
./carrefour.py carrito                      # ver el carrito + link para pagar
```

## Login (para ver tus pedidos y usar tu cuenta)

Dos formas:

1. **Usuario y clave de Mi Carrefour.** Creá `~/.config/changuito/credenciales`:
   ```
   usuario=<DNI o email>
   clave=<tu clave>
   ```
   `chmod 600 ~/.config/changuito/credenciales`, y después `./carrefour.py login clave`.
2. **Código por email:** `./carrefour.py login email vos@ejemplo.com`, y con el
   código que te llega: `./carrefour.py login codigo 123456`.

Después:

```
./carrefour.py login estado
./carrefour.py pedidos                      # tus últimos pedidos
./carrefour.py pedido <id> --a-lista habitual   # copiar un pedido a una lista
./carrefour.py login salir
```
