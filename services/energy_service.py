"""
Energía — CandleClash
Tope 10, 1 energía por batalla, regenera 1 cada 30 min.
La regeneración se calcula al leer (sin jobs en segundo plano): el
usuario guarda `energy` y `energy_updated_at`; al leer se avanza el
reloj por los intervalos completos de 30 min transcurridos. Los
intervalos parciales no se pierden: `energy_updated_at` avanza solo
por los intervalos ya ganados. Usuarios viejos sin estos campos
empiezan llenos (10).
"""

import math
from datetime import datetime, timedelta
from config import settings


def compute_energy(user: dict, now: datetime = None) -> dict:
    """Energía actual + minutos hasta la próxima, sin tocar la BD.

    Retorna {"energy", "minutes_to_next", "energy_updated_at"}.
    """
    now = now or datetime.utcnow()
    energy = user.get("energy", settings.ENERGY_MAX)
    updated_at = user.get("energy_updated_at") or now
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)

    if energy >= settings.ENERGY_MAX:
        return {
            "energy": settings.ENERGY_MAX,
            "minutes_to_next": 0,
            "energy_updated_at": now,
        }

    elapsed = max(0.0, (now - updated_at).total_seconds())
    gained = int(elapsed // settings.ENERGY_REGEN_SECONDS)
    new_energy = min(settings.ENERGY_MAX, energy + gained)

    if new_energy >= settings.ENERGY_MAX:
        new_updated_at = now
        minutes = 0
    else:
        # Avanzar solo por los intervalos ganados: el progreso parcial
        # hacia la próxima energía se conserva.
        new_updated_at = updated_at + timedelta(
            seconds=gained * settings.ENERGY_REGEN_SECONDS
        )
        remaining = settings.ENERGY_REGEN_SECONDS - (
            (now - new_updated_at).total_seconds()
            % settings.ENERGY_REGEN_SECONDS
        )
        minutes = math.ceil(remaining / 60)

    return {
        "energy": new_energy,
        "minutes_to_next": minutes,
        "energy_updated_at": new_updated_at,
    }


async def refresh_energy(db, wallet: str, now: datetime = None) -> dict:
    """Recalcula la energía del usuario y la persiste.

    Retorna {"energy", "minutes_to_next", "energy_updated_at"}.
    Usuario inexistente: lleno.
    """
    user = await db.users.find_one({"wallet_address": wallet.lower()})
    if not user:
        return {"energy": settings.ENERGY_MAX, "minutes_to_next": 0,
                "energy_updated_at": now or datetime.utcnow()}
    state = compute_energy(user, now)
    await db.users.update_one(
        {"wallet_address": wallet.lower()},
        {"$set": {
            "energy": state["energy"],
            "energy_updated_at": state["energy_updated_at"],
        }},
    )
    return {"energy": state["energy"], "minutes_to_next": state["minutes_to_next"],
            "energy_updated_at": state["energy_updated_at"]}


async def consume_energy(db, wallet: str) -> dict:
    """Consume 1 energía para una batalla.

    Retorna {"energy", "minutes_to_next"} con la energía restante.
    Si no hay energía, lanza ValueError con los minutos que faltan.
    El decremento es atómico ($inc con condición energy >= 1), así
    dos batallas simultáneas no pueden gastar la última energía dos
    veces.
    """
    state = await refresh_energy(db, wallet)
    if state["energy"] < settings.ENERGY_PER_BATTLE:
        raise ValueError(
            f"Sin energía. Próxima energía en {state['minutes_to_next']} min."
        )
    res = await db.users.update_one(
        {
            "wallet_address": wallet.lower(),
            "energy": {"$gte": settings.ENERGY_PER_BATTLE},
        },
        {"$inc": {"energy": -settings.ENERGY_PER_BATTLE}},
    )
    if res.modified_count == 0:
        # Otro consumo simultáneo ganó la última energía: recalcular.
        state = await refresh_energy(db, wallet)
        raise ValueError(
            f"Sin energía. Próxima energía en {state['minutes_to_next']} min."
        )
    # Recalcular los minutos con la energía YA descontada: si estaba
    # llena, la próxima llega en 30 min. Antes se devolvían los minutos
    # del estado previo al consumo (0 con energía llena) y la cabecera
    # se quedaba en "+1 en 0 min" sin regenerar nunca en pantalla.
    post_state = compute_energy(
        {
            "energy": state["energy"] - settings.ENERGY_PER_BATTLE,
            "energy_updated_at": state["energy_updated_at"],
        }
    )
    return {
        "energy": post_state["energy"],
        "minutes_to_next": post_state["minutes_to_next"],
    }
