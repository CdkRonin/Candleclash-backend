"""
Motor de Batalla — CandleClash
TODA la lógica ocurre en el servidor. El cliente solo recibe el log de resultado.
Los atributos son calculados aquí con los multiplicadores del oráculo en tiempo real.
"""

import random
from datetime import datetime
from models.gladiator import Gladiator
from services.oracle_service import OracleService
from database import get_db


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


def simulate_battle(attacker: dict, defender: dict) -> tuple[dict, list]:
    """
    Simula una batalla completa entre dos gladiadores (stats ya computados).
    Retorna (resultado_dict, log_de_rondas).
    """
    p1_hp = attacker["vida"]
    p2_hp = defender["vida"]
    log = []

    for rnd in range(1, MAX_ROUNDS + 1):
        # Orden de ataque: mayor vel_ataque va primero
        first = attacker if attacker["vel_ataque"] >= defender["vel_ataque"] else defender
        second = defender if first is attacker else attacker
        first_is_p1 = first is attacker

        def attack(att, dfn, hp_att, hp_dfn):
            # El crítico es del atacante; el bloqueo y el reflejo son del defensor.
            crit   = random.random() < att.get("prob_critico", 0.15)
            block  = random.random() < dfn.get("prob_bloqueo", 0.10)
            reflect = random.random() < dfn.get("prob_reflejo", 0.08)

            if block:
                log.append({"round": rnd, "type": "block",
                             "attacker": att["race"], "defender": dfn["race"]})
                return hp_att, hp_dfn  # Sin daño

            dmg = max(5, int(
                att["atq_fisico"] * (0.80 + random.random() * 0.45)
                - dfn["defensa"] * 0.55
            ))
            if crit:
                dmg = int(dmg * 2.1)

            # Reflejo: el defensor devuelve parte del daño
            reflected = int(dmg * dfn.get("reflejo", 0) / 100) if reflect else 0

            hp_dfn = max(0, hp_dfn - dmg)
            hp_att = max(0, hp_att - reflected)

            log.append({
                "round":     rnd,
                "type":      "crit" if crit else "reflect" if reflect else "normal",
                "attacker":  att["race"],
                "defender":  dfn["race"],
                "damage":    dmg,
                "reflected": reflected,
                "crit":      crit,
            })
            return hp_att, hp_dfn

        # Primer atacante
        if first_is_p1:
            p1_hp, p2_hp = attack(attacker, defender, p1_hp, p2_hp)
        else:
            p2_hp, p1_hp = attack(defender, attacker, p2_hp, p1_hp)

        if p1_hp <= 0 or p2_hp <= 0:
            break

        # Segundo atacante
        if first_is_p1:
            p2_hp, p1_hp = attack(defender, attacker, p2_hp, p1_hp)
        else:
            p1_hp, p2_hp = attack(attacker, defender, p1_hp, p2_hp)

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
    4. Simula la batalla en el servidor
    5. Guarda el resultado en MongoDB
    6. Retorna log + recompensas
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

    # Simular
    result, log = simulate_battle(p1_stats, p2_stats)
    player_won = result["winner"] == "p1"

    # Recompensas
    tick_reward  = random.randint(8, 30) if player_won else 3
    xp_gained    = XP_WIN if player_won else XP_LOSE
    trophy_delta = 25 if player_won else -10

    # Actualizar jugador en MongoDB
    update = {
        "$inc": {
            "tick_balance": tick_reward,
            "trophies":     trophy_delta,
            "wins":         1 if player_won else 0,
            "losses":       0 if player_won else 1,
        }
    }
    await db.users.update_one({"wallet_address": player_wallet.lower()}, update)

    # Actualizar XP del gladiador
    new_xp = p1_gladiator.xp + xp_gained
    new_level = p1_gladiator.level
    while new_level < 50 and new_xp >= int(80 * (new_level ** 1.35)):
        new_xp -= int(80 * (new_level ** 1.35))
        new_level += 1

    await db.gladiators.update_one(
        {"token_id": gladiator_id},
        {"$set": {"xp": new_xp, "level": new_level, "updated_at": datetime.utcnow()}},
    )

    # Guardar batalla
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
    await db.battles.insert_one(battle_doc)

    return {
        "result":       result["winner"],
        "rounds":       result["rounds"],
        "tick_reward":  tick_reward,
        "xp_gained":    xp_gained,
        "level_after":  new_level,
        "leveled_up":   new_level > p1_gladiator.level,
        "opponent": {
            "race":    opp.race,
            "lineage": opp.lineage,
            "rarity":  opp.rarity,
            "level":   opp.level,
            "vida":    p2_stats["vida"],
            "atq":     p2_stats["atq_fisico"],
        },
        "log":          log,
    }
