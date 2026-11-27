import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from pymongo import ReturnDocument
from typing import List
from routers.auth import get_current_user
from services.rate_limit import wallet_rate_limit
from services.tick_service import charge_tick, ensure_tick_split, split_tick
from database import get_db
from config import settings
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()

RARITY_ORDER = ["Cobre", "Bronce", "Plata", "Oro", "Diamante", "Legendario"]
RARITY_XP    = {"Cobre": 40, "Bronce": 90, "Plata": 200, "Oro": 420, "Diamante": 850, "Legendario": 1800}


class AscendRequest(BaseModel):
    main_id:     str
    sacrifice_ids: List[str]   # Exactamente 3


class FeedRequest(BaseModel):
    main_id:  str
    food_ids: List[str]        # 1–4 NFTs


class RebirthRequest(BaseModel):
    main_id:      str
    sacrifice_id: str


async def _log_forge_action(db, wallet: str, action: str):
    """Registro mínimo para la misión diaria 'Usar la Forja': el
    servidor verifica con sus propios datos, no con lo que diga el
    cliente. Es un log secundario: si falla no debe romper la forja."""
    try:
        await db.forge_actions.insert_one({
            "wallet_address": wallet,
            "action": action,
            "timestamp": datetime.utcnow(),
        })
    except Exception:
        pass


