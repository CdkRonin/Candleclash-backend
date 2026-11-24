from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class Rarity(str, Enum):
    COBRE     = "Cobre"
    BRONCE    = "Bronce"
    PLATA     = "Plata"
    ORO       = "Oro"
    DIAMANTE  = "Diamante"
    LEGENDARIO = "Legendario"


class GladiatorStats(BaseModel):
    """Stats base del gladiador (sin multiplicadores de cripto ni nivel)."""
    vida:          int = 500
    atq_fisico:    int = 95
    defensa:       int = 45
    vel_ataque:    int = 30
    recuperacion:  int = 25
    vel_recup:     int = 20
    prioridad:     int = 15
    critico:       int = 10
    prob_critico:  float = 0.15   # 15% base
    reflejo:       int = 8
    prob_reflejo:  float = 0.08
    atq_magico:    int = 30
    def_magica:    int = 25
    bloqueo:       int = 20
    prob_bloqueo:  float = 0.10


class Gladiator(BaseModel):
    """Documento MongoDB para cada NFT gladiador."""
    token_id:      str             # ID único en el contrato ERC-721
    owner_wallet:  str             # Dirección del dueño
    name:          Optional[str] = None  # Nombre elegido en el creador (máx. 20 caracteres); None = sin nombre
    race:          str             # Raza (Humano, Orco, etc.)
    lineage:       str             # Cripto linaje (BTC, ETH, etc.)
    rarity:        Rarity = Rarity.COBRE
    level:         int = 1         # 1–50
    xp:            int = 0
    rebirths:      int = 0         # Número de renacimientos (max 5)
    base_stats:    GladiatorStats = Field(default_factory=GladiatorStats)
    is_burned:     bool = False    # True si fue sacrificado en la Forja
    is_listed:     bool = False    # True si está en el Mercado P2P
    created_at:    datetime = Field(default_factory=datetime.utcnow)
    updated_at:    datetime = Field(default_factory=datetime.utcnow)

    def xp_to_next_level(self) -> int:
        if self.level >= 50:
            return 0
        return int(80 * (self.level ** 1.35))

    def display_name(self) -> str:
        """Nombre que se muestra: el elegido en el creador, o por defecto
        raza + linaje (p. ej. "Orco · BTC"). Los gladiadores viejos sin
        nombre (name=None) usan el nombre por defecto, sin migrar datos."""
        if self.name and self.name.strip():
            return self.name.strip()
        return f"{self.race} · {self.lineage}"

    def ability_info(self) -> dict:
        """Habilidad de la raza de este gladiador (ver config.RACE_ABILITIES)."""
        from config import ability_for_race
        return ability_for_race(self.race)

    def rarity_multiplier(self) -> float:
        return {
            "Cobre": 1.0, "Bronce": 1.4, "Plata": 1.8,
            "Oro": 2.4, "Diamante": 3.2, "Legendario": 4.5,
        }.get(self.rarity, 1.0)

    def level_multiplier(self) -> float:
        return 1 + (self.level - 1) * 0.07

    def rebirth_multiplier(self) -> float:
        return 1 + self.rebirths * 0.20

    def compute_stats(self, oracle_multipliers: dict) -> dict:
        """
        Calcula los stats efectivos incluyendo:
        - Multiplicador de raza (RACE_MODIFIERS: +2 stats, -1 stat por raza)
        - Multiplicador de rareza
        - Multiplicador de nivel
        - Multiplicador de renacimiento
        - Bono del oráculo cripto para el atributo del linaje
        Orden: base * raza * rareza * nivel * renacimiento, y al final el
        oráculo solo sobre el atributo del linaje.
        """
        base = self.base_stats
        rm = self.rarity_multiplier()
        lm = self.level_multiplier()
        rebm = self.rebirth_multiplier()
        total = rm * lm * rebm

        # Obtener el bono del oráculo para este linaje
        oracle_mult = oracle_multipliers.get(self.lineage, 1.0)

        from config import CRYPTO_ATTRIBUTE_MAP, RACE_MODIFIERS
        lineage_attr = CRYPTO_ATTRIBUTE_MAP.get(self.lineage, {}).get("attribute")
        race_mods = RACE_MODIFIERS.get(self.race, {})

        def race_mult(stat: str) -> float:
            return race_mods.get(stat, 1.0)

        def apply(val, stat: str, is_lineage_attr=False):
            computed = round(val * race_mult(stat) * total)
            if is_lineage_attr:
                computed = round(computed * oracle_mult)
            return max(1, computed)

        def apply_prob(val, stat: str, cap: float):
            mult = race_mult(stat) * (oracle_mult if lineage_attr == stat else 1.0)
            return round(min(cap, val * mult), 3)

        return {
            "race":         self.race,
            "lineage":      self.lineage,
            "vida":         apply(base.vida,         "vida",         lineage_attr == "vida"),
            "atq_fisico":   apply(base.atq_fisico,   "atq_fisico",   lineage_attr == "atq_fisico"),
            "defensa":      apply(base.defensa,       "defensa",       lineage_attr == "defensa") if self.level >= 10 else 0,
            "vel_ataque":   apply(base.vel_ataque,    "vel_ataque",    lineage_attr == "vel_ataque") if self.level >= 25 else 0,
            "recuperacion": apply(base.recuperacion,  "recuperacion",  lineage_attr == "recuperacion"),
            "vel_recup":    apply(base.vel_recup,     "vel_recup",     lineage_attr == "vel_recup"),
            "prioridad":    apply(base.prioridad,     "prioridad",     lineage_attr == "prioridad"),
            "critico":      apply(base.critico,       "critico",       lineage_attr == "critico"),
            "prob_critico": apply_prob(base.prob_critico, "prob_critico", 0.80),
            "reflejo":      apply(base.reflejo,       "reflejo",       lineage_attr == "reflejo"),
            "prob_reflejo": apply_prob(base.prob_reflejo, "prob_reflejo", 0.60),
            "atq_magico":   apply(base.atq_magico,   "atq_magico",   lineage_attr == "atq_magico"),
            "def_magica":   apply(base.def_magica,    "def_magica",    lineage_attr == "def_magica"),
            "bloqueo":      apply(base.bloqueo,       "bloqueo",       lineage_attr == "bloqueo"),
            "prob_bloqueo": apply_prob(base.prob_bloqueo, "prob_bloqueo", 0.50),
        }
