"""
Autenticación Web3 — CandleClash
No hay contraseñas. El usuario firma un mensaje con MetaMask.
El servidor verifica la firma y emite un JWT.
"""

import jwt
import secrets
from datetime import datetime, timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from database import get_db
from config import settings


def generate_nonce() -> str:
    """Genera un nonce único para que el usuario lo firme."""
    return secrets.token_hex(16)


# Dominio que se muestra en el mensaje de firma. MetaMask (vía Blockaid)
# marca como sospechosas las firmas de dominios nuevos sin contexto, así
# que el mensaje debe dejar claro QUÉ se está firmando: solo un login,
# gratis y sin mover fondos. El nonce es lo que se verifica; el resto del
# texto es contexto legible para el usuario.
SIGN_MESSAGE_DOMAIN = "candleclash.vercel.app"


def build_login_message(wallet_address: str, nonce: str) -> str:
    """Mensaje exacto que firma el usuario con MetaMask (EIP-191).
    Lo usan tanto /nonce (para mostrarlo) como verify_signature
    (para reconstruirlo): ambos deben coincidir byte a byte."""
    return (
        "CandleClash — Inicio de sesión\n"
        f"Dominio: {SIGN_MESSAGE_DOMAIN}\n"
        "Esta firma es gratuita: no mueve fondos ni autoriza transacciones.\n"
        f"Nonce: {nonce}\n"
        f"Dirección: {wallet_address}"
    )


def verify_signature(wallet_address: str, nonce: str, signature: str) -> bool:
    """
    Verifica que la firma corresponde al wallet_address.
    El usuario firmó el mensaje con MetaMask (EIP-191).
    """
    message = build_login_message(wallet_address, nonce)
    message_hash = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(message_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception:
        return False


def create_jwt(wallet_address: str) -> str:
    """Crea un JWT con expiración de 7 días."""
    payload = {
        "sub":  wallet_address.lower(),
        "iat":  datetime.utcnow(),
        "exp":  datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decodifica y valida el JWT. Lanza excepción si es inválido/expirado."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


async def get_or_create_user(wallet_address: str) -> dict:
    """Retorna el usuario existente o crea uno nuevo."""
    db = get_db()
    wallet = wallet_address.lower()
    user = await db.users.find_one({"wallet_address": wallet})
    if not user:
        user = {
            "wallet_address": wallet,
            "tick_balance":   0,
            "usdt_balance":   0,
            "trophies":       0,
            "league":         "Liga Bronce",
            "wins":           0,
            "losses":         0,
            "energy":         settings.ENERGY_MAX,  # 10: empieza lleno
            "energy_updated_at": datetime.utcnow(),
            "created_at":     datetime.utcnow(),
            "last_login":     datetime.utcnow(),
            "nonce":          generate_nonce(),
        }
        await db.users.insert_one(user)
    else:
        # Renovar nonce en cada login para seguridad
        new_nonce = generate_nonce()
        await db.users.update_one(
            {"wallet_address": wallet},
            {"$set": {"last_login": datetime.utcnow(), "nonce": new_nonce}},
        )
        user["nonce"] = new_nonce
    return user
