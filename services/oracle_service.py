"""
Oráculo de Precios — CandleClash
Obtiene variación % a 24h de las 15 criptos y calcula multiplicadores dinámicos.
Se refresca cada 4 horas. En bear market, las "velas verdes" diarias siguen dando bonos.

Fuente principal: Binance (API pública, sin clave) para las 12 criptos con
par USDT; XMR/XAG/XAU no están en Binance y siguen por CoinGecko. Si
Binance no responde en ningún dominio, CoinGecko cubre los 15 como respaldo.
"""

import asyncio
import json
import httpx
from datetime import datetime
from database import get_db
from config import (
    settings, CRYPTO_ATTRIBUTE_MAP, COINGECKO_IDS,
    BINANCE_SYMBOLS, BINANCE_BASES, COINGECKO_ONLY,
)

# Cache en memoria para no golpear MongoDB en cada batalla
_oracle_cache: dict = {}   # {"BTC": 1.03, "ETH": 0.97, ...}
_last_update: datetime = None


class OracleService:

    @staticmethod
    def get_cached_multipliers() -> dict:
        """Retorna multiplicadores actuales desde la cache en memoria."""
        return dict(_oracle_cache) if _oracle_cache else {k: 1.0 for k in CRYPTO_ATTRIBUTE_MAP}

    @staticmethod
    async def _fetch_binance(base_url: str) -> dict:
        """
        Llama a Binance (ticker 24h) para las 12 criptos con par USDT.
        Una sola llamada con symbols=[...]. Retorna el mismo formato que
        CoinGecko: {"BTC": {"price": 61087.42, "change_24h": 0.227}, ...}
        donde change_24h es la variación % a 24h (priceChangePercent).
        Lanza excepción si el dominio falla (451 ubicación restringida,
        429, timeout, red) para que el llamador pruebe la siguiente fuente.
        """
        pairs = list(BINANCE_SYMBOLS.values())
        url = f"{base_url}/api/v3/ticker/24hr"
        # OJO: json.dumps por defecto mete un espacio tras cada coma
        # ('["BTCUSDT", "ETHUSDT"]') y httpx lo codifica como %20; el
        # validador de Binance rechaza el espacio con 400 "Illegal
        # characters found in parameter 'symbols'". JSON compacto, sin
        # espacios, es la forma que Binance acepta.
        params = {"symbols": json.dumps(pairs, separators=(",", ":"))}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, dict):
            # Binance.com desde IP restringida (EEUU) puede responder con
            # {"code": 0, "msg": "Service unavailable from a restricted
            # location..."} en vez de la lista de tickers.
            raise ValueError(
                f"Binance {base_url} restringido: {data.get('msg', data)}"
            )

        reverse_map = {v: k for k, v in BINANCE_SYMBOLS.items()}
        result = {}
        for item in data:
            symbol = reverse_map.get(item.get("symbol"))
            if not symbol:
                continue
            try:
                change = float(item.get("priceChangePercent"))
                price = float(item.get("lastPrice"))
            except (TypeError, ValueError):
                # Dato inválido: se deja en None y refresh() conserva el
                # último multiplicador bueno de ese cripto.
                change, price = None, None
            result[symbol] = {"price": price, "change_24h": change}
        return result

    @staticmethod
    async def _fetch_coingecko(symbols: list | None = None) -> dict:
        """
        Llama a CoinGecko para obtener precios y variación a 24h.
        symbols: subconjunto a pedir (None = los 15). Retorna:
        {"BTC": {"price": 68000, "change_24h": 2.4}, ...}
        """
        wanted = symbols or list(COINGECKO_IDS)
        ids = ",".join(COINGECKO_IDS[s] for s in wanted)
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
    async def fetch_prices() -> dict:
        """
        Orquesta las fuentes del oráculo. Orden de fuentes:
        1. Binance (binance.com, luego binance.us si el primero responde
           ubicación restringida o falla) para las 12 criptos con par USDT.
        2. CoinGecko completa los 3 que no están en Binance (XMR, XAG, XAU).
        3. Si Binance no responde en ningún dominio, CoinGecko cubre los 15
           como respaldo. Si también falla, se propaga la excepción y
           refresh() conserva los multiplicadores anteriores.
        Retorna: {"BTC": {"price": ..., "change_24h": ...}, ...}
        """
        prices = {}
        binance_base = None
        for base in BINANCE_BASES:
            try:
                prices = await OracleService._fetch_binance(base)
                binance_base = base
                print(f"[Oracle] fuente: Binance ({base}) — "
                      f"{len(prices)} símbolos")
                break
            except Exception as e:
                print(f"[Oracle] ⚠ Binance {base} no disponible ({e})")
                continue

        if binance_base:
            try:
                cg = await OracleService._fetch_coingecko(symbols=COINGECKO_ONLY)
                prices.update(cg)
                print("[Oracle] fuente: CoinGecko completa XMR/XAG/XAU")
            except Exception as e:
                print(f"[Oracle] ⚠ CoinGecko (XMR/XAG/XAU) falló ({e}); "
                      f"se conservan sus últimos multiplicadores.")
            return prices

        # Sin Binance: CoinGecko para los 15 (propaga la excepción si falla).
        print("[Oracle] fuente: CoinGecko (respaldo, Binance no disponible)")
        return await OracleService._fetch_coingecko()

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

        Robusto ante fallos de las fuentes (Binance / CoinGecko):
        - Si la llamada falla (429, 451, timeout, red), NO se toca nada: se
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
            # 429/451, timeout o error de red en todas las fuentes: no romper nada.
            print(f"[Oracle] ⚠ Ninguna fuente respondió ({e}). "
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
                print(f"[Oracle] ⚠ {symbol}: sin cambio 24h válido, "
                      f"se conserva multiplicador {kept}")
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
