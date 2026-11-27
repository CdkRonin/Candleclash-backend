"""
Duelo táctico — CandleClash
Modo por turnos opcional: en cada ronda el jugador elige una acción
(Atacar, Habilidad o Defender) y el rival responde con una IA simple.
TODA la resolución ocurre en el servidor, como en la batalla automática:
el cliente solo envía la acción elegida y recibe el log de la ronda.

Acciones:
- attack: un golpe normal.
- ability: la habilidad de la raza. Las de daño (damage_mult, crit,
  extra_attack) pegan en esa misma ronda; las de cura, bloqueo y
  reducción no pegan (cuestan el ataque de la ronda).
- defend: te guardas; el próximo ataque que recibas hace la mitad
  de daño. Cuesta el ataque de la ronda.

La fórmula de daño es la misma que en services/battle_service.py
(simulate_battle): si cambia allí, hay que cambiarla aquí también.
El estado del duelo vive en Mongo (colección duels) para que sobreviva
a un reinicio del servidor; cada acto usa el número de ronda como
filtro optimista para que dos actos simultáneos no pisen el estado.
"""

import logging
import random
import uuid
from datetime import datetime

from models.gladiator import Gladiator
from services.battle_service import (
    MAX_ROUNDS,
    NoEnergyError,
    _crit_mult,
    _first_attacker,
    _vel_recup_reduction,
    battle_rewards,
    generate_opponent,
)
from services.energy_service import compute_energy, refresh_energy
from services.oracle_service import OracleService
from services.tick_service import ensure_tick_split
from config import ability_for_race
from database import get_db

logger = logging.getLogger(__name__)

ACTIONS = ("attack", "ability", "defend")


def _fighter_state(stats: dict, is_p1: bool) -> dict:
    """Estado de un luchador en el duelo. Espejo de
    battle_service._fighter_state, más la vida actual, la guardia
    (Defender) y los golpes dados (la magia cae cada 3er golpe)."""
    ability = ability_for_race(stats.get("race", "Humano"))
    return {
        "stats": stats,
        "is_p1": is_p1,
        "hp": stats["vida"],
        "max_hp": stats["vida"],
        "cd": 0,
        "cd_max": max(1, ability["cooldown"] - _vel_recup_reduction(stats)),
        "block_next": False,
        "reduce_next": False,
        "guard": False,
        "ability": ability,
        "strikes": 0,
    }


def _public_fighter(st: dict) -> dict:
    """Lo que el cliente ve de un luchador (sin los stats completos)."""
    stats = st["stats"]
    return {
        "race": stats.get("race"),
        "lineage": stats.get("lineage"),
        "hp": st["hp"],
        "max_hp": st["max_hp"],
        "cd": st["cd"],
        "cd_max": st["cd_max"],
        "ability": {
            "id": st["ability"]["id"],
            "nombre": st["ability"]["nombre"],
            "descripcion": st["ability"]["descripcion"],
            "cooldown": st["ability"]["cooldown"],
        },
    }


def public_duel(duel: dict, round_log: list = None) -> dict:
    """Vista pública de un duelo (sin _id de Mongo ni stats internos)."""
    out = {
        "duel_id": duel["duel_id"],
        "status": duel["status"],
        "round": duel["round"],
        "winner": duel.get("winner"),
        "p1": _public_fighter(duel["p1"]),
        "p2": _public_fighter(duel["p2"]),
        "opponent": duel["opponent"],
        "round_log": round_log if round_log is not None else [],
    }
    if duel["status"] == "finished":
        out["rewards"] = duel.get("rewards")
    return out


def choose_rival_action(p2: dict) -> str:
    """IA simple del rival: usa la habilidad cuando está lista y conviene,
    se defiende con poca vida, y si no ataca."""
    ability = p2["ability"]
    hp_frac = p2["hp"] / max(1, p2["max_hp"])
    if p2["cd"] == 0:
        if ability["kind"] == "heal" and hp_frac < 0.7:
            return "ability"
        if ability["kind"] in ("damage_mult", "crit", "extra_attack"):
            return "ability" if random.random() < 0.5 else "attack"
        if ability["kind"] in ("block", "reduce"):
            return "ability" if random.random() < 0.4 else "attack"
    if hp_frac < 0.25 and random.random() < 0.3:
        return "defend"
    return "attack"


