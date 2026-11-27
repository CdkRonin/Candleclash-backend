"""Separación $TICK comprado vs ganado + límites de venta.

Decisiones del usuario (2026-11-26, no negociables):
- El $TICK comprado (con USDT) se vende libre, sin tope.
- El $TICK ganado (batallas, misiones, bonus del pack) tiene tope
  diario de 17/día.
- Una ventana mensual de 500 cubre las ventas de $TICK ganado por
  encima del tope diario.
- Las tasas (1 USDT = 10 $TICK; 10 $TICK = 0.80 USDT) y los mínimos
  no cambian.

Modelo (colección users, backfill perezoso):
- `tick_bought`: $TICK comprado. `tick_earned`: $TICK ganado.
  Su suma es `tick_balance` (que se mantiene como total para no
  romper el frontend).
- `tick_earned_sold_day` (YYYY-MM-DD) + `tick_earned_sold_today`:
  cuánto ganado se vendió hoy (por tope diario).
- `tick_window_month` (YYYY-MM) + `tick_window_used`: cuánto ganado
  se vendió por ventana este mes.
- Usuarios viejos sin estos campos: todo su saldo es ganado
  (casi todo el $TICK en circulación se ganó jugando).

Venta: se vende primero el comprado (libre), luego el ganado por
tope diario y por último el ganado por ventana mensual. Todo el
débito y los contadores se actualizan en UNA operación atómica
(update_one condicional con reintento si otra operación concurrente
cambió el saldo: el filtro revalida saldo y contadores leídos).
"""

from datetime import datetime

from config import settings

EARNED_DAILY_CAP = settings.TICK_EARNED_DAILY_SELL_CAP   # 17/día
MONTHLY_WINDOW = settings.TICK_EARNED_MONTHLY_WINDOW      # 500/mes

MAX_RETRIES = 5


def today_str(now: datetime = None) -> str:
    return (now or datetime.utcnow()).date().isoformat()


def month_str(now: datetime = None) -> str:
    return (now or datetime.utcnow()).strftime("%Y-%m")


def split_tick(user: dict) -> tuple:
    """(comprado, ganado) con backfill perezoso para usuarios viejos.

    Si falta un campo, se deduce del saldo: todo lo que no es
    comprado es ganado y viceversa. Si faltan los dos, todo es ganado.
    """
    balance = int(user.get("tick_balance", 0))
    bought = user.get("tick_bought")
    earned = user.get("tick_earned")
    if bought is None and earned is None:
        return 0, balance
    if bought is None:
        bought = balance - int(earned)
    if earned is None:
        earned = balance - int(bought)
    return int(bought), int(earned)


def sell_counters(user: dict, today: str = None, month: str = None) -> tuple:
    """(ganado vendido hoy por tope, usado de ventana este mes).

    Los contadores se reinician por fecha: si el día/mes guardado no
    es el actual, el contador vale 0 (sin escribir en la BD: el
    reinicio se materializa en la próxima venta).
    """
    today = today or today_str()
    month = month or month_str()
    sold_today = (
        int(user.get("tick_earned_sold_today", 0))
        if user.get("tick_earned_sold_day") == today else 0
    )
    window_used = (
        int(user.get("tick_window_used", 0))
        if user.get("tick_window_month") == month else 0
    )
    return sold_today, window_used


def limits_remaining(user: dict, today: str = None, month: str = None) -> dict:
    """Límites que le quedan al usuario hoy / este mes."""
    sold_today, window_used = sell_counters(user, today, month)
    return {
        "earned_daily_remaining": max(0, EARNED_DAILY_CAP - sold_today),
        "window_remaining": max(0, MONTHLY_WINDOW - window_used),
    }


def max_sellable(user: dict, today: str = None, month: str = None) -> int:
    """Máximo $TICK vendible ahora: comprado libre + ganado con
    tope diario + ventana mensual."""
    bought, earned = split_tick(user)
    lim = limits_remaining(user, today, month)
    return bought + min(earned, lim["earned_daily_remaining"] + lim["window_remaining"])


