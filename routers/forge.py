from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from routers.auth import get_current_user
from database import get_db
from config import settings
from datetime import datetime

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


@router.post("/ascend")
async def ascend(req: AscendRequest, wallet: str = Depends(get_current_user)):
    """Funde 4 NFTs idénticos para subir rareza. Cuesta 50 $TICK."""
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

    # Ejecutar
    await db.users.update_one({"wallet_address": wallet},
        {"$inc": {"tick_balance": -settings.FORGE_ASCEND_COST}})
    await db.gladiators.update_many(
        {"token_id": {"$in": req.sacrifice_ids}},
        {"$set": {"is_burned": True, "updated_at": datetime.utcnow()}},
    )
    await db.gladiators.update_one(
        {"token_id": req.main_id},
        {"$set": {"rarity": next_rarity, "updated_at": datetime.utcnow()}},
    )
    return {"success": True, "new_rarity": next_rarity, "tick_spent": settings.FORGE_ASCEND_COST}


@router.post("/feed")
async def feed(req: FeedRequest, wallet: str = Depends(get_current_user)):
    """Sacrifica NFTs como alimento para dar XP al gladiador principal."""
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

    await db.gladiators.update_many(
        {"token_id": {"$in": req.food_ids}},
        {"$set": {"is_burned": True, "updated_at": datetime.utcnow()}},
    )
    await db.gladiators.update_one(
        {"token_id": req.main_id},
        {"$set": {"xp": new_xp, "level": new_level, "updated_at": datetime.utcnow()}},
    )
    return {"success": True, "xp_gained": total_xp, "new_level": new_level, "new_xp": new_xp}


@router.post("/rebirth")
async def rebirth(req: RebirthRequest, wallet: str = Depends(get_current_user)):
    """Renacimiento: NFT nivel 50 sacrifica clon idéntico nivel 50. Vuelve a Lv1 con +20% stats."""
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

    await db.users.update_one({"wallet_address": wallet},
        {"$inc": {"tick_balance": -settings.REBIRTH_COST}})
    await db.gladiators.update_one(
        {"token_id": req.sacrifice_id},
        {"$set": {"is_burned": True}},
    )
    new_rebirths = main.get("rebirths", 0) + 1
    await db.gladiators.update_one(
        {"token_id": req.main_id},
        {"$set": {"level": 1, "xp": 0, "rebirths": new_rebirths, "updated_at": datetime.utcnow()}},
    )
    return {
        "success":     True,
        "rebirths":    new_rebirths,
        "stat_bonus_pct": new_rebirths * 20,
        "tick_spent":  settings.REBIRTH_COST,
    }
