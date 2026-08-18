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
        - Multiplicador de rareza
        - Multiplicador de nivel
        - Multiplicador de renacimiento
        - Bono del oráculo cripto para el atributo del linaje
        """
        base = self.base_stats
        rm = self.rarity_multiplier()
        lm = self.level_multiplier()
        rebm = self.rebirth_multiplier()
        total = rm * lm * rebm

        # Obtener el bono del oráculo para este linaje
        oracle_mult = oracle_multipliers.get(self.lineage, 1.0)

        def apply(val, is_lineage_attr=False):
            computed = round(val * total)
            if is_lineage_attr:
                computed = round(computed * oracle_mult)
            return max(1, computed)

        from config import CRYPTO_ATTRIBUTE_MAP
        lineage_attr = CRYPTO_ATTRIBUTE_MAP.get(self.lineage, {}).get("attribute")

        return {
            "vida":         apply(base.vida,         lineage_attr == "vida"),
            "atq_fisico":   apply(base.atq_fisico,   lineage_attr == "atq_fisico"),
            "defensa":      apply(base.defensa,       lineage_attr == "defensa") if self.level >= 10 else 0,
            "vel_ataque":   apply(base.vel_ataque,    lineage_attr == "vel_ataque") if self.level >= 25 else 0,
            "recuperacion": apply(base.recuperacion,  lineage_attr == "recuperacion"),
            "vel_recup":    apply(base.vel_recup,     lineage_attr == "vel_recup"),
            "prioridad":    apply(base.prioridad,     lineage_attr == "prioridad"),
            "critico":      apply(base.critico,       lineage_attr == "critico"),
            "prob_critico": round(min(0.80, base.prob_critico * (oracle_mult if lineage_attr == "prob_critico" else 1.0)), 3),
            "reflejo":      apply(base.reflejo,       lineage_attr == "reflejo"),
            "prob_reflejo": round(min(0.60, base.prob_reflejo * (oracle_mult if lineage_attr == "prob_reflejo" else 1.0)), 3),
            "atq_magico":   apply(base.atq_magico,   lineage_attr == "atq_magico"),
            "def_magica":   apply(base.def_magica,    lineage_attr == "def_magica"),
            "bloqueo":      apply(base.bloqueo,       lineage_attr == "bloqueo"),
            "prob_bloqueo": round(min(0.50, base.prob_bloqueo * (oracle_mult if lineage_attr == "prob_bloqueo" else 1.0)), 3),
        }
