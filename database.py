from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
import logging

logger = logging.getLogger(__name__)

client: AsyncIOMotorClient = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]
    # Crear índices al arrancar
    await db.users.create_index("wallet_address", unique=True)
    await db.gladiators.create_index([("owner_wallet", 1), ("token_id", 1)])
    # token_id único en toda la colección: dos gladiadores no pueden
    # compartir token_id (antes solo existía el índice compuesto por
    # owner+token, así que un duplicado entre wallets distintas o por
    # un fallo de inserción no se detectaba). Si ya hubiera duplicados
    # en los datos, el índice no se crea y queda en el log: no se
    # borra ningún dato, pero el arranque sigue.
    try:
        await db.gladiators.create_index("token_id", unique=True)
    except Exception as e:
        logger.warning(
            "No se pudo crear el índice único token_id (¿duplicados "
            "existentes?): %s", e
        )
    await db.market_listings.create_index("status")
    await db.oracle_snapshots.create_index([("symbol", 1), ("timestamp", -1)])
    # Misiones diarias: reclamo único por wallet+misión+día (UTC) y
    # verificación de progreso con datos del servidor.
    await db.mission_claims.create_index(
        [("wallet_address", 1), ("mission_id", 1), ("date", 1)], unique=True)
    await db.forge_actions.create_index([("wallet_address", 1), ("timestamp", -1)])
    await db.battles.create_index([("player_wallet", 1), ("timestamp", -1)])
    print("✅ MongoDB conectado")


async def close_db():
    if client:
        client.close()


def get_db():
    return db
