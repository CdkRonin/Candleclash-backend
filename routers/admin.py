"""Endpoint TEMPORAL de un solo uso (2026-11-25): crea en una wallet los
gladiadores necesarios para probar las 3 ventanas de la Forja
(Ascender, Subir de nivel, Renacer) sin tener que subirlos a mano.

Se BORRA tras usarlo en producción, junto con la variable
ADMIN_GRANT_TOKEN de Render: no es parte del juego.

Seguridad: exige el header X-Admin-Token igual a la variable de
entorno ADMIN_GRANT_TOKEN. Si la variable está vacía, el endpoint no
existe (404). El token solo vive en Render, nunca en el repo.
"""
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import settings
from database import get_db
from models.gladiator import Gladiator, Rarity

logger = logging.getLogger(__name__)

router = APIRouter()

# Sets de prueba. Todos los nombres empiezan por "Prueba " para
# encontrarlos en Barracas y para que una segunda llamada no duplique
# el set (409 si ya existe alguno sin quemar).
GRANT_SETS = [
    # Ascender: 4 idénticos (misma raza, linaje y rareza) Cobre Lv1.
    *[{"race": "Orco", "lineage": "BTC", "rarity": Rarity.COBRE,
       "level": 1, "name": f"Prueba Ascenso {i}"} for i in (1, 2, 3, 4)],
    # Subir de nivel: 1 principal + 4 alimentos (cualquiera vale).
    {"race": "Humano", "lineage": "ETH", "rarity": Rarity.COBRE,
     "level": 1, "name": "Prueba Nivel"},
    *[{"race": "Goblin", "lineage": "SOL", "rarity": Rarity.COBRE,
       "level": 1, "name": f"Prueba Alimento {i}"} for i in (1, 2, 3, 4)],
    # Renacer: 2 de Lv50, misma raza y linaje. La rareza NO tiene que
    # coincidir: el principal es Cobre y el sacrificio Bronce.
    {"race": "Dracónido", "lineage": "ETH", "rarity": Rarity.COBRE,
     "level": 50, "name": "Prueba Renacer"},
    {"race": "Dracónido", "lineage": "ETH", "rarity": Rarity.BRONCE,
     "level": 50, "name": "Prueba Renacer 2"},
]
# $TICK para probar: ascender 50 + renacer 100, con margen para repetir.
TICK_TOPUP_TO = 200


class GrantRequest(BaseModel):
    wallet: str


@router.post("/grant-forge-test")
async def grant_forge_test(
    req: GrantRequest, x_admin_token: str = Header(default="")
):
    """Crea el set de prueba de la Forja en la wallet indicada."""
    if not settings.ADMIN_GRANT_TOKEN or x_admin_token != settings.ADMIN_GRANT_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    wallet = req.wallet.strip().lower()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(status_code=400, detail="Wallet inválida")
    db = get_db()

    existing = await db.gladiators.count_documents(
        {"owner_wallet": wallet, "name": {"$regex": "^Prueba "},
         "is_burned": False}
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="El set de prueba ya existe en esta wallet",
        )

    docs = []
    for spec in GRANT_SETS:
        g = Gladiator(token_id=str(uuid.uuid4()), owner_wallet=wallet, **spec)
        docs.append(g.model_dump())
    await db.gladiators.insert_many(docs)

    # $TICK para probar. $max solo sube el saldo, nunca lo baja; si el
    # usuario no existe se crea con el límite de reclutas ya gastado
    # (2), para no regalar huecos de mint gratis.
    await db.users.update_one(
        {"wallet_address": wallet},
        {"$setOnInsert": {"wallet_address": wallet, "free_mints_used": 2},
         "$max": {"tick_balance": TICK_TOPUP_TO}},
        upsert=True,
    )
    logger.info(
        "admin grant-forge-test wallet=%s creados=%d tick_to=%d",
        wallet, len(docs), TICK_TOPUP_TO,
    )
    return {"success": True, "created": len(docs),
            "token_ids": [d["token_id"] for d in docs],
            "tick_balance_min": TICK_TOPUP_TO}
