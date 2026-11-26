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


# ── Fail-fast de configuración ──────────────────────────────────────────────
# El servidor NO arranca con secretos de ejemplo: si SECRET_KEY es la de
# ejemplo (o está vacía) o MONGODB_URL es el placeholder, fallar con un
# error claro en vez de arrancar inseguro. En local el .env válido
# (nunca se sube: está en .gitignore) pone los valores reales; en
# Render los ponen las variables de entorno. Sin esto, un despliegue
# sin variables arrancaba con la clave JWT pública del ejemplo y una
# URL de Mongo que no existe.
if not settings.SECRET_KEY or settings.SECRET_KEY == "change-this-in-production":
    raise RuntimeError(
        "SECRET_KEY no válida: configura una SECRET_KEY real "
        "(variable de entorno o .env). El valor de ejemplo "
        "'change-this-in-production' no se permite."
    )
if not settings.MONGODB_URL or settings.MONGODB_URL == "mongodb+srv://user:pass@cluster.mongodb.net":
    raise RuntimeError(
        "MONGODB_URL no válida: configura la URL real de MongoDB Atlas "
        "(variable de entorno o .env). El placeholder del ejemplo no se permite."
    )


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

# ── Desbloqueo de stats por nivel ─────────────────────────────────────────
# Los 15 stats del gladiador, uno por cada una de las 15 criptos del
# oráculo (CRYPTO_ATTRIBUTE_MAP): cada linaje sube UN stat con el precio
# en vivo de su cripto, y cada stat se DESBLOQUEA a un nivel (máx. 50).
# Por debajo de su nivel, el stat vale 0 en compute_stats: no existe en
# la pelea. Antes solo defensa (Nv10) y vel_ataque (Nv25) estaban
# bloqueados; los otros 7 stats que la batalla no leía (atq_magico,
# def_magica, recuperacion, vel_recup, prioridad, critico, bloqueo)
# se calculaban desde Nv1 sin hacer nada. Ahora cada uno tiene su nivel
# y su efecto en el motor (services/battle_service.py), así que subir de
# nivel desbloquea mecánicas nuevas. La escalera se midió con la
# auditoría de balance: prob_bloqueo y reflejo quedan en Nv1 porque son
# el kit defensivo base (bloquearlos a Nv5 dejaba al Orco en 90% en Nv1),
# y atq_magico (Nv30) llega antes que def_magica (Nv35) para que haya
# una ventana en la que la magia pega sin resistencia.
#   Nv 1: vida (ETH), atq_fisico (BTC), prob_critico (ADA),
#         prob_bloqueo (SUI), reflejo (SHIB) — el kit base
#   Nv 5: prob_reflejo (DOT) — el reflejo entra en juego
#   Nv10: defensa (BNB) — ya existía
#   Nv15: critico (LINK) — los críticos pegan más fuerte
#   Nv20: prioridad (XRP) — decide quién pega primero en empates
#   Nv25: vel_ataque (SOL) — ya existía
#   Nv30: atq_magico (AVAX) — ataques mágicos
#   Nv35: def_magica (TRX) — resistencia a la magia
#   Nv40: bloqueo (XAU) — reducción fija por golpe
#   Nv45: recuperacion (DOGE) — regenerar vida cada ronda
#   Nv50: vel_recup (LTC) — las habilidades se recargan más rápido
STAT_UNLOCK_LEVEL = {
    "vida": 1, "atq_fisico": 1, "prob_critico": 1,
    "prob_bloqueo": 1, "reflejo": 1,
    "prob_reflejo": 5,
    "defensa": 10,
    "critico": 15,
    "prioridad": 20,
    "vel_ataque": 25,
    "atq_magico": 30,
    "def_magica": 35,
    "bloqueo": 40,
    "recuperacion": 45,
    "vel_recup": 50,
}


