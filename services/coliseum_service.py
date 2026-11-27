"""
Coliseo táctico — CandleClash
Evento por tabla de posiciones (decisión 2026-11-26): un solo coliseo
activo a la vez, 24 h de duración, cupo de 50 participantes, entrada
de 10 $TICK. Cada victoria en duelo táctico (gratis o pagado) mientras
el jugador está inscrito suma 1 punto. Al cerrar, el pozo (10 $TICK
por inscrito) pierde el 10% para la casa y el resto se reparte entre
el top 10 con las fracciones de config.COLISEUM_PRIZE_SHARES (para 50
jugadores el pozo de premios es 450 y da 120/80/60/50/40/30/20/20/20/10).

El cierre se materializa al leer o entrar (sin jobs en segundo plano,
mismo patrón que la regeneración de energía): se cierra al llenarse
(50 inscritos) o al vencer las 24 h. Solo los inscritos con al menos
1 punto cobran premio; los puestos vacíos y el redondeo quedan en la
casa. Los empates en puntos se desempatan por victorias totales de la
carrera (users.wins) y luego por fecha de inscripción más temprana.

Atomicidad: el cierre se reclama con find_one_and_update (active →
closing) para que solo un proceso pague; los premios se acreditan a
tick_earned con $inc por ganador y quedan marcados como pagados en el
documento, así que un cierre interrumpido se puede reanudar sin pagar
dos veces (mismo compromiso que las recompensas del duelo: la ventana
entre el $inc y la marca es de milisegundos dentro de una petición
síncrona).
"""

import logging
import uuid
from datetime import datetime, timedelta

from pymongo.errors import DuplicateKeyError

from config import settings
from services.tick_service import charge_tick, refund_tick
from database import get_db

logger = logging.getLogger(__name__)

DURATION = timedelta(seconds=settings.COLISEUM_DURATION_SECONDS)
MAX_PLAYERS = settings.COLISEUM_MAX_PLAYERS
ENTRY_TICK = settings.COLISEUM_ENTRY_TICK
HOUSE_PCT = settings.COLISEUM_HOUSE_PCT
PRIZE_SHARES = settings.COLISEUM_PRIZE_SHARES  # [(num, den)] x10


def _now():
    return datetime.utcnow()


def public_coliseum(col: dict, my_wallet: str = None,
                    career: dict = None) -> dict:
    """Vista pública del coliseo (sin _id de Mongo). La tabla trae el
    top 10 por puntos y, si el jugador está inscrito fuera del top 10,
    su puesto también. El desempate es el mismo que en la tabla final
    (_compute_standings): más victorias de carrera, luego inscripción
    más temprana, para que la vista en vivo nunca ordene distinto que
    el resultado que paga."""
    career = career or {}
    parts = sorted(
        col.get("participants", []),
        key=lambda p: (-p.get("points", 0),
                       -career.get(p["wallet"], 0),
                       p.get("entered_at") or datetime.min),
    )
    standings = []
    for i, p in enumerate(parts):
        standings.append({
            "rank": i + 1,
            "wallet": p["wallet"],
            "points": p.get("points", 0),
            "wins": p.get("wins", 0),
            "prize": 0,  # Solo hay premio cuando está cerrado (abajo).
        })
    # Los premios solo existen cuando está cerrado (final_standings).
    final = {s["wallet"]: s for s in (col.get("final_standings") or [])}
    for s in standings:
        s["prize"] = final.get(s["wallet"], {}).get("prize", 0) \
            if col.get("status") == "closed" else 0
    out = {
        "coliseum_id": col["coliseum_id"],
        "status": col["status"],
        "created_at": col["created_at"].isoformat(),
        "ends_at": col["ends_at"].isoformat(),
        "seconds_left": max(0, int((col["ends_at"] - _now()).total_seconds()))
        if col.get("status") == "active" else 0,
        "max_players": col.get("max_players", MAX_PLAYERS),
        "entry_tick": col.get("entry_tick", ENTRY_TICK),
        "players": len(col.get("participants", [])),
        "standings": standings[:10],
        "prize_pool": col.get("prize_pool", 0),
        "house_cut": col.get("house_cut", 0),
        "closed_reason": col.get("closed_reason"),
    }
    if my_wallet:
        mine = next((s for s in standings
                     if s["wallet"] == my_wallet.lower()), None)
        out["my_rank"] = mine["rank"] if mine else None
        out["my_points"] = mine["points"] if mine else 0
        out["enrolled"] = mine is not None
    return out


async def _fetch_career_wins(db, wallets: list) -> dict:
    """Victorias de carrera (users.wins) por wallet, para el
    desempate de la tabla (mismo criterio en vivo y al cerrar)."""
    career = {}
    if wallets:
        async for u in db.users.find(
                {"wallet_address": {"$in": wallets}},
                {"wallet_address": 1, "wins": 1}):
            career[u["wallet_address"]] = int(u.get("wins", 0))
    return career


