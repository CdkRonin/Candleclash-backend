from fastapi import APIRouter
from services.oracle_service import OracleService

router = APIRouter()


@router.get("/current")
async def get_current_prices():
    """
    Retorna el estado actual del oráculo: precio, variación 24h y multiplicador
    para los 15 criptos del juego.
    """
    snapshot = await OracleService.get_current_snapshot()
    return snapshot


@router.get("/multipliers")
async def get_multipliers():
    """
    Retorna solo los multiplicadores activos (lo que usa el motor de batalla).
    El frontend puede mostrarlo en las Barracas junto a cada stat.
    """
    return OracleService.get_cached_multipliers()
