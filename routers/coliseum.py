"""Coliseo táctico: evento de 24 h, cupo 50, entrada 10 $TICK,
premio al top 10. Ver services/coliseum_service.py."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from routers.auth import get_current_user
from services.rate_limit import wallet_rate_limit
from services.coliseum_service import (
    enter_coliseum,
    get_active_public,
    get_coliseum_public,
)
from database import get_db

router = APIRouter()


class ColiseumEnterRequest(BaseModel):
    gladiator_id: str   # token_id del NFT con el que participas


@router.post("/enter")
async def coliseum_enter(
    req: ColiseumEnterRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(10, 60, "coliseum_enter")),
):
    """Inscribe al jugador en el coliseo activo (10 $TICK, ganado
    primero, igual que la forja). Si ya está inscrito o el coliseo
    está lleno responde 409; sin saldo responde 400. La entrada no
    se devuelve si el jugador no juega. Si al entrar se llega a 50
    inscritos, el coliseo se cierra en el acto y se pagan los premios.
    Límite: 10/min por wallet."""
    db = get_db()
    try:
        col = await enter_coliseum(db, wallet, req.gladiator_id)
    except ValueError as e:
        msg = str(e)
        if "Ya estás inscrito" in msg or "lleno" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    from services.coliseum_service import (
        public_coliseum,
        _fetch_career_wins,
    )
    career = await _fetch_career_wins(
        db, [p["wallet"] for p in col.get("participants", [])])
    return public_coliseum(col, wallet, career)


@router.get("/active")
async def coliseum_active(wallet: str = Depends(get_current_user)):
    """Coliseo activo actual (se crea uno si no hay ninguno). Trae el
    estado, los inscritos, el tiempo restante y la tabla del top 10,
    además del puesto y los puntos del jugador si está inscrito."""
    db = get_db()
    return await get_active_public(db, wallet)


@router.get("/{coliseum_id}")
async def coliseum_state(
    coliseum_id: str,
    wallet: str = Depends(get_current_user),
):
    """Un coliseo concreto: si está activo trae la tabla en vivo; si
    está cerrado trae la tabla final con los premios pagados."""
    db = get_db()
    try:
        return await get_coliseum_public(db, coliseum_id, wallet)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
