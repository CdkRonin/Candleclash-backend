"""
Límites de velocidad en memoria — CandleClash.

Sin Redis ni servicios nuevos: una sola instancia de Render gratis,
así que un diccionario en memoria basta. Ventana deslizante por clave
(ruta + wallet o IP). Cuando se supera el límite, la petición recibe
429 con un mensaje claro; el juego sigue igual para un humano normal.

Límites (por minuto, salvo que se diga otra cosa):
- nonce:  10 por wallet+IP — un login pide un nonce; 10 es de sobra.
- login:  30 por IP — no hay contraseñas que adivinar (firma ECDSA);
  el freno anti-abuso real es el nonce por wallet. 30/min frena
  martilleo sin molestar a un humano.
- mint:   5 por wallet — el límite total es 2 gratis por wallet.
- fight:  30 por wallet — la energía (10) limita mucho antes.
- pack:   5 por wallet — 1 reclamo por cuenta en total.
- forge:  20 por wallet — ascender/alimentar/renacer.
- market: 20 por wallet — listar, comprar, tick buy/sell.
- missions: 10 por wallet — reclamar misiones.

Nota: la clave usa la wallet cuando hay sesión (dependencia
get_current_user) y la IP en auth. La IP se lee de X-Forwarded-For
(Render está detrás de un proxy) o de client.host.
"""
import time
from collections import deque

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer(auto_error=False)

_buckets: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """True si la petición cabe en la ventana; False si supera el límite."""
    now = time.monotonic()
    bucket = _buckets.setdefault(key, deque())
    while bucket and bucket[0] <= now - window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    # Limpieza perezosa: borra cubos vacíos para no crecer sin fin.
    if len(_buckets) > 10000:
        for k in [k for k, b in _buckets.items() if not b]:
            del _buckets[k]
    return True


def _too_many():
    return HTTPException(
        status_code=429,
        detail="Demasiadas peticiones. Espera unos segundos e inténtalo de nuevo.",
        headers={"Retry-After": "60"},
    )


def ip_rate_limit(limit: int, window_seconds: int, scope: str):
    """Dependencia FastAPI: límite por IP (endpoints sin sesión)."""

    async def _dep(request: Request):
        if not allow(f"{scope}:{_client_ip(request)}", limit, window_seconds):
            raise _too_many()

    return _dep


def wallet_rate_limit(limit: int, window_seconds: int, scope: str):
    """Dependencia FastAPI: límite por wallet (endpoints con sesión).
    Decodifica el JWT aquí mismo (sin importar routers.auth, para no
    crear un import circular). Sin JWT válido no limita: es
    get_current_user quien responde 401."""

    async def _dep(
        credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    ):
        if not credentials:
            return
        try:
            from services.auth_service import decode_jwt

            wallet = decode_jwt(credentials.credentials)["sub"]
        except Exception:
            return
        if not allow(f"{scope}:{str(wallet).lower()}", limit, window_seconds):
            raise _too_many()

    return _dep


def wallet_ip_rate_limit(limit: int, window_seconds: int, scope: str):
    """Dependencia FastAPI: límite por wallet+IP (nonce: el wallet viene
    en la ruta y aún no hay sesión)."""

    async def _dep(request: Request, wallet_address: str):
        key = f"{scope}:{wallet_address.lower()}:{_client_ip(request)}"
        if not allow(key, limit, window_seconds):
            raise _too_many()

    return _dep
