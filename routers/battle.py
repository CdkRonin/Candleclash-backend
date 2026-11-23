from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from routers.auth import get_current_user
from services.battle_service import run_battle
from services.energy_service import consume_energy
from database import get_db

router = APIRouter()


class BattleRequest(BaseModel):
    gladiator_id: str   # token_id del NFT que peleará


@router.post("/fight")
async def fight(req: BattleRequest, wallet: str = Depends(get_current_user)):
    """Inicia una batalla automática con matchmaking del servidor.

    Cada batalla cuesta 1 energía (tope 10, regenera 1 cada 30 min).
    Sin energía responde 403 con los minutos que faltan. El gladiador
    se valida ANTES de consumir, para no gastar energía en una pelea
    que no puede ocurrir.
    """
    db = get_db()
    nft = await db.gladiators.find_one({
        "token_id": req.gladiator_id,
        "owner_wallet": wallet.lower(),
        "is_burned": False,
    })
    if not nft:
        raise HTTPException(
            status_code=400, detail="Gladiador no encontrado o no te pertenece"
        )
    try:
        energy = await consume_energy(db, wallet)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        result = await run_battle(wallet, req.gladiator_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result["energy"] = energy["energy"]
    result["energy_next_in_min"] = energy["minutes_to_next"]
    return result


@router.get("/history")
async def battle_history(wallet: str = Depends(get_current_user)):
    """Últimas 20 batallas del jugador."""
    db = get_db()
    cursor = db.battles.find(
        {"player_wallet": wallet},
        {"_id": 0, "log": 0},   # Excluir el log completo para ahorrar payload
    ).sort("timestamp", -1).limit(20)
    return await cursor.to_list(length=20)