def plan_sale(user: dict, amount: int,
              today: str = None, month: str = None) -> dict:
    """Desglose de una venta: cuánto sale de comprado, cuánto de
    ganado por tope diario y cuánto por ventana mensual.

    Lanza ValueError con un mensaje para el usuario si no alcanza
    (incluye el máximo vendible y los límites restantes).
    """
    today = today or today_str()
    month = month or month_str()
    bought, earned = split_tick(user)
    lim = limits_remaining(user, today, month)
    max_sell = bought + min(
        earned, lim["earned_daily_remaining"] + lim["window_remaining"])
    if amount > max_sell:
        raise ValueError(
            f"Solo puedes vender {max_sell} $TICK ahora: "
            f"{bought} comprados (sin límite) y "
            f"{max_sell - bought} ganados "
            f"(quedan {lim['earned_daily_remaining']} del tope diario "
            f"de {EARNED_DAILY_CAP} y {lim['window_remaining']} de la "
            f"ventana mensual de {MONTHLY_WINDOW})"
        )
    from_bought = min(amount, bought)
    rest = amount - from_bought
    from_daily = min(rest, lim["earned_daily_remaining"])
    from_window = rest - from_daily
    return {
        "from_bought": from_bought,
        "from_earned_daily": from_daily,
        "from_earned_window": from_window,
        "earned_daily_remaining_after": lim["earned_daily_remaining"] - from_daily,
        "window_remaining_after": lim["window_remaining"] - from_window,
        "max_sellable": max_sell,
    }


async def ensure_tick_split(db, wallet: str, user: dict = None) -> dict:
    """Backfill de los campos de separación para usuarios viejos.

    Una sola actualización atómica con pipeline: rellena lo que falta
    con los valores actuales del documento (ganado = saldo -
    comprado). Si los campos ya existen, no escribe nada. Devuelve
    el documento actualizado (o el que se pasó, si ya estaba completo).
    El filtro ($exists) hace que dos normalizaciones simultáneas no
    se pisen: solo la primera escribe. El documento devuelto excluye
    _id y nonce (igual que /me): nunca se filtra el nonce.
    """
    if user is None:
        user = await db.users.find_one({"wallet_address": wallet},
                                       {"_id": 0, "nonce": 0})
        if not user:
            return None
    if user.get("tick_bought") is not None and user.get("tick_earned") is not None:
        return user
    await db.users.update_one(
        {"wallet_address": wallet,
         "$or": [{"tick_bought": {"$exists": False}},
                 {"tick_earned": {"$exists": False}}]},
        [{"$set": {
            "tick_bought": {"$ifNull": ["$tick_bought", 0]},
            "tick_earned": {"$ifNull": ["$tick_earned", {"$subtract": [
                "$tick_balance", {"$ifNull": ["$tick_bought", 0]}]}]},
            "tick_earned_sold_day": {"$ifNull": ["$tick_earned_sold_day", None]},
            "tick_earned_sold_today": {"$ifNull": ["$tick_earned_sold_today", 0]},
            "tick_window_month": {"$ifNull": ["$tick_window_month", None]},
            "tick_window_used": {"$ifNull": ["$tick_window_used", 0]},
        }}],
    )
    return await db.users.find_one({"wallet_address": wallet},
                                   {"_id": 0, "nonce": 0})


