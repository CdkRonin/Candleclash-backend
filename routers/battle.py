from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from routers.auth import get_current_user
from services.battle_service import run_battle

router = APIRouter()


class BattleRequest(BaseModel):
    gladiator_id: str   # token_id del NFT que peleará


@router.post("/fight")
async def fight(req: BattleRequest, wallet: str = Depends(get_current_user)):
    """Inicia una batalla automática con matchmaking del servidor."""
    try:
        result = await run_battle(wallet, req.gladiator_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/history")
async def battle_history(wallet: str = Depends(get_current_user)):
    """Últimas 20 batallas del jugador."""
    from database import get_db
    db = get_db()
    cursor = db.battles.find(
        {"player_wallet": wallet},
        {"_id": 0, "log": 0},   # Excluir el log completo para ahorrar payload
    ).sort("timestamp", -1).limit(20)
    return await cursor.to_list(length=20)
