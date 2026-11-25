"""
Motor de Batalla — CandleClash
TODA la lógica ocurre en el servidor. El cliente solo recibe el log de resultado.
Los atributos son calculados aquí con los multiplicadores del oráculo en tiempo real.
"""

import logging
import random
from datetime import datetime
from models.gladiator import Gladiator
from services.oracle_service import OracleService
from services.energy_service import refresh_energy, compute_energy
from config import ability_for_race
from database import get_db

logger = logging.getLogger(__name__)


class NoEnergyError(ValueError):
    """Sin energía para pelear (el router lo traduce a 403)."""


MAX_ROUNDS = 25
XP_WIN  = 40
XP_LOSE = 20

RARITY_ORDER = ["Cobre", "Bronce", "Plata", "Oro", "Diamante", "Legendario"]


def _opp_rarity_pool(player_rarity: str) -> list:
    """Rarezas posibles del rival: hasta la del jugador y como mucho dos
    escalones por debajo, ponderadas hacia abajo. Con el pool viejo
    (máx. Plata) un Legendario Lv42 (~12000 HP) se enfrentaba a rivales
    de ~3800 HP: el rival no puede ser mucho más débil que el jugador."""
    idx = RARITY_ORDER.index(player_rarity) if player_rarity in RARITY_ORDER else 0
    pool = RARITY_ORDER[max(0, idx - 2):idx + 1]
    return pool + pool[:-1] if len(pool) > 1 else pool


def _fighter_state(stats: dict, is_p1: bool) -> dict:
    """Estado de un luchador en la batalla: stats, habilidad de su raza
    (config.RACE_ABILITIES) y enfriamiento. El enfriamiento empieza en 0,
    así que la habilidad se dispara en el primer ataque de la ronda 1."""
    return {
        "stats": stats,
        "is_p1": is_p1,
        "cd": 0,
        "block_next": False,   # la habilidad de bloqueo protege del próximo ataque
        "reduce_next": False,  # la habilidad de reducción amortigua el próximo ataque
        "ability": ability_for_race(stats.get("race", "Humano")),
        "max_hp": stats["vida"],
    }


