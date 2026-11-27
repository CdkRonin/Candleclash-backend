import logging

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from pymongo import ReturnDocument
from services.auth_service import (
    generate_nonce, verify_signature, create_jwt,
    decode_jwt, get_or_create_user, build_login_message,
)
from services.energy_service import refresh_energy
from services.rate_limit import ip_rate_limit, wallet_ip_rate_limit
from services.tick_service import (
    ensure_tick_split, split_tick, limits_remaining,
)
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
bearer = HTTPBearer()


class NonceRequest(BaseModel):
    wallet_address: str


class LoginRequest(BaseModel):
    wallet_address: str
    signature:      str
    nonce:          str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Dependencia: valida JWT y retorna el wallet del usuario."""
    try:
        payload = decode_jwt(credentials.credentials)
        return payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")


@router.get("/nonce/{wallet_address}")
async def get_nonce(
    wallet_address: str,
    _limit=Depends(wallet_ip_rate_limit(10, 60, "nonce")),
):
    """
    Paso 1 del login: el frontend pide un nonce para firmar con MetaMask.
    Límite: 10/min por wallet+IP.
    """
    user = await get_or_create_user(wallet_address)
    return {
        "nonce":   user["nonce"],
        "message": build_login_message(wallet_address, user["nonce"]),
    }


@router.post("/login")
async def login(
    req: LoginRequest,
    _limit=Depends(ip_rate_limit(30, 60, "login")),
):
    """
    Paso 2: el frontend envía la firma, el servidor la verifica y emite JWT.
    Límite: 30/min por IP.

    El consumo del nonce es ATÓMICO: primero se verifica la firma (cálculo
    puro, sin escribir) y luego se rota el nonce en una única actualización
    condicional (solo si el nonce guardado sigue siendo el firmado). Dos
    logins concurrentes con el mismo nonce: el primero rota el nonce y el
    segundo no encuentra el filtro → 400 "Nonce inválido o ya usado".
    Antes la verificación y la rotación eran pasos separados y dos
    peticiones simultáneas podían pasar las dos.
    El flujo no cambia (nonce → firma → JWT) ni el mensaje de firma.
    """
    db = get_db()
    wallet = req.wallet_address.lower()

    if not verify_signature(wallet, req.nonce, req.signature):
        logger.warning("login fallido (firma inválida) wallet=%s", wallet)
        raise HTTPException(status_code=401, detail="Firma inválida")

    updated = await db.users.find_one_and_update(
        {"wallet_address": wallet, "nonce": req.nonce},
        {"$set": {"nonce": generate_nonce()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        # El nonce no coincide o ya lo consumió otro login concurrente.
        raise HTTPException(
            status_code=400, detail="Nonce inválido o ya usado"
        )

    token = create_jwt(wallet)
    logger.info("login ok wallet=%s", wallet)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def get_me(wallet: str = Depends(get_current_user)):
    """Retorna el perfil del usuario autenticado, con su energía
    actualizada (regeneración calculada al leer) y la separación
    $TICK comprado vs ganado con sus límites de venta
    (ver services/tick_service.py)."""
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet}, {"_id": 0, "nonce": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    # Backfill perezoso en BD para usuarios viejos (una sola vez).
    user = await ensure_tick_split(db, wallet, user)
    bought, earned = split_tick(user)
    lim = limits_remaining(user)
    user["tick_bought"] = bought
    user["tick_earned"] = earned
    user["earned_daily_remaining"] = lim["earned_daily_remaining"]
    user["window_remaining"] = lim["window_remaining"]
    energy = await refresh_energy(db, wallet)
    user["energy"] = energy["energy"]
    user["energy_next_in_min"] = energy["minutes_to_next"]
    return user
