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
    "XMR":  {"attribute": "prob_reflejo", "tier": "Comun",      "sensitivity": 0.2},
    "XAG":  {"attribute": "prob_bloqueo", "tier": "Comun",      "sensitivity": 0.2},
    "XAU":  {"attribute": "bloqueo",      "tier": "Comun",      "sensitivity": 0.2},
}

# IDs de CoinGecko para cada cripto
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
    "ADA": "cardano", "LINK": "chainlink", "AVAX": "avalanche-2",
    "TRX": "tron", "SHIB": "shiba-inu", "LTC": "litecoin",
    "XMR": "monero", "XAG": "silver", "XAU": "gold",
}