def simulate_battle(attacker: dict, defender: dict) -> tuple[dict, list]:
    """
    Simula una batalla completa entre dos gladiadores (stats ya computados).
    Retorna (resultado_dict, log_de_rondas).

    Habilidades (una por raza, ver config.RACE_ABILITIES): se disparan
    automáticamente en el primer ataque de la ronda en que el
    enfriamiento está listo (rondas 1, 1+cooldown, 1+2*cooldown…).
    El efecto se aplica en el servidor y queda en el log con
    type "ability" (nombre de la habilidad, quién la usó y su efecto).
    La batalla sigue siendo automática: el jugador no decide nada.
    """
    p1 = _fighter_state(attacker, True)
    p2 = _fighter_state(defender, False)
    p1_hp = attacker["vida"]
    p2_hp = defender["vida"]
    log = []

    def ability_entry(rnd, att, ability, **extra):
        entry = {
            "round": rnd,
            "type": "ability",
            "attacker": att["stats"]["race"],
            "ability": ability["nombre"],
            "ability_id": ability["id"],
            "effect": ability["kind"],
            "value": ability["value"],
        }
        entry.update(extra)
        log.append(entry)

    def attack(att, dfn, hp_att, hp_dfn, allow_ability=True):
        """Un ataque de `att` contra `dfn`. Devuelve (hp_att, hp_dfn).
        Si la habilidad es de ataque extra, ataca dos veces en la ronda
        (el segundo golpe no vuelve a disparar la habilidad)."""
        rnd = battle_round[0]
        force_crit = False
        dmg_mult = 1.0
        extra = False

        # Habilidad: se dispara al atacar, si el enfriamiento está listo.
        if allow_ability and att["cd"] == 0:
            ability = att["ability"]
            att["cd"] = ability["cooldown"]
            kind = ability["kind"]
            if kind == "heal":
                amount = min(int(att["max_hp"] * ability["value"]),
                             att["max_hp"] - hp_att)
                hp_att = min(att["max_hp"], hp_att + amount)
                ability_entry(rnd, att, ability, healed=amount)
            elif kind == "block":
                att["block_next"] = True
                ability_entry(rnd, att, ability)
            elif kind == "reduce":
                att["reduce_next"] = True
                ability_entry(rnd, att, ability)
            elif kind == "crit":
                force_crit = True
                ability_entry(rnd, att, ability)
            elif kind == "damage_mult":
                dmg_mult = ability["value"]
                ability_entry(rnd, att, ability)
            elif kind == "extra_attack":
                extra = True
                ability_entry(rnd, att, ability)

        def strike(hp_att, hp_dfn, force_crit, dmg_mult):
            # El crítico es del atacante; el bloqueo y el reflejo son del defensor.
            crit = force_crit or random.random() < att["stats"].get("prob_critico", 0.15)
            # Bloqueo por habilidad: protege del próximo ataque, sin sorteo.
            if dfn["block_next"]:
                dfn["block_next"] = False
                log.append({"round": rnd, "type": "block",
                            "attacker": att["stats"]["race"], "defender": dfn["stats"]["race"]})
                return hp_att, hp_dfn
            block = random.random() < dfn["stats"].get("prob_bloqueo", 0.10)
            reflect = random.random() < dfn["stats"].get("prob_reflejo", 0.08)

            if block:
                log.append({"round": rnd, "type": "block",
                            "attacker": att["stats"]["race"], "defender": dfn["stats"]["race"]})
                return hp_att, hp_dfn  # Sin daño

            dmg = max(5, int(
                att["stats"]["atq_fisico"] * (0.80 + random.random() * 0.45)
                - dfn["stats"]["defensa"] * 0.55
            ))
            if crit:
                dmg = int(dmg * 2.1)
            if dmg_mult != 1.0:
                dmg = int(dmg * dmg_mult)
            # Reducción por habilidad: el ataque hace (1 − value) de daño.
            if dfn["reduce_next"]:
                dfn["reduce_next"] = False
                dmg = max(1, int(dmg * (1 - dfn["ability"]["value"])))

            # Reflejo: el defensor devuelve parte del daño
            reflected = int(dmg * dfn["stats"].get("reflejo", 0) / 100) if reflect else 0

            hp_dfn = max(0, hp_dfn - dmg)
            hp_att = max(0, hp_att - reflected)

            log.append({
                "round":     rnd,
                "type":      "crit" if crit else "reflect" if reflect else "normal",
                "attacker":  att["stats"]["race"],
                "defender":  dfn["stats"]["race"],
                "damage":    dmg,
                "reflected": reflected,
                "crit":      crit,
            })
            return hp_att, hp_dfn

        hp_att, hp_dfn = strike(hp_att, hp_dfn, force_crit, dmg_mult)
        if extra and hp_dfn > 0:
            # Segundo golpe: sin habilidad y sin crítico forzado.
            hp_att, hp_dfn = strike(hp_att, hp_dfn, False, 1.0)
        return hp_att, hp_dfn

    battle_round = [1]
    for rnd in range(1, MAX_ROUNDS + 1):
        battle_round[0] = rnd
        # El enfriamiento baja 1 por ronda (después de la ronda 1, en la
        # que ya se disparó si estaba listo).
        for st in (p1, p2):
            if st["cd"] > 0:
                st["cd"] -= 1
        # Orden de ataque: mayor vel_ataque va primero
        first, second = (p1, p2) if p1["stats"]["vel_ataque"] >= p2["stats"]["vel_ataque"] else (p2, p1)
        first_is_p1 = first is p1

        # Primer atacante
        if first_is_p1:
            p1_hp, p2_hp = attack(p1, p2, p1_hp, p2_hp)
        else:
            p2_hp, p1_hp = attack(p2, p1, p2_hp, p1_hp)

        if p1_hp <= 0 or p2_hp <= 0:
            break

        # Segundo atacante
        if first_is_p1:
            p2_hp, p1_hp = attack(p2, p1, p2_hp, p1_hp)
        else:
            p1_hp, p2_hp = attack(p1, p2, p1_hp, p2_hp)

        if p1_hp <= 0 or p2_hp <= 0:
            break

    winner = "p1" if p1_hp > p2_hp else "p2" if p2_hp > p1_hp else "draw"
    return {
        "winner":   winner,
        "p1_hp_left": p1_hp,
        "p2_hp_left": p2_hp,
        "rounds":   max((r["round"] for r in log), default=0),
    }, log


