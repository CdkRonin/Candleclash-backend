import logging
import random
import uuid

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from pymongo import ReturnDocument
from routers.auth import get_current_user
from database import get_db
from config import (
    settings,
    CRYPTO_ATTRIBUTE_MAP,
    RACE_MODIFIERS,
    roll_lineage_tier,
    roll_rarity,
)
from models.gladiator import Gladiator
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


class ListingCreate(BaseModel):
    gladiator_id: str
    price_usdt:   float


class BuyTick(BaseModel):
    usdt_amount: float


class SellTick(BaseModel):
    tick_amount: int


@router.get("/listings")
async def get_listings():
    """Todos los NFTs en venta en el P2P (activos).

    Serialización manual: el doc de Mongo trae ObjectId (_id) y
    datetimes, que FastAPI no puede serializar a JSON (antes este
    endpoint devolvía 500 en cuanto había un anuncio real). Se
    devuelve listing_id como string y el snapshot limpio, con los
    computed_stats del oráculo para que el frontend no recalcule.
    """
    db = get_db()
    oracle_mults = {}
    try:
        from services.oracle_service import OracleService
        oracle_mults = OracleService.get_cached_multipliers()
    except Exception:
        oracle_mults = {}
    docs = await db.market_listings.find({"status": "active"}).to_list(length=100)
    out = []
    for d in docs:
        snap = dict(d.get("nft_snapshot") or {})
        snap.pop("_id", None)
        for k in ("created_at", "updated_at"):
            if hasattr(snap.get(k), "isoformat"):
                snap[k] = snap[k].isoformat()
        try:
            g = Gladiator(**{k: v for k, v in snap.items() if k in Gladiator.model_fields})
            snap["computed_stats"] = g.compute_stats(oracle_mults)
        except Exception:
            snap["computed_stats"] = None
        created = d.get("created_at")
        out.append({
            "listing_id":    str(d["_id"]),
            "gladiator_id":  d["gladiator_id"],
            "seller_wallet": d["seller_wallet"],
            "price_usdt":    d["price_usdt"],
            "status":        d["status"],
            "created_at":    created.isoformat() if hasattr(created, "isoformat") else created,
            "nft_snapshot":  snap,
        })
    return out


@router.post("/list")
async def list_nft(req: ListingCreate, wallet: str = Depends(get_current_user)):
    """Publica un NFT a la venta. El NFT se marca como is_listed=True."""
    if req.price_usdt < 0.10:
        raise HTTPException(status_code=400, detail="Precio mínimo: 0.10 USDT")
    db = get_db()
    nft = await db.gladiators.find_one({
        "token_id": req.gladiator_id,
        "owner_wallet": wallet,
        "is_burned": False,
        "is_listed": False,
    })
    if not nft:
        raise HTTPException(status_code=404, detail="NFT no encontrado o ya listado")

    await db.gladiators.update_one(
        {"token_id": req.gladiator_id},
        {"$set": {"is_listed": True}},
    )
    listing = {
        "gladiator_id": req.gladiator_id,
        "seller_wallet": wallet,
        "price_usdt":   round(req.price_usdt, 2),
        "nft_snapshot": nft,   # Snapshot del NFT al momento de listar
        "status":       "active",
        "created_at":   datetime.utcnow(),
    }
    result = await db.market_listings.insert_one(listing)
    return {"listing_id": str(result.inserted_id)}


@router.post("/buy/{listing_id}")
async def buy_nft(listing_id: str, wallet: str = Depends(get_current_user)):
    """Compra un NFT listado."""
    from bson import ObjectId
    from bson.errors import InvalidId
    db = get_db()
    try:
        oid = ObjectId(listing_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Listing no encontrado")
    listing = await db.market_listings.find_one({"_id": oid, "status": "active"})
    if not listing:
        raise HTTPException(status_code=404, detail="Listing no encontrado")
    if listing["seller_wallet"] == wallet:
        raise HTTPException(status_code=400, detail="No puedes comprar tu propio NFT")

    buyer = await db.users.find_one({"wallet_address": wallet})
    if not buyer or buyer.get("usdt_balance", 0) < listing["price_usdt"]:
        raise HTTPException(status_code=400, detail="Saldo USDT insuficiente")

    platform_fee = round(listing["price_usdt"] * settings.MARKET_FEE_PCT, 2)
    seller_receives = round(listing["price_usdt"] - platform_fee, 2)

    # Transferencias
    await db.users.update_one({"wallet_address": wallet},
        {"$inc": {"usdt_balance": -listing["price_usdt"]}})
    await db.users.update_one({"wallet_address": listing["seller_wallet"]},
        {"$inc": {"usdt_balance": seller_receives}})

    # Transferir NFT
    await db.gladiators.update_one(
        {"token_id": listing["gladiator_id"]},
        {"$set": {"owner_wallet": wallet, "is_listed": False}},
    )
    await db.market_listings.update_one(
        {"_id": oid},
        {"$set": {"status": "sold", "buyer_wallet": wallet, "sold_at": datetime.utcnow()}},
    )
    return {"success": True, "paid": listing["price_usdt"], "platform_fee": platform_fee}


@router.post("/tick/buy")
async def buy_tick(req: BuyTick, wallet: str = Depends(get_current_user)):
    """Compra $TICK con USDT a precio fijo."""
    if req.usdt_amount < 0.10:
        raise HTTPException(status_code=400, detail="Mínimo 0.10 USDT")
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet})
    if not user or user.get("usdt_balance", 0) < req.usdt_amount:
        raise HTTPException(status_code=400, detail="Saldo USDT insuficiente")
    tick_received = int(req.usdt_amount * settings.TICK_BUY_RATE)
    await db.users.update_one({"wallet_address": wallet}, {
        "$inc": {"usdt_balance": -req.usdt_amount, "tick_balance": tick_received},
    })
    return {"tick_received": tick_received, "usdt_spent": req.usdt_amount}


