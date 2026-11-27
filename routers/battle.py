from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from routers.auth import get_current_user
from services.battle_service import run_battle, NoEnergyError
from services.duel_service import start_duel, act_duel, get_duel
from services.rate_limit import wallet_rate_limit
from database import get_db

router = APIRouter()


class BattleRequest(BaseModel):
    gladiator_id: str   # token_id del NFT que peleará


class DuelStartRequest(BaseModel):
    gladiator_id: str   # token_id del NFT que peleará
    paid: bool = False  # Duelo con entrada: 10 $TICK, ganador 18


class DuelActRequest(BaseModel):
    duel_id: str
    action: str   # attack | ability | defend


@router.post("/fight")
async def fight(
    req: BattleRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(30, 60, "fight")),
):
    """Inicia una batalla automática con matchmaking del servidor.

    Cada batalla cuesta 1 energía (tope 10, regenera 1 cada 30 min).
    Sin energía responde 403 con los minutos que faltan. El gladiador
    se valida ANTES de simular, para no gastar energía en una pelea
    que no puede ocurrir. La energía y las recompensas se aplican en
    UNA sola actualización atómica dentro de run_battle: si la pelea
    falla, no se pierde energía en vano. Límite: 30/min por wallet.
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
        result = await run_battle(wallet, req.gladiator_id)
    except NoEnergyError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/duel/start")
async def duel_start(
    req: DuelStartRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(5, 60, "duel_start")),
):
    """Crea un duelo táctico (por turnos) con matchmaking del servidor.

    Cuesta 1 energía, como la batalla automática. Sin energía responde
    403. Devuelve el estado inicial del duelo y el oponente.
    Límite: 5/min por wallet.

    Con paid=true es un duelo con entrada (decisión 2026-11-26):
    cobra 10 $TICK al empezar (sin saldo responde 400 y no se crea
    nada); si ganas recibes 18 $TICK, si empatas se te devuelven los
    10 y si pierdes no recibes nada. El premio sustituye al premio de
    batalla de 8–30 (no se suma).
    """
    try:
        return await start_duel(wallet, req.gladiator_id, paid=req.paid)
    except NoEnergyError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/duel/act")
async def duel_act(
    req: DuelActRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(30, 60, "duel_act")),
):
    """Resuelve una ronda del duelo con la acción elegida por el jugador
    (attack | ability | defend). El rival responde con su IA y el
    servidor devuelve el log de la ronda y el estado nuevo. Si la
    acción no se puede hacer (habilidad en enfriamiento o cura con la
    vida llena) responde 400 y la ronda no se consume.
    Límite: 30/min por wallet.
    """
    try:
        return await act_duel(wallet, req.duel_id, req.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/duel/{duel_id}")
async def duel_state(
    duel_id: str,
    wallet: str = Depends(get_current_user),
):
    """Estado actual de un duelo del jugador (para reconectar)."""
    try:
        return await get_duel(wallet, duel_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/history")
async def battle_history(wallet: str = Depends(get_current_user)):
    """Últimas 20 batallas del jugador."""
    db = get_db()
    cursor = db.battles.find(
        {"player_wallet": wallet},
        {"_id": 0, "log": 0},   # Excluir el log completo para ahorrar payload
    ).sort("timestamp", -1).limit(20)
    return await cursor.to_list(length=20)
