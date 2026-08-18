from motor.motor_asyncio import AsyncIOMotorClient
from config import settings

client: AsyncIOMotorClient = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]
    # Crear índices al arrancar
    await db.users.create_index("wallet_address", unique=True)
    await db.gladiators.create_index([("owner_wallet", 1), ("token_id", 1)])
    await db.market_listings.create_index("status")
    await db.oracle_snapshots.create_index([("symbol", 1), ("timestamp", -1)])
    print("✅ MongoDB conectado")


async def close_db():
    if client:
        client.close()


def get_db():
    return db
