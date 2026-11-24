from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str = "mongodb+srv://user:pass@cluster.mongodb.net"
    DB_NAME: str = "candleclash"

    # JWT
    SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 días

    # CoinGecko
    COINGECKO_API_KEY: str = ""          # Vacío = free tier (50 req/min)
    ORACLE_REFRESH_SECONDS: int = 14400  # 4 horas

    # Polygon / Web3
    POLYGON_RPC_URL: str = "https://polygon-rpc.com"
    NFT_CONTRACT_ADDRESS: str = ""
    TICK_CONTRACT_ADDRESS: str = ""
    MARKET_CONTRACT_ADDRESS: str = ""
    GAME_WALLET_PRIVATE_KEY: str = ""   # Wallet del servidor para firmar txs

    # Economía
    TICK_BUY_RATE: float = 10.0     # 1 USDT = 10 $TICK
    TICK_SELL_RATE: float = 0.08    # 10 $TICK = 0.80 USDT
    FORGE_ASCEND_COST: int = 50     # $TICK para ascender rareza
    FORGE_FEED_COST: int = 0        # Gratis alimentar (solo NFT sacrificio)
    REBIRTH_COST: int = 100         # $TICK para renacimiento
    MARKET_FEE_PCT: float = 0.05    # 5% plataforma en ventas P2P

    # Paquete Recluta (primera vez, 1 por cuenta): 5 NFTs + 100 $TICK,
    # el primero en Lv25. El cobro es 15 USDT del saldo del servidor
    # cuando lo hay; sin depósitos todavía, el reclamo es beta (gratis)
    # y queda registrado en el log del endpoint.
    PACK_RECLUTA_PRICE_USDT: float = 15.0
    PACK_RECLUTA_COUNT: int = 5
    PACK_RECLUTA_TICK_BONUS: int = 100

    # Misiones diarias (día UTC, como el resto del servidor).
    # 'verified': True = el servidor comprueba el progreso con sus
    # propios datos antes de pagar. 'boxes' es False: las cajas
    # Básica/Escuadrón/Batallón siguen siendo DEMO (no hay endpoint
    # de cajas), así que no hay nada que verificar en el servidor;
    # su premio se reclama una vez al día por confianza (beta) y se
    # verificará cuando las cajas sean reales.
    MISSIONS: dict = {
        "battles": {"label": "Ganar 3 batallas", "target": 3, "reward": 10, "verified": True},
        "boxes":   {"label": "Abrir 1 caja",     "target": 1, "reward": 5,  "verified": False,
                    "note": "Beta: las cajas aún son demo; el reclamo es por confianza hasta que las cajas sean reales."},
        "forge":   {"label": "Usar la Forja",    "target": 1, "reward": 15, "verified": True},
    }

    # Energía (batallas): tope 10, 1 por batalla, regenera 1 cada 30 min.
    ENERGY_MAX: int = 10
    ENERGY_REGEN_SECONDS: int = 1800
    ENERGY_PER_BATTLE: int = 1

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "https://candleclash.vercel.app",
        "http://localhost:3000",
        "http://localhost:8080",
    ]

    class Config:
        env_file = ".env"


settings = Settings()


# ── Mapeo cripto → atributo ──────────────────────────────────────────────────
# Cada cripto impacta UN atributo específico del NFT que la lleva como linaje.
# El bono = variación % a 24h × sensibilidad (0.5), clampeado a ±20%.
CRYPTO_ATTRIBUTE_MAP = {
    "BTC":  {"attribute": "atq_fisico",   "tier": "Legendario", "sensitivity": 0.5},
    "ETH":  {"attribute": "vida",         "tier": "Legendario", "sensitivity": 0.5},
    "SOL":  {"attribute": "vel_ataque",   "tier": "Epico",      "sensitivity": 0.5},
    "BNB":  {"attribute": "defensa",      "tier": "Epico",      "sensitivity": 0.5},
    "XRP":  {"attribute": "prioridad",    "tier": "Epico",      "sensitivity": 0.4},
    "DOGE": {"attribute": "recuperacion", "tier": "Epico",      "sensitivity": 0.5},
    "ADA":  {"attribute": "prob_critico", "tier": "Comun",      "sensitivity": 0.2},
    "LINK": {"attribute": "critico",      "tier": "Comun",      "sensitivity": 0.3},
    "AVAX": {"attribute": "atq_magico",   "tier": "Epico",      "sensitivity": 0.4},
    "TRX":  {"attribute": "def_magica",   "tier": "Comun",      "sensitivity": 0.3},
    "SHIB": {"attribute": "reflejo",      "tier": "Comun",      "sensitivity": 0.3},
    "LTC":  {"attribute": "vel_recup",    "tier": "Comun",      "sensitivity": 0.3},
    "DOT":  {"attribute": "prob_reflejo", "tier": "Comun",      "sensitivity": 0.2},
    "SUI":  {"attribute": "prob_bloqueo", "tier": "Comun",      "sensitivity": 0.2},
    # XAU (oro): su precio en Binance viene de PAXG (PAX Gold, oro
    # tokenizado que sigue al oro); en CoinGecko el respaldo es "gold".
    "XAU":  {"attribute": "bloqueo",      "tier": "Comun",      "sensitivity": 0.2},
}

