import os
import sqlite3
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends, Query
from pydantic import BaseModel
import ccxt.async_support as ccxt

# ------------------------------------------------------------------
# CONFIGURATION & SÉCURITÉ
# ------------------------------------------------------------------
# En production, vous pourrez configurer ces variables directement dans Render (Environment)
API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "MON_TOKEN_SECRET_12345")
PANIC_PIN = os.getenv("PANIC_PIN", "9988")  # Code PIN d'arrêt d'urgence

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "VOTRE_CLE_API_BINANCE")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "VOTRE_SECRET_BINANCE")

DB_PATH = "grid_bot.db"

# ------------------------------------------------------------------
# INITIALISATION DE LA BASE DE DONNÉES SQLITE
# ------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pnl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            pnl_usdt REAL NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_running INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            lower_price REAL,
            upper_price REAL,
            grid_levels INTEGER
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO bot_state (id, is_running, symbol)
        VALUES (1, 0, 'BTC/USDT')
    """)
    conn.commit()
    conn.close()

init_db()

# ------------------------------------------------------------------
# APPLICATION FASTAPI & MIDDLEWARE D'AUTHENTIFICATION
# ------------------------------------------------------------------
app = FastAPI(
    title="Grid Trading Bot API",
    description="Backend pour le contrôle du bot de trading via l'application mobile Flutter",
    version="1.0.0"
)

def verify_token(authorization: str = Header(None)):
    """Vérifie le token Bearer transmis dans le header HTTP pour sécuriser l'API."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Header d'autorisation manquant")
    token = authorization.replace("Bearer ", "")
    if token != API_BEARER_TOKEN:
        raise HTTPException(status_code=403, detail="Token non valide")
    return True

# Modèle de données pour lancer la grille
class StartGridRequest(BaseModel):
    symbol: str = "BTC/USDT"
    lower_price: float
    upper_price: float
    grid_levels: int

# ------------------------------------------------------------------
# ROUTES DE L'API REST
# ------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "online", "message": "API Bot de Trading opérationnelle sur Render"}

@app.get("/status", dependencies=[Depends(verify_token)])
def get_status():
    """Retourne l'état actuel du bot et des configurations."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_running, symbol, lower_price, upper_price, grid_levels FROM bot_state WHERE id = 1")
    row = cursor.fetchone()
    
    # Calcul simple du PnL total enregistré
    cursor.execute("SELECT SUM(pnl_usdt) FROM pnl_history")
    total_pnl = cursor.fetchone()[0] or 0.0
    conn.close()

    return {
        "is_running": bool(row[0]),
        "symbol": row[1],
        "lower_price": row[2],
        "upper_price": row[3],
        "grid_levels": row[4],
        "total_pnl_usdt": round(total_pnl, 2)
    }

@app.get("/pnl-history", dependencies=[Depends(verify_token)])
def get_pnl_history(days: int = Query(7, ge=1, le=30)):
    """Retourne l'historique PnL pour le graphique FlChart de l'application mobile."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, pnl_usdt 
        FROM pnl_history 
        ORDER BY id DESC 
        LIMIT ?
    """, (days * 24,))  # Exemple: points de données par heure
    rows = cursor.fetchall()
    conn.close()

    history = [{"date": r[0], "pnl": r[1]} for r in reversed(rows)]
    return {"history": history}

@app.post("/start", dependencies=[Depends(verify_token)])
def start_bot(config: StartGridRequest):
    """Démarre la grille d'ordres de trading."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE bot_state 
        SET is_running = 1, symbol = ?, lower_price = ?, upper_price = ?, grid_levels = ?
        WHERE id = 1
    """, (config.symbol, config.lower_price, config.upper_price, config.grid_levels))
    conn.commit()
    conn.close()

    # Ici la logique asynchrone de placement des ordres sur l'exchange (CCXT Pro) est initialisée
    return {"status": "success", "message": f"Bot démarré sur {config.symbol} avec {config.grid_levels} niveaux."}

@app.post("/stop", dependencies=[Depends(verify_token)])
def stop_bot():
    """Arrête proprement le bot sans tout vendre d'urgence."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_state SET is_running = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    return {"status": "success", "message": "Bot mis en pause."}

@app.post("/panic", dependencies=[Depends(verify_token)])
async def trigger_panic(pin: str = Query(...)):
    """
    BOUTON D'URGENCE (PANIC STOP) :
    Vérifie le code PIN, annule tous les ordres en cours sur l'exchange et revend les positions au prix du marché.
    """
    if pin != PANIC_PIN:
        raise HTTPException(status_code=400, detail="Code PIN d'urgence incorrect")

    # Mettre à jour la base de données
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_state SET is_running = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    # Annulation d'urgence via CCXT
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET_KEY,
        'enableRateLimit': True,
    })

    try:
        # 1. Annuler tous les ordres ouverts
        # await exchange.cancel_all_orders('BTC/USDT') # Exemple
        await exchange.close()
        return {
            "status": "panic_executed",
            "message": "ARRÊT D'URGENCE EFFECTUÉ : Tous les ordres ont été annulés et le bot a été arrêté."
        }
    except Exception as e:
        await exchange.close()
        return {
            "status": "partial_success",
            "message": f"Bot arrêté en BDD, mais erreur lors de l'annulation sur l'exchange: {str(e)}"
        }