def _strike(att: dict, dfn: dict, rnd: int, log: list,
            force_crit: bool = False, dmg_mult: float = 1.0):
    """Un golpe de `att` contra `dfn`. Misma fórmula que el strike de
    simulate_battle en battle_service.py: crítico del atacante, bloqueo
    y reflejo del defensor, daño físico menos defensa y bloqueo (XAU),
    magia cada 3er golpe, reducción por habilidad y guardia (Defender,
    que deja el golpe a la mitad). Devuelve nada; actualiza hp en sitio
    y añade la entrada al log."""
    # Bloqueo por habilidad: protege del próximo ataque, sin sorteo.
    if dfn["block_next"]:
        dfn["block_next"] = False
        log.append({"round": rnd, "type": "block",
                    "attacker": att["stats"]["race"],
                    "side": "p1" if att["is_p1"] else "p2",
                    "defender": dfn["stats"]["race"]})
        return
    crit = force_crit or random.random() < att["stats"].get("prob_critico", 0.15)
    block = random.random() < dfn["stats"].get("prob_bloqueo", 0.10)
    reflect = random.random() < dfn["stats"].get("prob_reflejo", 0.08)
    if block:
        log.append({"round": rnd, "type": "block",
                    "attacker": att["stats"]["race"],
                    "side": "p1" if att["is_p1"] else "p2",
                    "defender": dfn["stats"]["race"]})
        return

    dmg = max(5, int(
        att["stats"]["atq_fisico"] * (0.80 + random.random() * 0.45)
        - dfn["stats"]["defensa"] * 0.55
        - dfn["stats"].get("bloqueo", 0) * 0.30
    ))
    if crit:
        dmg = int(dmg * _crit_mult(att["stats"]))
    magic = 0
    att["strikes"] += 1
    if att["strikes"] % 3 == 0 and att["stats"].get("atq_magico", 0) > 0:
        magic = max(0, int(
            att["stats"]["atq_magico"]
            - dfn["stats"].get("def_magica", 0) * 0.55
        ))
        dmg += magic
    if dmg_mult != 1.0:
        dmg = int(dmg * dmg_mult)
    if dfn["reduce_next"]:
        dfn["reduce_next"] = False
        dmg = max(1, int(dmg * (1 - dfn["ability"]["value"])))
    guarded = False
    if dfn["guard"]:
        dfn["guard"] = False
        guarded = True
        dmg = max(1, int(dmg * 0.5))

    reflected = int(dmg * dfn["stats"].get("reflejo", 0) / 100) if reflect else 0
    dfn["hp"] = max(0, dfn["hp"] - dmg)
    att["hp"] = max(0, att["hp"] - reflected)

    entry = {
        "round": rnd,
        "type": "crit" if crit else "reflect" if reflect else "normal",
        "attacker": att["stats"]["race"],
        "side": "p1" if att["is_p1"] else "p2",
        "defender": dfn["stats"]["race"],
        "damage": dmg,
        "reflected": reflected,
        "crit": crit,
    }
    if magic:
        entry["magic"] = magic
    if guarded:
        entry["guarded"] = True
    log.append(entry)


def _ability_entry(rnd: int, st: dict, log: list, **extra):
    ability = st["ability"]
    entry = {
        "round": rnd,
        "type": "ability",
        "attacker": st["stats"]["race"],
        "side": "p1" if st["is_p1"] else "p2",
        "ability": ability["nombre"],
        "ability_id": ability["id"],
        "effect": ability["kind"],
        "value": ability["value"],
    }
    entry.update(extra)
    log.append(entry)


