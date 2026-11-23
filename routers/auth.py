from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from services.auth_service import (
    generate_nonce, verify_signature, create_jwt,
    decode_jwt, get_or_create_user, build_login_message,
)
from services.energy_service import refresh_energy
from database import get_db

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
async def get_nonce(wallet_address: str):
    """
    Paso 1 del login: el frontend pide un nonce para firmar con MetaMask.
    """
    user = await get_or_create_user(wallet_address)
    return {
        "nonce":   user["nonce"],
        "message": build_login_message(wallet_address, user["nonce"]),
    }


@router.post("/login")
async def login(req: LoginRequest):
    """
    Paso 2: el frontend envía la firma, el servidor la verifica y emite JWT.
    """
    db = get_db()
    wallet = req.wallet_address.lower()

    # Verificar que el nonce coincide con el que se emitió
    user = await db.users.find_one({"wallet_address": wallet})
    if not user or user.get("nonce") != req.nonce:
        raise HTTPException(status_code=400, detail="Nonce inválido")

    if not verify_signature(wallet, req.nonce, req.signature):
        raise HTTPException(status_code=401, detail="Firma inválida")

    # Invalidar el nonce usado: una firma capturada no se puede
    # reutilizar para sacar otro JWT (el próximo /nonce genera uno nuevo).
    await db.users.update_one(
        {"wallet_address": wallet},
        {"$set": {"nonce": generate_nonce()}},
    )

    token = create_jwt(wallet)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def get_me(wallet: str = Depends(get_current_user)):
    """Retorna el perfil del usuario autenticado, con su energía
    actualizada (regeneración calculada al leer)."""
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet}, {"_id": 0, "nonce": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    energy = await refresh_energy(db, wallet)
    user["energy"] = energy["energy"]
    user["energy_next_in_min"] = energy["minutes_to_next"]
    return user
