from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from routers.auth import get_current_user
from database import get_db
from config import settings
from datetime import datetime

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
    """Todos los NFTs en venta en el P2P (activos)."""
    db = get_db()
    cursor = db.market_listings.find({"status": "active"}, {"_id": 0})
    return await cursor.to_list(length=100)


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
    db = get_db()
    listing = await db.market_listings.find_one({"_id": ObjectId(listing_id), "status": "active"})
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
        {"_id": ObjectId(listing_id)},
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
