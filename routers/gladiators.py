import logging
import random
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from pymongo import ReturnDocument
from typing import Optional
from routers.auth import get_current_user
from services.oracle_service import OracleService
from services.rate_limit import wallet_rate_limit
from database import get_db
from models.gladiator import Gladiator, Rarity
from config import RACE_MODIFIERS, CRYPTO_ATTRIBUTE_MAP

logger = logging.getLogger(__name__)

router = APIRouter()

# Límite de reclutas gratis por wallet (decisión de Yordi 2026-11-23:
# máximo 2 gladiadores gratis; los ya minteados antes del límite se quedan).
FREE_MINT_LIMIT = 2


class MintRequest(BaseModel):
    """Raza y linaje opcionales: si no se mandan, se sortean.
    Nombre opcional del creador: se recortan espacios, máximo 20
    caracteres; vacío o ausente → None (nombre por defecto)."""
    race:    Optional[str] = None
    lineage: Optional[str] = None
    name:    Optional[str] = None


def serialize_gladiator(doc: dict, oracle_mults: dict) -> dict:
    """Gladiador listo para el frontend: doc + stats calculados +
    display_name (nombre elegido o "Raza · Linaje" por defecto) +
    ability (habilidad de su raza, fuente de verdad del servidor).
    Los docs viejos sin campo `name` funcionan igual (name=None)."""
    g = Gladiator(**doc)
    return {
        **doc,
        "computed_stats": g.compute_stats(oracle_mults),
        "display_name": g.display_name(),
        "ability": g.ability_info(),
    }


def sanitize_name(raw: Optional[str]) -> Optional[str]:
    """Nombre del creador: sin espacios de más, como mucho 20
    caracteres; vacío o ausente → None."""
    if not raw:
        return None
    name = raw.strip()[:20].strip()
    return name or None


@router.post("/mint")
async def mint_gladiator(
    req: MintRequest,
    wallet: str = Depends(get_current_user),
    _limit=Depends(wallet_rate_limit(5, 60, "mint")),
):
    """
    Recluta (crea) un gladiador Cobre nivel 1 para el usuario autenticado.
    Raza y linaje: los que mande el frontend si son válidos; si no, aleatorios.
    token_id es un uuid4 hasta que existan los contratos NFT.
    Límite: 2 gladiadores gratis por wallet (los existentes cuentan).
    Límite de velocidad: 5/min por wallet.

    El límite es ATÓMICO (contador free_mints_used en users, con
    find_one_and_update + $inc bajo el filtro < 2): antes se contaban
    los gladiadores y luego se insertaba, y dos mints simultáneos con
    1 gladiador pasaban los dos y dejaban 3 gratis. El contador se
    inicializa una sola vez con el conteo real de gladiadores no
    quemados, así los usuarios viejos (pack Recluta, compras P2P,
    Yordi con 20) quedan igual que antes: ya en el límite. Si la
    inserción falla tras reservar el hueco, se devuelve el contador.
    Nota: el contador es de por vida — quemar un recluta gratis en la
    forja no libera su hueco (antes el conteo de no quemados sí lo
    hacía); es el mismo sentido de la regla "máximo 2 gratis".
    """
    db = get_db()
    wallet_l = wallet.lower()

    # Validar ANTES de reservar el hueco: una raza/linaje inválido no
    # debe consumir uno de los 2 reclutas.
    race = req.race or random.choice(list(RACE_MODIFIERS.keys()))
    lineage = req.lineage or random.choice(list(CRYPTO_ATTRIBUTE_MAP.keys()))
    if race not in RACE_MODIFIERS:
        raise HTTPException(status_code=400, detail=f"Raza inválida: {race}")
    if lineage not in CRYPTO_ATTRIBUTE_MAP:
        raise HTTPException(status_code=400, detail=f"Linaje inválido: {lineage}")

    # Inicialización perezosa del contador (una sola vez por wallet):
    # solo si el campo no existe, se fija al conteo real actual.
    # Dos mints simultáneos en el primer uso: solo uno gana este
    # $set; el otro sigue y el candado real está en el $inc de abajo.
    # OJO: sin upsert aquí — con upsert, una segunda petición cuyo
    # filtro ya no casa (el campo ya existe) intentaría INSERTAR otro
    # usuario y daría DuplicateKeyError (500) en vez de seguir.
    existing = await db.gladiators.count_documents(
        {"owner_wallet": wallet_l, "is_burned": False}
    )
    # Si el usuario no existe (JWT válido pero doc borrado), crearlo
    # con el contador ya inicializado; filtro de igualdad pura, así
    # dos upserts simultáneos no chocan: el segundo encuentra el doc.
    await db.users.update_one(
        {"wallet_address": wallet_l},
        {"$setOnInsert": {"wallet_address": wallet_l,
                          "free_mints_used": existing}},
        upsert=True,
    )
    await db.users.find_one_and_update(
        {"wallet_address": wallet_l, "free_mints_used": {"$exists": False}},
        {"$set": {"free_mints_used": existing}},
    )

    # Reserva atómica del hueco: solo si free_mints_used < 2. Dos
    # peticiones simultáneas: la primera sube el contador a 2 y la
    # segunda ya no cumple el filtro → 403.
    claimed = await db.users.find_one_and_update(
        {"wallet_address": wallet_l, "free_mints_used": {"$lt": FREE_MINT_LIMIT}},
        {"$inc": {"free_mints_used": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        raise HTTPException(
            status_code=403,
            detail="Ya tienes tus 2 gladiadores gratis",
        )

    g = Gladiator(
        token_id=str(uuid.uuid4()),
        owner_wallet=wallet_l,
        name=sanitize_name(req.name),
        race=race,
        lineage=lineage,
        rarity=Rarity.COBRE,
        level=1,
        xp=0,
        rebirths=0,
    )
    doc = g.model_dump()
    try:
        await db.gladiators.insert_one(doc)
    except Exception:
        # La inserción falló: devolver el hueco reservado.
        await db.users.update_one(
            {"wallet_address": wallet_l},
            {"$inc": {"free_mints_used": -1}},
        )
        raise
    doc.pop("_id", None)  # insert_one añade _id (ObjectId) al dict; no se serializa a JSON
    logger.info(
        "mint wallet=%s token=%s raza=%s linaje=%s nombre=%r",
        wallet_l, doc["token_id"], race, lineage, doc.get("name"),
    )
    oracle_mults = OracleService.get_cached_multipliers()
    return serialize_gladiator(doc, oracle_mults)


@router.get("/my")
async def get_my_gladiators(wallet: str = Depends(get_current_user)):
    """Retorna todos los NFTs del jugador con stats calculados en tiempo real."""
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()

    cursor = db.gladiators.find(
        {"owner_wallet": wallet, "is_burned": False},
        {"_id": 0},
    )
    docs = await cursor.to_list(length=200)

    result = []
    for doc in docs:
        result.append(serialize_gladiator(doc, oracle_mults))
    return result


@router.get("/{token_id}")
async def get_gladiator(token_id: str, wallet: str = Depends(get_current_user)):
    """Detalle de un gladiador específico."""
    db = get_db()
    oracle_mults = OracleService.get_cached_multipliers()
    doc = await db.gladiators.find_one(
        {"token_id": token_id, "owner_wallet": wallet},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Gladiador no encontrado")
    return serialize_gladiator(doc, oracle_mults)
