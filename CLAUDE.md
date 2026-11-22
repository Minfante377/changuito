# Changuito

Scripts para los pedidos personales del usuario en Carrefour Argentina (ver README.md).
Cuando pida algo tipo «armame el pedido de siempre sin yerba y con 2 leches más»:
`./carrefour.py lista --json`, `./carrefour.py buscar … --json` para encontrar SKUs, y
`./carrefour.py carrito armar --sin … --mas SKU:N`. Pasale el link para pagar.

- Nunca pagues ni finalices compras: el pago lo hace el usuario en la web.
- Nunca muestres el contenido de `~/.config/changuito/credenciales` ni de las cookies.
- Sin dependencias: solo biblioteca estándar de Python.
