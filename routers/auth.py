from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from services.auth_service import (
    generate_nonce, verify_signature, create_jwt,
    decode_jwt, get_or_create_user,
)
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
        "message": f"CandleClash Login\nNonce: {user['nonce']}\nDirección: {wallet_address}",
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

    token = create_jwt(wallet)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def get_me(wallet: str = Depends(get_current_user)):
    """Retorna el perfil del usuario autenticado."""
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet}, {"_id": 0, "nonce": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user