# ── Modificadores por raza ──────────────────────────────────────────────────
# Cada raza sube 2 stats y baja 1, para que ninguna domine en todo y cada
# jugador pueda armar su build. Los valores son multiplicadores sobre el stat
# base (1.20 = +20%, 0.90 = -10%). Humano es la excepción equilibrada.
# Las claves deben coincidir exactamente con los nombres de raza del frontend.
# Los stats no listados quedan en 1.0 (sin cambio). Una raza desconocida
# queda en 1.0 en todo, así los gladiadores viejos siguen funcionando.
RACE_MODIFIERS = {
    # Rebalance 2026-11-25 (auditoría de balance + sistema de desbloqueo):
    # cada raza sube 2 stats que la batalla SÍ usa y baja 1 que también
    # usa, para que la ventaja y la desventaja sean reales. La tabla
    # anterior subía stats que el motor no leía (atq_magico, recuperacion,
    # vel_recup, prioridad, critico, def_magica, bloqueo): el Mediano
    # pagaba -10% de ataque por mejoras fantasma y el H.Lagarto no ganaba
    # nada. Medida en la auditoría (round-robin 200 reps/par, Lv25
    # Bronce): el rango pasó de 23–81% a 34–65% con esta tabla.
    "Humano":     {"vida": 1.10, "atq_fisico": 1.10, "vel_ataque": 0.95},
    "Elfo":       {"vel_ataque": 1.20, "prob_critico": 1.20, "vida": 0.90},
    "Enano":      {"defensa": 1.20, "prob_bloqueo": 1.20, "vel_ataque": 0.90},
    "Orco":       {"atq_fisico": 1.15, "vida": 1.10, "vel_ataque": 0.90},
    "Goblin":     {"vel_ataque": 1.20, "prob_critico": 1.20, "defensa": 0.90},
    "Mediano":    {"prob_reflejo": 1.20, "prob_bloqueo": 1.15, "vel_ataque": 0.95},
    "Gnomo":      {"vel_ataque": 1.20, "prob_critico": 1.20, "defensa": 0.90},
    "Dracónido":  {"atq_fisico": 1.15, "vida": 1.10, "vel_ataque": 0.90},
    "Tiefling":   {"prob_critico": 1.20, "vel_ataque": 1.15, "defensa": 0.90},
    "H.Lagarto":  {"vida": 1.15, "defensa": 1.10, "vel_ataque": 0.90},
    "Tabaxi":     {"vel_ataque": 1.20, "prioridad": 1.15, "defensa": 0.90},
    "Minotauro":  {"vida": 1.20, "atq_fisico": 1.10, "vel_ataque": 0.90},
    "Forjado":    {"defensa": 1.20, "vida": 1.10, "vel_ataque": 0.90},
    "Cíborg":     {"prob_bloqueo": 1.20, "defensa": 1.10, "vel_ataque": 0.95},
    "Alienígena": {"reflejo": 1.20, "prob_critico": 1.20, "defensa": 0.90},
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


# ── Habilidades por raza ────────────────────────────────────────────────────
# UNA habilidad por raza, en la batalla automática. La activa el servidor
# solo, cuando el enfriamiento está listo (ver services/battle_service.py).
# Regla simple: al empezar la batalla el enfriamiento es 0, así que la
# habilidad se dispara en el primer ataque de la ronda 1; después se
# dispara cada `cooldown` rondas (p. ej. cooldown 4 → rondas 1, 5, 9…).
# Efectos (kinds): damage_mult (daño ×value en ese ataque), crit (ese
# ataque siempre es crítico), heal (cura value × vida máxima), block
# (bloquea por completo el próximo ataque enemigo), extra_attack (un
# ataque más en la misma ronda), reduce (el próximo ataque enemigo hace
# (1 − value) de daño). Balance: daño de habilidad como mucho ×1.5 en
# una ronda y curación como mucho 15% de la vida máxima; los dos
# gladiadores tienen una habilidad cada uno, así que es simétrico.
# El frontend replica esta tabla SOLO para mostrarla y para la batalla
# demo de invitado; en batallas reales manda el servidor.
RACE_ABILITIES = {
    "Humano":     {"id": "golpe_decisivo",     "nombre": "Golpe Decisivo",     "descripcion": "Un golpe certero con +40% de daño.",              "kind": "damage_mult",  "value": 1.4,  "cooldown": 4},
    "Elfo":       {"id": "tiro_certero",       "nombre": "Tiro Certero",       "descripcion": "El próximo ataque siempre es crítico.",            "kind": "crit",         "value": None, "cooldown": 4},
    "Enano":      {"id": "muro_piedra",        "nombre": "Muro de Piedra",     "descripcion": "Bloquea por completo el próximo ataque enemigo.",  "kind": "block",        "value": None, "cooldown": 4},
    "Orco":       {"id": "furia_salvaje",      "nombre": "Furia Salvaje",      "descripcion": "Daño ×1.5 en el próximo ataque.",                  "kind": "damage_mult",  "value": 1.5,  "cooldown": 5},
    "Goblin":     {"id": "punalada_rapida",    "nombre": "Puñalada Rápida",    "descripcion": "Ataca una vez más en esta misma ronda.",           "kind": "extra_attack", "value": None, "cooldown": 5},
    "Mediano":    {"id": "suerte_mediana",     "nombre": "Suerte Mediana",     "descripcion": "Recupera 15% de su vida máxima.",                  "kind": "heal",         "value": 0.15, "cooldown": 4},
    "Gnomo":      {"id": "chispa_arcana",      "nombre": "Chispa Arcana",      "descripcion": "Daño mágico: +50% de daño en el próximo ataque.",  "kind": "damage_mult",  "value": 1.5,  "cooldown": 4},
    "Dracónido":  {"id": "aliento_dragon",     "nombre": "Aliento de Dragón",  "descripcion": "Daño ×1.5 en el próximo ataque.",                  "kind": "damage_mult",  "value": 1.5,  "cooldown": 5},
    "Tiefling":   {"id": "pacto_infernal",     "nombre": "Pacto Infernal",     "descripcion": "El próximo ataque siempre es crítico.",            "kind": "crit",         "value": None, "cooldown": 4},
    "H.Lagarto":  {"id": "regeneracion",       "nombre": "Regeneración",       "descripcion": "Recupera 15% de su vida máxima.",                  "kind": "heal",         "value": 0.15, "cooldown": 5},
    "Tabaxi":     {"id": "zarpazo_doble",      "nombre": "Zarpazo Doble",      "descripcion": "Ataca una vez más en esta misma ronda.",           "kind": "extra_attack", "value": None, "cooldown": 5},
    "Minotauro":  {"id": "embestida",          "nombre": "Embestida",          "descripcion": "Daño ×1.5 en el próximo ataque.",                  "kind": "damage_mult",  "value": 1.5,  "cooldown": 5},
    "Forjado":    {"id": "armadura_reforzada", "nombre": "Armadura Reforzada", "descripcion": "El próximo ataque enemigo hace la mitad de daño.", "kind": "reduce",       "value": 0.5,  "cooldown": 4},
    "Cíborg":     {"id": "escudo_energia",     "nombre": "Escudo de Energía",  "descripcion": "Bloquea por completo el próximo ataque enemigo.",  "kind": "block",        "value": None, "cooldown": 4},
    "Alienígena": {"id": "mente_colmena",      "nombre": "Mente Colmena",      "descripcion": "Recupera 15% de su vida máxima.",                  "kind": "heal",         "value": 0.15, "cooldown": 4},
}


def ability_for_race(race: str) -> dict:
    """Habilidad de una raza (RACE_ABILITIES). Raza desconocida →
    la de Humano, para que los gladiadores viejos sigan funcionando."""
    return RACE_ABILITIES.get(race, RACE_ABILITIES["Humano"])
