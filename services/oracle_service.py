"""
Oráculo de Precios — CandleClash
Obtiene variación % a 24h de las 15 criptos y calcula multiplicadores dinámicos.
Se refresca cada 4 horas. En bear market, las "velas verdes" diarias siguen dando bonos.
"""

import asyncio
import httpx
from datetime import datetime
from database import get_db
from config import settings, CRYPTO_ATTRIBUTE_MAP, COINGECKO_IDS

# Cache en memoria para no golpear MongoDB en cada batalla
_oracle_cache: dict = {}   # {"BTC": 1.03, "ETH": 0.97, ...}
_last_update: datetime = None


class OracleService:

    @staticmethod
    def get_cached_multipliers() -> dict:
        """Retorna multiplicadores actuales desde la cache en memoria."""
        return dict(_oracle_cache) if _oracle_cache else {k: 1.0 for k in CRYPTO_ATTRIBUTE_MAP}

    @staticmethod
    async def fetch_prices() -> dict:
        """
        Llama a CoinGecko para obtener precios y variación a 24h.
        Retorna: {"BTC": {"price": 68000, "change_24h": 2.4}, ...}
        """
        ids = ",".join(COINGECKO_IDS.values())
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        headers = {}
        if settings.COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        result = {}
        reverse_map = {v: k for k, v in COINGECKO_IDS.items()}
        for cg_id, values in data.items():
            symbol = reverse_map.get(cg_id)
            if symbol:
                result[symbol] = {
                    "price":      values.get("usd", 0),
                    "change_24h": values.get("usd_24h_change", 0.0),
                }
        return result

    @staticmethod
    def calculate_multiplier(change_24h: float, sensitivity: float) -> float:
        """
        Convierte variación % a 24h en multiplicador de atributo.
        Clamp a ±20% para evitar swings extremos.
        Ejemplo: BTC +6% → multiplier = 1 + (6 * 0.5 / 100) = 1.03
        """
        raw_bonus = (change_24h / 100) * sensitivity
        clamped = max(-0.20, min(0.20, raw_bonus))
        return round(1.0 + clamped, 4)

    @staticmethod
    async def refresh():
        """Actualiza precios y guarda en MongoDB + cache en memoria."""
        global _oracle_cache, _last_update

        print(f"[Oracle] Actualizando precios... {datetime.utcnow()}")
        prices = await OracleService.fetch_prices()

        db = get_db()
        if db is None:
            return

        new_multipliers = {}
        docs = []

        for symbol, cfg in CRYPTO_ATTRIBUTE_MAP.items():
            price_data = prices.get(symbol, {})
            change = price_data.get("change_24h", 0.0)
            mult = OracleService.calculate_multiplier(change, cfg["sensitivity"])
            new_multipliers[symbol] = mult

            docs.append({
                "symbol":      symbol,
                "attribute":   cfg["attribute"],
                "price_usd":   price_data.get("price", 0),
                "change_24h":  round(change, 4),
                "multiplier":  mult,
                "timestamp":   datetime.utcnow(),
            })

        # Guardar snapshot en MongoDB
        if docs:
            await db.oracle_snapshots.insert_many(docs)

        # Actualizar la colección "current" (upsert por símbolo)
        for doc in docs:
            await db.oracle_current.update_one(
                {"symbol": doc["symbol"]},
                {"$set": doc},
                upsert=True,
            )

        _oracle_cache = new_multipliers
        _last_update = datetime.utcnow()
        print(f"[Oracle] ✅ Actualizado. Muestras: {len(docs)}")

    @staticmethod
    async def run_forever():
        """Loop infinito: refresca al arrancar y luego cada ORACLE_REFRESH_SECONDS."""
        while True:
            try:
                await OracleService.refresh()
            except Exception as e:
                print(f"[Oracle] ⚠ Error: {e}")
            await asyncio.sleep(settings.ORACLE_REFRESH_SECONDS)

    @staticmethod
    async def get_current_snapshot() -> list:
        """Retorna el estado actual del oráculo desde MongoDB."""
        db = get_db()
        cursor = db.oracle_current.find({}, {"_id": 0})
        return await cursor.to_list(length=20)
