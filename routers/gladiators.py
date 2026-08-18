from fastapi import APIRouter, HTTPException, Depends
from routers.auth import get_current_user
from services.oracle_service import OracleService
from database import get_db
from models.gladiator import Gladiator

router = APIRouter()


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