async def _compute_standings(db, col: dict) -> list:
    """Tabla final ordenada con el premio de cada puesto. Solo cobran
    los inscritos con al menos 1 punto; los puestos vacíos no se
    pagan (su parte queda en la casa)."""
    parts = list(col.get("participants", []))
    wallets = [p["wallet"] for p in parts]
    career = await _fetch_career_wins(db, wallets)
    parts.sort(key=lambda p: (-p.get("points", 0),
                              -career.get(p["wallet"], 0),
                              p.get("entered_at") or datetime.min))
    pot = ENTRY_TICK * len(parts)
    house = int(pot * HOUSE_PCT)
    pool = pot - house
    standings = []
    for i, p in enumerate(parts):
        prize = 0
        if i < len(PRIZE_SHARES) and p.get("points", 0) > 0:
            num, den = PRIZE_SHARES[i]
            prize = pool * num // den
        standings.append({
            "rank": i + 1,
            "wallet": p["wallet"],
            "points": p.get("points", 0),
            "wins": p.get("wins", 0),
            "prize": prize,
            "paid": False,
        })
    return standings, pot, house, pool


async def _pay_standings(db, col: dict):
    """Paga los premios pendientes de la tabla final y los marca como
    pagados en el documento. Idempotente: solo paga los que aún tienen
    paid=False."""
    standings = col.get("final_standings") or []
    for s in standings:
        if s.get("paid") or not s.get("prize"):
            continue
        await db.users.update_one(
            {"wallet_address": s["wallet"]},
            {"$inc": {"tick_balance": s["prize"],
                      "tick_earned": s["prize"]}},
        )
        await db.coliseums.update_one(
            {"coliseum_id": col["coliseum_id"],
             "final_standings.wallet": s["wallet"]},
            {"$set": {"final_standings.$.paid": True}},
        )
        logger.info("coliseo %s premio puesto %d wallet=%s tick=+%d",
                    col["coliseum_id"], s["rank"], s["wallet"], s["prize"])


async def close_coliseum(db, col: dict, reason: str) -> dict:
    """Cierra un coliseo activo (reclamo atómico active → closing),
    calcula la tabla final, paga los premios y lo marca cerrado.
    Si ya está en 'closing' (un cierre anterior se interrumpió),
    reanuda el pago con la tabla ya guardada. Devuelve el documento
    actualizado (o el que hay, si otro proceso lo cerró primero)."""
    if col.get("status") == "closing":
        # Reanudar: pagar lo pendiente y finalizar.
        await _pay_standings(db, col)
        await db.coliseums.update_one(
            {"coliseum_id": col["coliseum_id"], "status": "closing"},
            {"$set": {"status": "closed",
                      "closed_at": col.get("closed_at") or _now()}},
        )
        return await db.coliseums.find_one(
            {"coliseum_id": col["coliseum_id"]})
    if col.get("status") != "active":
        return col
    standings, pot, house, pool = await _compute_standings(db, col)
    claimed = await db.coliseums.find_one_and_update(
        {"coliseum_id": col["coliseum_id"], "status": "active"},
        {"$set": {"status": "closing",
                  "closed_reason": reason,
                  "closed_at": _now(),
                  "pot": pot,
                  "house_cut": house,
                  "prize_pool": pool,
                  "final_standings": standings}},
    )
    if not claimed:
        # Otro proceso lo cerró primero.
        return await db.coliseums.find_one(
            {"coliseum_id": col["coliseum_id"]})
    col = await db.coliseums.find_one({"coliseum_id": col["coliseum_id"]})
    await _pay_standings(db, col)
    await db.coliseums.update_one(
        {"coliseum_id": col["coliseum_id"], "status": "closing"},
        {"$set": {"status": "closed"}},
    )
    logger.info("coliseo %s cerrado (%s) inscritos=%d pozo=%d premios=%d",
                col["coliseum_id"], reason, len(col.get("participants", [])),
                pot, pool)
    return await db.coliseums.find_one({"coliseum_id": col["coliseum_id"]})