# ── Modificadores por raza ──────────────────────────────────────────────────
# Cada raza sube 2 stats y baja 1, para que ninguna domine en todo y cada
# jugador pueda armar su build. Los valores son multiplicadores sobre el stat
# base (1.20 = +20%, 0.90 = -10%). Humano es la excepción equilibrada.
# Las claves deben coincidir exactamente con los nombres de raza del frontend.
# Los stats no listados quedan en 1.0 (sin cambio). Una raza desconocida
# queda en 1.0 en todo, así los gladiadores viejos siguen funcionando.
RACE_MODIFIERS = {
    "Humano":     {"vida": 1.10, "atq_fisico": 1.10, "vel_ataque": 0.95},
    "Elfo":       {"vel_ataque": 1.20, "prob_critico": 1.20, "vida": 0.90},
    "Enano":      {"defensa": 1.20, "bloqueo": 1.20, "vel_ataque": 0.90},
    "Orco":       {"atq_fisico": 1.20, "vida": 1.15, "def_magica": 0.90},
    "Goblin":     {"prioridad": 1.20, "critico": 1.20, "defensa": 0.90},
    "Mediano":    {"prob_reflejo": 1.20, "vel_recup": 1.15, "atq_fisico": 0.90},
    "Gnomo":      {"atq_magico": 1.20, "prob_critico": 1.10, "vida": 0.90},
    "Dracónido":  {"atq_fisico": 1.15, "atq_magico": 1.15, "vel_ataque": 0.90},
    "Tiefling":   {"atq_magico": 1.20, "critico": 1.15, "defensa": 0.90},
    "H.Lagarto":  {"recuperacion": 1.20, "vel_recup": 1.15, "atq_magico": 0.90},
    "Tabaxi":     {"vel_ataque": 1.20, "prioridad": 1.15, "defensa": 0.90},
    "Minotauro":  {"vida": 1.20, "atq_fisico": 1.10, "vel_ataque": 0.90},
    "Forjado":    {"defensa": 1.20, "vida": 1.10, "vel_recup": 0.90},
    "Cíborg":     {"prob_bloqueo": 1.20, "defensa": 1.10, "recuperacion": 0.90},
    "Alienígena": {"atq_magico": 1.15, "reflejo": 1.20, "atq_fisico": 0.90},
}

# IDs de CoinGecko para cada cripto
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
    "ADA": "cardano", "LINK": "chainlink", "AVAX": "avalanche-2",
    "TRX": "tron", "SHIB": "shiba-inu", "LTC": "litecoin",
    "DOT": "polkadot", "SUI": "sui", "XAU": "gold",
}

# Pares de Binance (USDT) para cada cripto. Los 15 linajes ya vienen de
# Binance: XAU (oro) usa el par PAXGUSDT (PAX Gold, oro tokenizado cuyo
# precio sigue al oro), porque el oro no cotiza como spot en Binance.
# Los futuros (perpetuos) NO sirven desde Render (EEUU): fapi.binance.com
# responde 451 "restricted location", así que oro/plata como perpetuos no
# son una opción en producción.
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT", "LINK": "LINKUSDT", "AVAX": "AVAXUSDT",
    "TRX": "TRXUSDT", "SHIB": "SHIBUSDT", "LTC": "LTCUSDT",
    "DOT": "DOTUSDT", "SUI": "SUIUSDT", "XAU": "PAXGUSDT",
}

# Dominios de Binance a probar, en orden. Binance.com restringe IPs de
# EEUU (Render está en Virginia) con 451 "restricted location"; en ese
# caso se prueba Binance.us, que tiene el mismo formato de respuesta.
BINANCE_BASES = ["https://api.binance.com", "https://api.binance.us"]

# Símbolos que solo existen en CoinGecko (no están en BINANCE_SYMBOLS).
# Vacío: los 15 linajes ya vienen de Binance; CoinGecko queda solo como
# respaldo completo de los 15 si Binance no responde.
COINGECKO_ONLY = []

# ── Sorteos de los packs ────────────────────────────────────────────────────
# Fuente única de verdad para el servidor (el frontend replica estas
# tablas en RARITY_ROLL / LINEAGE_TIER_ROLL de index.html). Son DOS
# sorteos independientes por gladiador:
#   1. RARITY_ROLL: la rareza del gladiador (qué tan fuerte es).
#   2. LINEAGE_TIER_ROLL: el tier del linaje (qué cripto sale).
# Las probabilidades de cada tabla suman 100. El pack Batallón garantiza
# 1 NFT de rareza Plata o mejor (sorteo restringido a Plata+).
# El mint gratis NO usa estas tablas: los reclutas gratis son Cobre Lv1.
RARITY_ROLL = [
    ("Cobre", 65.0),
    ("Bronce", 25.0),
    ("Plata", 7.0),
    ("Oro", 2.0),
    ("Diamante", 0.8),
    ("Legendario", 0.2),
]

LINEAGE_TIER_ROLL = [
    ("Comun", 65.0),
    ("Epico", 25.0),
    ("Legendario", 10.0),
]


def roll_from_table(table, value: float) -> str:
    """Devuelve el ítem cuya probabilidad acumulada cubre `value` (0–100).

    Ej.: RARITY_ROLL con value=66 → Bronce (65–90); con 99.9 → Legendario.
    Si el valor cae fuera por redondeo, devuelve el último ítem.
    """
    acc = 0.0
    for name, pct in table:
        acc += pct
        if value < acc:
            return name
    return table[-1][0]


def roll_rarity(value: float) -> str:
    """Rareza sorteada para un gladiador de pack (value: 0–100)."""
    return roll_from_table(RARITY_ROLL, value)


def roll_rarity_plata_plus(value: float) -> str:
    """Rareza sorteada restringida a Plata o mejor (value: 0–10, pesos
    relativos de la tabla: Plata 7, Oro 2, Diamante 0.8, Legendario 0.2).
    Es la garantía del pack Batallón."""
    return roll_from_table(
        [("Plata", 7.0), ("Oro", 2.0), ("Diamante", 0.8), ("Legendario", 0.2)],
        value,
    )


def roll_lineage_tier(value: float) -> str:
    """Tier de linaje sorteado (value: 0–100)."""
    return roll_from_table(LINEAGE_TIER_ROLL, value)
