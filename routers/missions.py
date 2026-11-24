"""Misiones diarias — CandleClash.

El problema que arregla este router: los premios de misión vivían
solo en el frontend (localStorage) y el siguiente sync con el
servidor (que trae los saldos reales de /api/auth/me) los
sobrescribía: el premio se perdía. Ahora el premio se acredita en
el servidor con $inc atómico sobre tick_balance, y el frontend ya
no suma $TICK local por misiones cuando hay sesión.

Reglas:
- Día UTC (igual que el resto del servidor, que usa datetime.utcnow()).
- Un reclamo por misión por día por wallet. La colección
  mission_claims tiene índice único (wallet_address, mission_id,
  date): el reclamo se registra ANTES de acreditar, así un doble
  reclamo no puede pagar dos veces (el segundo recibe 409).
- Verificación con datos del servidor, sin confiar en el cliente:
  'battles' cuenta victorias de hoy en la colección battles;
  'forge' cuenta acciones de forja de hoy en forge_actions
  (ascend/feed/rebirth registran ahí al tener éxito).
  'boxes' NO se puede verificar: las cajas Básica/Escuadrón/
  Batallón siguen siendo DEMO (no hay endpoints de cajas), así que
  su premio se reclama una vez al día por confianza (beta) y se
  verificará cuando las cajas sean reales. Eso queda marcado en
  la respuesta (verified: false + note) y en la UI.
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError
from routers.auth import get_current_user
from database import get_db
from config import settings

router = APIRouter()


def _today_str() -> str:
    return datetime.utcnow().date().isoformat()


def _day_start() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


async def _progress(db, wallet: str, mission_id: str):
    """Progreso de hoy verificado con datos del servidor.
    Devuelve (progress, completed). Para 'boxes' progress es None:
    no hay nada que verificar (cajas demo)."""
    start = _day_start()
    if mission_id == "battles":
        wins = await db.battles.count_documents({
            "player_wallet": wallet,
            "timestamp": {"$gte": start},
            "result.winner": "p1",
        })
        return wins, wins >= settings.MISSIONS["battles"]["target"]
    if mission_id == "forge":
        n = await db.forge_actions.count_documents({
            "wallet_address": wallet,
            "timestamp": {"$gte": start},
        })
        return n, n >= settings.MISSIONS["forge"]["target"]
    if mission_id == "boxes":
        return None, False
    return 0, False


@router.get("/status")
async def mission_status(wallet: str = Depends(get_current_user)):
    """Estado de las misiones de hoy para esta wallet: progreso
    verificado, si ya se reclamó y el premio de cada una."""
    db = get_db()
    date = _today_str()
    out = []
    for mid, m in settings.MISSIONS.items():
        progress, completed = await _progress(db, wallet, mid)
        claimed = await db.mission_claims.find_one({
            "wallet_address": wallet, "mission_id": mid, "date": date,
        })
        item = {
            "mission_id": mid,
            "label":      m["label"],
            "target":     m["target"],
            "reward":     m["reward"],
            "progress":   progress,
            "completed":  completed,
            "claimed":    bool(claimed),
            "verified":   m["verified"],
        }
        if "note" in m:
            item["note"] = m["note"]
        out.append(item)
    return {"date": date, "missions": out}


class ClaimRequest(BaseModel):
    mission_id: str


@router.post("/claim")
async def claim_mission(req: ClaimRequest, wallet: str = Depends(get_current_user)):
    """Reclama el premio de una misión completada hoy. Acredita el
    $TICK al saldo del servidor (emisión del juego, igual que los
    premios de batalla). Una sola vez por misión por día."""
    m = settings.MISSIONS.get(req.mission_id)
    if not m:
        raise HTTPException(status_code=400, detail="Misión no válida")
    db = get_db()
    date = _today_str()

    # Verificación con datos del servidor (salvo 'boxes': beta por
    # confianza, las cajas aún son demo y no hay nada que verificar).
    progress, completed = await _progress(db, wallet, req.mission_id)
    if m["verified"] and not completed:
        if req.mission_id == "battles":
            detail = (f"Aún no completas esta misión: "
                      f"llevas {progress}/{m['target']} victorias hoy")
        else:
            detail = "Aún no completas esta misión: usa la Forja hoy"
        raise HTTPException(status_code=400, detail=detail)

    # Reclamo atómico: el índice único impide dos reclamos el mismo día.
    try:
        await db.mission_claims.insert_one({
            "wallet_address": wallet,
            "mission_id":     req.mission_id,
            "date":           date,
            "reward":         m["reward"],
            "claimed_at":     datetime.utcnow(),
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Ya reclamaste esta misión hoy")

    await db.users.update_one(
        {"wallet_address": wallet},
        {"$inc": {"tick_balance": m["reward"]}},
    )
    return {
        "success":      True,
        "mission_id":   req.mission_id,
        "tick_awarded": m["reward"],
        "date":         date,
    }
