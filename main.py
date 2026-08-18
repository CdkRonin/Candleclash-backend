"""
CandleClash Backend — FastAPI
Autoridad del servidor: todos los cálculos ocurren aquí, nunca en el cliente.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio

from database import connect_db, close_db
from routers import auth, gladiators, battle, forge, market, oracle
from services.oracle_service import OracleService
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup y shutdown del servidor."""
    await connect_db()
    # Iniciar el oráculo de precios en background
    oracle_task = asyncio.create_task(OracleService.run_forever())
    yield
    oracle_task.cancel()
    await close_db()


app = FastAPI(
    title="CandleClash API",
    version="1.0.0",
    description="Backend del Web3 Auto-Battler CandleClash",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas
app.include_router(auth.router,       prefix="/api/auth",       tags=["Auth"])
app.include_router(gladiators.router, prefix="/api/gladiators", tags=["Gladiadores"])
app.include_router(battle.router,     prefix="/api/battle",     tags=["Batalla"])
app.include_router(forge.router,      prefix="/api/forge",      tags=["Forja"])
app.include_router(market.router,     prefix="/api/market",     tags=["Mercado"])
app.include_router(oracle.router,     prefix="/api/oracle",     tags=["Oráculo"])


@app.get("/")
async def root():
    return {"status": "CandleClash API online", "version": "1.0.0"}
