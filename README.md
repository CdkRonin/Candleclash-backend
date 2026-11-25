# CandleClash — Backend

API del auto-battler Web3 CandleClash. FastAPI + MongoDB (Motor). El servidor
es la autoridad: stats, batallas, forja, mercado, misiones y energía se
calculan aquí, nunca en el cliente.

## Qué hay

- `main.py` — app FastAPI, routers, lifespan (Mongo + oráculo).
- `config.py` — ajustes y tablas del juego (oráculo, razas, habilidades,
  sorteos, misiones, energía). **Falla al arrancar** si `SECRET_KEY` es la
  de ejemplo o `MONGODB_URL` es el placeholder.
- `database.py` — conexión Motor e índices (incl. `token_id` único).
- `routers/` — auth (firma MetaMask + JWT), gladiators (mint), battle,
  forge, market (P2P + $TICK + Pack Recluta), missions, oracle.
- `services/` — auth_service, battle_service (simulación), energy_service,
  oracle_service (Binance principal, CoinGecko respaldo), rate_limit.

## Correr en local

```bash
cd backend
cp .env.example .env   # y pon tus valores reales (nunca se sube: .env está en .gitignore)
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Necesitas un MongoDB local (`mongodb://127.0.0.1:27017`) o Atlas.
Docs interactivas: http://127.0.0.1:8000/docs

## Variables de entorno (`.env`)

| Variable | Para qué |
|---|---|
| `MONGODB_URL` | URL de MongoDB (local o Atlas `mongodb+srv://...`) |
| `DB_NAME` | `candleclash` |
| `SECRET_KEY` | Clave JWT (64 caracteres aleatorios; **nunca** la de ejemplo) |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `10080` (7 días) |
| `ORACLE_REFRESH_SECONDS` | `14400` (4 h) |
| `POLYGON_RPC_URL` | `https://polygon-rpc.com` |
| `ALLOWED_ORIGINS` | Orígenes CORS (incl. `https://candleclash.vercel.app`) |
| `COINGECKO_API_KEY` | Opcional (respaldo del oráculo) |
| `NFT_CONTRACT_ADDRESS` / `TICK_CONTRACT_ADDRESS` / `MARKET_CONTRACT_ADDRESS` / `GAME_WALLET_PRIVATE_KEY` | Vacíos por ahora: sin contratos desplegados |

## Despliegue (producción)

Render, servicio **Candleclash-backend**, plan Free, repo
`CdkRonin/Candleclash-backend` rama `main`, región Virginia.
Build: `pip install -r requirements.txt`. Start:
`uvicorn main:app --host 0.0.0.0 --port $PORT`.
URL: https://candleclash-backend-o1df.onrender.com/ (ojo: el dominio
corto `candleclash-backend.onrender.com` es OTRA app).

- El auto-deploy NO dispara solo de forma fiable: tras un push, haz
  **Manual Deploy** en el panel de Render y verifica `/` y `/docs`.
- El plan gratis se duerme tras inactividad: la primera petición tarda
  50 s o más.
- `PYTHON_VERSION=3.11.0` está en las variables de Render y el repo
  fija `3.11.0` con `.python-version`.

## Seguridad (decisiones)

- Login: nonce de un solo uso, consumido atómicamente (dos logins
  concurrentes con el mismo nonce: solo uno pasa). El mensaje de firma
  incluye el dominio y "no mueve fondos".
- JWT en `localStorage` del frontend (no cookie HttpOnly): el frontend
  está en Vercel y el backend en Render (dominios distintos). La
  defensa es higiene XSS + firma con dominio/nonce + expiración.
  Ver el comentario en `services/auth_service.py`.
- Límites de velocidad en memoria (`services/rate_limit.py`): auth,
  mint, pelea, pack, forja, mercado y misiones. Respuesta 429.
- Forja, batalla y compras P2P usan escrituras condicionales con
  rollback: dos peticiones simultáneas no pueden gastar el mismo
  sacrificio, vender el mismo anuncio dos veces ni cobrar dos veces.
- El Pack Recluta es beta: si no hay saldo USDT, se reclama gratis
  una sola vez (sin depósitos todavía). Cuando haya depósitos, el
  cobro de 15 USDT debe ser obligatorio (TODO en el código).

## Límites conocidos

- Sin tests automatizados en el repo (hay scripts de prueba en `/tmp`,
  no versionados). Sin rate limiting distribuido (una sola instancia).
- Sin contratos NFT/$TICK desplegados: `token_id` es uuid hasta que
  existan. Sin depósitos ni retiros de dinero real.
- El oráculo usa Binance (principal) y CoinGecko (respaldo); el oro
  (XAU) viene de PAXGUSDT.