def _do_action(actor: dict, target: dict, action: str, rnd: int, log: list):
    """Resuelve la acción de un luchador en la ronda. Las habilidades de
    daño pegan en esa misma ronda; las de cura, bloqueo y reducción no
    pegan. Defender pone la guardia y no pega. Lanza ValueError si la
    acción no se puede hacer (habilidad en enfriamiento o cura con la
    vida llena); en ese caso la ronda no se consume."""
    ability = actor["ability"]
    if action == "ability" and actor["cd"] > 0:
        raise ValueError(
            f"Habilidad en enfriamiento: lista en {actor['cd']} rondas.")
    if action == "ability" and ability["kind"] == "heal" \
            and actor["hp"] >= actor["max_hp"]:
        raise ValueError("No hay nada que curar: la vida está llena.")
    if action == "defend":
        actor["guard"] = True
        log.append({"round": rnd, "type": "defend",
                    "attacker": actor["stats"]["race"],
                    "side": "p1" if actor["is_p1"] else "p2"})
        return
    if action == "attack":
        _strike(actor, target, rnd, log)
        return
    # action == "ability"
    actor["cd"] = actor["cd_max"]
    kind = ability["kind"]
    if kind == "heal":
        amount = min(int(actor["max_hp"] * ability["value"]),
                     actor["max_hp"] - actor["hp"])
        actor["hp"] = min(actor["max_hp"], actor["hp"] + amount)
        _ability_entry(rnd, actor, log, healed=amount)
    elif kind == "block":
        actor["block_next"] = True
        _ability_entry(rnd, actor, log)
    elif kind == "reduce":
        actor["reduce_next"] = True
        _ability_entry(rnd, actor, log)
    elif kind == "crit":
        _ability_entry(rnd, actor, log)
        _strike(actor, target, rnd, log, force_crit=True)
    elif kind == "damage_mult":
        _ability_entry(rnd, actor, log)
        _strike(actor, target, rnd, log, dmg_mult=ability["value"])
    elif kind == "extra_attack":
        _ability_entry(rnd, actor, log)
        _strike(actor, target, rnd, log)
        if target["hp"] > 0:
            _strike(actor, target, rnd, log)
    else:
        raise ValueError(f"Habilidad desconocida: {kind}")


def _regen(st: dict, rnd: int, log: list):
    """Regeneración (DOGE, Nv45) al empezar la ronda, igual que en la
    batalla automática. Solo se registra si cura algo."""
    amount = int(st["stats"].get("recuperacion", 0) * 0.30)
    healed = min(amount, st["max_hp"] - st["hp"]) if amount > 0 else 0
    if healed > 0:
        log.append({"round": rnd, "type": "regen",
                    "attacker": st["stats"]["race"],
                    "side": "p1" if st["is_p1"] else "p2",
                    "healed": healed})
    st["hp"] += healed


def resolve_round(duel: dict, p1_action: str) -> list:
    """Resuelve una ronda del duelo y actualiza el documento en sitio.
    Devuelve el log de la ronda. No escribe en la base de datos."""
    if p1_action not in ACTIONS:
        raise ValueError(f"Acción inválida: {p1_action}")
    rnd = duel["round"]
    p1, p2 = duel["p1"], duel["p2"]
    log = []

    for st in (p1, p2):
        if st["cd"] > 0:
            st["cd"] -= 1
    _regen(p1, rnd, log)
    _regen(p2, rnd, log)

    p2_action = choose_rival_action(p2)
    first, second = _first_attacker(p1, p2)
    first_is_p1 = first is p1
    first_action = p1_action if first_is_p1 else p2_action
    second_action = p2_action if first_is_p1 else p1_action
    # La acción del jugador se valida antes de resolver nada: si no se
    # puede hacer, la ronda no se consume y no se toca el estado.
    _validate_action(p1, p1_action)

    _do_action(first, second, first_action, rnd, log)
    if p1["hp"] > 0 and p2["hp"] > 0:
        _do_action(second, first, second_action, rnd, log)

    duel["round_log"] = log
    duel["last_rival_action"] = p2_action
    if p1["hp"] <= 0 or p2["hp"] <= 0:
        duel["status"] = "finished"
        duel["winner"] = ("p1" if p1["hp"] > p2["hp"]
                          else "p2" if p2["hp"] > p1["hp"] else "draw")
    elif rnd >= MAX_ROUNDS:
        duel["status"] = "finished"
        duel["winner"] = ("p1" if p1["hp"] > p2["hp"]
                          else "p2" if p2["hp"] > p1["hp"] else "draw")
    else:
        duel["round"] = rnd + 1
    duel["log"].extend(log)
    duel["updated_at"] = datetime.utcnow()
    return log