async def _burn_one(db, wallet: str, token_id: str, extra: dict = None):
    """Quema UN gladiador de forma atómica: solo si sigue siendo del
    jugador, no quemado y cumple el filtro extra (raza/linaje/rareza/
    nivel/listado). Devuelve el doc quemado o None si otra petición
    simultánea ya se lo llevó o cambió su estado. Sin esta condición
    en la escritura, dos forjas simultáneas podían gastar el mismo
    sacrificio dos veces."""
    filt = {"token_id": token_id, "owner_wallet": wallet, "is_burned": False}
    if extra:
        filt.update(extra)
    return await db.gladiators.find_one_and_update(
        filt,
        {"$set": {"is_burned": True, "updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )


async def _unburn(db, token_ids: list):
    """Deshace quemas hechas por esta petición (rollback)."""
    if token_ids:
        await db.gladiators.update_many(
            {"token_id": {"$in": token_ids}},
            {"$set": {"is_burned": False, "updated_at": datetime.utcnow()}},
        )


async def _charge_tick(db, wallet: str, cost: int) -> bool:
    """Cobra $TICK solo si el saldo cubre. True si cobró; False si no
    había saldo (otra petición simultánea se lo llevó primero).

    Delega en services.tick_service.charge_tick: la regla (gastar
    primero el ganado y luego el comprado, con reintento optimista)
    vive en un solo sitio para forja, duelo y coliseo."""
    return await charge_tick(db, wallet, cost) is not None


@router.post("/ascend")
async def ascend(
    req: AscendRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(20, 60, "forge")),
):
    """Funde 4 NFTs idénticos para subir rareza. Cuesta 50 $TICK.

    Atómico por pasos con rollback: cada escritura revalida propiedad
    y estado en el filtro. Si algo falla a mitad, se deshacen las
    quemas y el cambio de rareza; el saldo solo se cobra al final,
    cuando todo lo demás ya está aplicado.
    """
    if len(req.sacrifice_ids) != 3:
        raise HTTPException(status_code=400, detail="Se requieren exactamente 3 sacrificios")
    db = get_db()

    user = await db.users.find_one({"wallet_address": wallet})
    if not user or user.get("tick_balance", 0) < settings.FORGE_ASCEND_COST:
        raise HTTPException(status_code=400, detail=f"Necesitas {settings.FORGE_ASCEND_COST} $TICK")

    # Cargar los 4 NFTs y validar que son del jugador
    all_ids = [req.main_id] + req.sacrifice_ids
    nfts = await db.gladiators.find({
        "token_id": {"$in": all_ids},
        "owner_wallet": wallet,
        "is_burned": False,
        "is_listed": False,
    }).to_list(length=10)

    if len(nfts) != 4:
        raise HTTPException(status_code=400, detail="Uno o más NFTs no válidos")

    main = next(n for n in nfts if n["token_id"] == req.main_id)
    sacs = [n for n in nfts if n["token_id"] in req.sacrifice_ids]

    # Validar que son idénticos
    for s in sacs:
        if s["race"] != main["race"] or s["lineage"] != main["lineage"] or s["rarity"] != main["rarity"]:
            raise HTTPException(status_code=400, detail="Los 4 NFTs deben ser de la misma raza, linaje y rareza")

    current_idx = RARITY_ORDER.index(main["rarity"])
    if current_idx >= len(RARITY_ORDER) - 1:
        raise HTTPException(status_code=400, detail="Ya está en rareza máxima (Legendario)")

    next_rarity = RARITY_ORDER[current_idx + 1]

    # 1) Quemar los 3 sacrificios, uno a uno y con condición.
    burned = []
    for s in sacs:
        got = await _burn_one(db, wallet, s["token_id"], {
            "race": main["race"], "lineage": main["lineage"],
            "rarity": main["rarity"], "is_listed": False,
        })
        if not got:
            await _unburn(db, burned)
            raise HTTPException(
                status_code=400,
                detail="Uno de los sacrificios ya no está disponible",
            )
        burned.append(s["token_id"])

    # 2) Subir la rareza del principal, revalidando su estado.
    main_res = await db.gladiators.update_one(
        {"token_id": req.main_id, "owner_wallet": wallet,
         "is_burned": False, "is_listed": False, "rarity": main["rarity"]},
        {"$set": {"rarity": next_rarity, "updated_at": datetime.utcnow()}},
    )
    if main_res.modified_count == 0:
        await _unburn(db, burned)
        raise HTTPException(
            status_code=400, detail="El gladiador principal cambió de estado"
        )

    # 3) Cobrar al final: si no hay saldo, deshacer todo.
    if not await _charge_tick(db, wallet, settings.FORGE_ASCEND_COST):
        await _unburn(db, burned)
        await db.gladiators.update_one(
            {"token_id": req.main_id},
            {"$set": {"rarity": main["rarity"]}},
        )
        raise HTTPException(
            status_code=400,
            detail=f"Necesitas {settings.FORGE_ASCEND_COST} $TICK",
        )

    await _log_forge_action(db, wallet, "ascend")
    logger.info(
        "forge ascend wallet=%s main=%s %s->%s tick=-%d",
        wallet, req.main_id, main["rarity"], next_rarity,
        settings.FORGE_ASCEND_COST,
    )
    return {"success": True, "new_rarity": next_rarity, "tick_spent": settings.FORGE_ASCEND_COST}


@router.post("/feed")
async def feed(
    req: FeedRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(20, 60, "forge")),
):
    """Sacrifica NFTs como alimento para dar XP al gladiador principal.
    Quemar el alimento y subir el nivel son escrituras condicionales
    con rollback, igual que en ascend."""
    if not req.food_ids or len(req.food_ids) > 4:
        raise HTTPException(status_code=400, detail="1–4 NFTs de alimento")

    db = get_db()
    all_ids = [req.main_id] + req.food_ids
    nfts = await db.gladiators.find({
        "token_id": {"$in": all_ids},
        "owner_wallet": wallet,
        "is_burned": False,
    }).to_list(length=10)

    if len(nfts) != len(all_ids):
        raise HTTPException(status_code=400, detail="Uno o más NFTs no válidos")

    main = next(n for n in nfts if n["token_id"] == req.main_id)
    foods = [n for n in nfts if n["token_id"] in req.food_ids]

    if main["level"] >= 50:
        raise HTTPException(status_code=400, detail="Nivel máximo alcanzado. Usa Renacimiento.")

    total_xp = sum(
        int(RARITY_XP.get(f["rarity"], 40) * max(1, f["level"] * 0.6))
        for f in foods
    )

    # Calcular nuevo nivel
    new_xp = main["xp"] + total_xp
    new_level = main["level"]
    while new_level < 50:
        need = int(80 * (new_level ** 1.35))
        if new_xp < need:
            break
        new_xp -= need
        new_level += 1

    burned = []
    for f in foods:
        got = await _burn_one(db, wallet, f["token_id"])
        if not got:
            await _unburn(db, burned)
            raise HTTPException(
                status_code=400,
                detail="Uno de los alimentos ya no está disponible",
            )
        burned.append(f["token_id"])

    main_res = await db.gladiators.update_one(
        {"token_id": req.main_id, "owner_wallet": wallet,
         "is_burned": False, "level": main["level"], "xp": main["xp"]},
        {"$set": {"xp": new_xp, "level": new_level,
                  "updated_at": datetime.utcnow()}},
    )
    if main_res.modified_count == 0:
        await _unburn(db, burned)
        raise HTTPException(
            status_code=400, detail="El gladiador principal cambió de estado"
        )

    await _log_forge_action(db, wallet, "feed")
    logger.info(
        "forge feed wallet=%s main=%s xp=+%d level=%d->%d",
        wallet, req.main_id, total_xp, main["level"], new_level,
    )
    return {"success": True, "xp_gained": total_xp, "new_level": new_level, "new_xp": new_xp}


@router.post("/rebirth")
async def rebirth(
    req: RebirthRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(20, 60, "forge")),
):
    """Renacimiento: NFT nivel 50 sacrifica clon idéntico nivel 50. Vuelve a Lv1 con +50% stats.
    Igual que ascend: quemar, renacer y cobrar son pasos condicionales
    con rollback; el cobro va al final."""
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet})
    if not user or user.get("tick_balance", 0) < settings.REBIRTH_COST:
        raise HTTPException(status_code=400, detail=f"Necesitas {settings.REBIRTH_COST} $TICK")

    nfts = await db.gladiators.find({
        "token_id": {"$in": [req.main_id, req.sacrifice_id]},
        "owner_wallet": wallet,
        "is_burned": False,
    }).to_list(length=5)

    if len(nfts) != 2:
        raise HTTPException(status_code=400, detail="NFTs no válidos")

    main = next(n for n in nfts if n["token_id"] == req.main_id)
    sac  = next(n for n in nfts if n["token_id"] == req.sacrifice_id)

    if main["level"] < 50 or sac["level"] < 50:
        raise HTTPException(status_code=400, detail="Ambos NFTs deben estar al Nivel 50")
    if main["race"] != sac["race"] or main["lineage"] != sac["lineage"]:
        raise HTTPException(status_code=400, detail="Los NFTs deben ser de la misma raza y linaje")
    if main.get("rebirths", 0) >= 5:
        raise HTTPException(status_code=400, detail="Máximo de renacimientos (5) alcanzado")

    new_rebirths = main.get("rebirths", 0) + 1

    # 1) Quemar el clon (Lv50, misma raza/linaje, del jugador).
    got = await _burn_one(db, wallet, req.sacrifice_id, {
        "level": 50, "race": main["race"], "lineage": main["lineage"],
    })
    if not got:
        raise HTTPException(
            status_code=400, detail="El clon ya no está disponible"
        )

    # 2) Renacer el principal, revalidando nivel y renacimientos.
    main_res = await db.gladiators.update_one(
        {"token_id": req.main_id, "owner_wallet": wallet,
         "is_burned": False, "level": 50, "rebirths": main.get("rebirths", 0)},
        {"$set": {"level": 1, "xp": 0, "rebirths": new_rebirths,
                  "updated_at": datetime.utcnow()}},
    )
    if main_res.modified_count == 0:
        await _unburn(db, [req.sacrifice_id])
        raise HTTPException(
            status_code=400, detail="El gladiador principal cambió de estado"
        )

    # 3) Cobrar al final; si no hay saldo, deshacer todo.
    if not await _charge_tick(db, wallet, settings.REBIRTH_COST):
        await _unburn(db, [req.sacrifice_id])
        await db.gladiators.update_one(
            {"token_id": req.main_id},
            {"$set": {"level": 50, "xp": main["xp"],
                      "rebirths": main.get("rebirths", 0)}},
        )
        raise HTTPException(
            status_code=400,
            detail=f"Necesitas {settings.REBIRTH_COST} $TICK",
        )

    await _log_forge_action(db, wallet, "rebirth")
    logger.info(
        "forge rebirth wallet=%s main=%s rebirths=%d tick=-%d",
        wallet, req.main_id, new_rebirths, settings.REBIRTH_COST,
    )
    return {
        "success":     True,
        "rebirths":    new_rebirths,
        "stat_bonus_pct": new_rebirths * 50,
        "tick_spent":  settings.REBIRTH_COST,
    }