async def get_or_create_active(db) -> dict:
    """Coliseo activo actual: si el que hay venció, se cierra (con
    pago de premios) y se crea uno nuevo. Un solo activo a la vez:
    lo garantiza el índice único parcial sobre status='active'."""
    col = await db.coliseums.find_one(
        {"status": {"$in": ["active", "closing"]}},
        sort=[("created_at", -1)])
    if col and col.get("status") == "closing":
        col = await close_coliseum(db, col, col.get("closed_reason") or "expired")
    if col and col.get("status") == "active":
        if col["ends_at"] <= _now():
            col = await close_coliseum(db, col, "expired")
        else:
            return col
    now = _now()
    new = {
        "coliseum_id": uuid.uuid4().hex,
        "status": "active",
        "created_at": now,
        "ends_at": now + DURATION,
        "max_players": MAX_PLAYERS,
        "entry_tick": ENTRY_TICK,
        "participants": [],
        "pot": 0,
        "house_cut": 0,
        "prize_pool": 0,
        "final_standings": [],
        "closed_reason": None,
        "closed_at": None,
    }
    try:
        await db.coliseums.insert_one(new)
    except DuplicateKeyError:
        # Otro proceso creó el activo entre la lectura y la inserción.
        existing = await db.coliseums.find_one(
            {"status": "active"}, sort=[("created_at", -1)])
        if existing:
            return existing
        raise
    logger.info("coliseo %s creado, cierra %s",
                new["coliseum_id"], new["ends_at"].isoformat())
    return new


async def enter_coliseum(db, wallet: str, gladiator_id: str) -> dict:
    """Inscribe al jugador en el coliseo activo (10 $TICK, ganado
    primero). La inscripción es atómica: si el coliseo se llenó entre
    el cobro y la inscripción, se devuelve la entrada. Si al entrar
    se llega a 50 inscritos, el coliseo se cierra en el acto."""
    wallet = wallet.lower()
    nft = await db.gladiators.find_one({
        "token_id": gladiator_id,
        "owner_wallet": wallet,
        "is_burned": False,
    })
    if not nft:
        raise ValueError("Gladiador no encontrado o no te pertenece")
    col = await get_or_create_active(db)
    if any(p["wallet"] == wallet for p in col.get("participants", [])):
        raise ValueError("Ya estás inscrito en este coliseo")
    if len(col.get("participants", [])) >= MAX_PLAYERS:
        raise ValueError("El coliseo está lleno")
    charged = await charge_tick(db, wallet, ENTRY_TICK)
    if charged is None:
        raise ValueError(f"Necesitas {ENTRY_TICK} $TICK para entrar al coliseo")
    participant = {
        "wallet": wallet,
        "gladiator_id": gladiator_id,
        "points": 0,
        "wins": 0,
        "entered_at": _now(),
    }
    applied = await db.coliseums.update_one(
        {"coliseum_id": col["coliseum_id"],
         "status": "active",
         "participants.wallet": {"$ne": wallet},
         f"participants.{MAX_PLAYERS - 1}": {"$exists": False}},
        {"$push": {"participants": participant}},
    )
    if applied.modified_count == 0:
        # Se llenó o se inscribió otro igual al mismo tiempo: devolver.
        await refund_tick(db, wallet, charged["from_earned"],
                          charged["from_bought"])
        fresh = await db.coliseums.find_one(
            {"coliseum_id": col["coliseum_id"]})
        if fresh and any(p["wallet"] == wallet
                         for p in fresh.get("participants", [])):
            raise ValueError("Ya estás inscrito en este coliseo")
        raise ValueError("El coliseo está lleno")
    logger.info("coliseo %s entrada wallet=%s tick=-10",
                col["coliseum_id"], wallet)
    col = await db.coliseums.find_one({"coliseum_id": col["coliseum_id"]})
    if len(col.get("participants", [])) >= MAX_PLAYERS:
        col = await close_coliseum(db, col, "full")
    return col


async def record_win(db, wallet: str) -> bool:
    """Suma 1 punto (y 1 victoria) al inscrito en el coliseo activo.
    Se llama desde las recompensas del duelo, que ya corren una sola
    vez por duelo ganado (concurrencia optimista): el punto no se
    puede acreditar dos veces. No crea coliseo si no hay uno activo
    ni falla si el jugador no está inscrito."""
    res = await db.coliseums.update_one(
        {"status": "active", "participants.wallet": wallet.lower()},
        {"$inc": {"participants.$.points": 1,
                  "participants.$.wins": 1}},
    )
    return res.modified_count == 1


async def get_active_public(db, my_wallet: str = None) -> dict:
    """Coliseo activo para mostrar (lo crea si no hay ninguno)."""
    col = await get_or_create_active(db)
    career = await _fetch_career_wins(
        db, [p["wallet"] for p in col.get("participants", [])])
    return public_coliseum(col, my_wallet, career)


async def get_coliseum_public(db, coliseum_id: str,
                              my_wallet: str = None) -> dict:
    """Un coliseo concreto (activo o cerrado con su tabla final)."""
    col = await db.coliseums.find_one({"coliseum_id": coliseum_id})
    if not col:
        raise ValueError("Coliseo no encontrado")
    if col.get("status") == "active" and col["ends_at"] <= _now():
        col = await close_coliseum(db, col, "expired")
    career = await _fetch_career_wins(
        db, [p["wallet"] for p in col.get("participants", [])])
    return public_coliseum(col, my_wallet, career)