async def apply_sale(db, wallet: str, amount: int,
                     usdt_received: float) -> dict:
    """Vende `amount` $TICK por `usdt_received` USDT.

    El débito (saldo, comprado, ganado, USDT) y los contadores
    (tope diario, ventana) se actualizan en UNA operación atómica:
    el filtro revalida el saldo y los contadores leídos, así que si
    otra operación concurrente cambió el saldo, la actualización no
    aplica y se reintenta con valores frescos (hasta 5 veces).
    Devuelve el desglose de la venta y el saldo resultante.
    Lanza ValueError si no hay saldo o si los límites no alcanzan.
    """
    for _ in range(MAX_RETRIES):
        user = await db.users.find_one({"wallet_address": wallet})
        if not user:
            raise ValueError("Usuario no encontrado")
        if user.get("tick_bought") is None or user.get("tick_earned") is None:
            user = await ensure_tick_split(db, wallet, user)
        balance = int(user.get("tick_balance", 0))
        if balance < amount:
            raise ValueError("Saldo $TICK insuficiente")
        today = today_str()
        month = month_str()
        plan = plan_sale(user, amount, today, month)
        sold_today, window_used = sell_counters(user, today, month)
        bought, earned = split_tick(user)
        res = await db.users.update_one(
            {
                "wallet_address": wallet,
                "tick_balance": balance,
                "tick_bought": bought,
                "tick_earned": earned,
                "tick_earned_sold_day": user.get("tick_earned_sold_day"),
                "tick_earned_sold_today": user.get("tick_earned_sold_today", 0),
                "tick_window_month": user.get("tick_window_month"),
                "tick_window_used": user.get("tick_window_used", 0),
            },
            {
                "$inc": {
                    "tick_balance": -amount,
                    "tick_bought": -plan["from_bought"],
                    "tick_earned": -(plan["from_earned_daily"]
                                     + plan["from_earned_window"]),
                    "usdt_balance": usdt_received,
                },
                "$set": {
                    "tick_earned_sold_day": today,
                    "tick_earned_sold_today": sold_today + plan["from_earned_daily"],
                    "tick_window_month": month,
                    "tick_window_used": window_used + plan["from_earned_window"],
                },
            },
        )
        if res.modified_count == 1:
            return {
                **plan,
                "tick_balance_after": balance - amount,
                "tick_bought_after": bought - plan["from_bought"],
                "tick_earned_after": (
                    earned - plan["from_earned_daily"] - plan["from_earned_window"]
                ),
            }
        # Otra operación concurrente cambió el saldo: reintentar
        # con valores frescos.
    raise ValueError("No se pudo completar la venta, reintenta")


async def charge_tick(db, wallet: str, cost: int):
    """Cobra `cost` $TICK del saldo total, gastando primero el ganado
    y luego el comprado (misma regla para forja, duelo con entrada y
    coliseo: una sola implementación, sin duplicar la regla).

    Devuelve {"from_earned": int, "from_bought": int} si cobró, o
    None si no hay saldo. La actualización es condicional: el filtro
    revalida saldo y separación leídos, así que dos cobros
    simultáneos no pueden gastar el mismo $TICK dos veces; se
    reintenta hasta 3 veces y luego devuelve None (igual que saldo
    insuficiente, el llamador revierte su operación).
    """
    for _ in range(3):
        user = await db.users.find_one({"wallet_address": wallet})
        if not user:
            return None
        if user.get("tick_bought") is None or user.get("tick_earned") is None:
            user = await ensure_tick_split(db, wallet, user)
        balance = int(user.get("tick_balance", 0))
        if balance < cost:
            return None
        bought, earned = split_tick(user)
        from_earned = min(cost, earned)
        from_bought = cost - from_earned
        res = await db.users.update_one(
            {"wallet_address": wallet,
             "tick_balance": balance,
             "tick_bought": bought,
             "tick_earned": earned},
            {"$inc": {"tick_balance": -cost,
                      "tick_earned": -from_earned,
                      "tick_bought": -from_bought}},
        )
        if res.modified_count == 1:
            return {"from_earned": from_earned, "from_bought": from_bought}
    return None


async def refund_tick(db, wallet: str, from_earned: int, from_bought: int):
    """Devuelve $TICK cobrado antes, a los mismos cubos de los que
    salió (para compensar un cobro cuando la operación posterior
    falla, p. ej. no se pudo crear el duelo o el coliseo se llenó
    entre el cobro y la inscripción). Suma simple: no puede fallar
    por saldo insuficiente."""
    total = int(from_earned) + int(from_bought)
    if total <= 0:
        return
    await db.users.update_one(
        {"wallet_address": wallet},
        {"$inc": {"tick_balance": total,
                  "tick_earned": int(from_earned),
                  "tick_bought": int(from_bought)}},
    )