def _validate_action(st: dict, action: str):
    """Valida la acción del jugador sin tocar el estado: la habilidad
    tiene que estar lista y la cura solo vale con la vida incompleta."""
    if action == "ability" and st["cd"] > 0:
        raise ValueError(
            f"Habilidad en enfriamiento: lista en {st['cd']} rondas.")
    if action == "ability" and st["ability"]["kind"] == "heal" \
            and st["hp"] >= st["max_hp"]:
        raise ValueError("No hay nada que curar: la vida está llena.")


async def start_duel(player_wallet: str, gladiator_id: str) -> dict:
    """Crea un duelo táctico: carga el gladiador, genera un oponente
    calibrado como en la batalla automática y cobra 1 energía al
    empezar (si no hay energía, no se crea nada)."""
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()
    nft_doc = await db.gladiators.find_one({
        "token_id": gladiator_id,
        "owner_wallet": player_wallet.lower(),
        "is_burned": False,
    })
    if not nft_doc:
        raise ValueError("Gladiador no encontrado o no te pertenece")

    p1_gladiator = Gladiator(**nft_doc)
    p1_stats = p1_gladiator.compute_stats(oracle_mults)
    opp, p2_stats = generate_opponent(p1_gladiator, p1_stats, oracle_mults)

    energy_state = await refresh_energy(db, player_wallet)
    if energy_state["energy"] < 1:
        raise NoEnergyError(
            f"Sin energía. Próxima energía en "
            f"{energy_state['minutes_to_next']} min."
        )
    applied = await db.users.update_one(
        {"wallet_address": player_wallet.lower(), "energy": {"$gte": 1}},
        {"$inc": {"energy": -1}},
    )
    if applied.modified_count == 0:
        state = await refresh_energy(db, player_wallet)
        raise NoEnergyError(
            f"Sin energía. Próxima energía en {state['minutes_to_next']} min."
        )
    post_state = compute_energy({
        "energy": energy_state["energy"] - 1,
        "energy_updated_at": energy_state["energy_updated_at"],
    })

    duel = {
        "duel_id": uuid.uuid4().hex,
        "player_wallet": player_wallet.lower(),
        "gladiator_id": gladiator_id,
        "status": "active",
        "round": 1,
        "winner": None,
        "p1": _fighter_state(p1_stats, True),
        "p2": _fighter_state(p2_stats, False),
        "opponent": {
            "race": opp.race,
            "lineage": opp.lineage,
            "rarity": opp.rarity,
            "level": opp.level,
            "vida": p2_stats["vida"],
            "max_hp": p2_stats["vida"],
            "atq": p2_stats["atq_fisico"],
            "display_name": opp.display_name(),
            "ability": opp.ability_info(),
        },
        "log": [],
        "rewards": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    await db.duels.insert_one(duel)
    logger.info("duelo %s creado wallet=%s gladiador=%s",
                duel["duel_id"], player_wallet.lower(), gladiator_id)
    out = public_duel(duel)
    out["energy"] = post_state["energy"]
    out["energy_next_in_min"] = post_state["minutes_to_next"]
    return out


async def _apply_duel_rewards(db, duel: dict) -> dict:
    """Recompensas al terminar el duelo: mismas que la batalla
    automática (ver battle_rewards) y piso de trofeos en 0. Devuelve
    el resumen de recompensas."""
    wallet = duel["player_wallet"]
    rewards = battle_rewards(duel["winner"])
    # El $TICK del duelo es ganado, igual que en la batalla automática.
    await ensure_tick_split(db, wallet)
    await db.users.update_one(
        {"wallet_address": wallet},
        {"$inc": {
            "tick_balance": rewards["tick"],
            "tick_earned": rewards["tick"],
            "trophies": rewards["trophies"],
            "wins": rewards["wins"],
            "losses": rewards["losses"],
        }},
    )
    await db.users.update_one(
        {"wallet_address": wallet},
        {"$max": {"trophies": 0}},
    )
    # XP del gladiador, releyendo sus valores justo antes (igual que
    # en run_battle, para no pisar una forja simultánea).
    fresh = await db.gladiators.find_one({
        "token_id": duel["gladiator_id"],
        "owner_wallet": wallet,
        "is_burned": False,
    })
    new_level = None
    new_xp = None
    if fresh:
        new_xp = fresh["xp"] + rewards["xp"]
        new_level = fresh["level"]
        while new_level < 50 and new_xp >= int(80 * (new_level ** 1.35)):
            new_xp -= int(80 * (new_level ** 1.35))
            new_level += 1
        await db.gladiators.update_one(
            {"token_id": duel["gladiator_id"],
             "owner_wallet": wallet,
             "is_burned": False,
             "level": fresh["level"], "xp": fresh["xp"]},
            {"$set": {"xp": new_xp, "level": new_level,
                      "updated_at": datetime.utcnow()}},
        )
    summary = {
        "tick_reward": rewards["tick"],
        "xp_gained": rewards["xp"],
        "trophies_delta": rewards["trophies"],
        "level_after": new_level,
        "leveled_up": new_level is not None and new_level > fresh["level"]
        if fresh else False,
    }
    try:
        await db.battles.insert_one({
            "player_wallet": wallet,
            "gladiator_id": duel["gladiator_id"],
            "opponent_race": duel["opponent"]["race"],
            "opponent_lineage": duel["opponent"]["lineage"],
            "opponent_rarity": duel["opponent"]["rarity"],
            "mode": "duel",
            "result": {"winner": duel["winner"]},
            "log": duel["log"],
            "tick_reward": rewards["tick"],
            "xp_gained": rewards["xp"],
            "level_after": new_level,
            "timestamp": datetime.utcnow(),
        })
    except Exception as e:
        logger.warning("duelo: no se pudo guardar el documento: %s", e)
    return summary


async def act_duel(player_wallet: str, duel_id: str, action: str) -> dict:
    """Resuelve una ronda del duelo. El filtro incluye el número de
    ronda para que dos actos simultáneos no pisen el mismo estado:
    solo uno aplica y el otro recibe el error y recarga el estado."""
    db = get_db()
    duel = await db.duels.find_one({
        "duel_id": duel_id,
        "player_wallet": player_wallet.lower(),
    })
    if not duel:
        raise ValueError("Duelo no encontrado")
    if duel["status"] != "active":
        raise ValueError("El duelo ya terminó")
    if action not in ACTIONS:
        raise ValueError(f"Acción inválida: {action}")

    current_round = duel["round"]
    _validate_action(duel["p1"], action)
    round_log = resolve_round(duel, action)

    update = {
        "p1": duel["p1"],
        "p2": duel["p2"],
        "round": duel["round"],
        "status": duel["status"],
        "winner": duel["winner"],
        "log": duel["log"],
        "updated_at": duel["updated_at"],
    }
    applied = await db.duels.update_one(
        {"duel_id": duel_id,
         "player_wallet": player_wallet.lower(),
         "status": "active",
         "round": current_round},
        {"$set": update},
    )
    if applied.modified_count == 0:
        raise ValueError("El duelo ya avanzó, recarga el estado")
    duel.update(update)
    # Las recompensas se aplican DESPUÉS de ganar la actualización
    # optimista: si dos actos simultáneos pelean por la misma ronda,
    # solo uno aplica y el otro no acredita nada dos veces.
    if duel["status"] == "finished":
        rewards = await _apply_duel_rewards(db, duel)
        await db.duels.update_one(
            {"duel_id": duel_id,
             "player_wallet": player_wallet.lower()},
            {"$set": {"rewards": rewards}},
        )
        duel["rewards"] = rewards
    out = public_duel(duel, round_log)
    out["rival_action"] = duel.get("last_rival_action")
    return out


async def get_duel(player_wallet: str, duel_id: str) -> dict:
    """Estado actual de un duelo del jugador (para reconectar)."""
    db = get_db()
    duel = await db.duels.find_one({
        "duel_id": duel_id,
        "player_wallet": player_wallet.lower(),
    })
    if not duel:
        raise ValueError("Duelo no encontrado")
    out = public_duel(duel, [])
    out["log"] = duel["log"]
    return out
