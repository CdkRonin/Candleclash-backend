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
                # CoinGecko puede devolver null en usd/usd_24h_change cuando no
                # tiene dato para ese activo (o tras un rate-limit parcial).
                # .get() con default NO protege de un null explícito: lo dejamos
                # como None y es refresh() quien decide saltar ese cripto.
                result[symbol] = {
                    "price":      values.get("usd"),
                    "change_24h": values.get("usd_24h_change"),
                }
        return result

    @staticmethod
    def _as_number(value):
        """Devuelve el valor solo si es un número real; None en caso contrario."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    @staticmethod
    def calculate_multiplier(change_24h: float, sensitivity: float) -> float:
        """
        Convierte variación % a 24h en multiplicador de atributo.
        Clamp a ±20% para evitar swings extremos.
        Ejemplo: BTC +6% → multiplier = 1 + (6 * 0.5 / 100) = 1.03
        """
        # Guardia: un None (dato vacío de CoinGecko) nunca debe llegar a la
        # división — antes rompía todo el refresco con TypeError.
        if isinstance(change_24h, bool) or not isinstance(change_24h, (int, float)):
            raise ValueError(f"change_24h inválido para calcular multiplicador: {change_24h!r}")
        raw_bonus = (change_24h / 100) * sensitivity
        clamped = max(-0.20, min(0.20, raw_bonus))
        return round(1.0 + clamped, 4)

    @staticmethod
    async def _last_good_multipliers(db) -> dict:
        """
        Lee los últimos multiplicadores buenos guardados en oracle_current.
        Es el respaldo cuando la cache en memoria está vacía (p. ej. tras un
        reinicio del servicio) y CoinGecko falla.
        """
        try:
            docs = await db.oracle_current.find(
                {}, {"_id": 0, "symbol": 1, "multiplier": 1}
            ).to_list(length=20)
        except Exception as e:
            print(f"[Oracle] ⚠ No se pudo leer oracle_current: {e}")
            return {}
        good = {}
        for doc in docs:
            mult = OracleService._as_number(doc.get("multiplier"))
            if mult is not None and doc.get("symbol"):
                good[doc["symbol"]] = mult
        return good

    @staticmethod
    async def refresh():
        """
        Actualiza precios y guarda en MongoDB + cache en memoria.

        Robusto ante fallos de CoinGecko:
        - Si la llamada falla (429, timeout, red), NO se toca nada: se
          conservan los multiplicadores anteriores y se registra un warning.
        - Si un cripto viene sin precio/cambio 24h válido (None o ausente),
          se salta SOLO ese cripto y conserva su último multiplicador bueno
          (cache en memoria o oracle_current en Mongo); el resto se actualiza.
        - Si TODOS fallan, se conservan todos los multiplicadores anteriores
          en vez de resetear a 1.0.
        """
        global _oracle_cache, _last_update

        print(f"[Oracle] Actualizando precios... {datetime.utcnow()}")
        try:
            prices = await OracleService.fetch_prices()
        except Exception as e:
            # 429 de CoinGecko, timeout o error de red: no romper nada.
            print(f"[Oracle] ⚠ CoinGecko no respondió ({e}). "
                  f"Se conservan los multiplicadores anteriores.")
            if not _oracle_cache:
                # Arranque en frío tras reinicio: intentar recuperar el último
                # estado bueno desde Mongo antes de servir 1.0 por defecto.
                db = get_db()
                if db is not None:
                    last_good = await OracleService._last_good_multipliers(db)
                    if last_good:
                        _oracle_cache = last_good
                        print(f"[Oracle] Recuperados {len(last_good)} "
                              f"multiplicadores previos desde MongoDB.")
            return

        db = get_db()
        if db is None:
            return

        # Último multiplicador bueno por cripto: la cache en memoria manda,
        # y oracle_current cubre los huecos (p. ej. tras un reinicio).
        previous = await OracleService._last_good_multipliers(db)
        previous.update(_oracle_cache)

        new_multipliers = {}
        docs = []
        skipped = []

        for symbol, cfg in CRYPTO_ATTRIBUTE_MAP.items():
            price_data = prices.get(symbol) or {}
            change = OracleService._as_number(price_data.get("change_24h"))
            price = OracleService._as_number(price_data.get("price"))

            if change is None:
                # Sin dato válido: conservar el último multiplicador bueno.
                kept = previous.get(symbol, 1.0)
                new_multipliers[symbol] = kept
                skipped.append(symbol)
                print(f"[Oracle] ⚠ {symbol}: sin cambio 24h válido de "
                      f"CoinGecko, se conserva multiplicador {kept}")
                continue

            mult = OracleService.calculate_multiplier(change, cfg["sensitivity"])
            new_multipliers[symbol] = mult

            docs.append({
                "symbol":      symbol,
                "attribute":   cfg["attribute"],
                "price_usd":   price,
                "change_24h":  round(change, 4),
                "multiplier":  mult,
                "timestamp":   datetime.utcnow(),
            })

        if not docs:
            # Fallaron todos: no se escribe nada y no se resetea a 1.0.
            _oracle_cache = new_multipliers
            print(f"[Oracle] ⚠ Ningún cripto se pudo actualizar. "
                  f"Se conservan los {len(new_multipliers)} multiplicadores anteriores.")
            return

        # Guardar snapshot en MongoDB (solo criptos con dato válido)
        await db.oracle_snapshots.insert_many(docs)

        # Actualizar la colección "current" (upsert por símbolo)
        # OJO: insert_many añade _id a los dicts in-place; hay que excluirlo
        # del $set porque _id es inmutable y Mongo rechaza el update (code 66).
        for doc in docs:
            await db.oracle_current.update_one(
                {"symbol": doc["symbol"]},
                {"$set": {k: v for k, v in doc.items() if k != "_id"}},
                upsert=True,
            )

        _oracle_cache = new_multipliers
        _last_update = datetime.utcnow()
        print(f"[Oracle] ✅ Actualizado. Muestras: {len(docs)}"
              + (f" | Sin dato (conservados): {', '.join(skipped)}" if skipped else ""))

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
