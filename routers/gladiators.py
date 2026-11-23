import random
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from routers.auth import get_current_user
from services.oracle_service import OracleService
from database import get_db
from models.gladiator import Gladiator, Rarity
from config import RACE_MODIFIERS, CRYPTO_ATTRIBUTE_MAP

router = APIRouter()


class MintRequest(BaseModel):
    """Raza y linaje opcionales: si no se mandan, se sortean."""
    race:    Optional[str] = None
    lineage: Optional[str] = None


@router.post("/mint")
async def mint_gladiator(req: MintRequest, wallet: str = Depends(get_current_user)):
    """
    Recluta (crea) un gladiador Cobre nivel 1 para el usuario autenticado.
    Raza y linaje: los que mande el frontend si son válidos; si no, aleatorios.
    token_id es un uuid4 hasta que existan los contratos NFT.
    """
    race = req.race or random.choice(list(RACE_MODIFIERS.keys()))
    lineage = req.lineage or random.choice(list(CRYPTO_ATTRIBUTE_MAP.keys()))

    if race not in RACE_MODIFIERS:
        raise HTTPException(status_code=400, detail=f"Raza inválida: {race}")
    if lineage not in CRYPTO_ATTRIBUTE_MAP:
        raise HTTPException(status_code=400, detail=f"Linaje inválido: {lineage}")

    db = get_db()
    g = Gladiator(
        token_id=str(uuid.uuid4()),
        owner_wallet=wallet.lower(),
        race=race,
        lineage=lineage,
        rarity=Rarity.COBRE,
        level=1,
        xp=0,
        rebirths=0,
    )
    doc = g.model_dump()
    await db.gladiators.insert_one(doc)
    doc.pop("_id", None)  # insert_one añade _id (ObjectId) al dict; no se serializa a JSON
    oracle_mults = OracleService.get_cached_multipliers()
    return {**doc, "computed_stats": g.compute_stats(oracle_mults)}


@router.get("/my")
async def get_my_gladiators(wallet: str = Depends(get_current_user)):
    """Retorna todos los NFTs del jugador con stats calculados en tiempo real."""
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()

    cursor = db.gladiators.find(
        {"owner_wallet": wallet, "is_burned": False},
        {"_id": 0},
    )
    docs = await cursor.to_list(length=200)

    result = []
    for doc in docs:
        g = Gladiator(**doc)
        result.append({
            **doc,
            "computed_stats": g.compute_stats(oracle_mults),
        })
    return result


@router.get("/{token_id}")
async def get_gladiator(token_id: str, wallet: str = Depends(get_current_user)):
    """Detalle de un gladiador específico."""
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()
    doc = await db.gladiators.find_one(
        {"token_id": token_id, "owner_wallet": wallet},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Gladiador no encontrado")
    g = Gladiator(**doc)
    return {**doc, "computed_stats": g.compute_stats(oracle_mults)}