@router.post("/tick/sell")
async def sell_tick(req: SellTick, wallet: str = Depends(get_current_user)):
    """Vende $TICK por USDT con fee del 20%."""
    if req.tick_amount < 10:
        raise HTTPException(status_code=400, detail="Mínimo 10 $TICK")
    db = get_db()
    user = await db.users.find_one({"wallet_address": wallet})
    if not user or user.get("tick_balance", 0) < req.tick_amount:
        raise HTTPException(status_code=400, detail="Saldo $TICK insuficiente")
    usdt_received = round(req.tick_amount * settings.TICK_SELL_RATE, 2)
    await db.users.update_one({"wallet_address": wallet}, {
        "$inc": {"tick_balance": -req.tick_amount, "usdt_balance": usdt_received},
    })
    return {"usdt_received": usdt_received, "tick_sold": req.tick_amount}


@router.post("/packs/recluta")
async def claim_starter_pack(wallet: str = Depends(get_current_user)):
    """Paquete Recluta — PRIMERA VEZ, 1 por cuenta.

    5 NFTs generados EN EL SERVIDOR (sorteos reales de linaje y rareza,
    los mismos de config.py) + 100 $TICK; el primer NFT nace en Lv25.
    El reclamo es atómico: find_one_and_update con
    starter_pack_claimed != True, así dos reclamos simultáneos no
    pueden colarse. Si ya lo reclamó, 403.
    Cobro: 15 USDT del saldo del servidor si lo tiene. Si no lo tiene,
    se permite el reclamo UNA vez como beta (todavía no hay depósitos
    de donde cobrar) y queda en el log.
    TODO(depósitos): cuando existan depósitos, el reclamo beta debe
    desaparecer y el cobro de 15 USDT debe ser obligatorio.
    """
    db = get_db()
    user = await db.users.find_one_and_update(
        {"wallet_address": wallet.lower(), "starter_pack_claimed": {"$ne": True}},
        {"$set": {"starter_pack_claimed": True}},
        return_document=ReturnDocument.AFTER,
    )
    if not user:
        raise HTTPException(status_code=403, detail="Ya reclamaste el Paquete Recluta")

    created_ids = []
    try:
        # Cobro si hay saldo; si no, reclamo beta (sin depósitos aún).
        charged_usdt = 0.0
        if user.get("usdt_balance", 0) >= settings.PACK_RECLUTA_PRICE_USDT:
            await db.users.update_one(
                {"wallet_address": wallet.lower()},
                {"$inc": {"usdt_balance": -settings.PACK_RECLUTA_PRICE_USDT}},
            )
            charged_usdt = settings.PACK_RECLUTA_PRICE_USDT
        else:
            logger.warning(
                "Paquete Recluta beta sin cobro para %s "
                "(saldo USDT %.2f < %.2f; depósitos aún no existen)",
                wallet.lower(), user.get("usdt_balance", 0),
                settings.PACK_RECLUTA_PRICE_USDT,
            )

        # 5 NFTs con los sorteos reales (linaje + rareza independientes).
        oracle_mults = {}
        try:
            from services.oracle_service import OracleService
            oracle_mults = OracleService.get_cached_multipliers()
        except Exception:
            oracle_mults = {}
        gladiators = []
        for i in range(settings.PACK_RECLUTA_COUNT):
            tier = roll_lineage_tier(random.uniform(0, 100))
            pool = [k for k, v in CRYPTO_ATTRIBUTE_MAP.items() if v["tier"] == tier]
            lineage = random.choice(pool) if pool else "ADA"
            race = random.choice(list(RACE_MODIFIERS.keys()))
            rarity = roll_rarity(random.uniform(0, 100))
            g = Gladiator(
                token_id=str(uuid.uuid4()),
                owner_wallet=wallet.lower(),
                race=race,
                lineage=lineage,
                rarity=rarity,
                level=25 if i == 0 else 1,  # el primero nace en Lv25
                xp=0,
                rebirths=0,
            )
            doc = g.model_dump()
            await db.gladiators.insert_one(doc)
            created_ids.append(doc["token_id"])
            doc.pop("_id", None)  # insert_one añade _id (ObjectId); no se serializa
            gladiators.append({**doc, "computed_stats": g.compute_stats(oracle_mults)})

        await db.users.update_one(
            {"wallet_address": wallet.lower()},
            {"$inc": {"tick_balance": settings.PACK_RECLUTA_TICK_BONUS}},
        )
    except Exception:
        # Si algo falló a mitad: devolver el reclamo para no dejar al
        # usuario marcado sin sus NFTs, y borrar los que sí se crearon.
        await db.users.update_one(
            {"wallet_address": wallet.lower()},
            {"$set": {"starter_pack_claimed": False}},
        )
        if created_ids:
            await db.gladiators.delete_many({"token_id": {"$in": created_ids}})
        raise

    return {
        "gladiators": gladiators,
        "tick_bonus": settings.PACK_RECLUTA_TICK_BONUS,
        "charged_usdt": charged_usdt,
        "beta_no_charge": charged_usdt == 0,
    }