async def run_battle(player_wallet: str, gladiator_id: str) -> dict:
    """
    Endpoint principal de batalla:
    1. Carga el gladiador del jugador
    2. Genera un oponente automático calibrado
    3. Calcula stats con el oráculo
    4. Simula la batalla EN MEMORIA (sin escribir nada)
    5. Aplica en UNA sola actualización atómica: −1 energía + recompensas
       ($TICK, trofeos, victorias/derrotas), con filtro energy >= 1. Si
       dos batallas simultáneas pelean por la última energía, solo una
       aplica; la otra recibe NoEnergyError y no pierde nada.
    6. Actualiza el XP del gladiador (releyendo sus valores justo antes).
    7. Guarda el documento de batalla; si ESO falla, solo queda en el
       log: el usuario ya tiene su energía descontada y sus recompensas.

    Antes la energía se descontaba en el router ANTES de simular: si
    algo fallaba después, el usuario perdía energía sin resultado.
    """
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()

    # Cargar gladiador del jugador
    nft_doc = await db.gladiators.find_one({
        "token_id": gladiator_id,
        "owner_wallet": player_wallet.lower(),
        "is_burned": False,
    })
    if not nft_doc:
        raise ValueError("Gladiador no encontrado o no te pertenece")

    p1_gladiator = Gladiator(**nft_doc)
    p1_stats = p1_gladiator.compute_stats(oracle_mults)

    # Generar oponente de poder SIMILAR al jugador. Antes: nivel ±5 con
    # rareza al azar, lo que daba peleas absurdas (p. ej. 12016 HP contra
    # 3831 HP) sin explicación. Ahora se prueban candidatos (nivel ±5,
    # rareza del pool habitual) y se queda el más cercano en vida; si
    # uno cae dentro del ±30%, se toma ese.
    p1_hp = p1_stats["vida"]
    opp = None
    p2_stats = None
    best_gap = None
    for _ in range(40):
        cand = Gladiator(
            token_id="npc",
            owner_wallet="system",
            race=random.choice(["Orco", "Dracónido", "Minotauro", "Forjado", "Tiefling"]),
            lineage=random.choice(list(oracle_mults.keys())),
            rarity=random.choice(_opp_rarity_pool(p1_gladiator.rarity)),
            level=max(1, min(50, p1_gladiator.level + random.randint(-5, 5))),
        )
        cand_stats = cand.compute_stats(oracle_mults)
        gap = abs(cand_stats["vida"] - p1_hp) / max(1, p1_hp)
        if best_gap is None or gap < best_gap:
            opp, p2_stats, best_gap = cand, cand_stats, gap
        if 0.70 <= cand_stats["vida"] / max(1, p1_hp) <= 1.30:
            opp, p2_stats = cand, cand_stats
            break
    opp_lineage = opp.lineage
    opp_rarity = opp.rarity
    opp_level = opp.level

    # Simular (en memoria: no se ha escrito nada todavía)
    result, log = simulate_battle(p1_stats, p2_stats)
    player_won = result["winner"] == "p1"

    # Recompensas
    tick_reward  = random.randint(8, 30) if player_won else 3
    xp_gained    = XP_WIN if player_won else XP_LOSE
    trophy_delta = 25 if player_won else -10

    # Energía regenerada hasta ahora (lectura con persistencia, como
    # siempre: la regeneración se calcula al leer, sin jobs).
    energy_state = await refresh_energy(db, player_wallet)
    if energy_state["energy"] < 1:
        raise NoEnergyError(
            f"Sin energía. Próxima energía en "
            f"{energy_state['minutes_to_next']} min."
        )

    # UNA sola actualización atómica: energía + recompensas juntas.
    applied = await db.users.update_one(
        {"wallet_address": player_wallet.lower(), "energy": {"$gte": 1}},
        {"$inc": {
            "energy": -1,
            "tick_balance": tick_reward,
            "trophies":     trophy_delta,
            "wins":         1 if player_won else 0,
            "losses":       0 if player_won else 1,
        }},
    )
    if applied.modified_count == 0:
        # Otra batalla simultánea ganó la última energía.
        state = await refresh_energy(db, player_wallet)
        raise NoEnergyError(
            f"Sin energía. Próxima energía en {state['minutes_to_next']} min."
        )
    # Energía restante y minutos con la energía YA descontada (igual
    # que hacía consume_energy: con energía llena, la próxima en 30 min).
    post_state = compute_energy({
        "energy": energy_state["energy"] - 1,
        "energy_updated_at": energy_state["energy_updated_at"],
    })

    # XP del gladiador: releer sus valores justo antes de escribir,
    # para no pisar una forja simultánea con valores viejos.
    fresh = await db.gladiators.find_one({
        "token_id": gladiator_id,
        "owner_wallet": player_wallet.lower(),
        "is_burned": False,
    })
    new_level = p1_gladiator.level
    new_xp = p1_gladiator.xp
    if fresh:
        new_xp = fresh["xp"] + xp_gained
        new_level = fresh["level"]
        while new_level < 50 and new_xp >= int(80 * (new_level ** 1.35)):
            new_xp -= int(80 * (new_level ** 1.35))
            new_level += 1
        xp_res = await db.gladiators.update_one(
            {"token_id": gladiator_id,
             "owner_wallet": player_wallet.lower(),
             "is_burned": False,
             "level": fresh["level"], "xp": fresh["xp"]},
            {"$set": {"xp": new_xp, "level": new_level,
                      "updated_at": datetime.utcnow()}},
        )
        if xp_res.modified_count == 0:
            logger.warning(
                "batalla: no se pudo aplicar el XP al gladiador %s "
                "(cambio concurrente); recompensas ya acreditadas",
                gladiator_id,
            )
    else:
        logger.warning(
            "batalla: gladiador %s desapareció tras la pelea; "
            "recompensas ya acreditadas", gladiator_id
        )

    # Guardar batalla: si falla, solo queda en el log; el usuario ya
    # tiene su resultado y sus recompensas.
    battle_doc = {
        "player_wallet":   player_wallet.lower(),
        "gladiator_id":    gladiator_id,
        "opponent_race":   opp.race,
        "opponent_lineage": opp_lineage,
        "opponent_rarity": opp_rarity,
        "result":          result,
        "log":             log,
        "tick_reward":     tick_reward,
        "xp_gained":       xp_gained,
        "level_after":     new_level,
        "timestamp":       datetime.utcnow(),
    }
    try:
        await db.battles.insert_one(battle_doc)
    except Exception as e:
        logger.warning("batalla: no se pudo guardar el documento: %s", e)

    logger.info(
        "batalla wallet=%s gladiador=%s resultado=%s tick=+%d xp=+%d",
        player_wallet.lower(), gladiator_id, result["winner"],
        tick_reward, xp_gained,
    )
    return {
        "result":       result["winner"],
        "rounds":       result["rounds"],
        "tick_reward":  tick_reward,
        "xp_gained":    xp_gained,
        "level_after":  new_level,
        "leveled_up":   new_level > p1_gladiator.level,
        "energy":       post_state["energy"],
        "energy_next_in_min": post_state["minutes_to_next"],
        "opponent": {
            "race":    opp.race,
            "lineage": opp.lineage,
            "rarity":  opp.rarity,
            "level":   opp.level,
            "vida":    p2_stats["vida"],
            "atq":     p2_stats["atq_fisico"],
            "display_name": opp.display_name(),
            "ability": opp.ability_info(),
        },
        "log":          log,
    }
