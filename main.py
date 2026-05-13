import os
import time
import requests
import yfinance as yf
import pandas as pd
import pytz
from datetime import datetime

# --------------------- CONFIG ---------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
TZ = pytz.timezone(os.getenv("TIMEZONE", "Europe/Paris"))

# --------------------- 1. RÉCUPÉRATION DU PRIX ---------------------
def get_spot_price():
    # Récupère le prix spot via les futures GC=F et le ratio GLD/GC
    try:
        gc = yf.Ticker("GC=F")
        gc_price = gc.history(period="1d", interval="15m")['Close'].iloc[-1]
        # Si le prix est > 4000, on suppose que c'est le future x2 (ex: 4713 / 2 = 2356)
        if gc_price > 4000:
            return gc_price / 2
        else:
            return gc_price
    except:
        return None

# --------------------- 2. DÉTECTION DE PATTERNS ---------------------
def detect_patterns(df):
    patterns = []
    if df is None or len(df) < 10:
        return patterns

    closes = df['Close']
    opens = df['Open']
    highs = df['High']
    lows = df['Low']

    # --- Order Block haussier simplifié ---
    for i in range(2, len(df)-2):
        # Engulfing haussier
        if closes[i] > opens[i] and closes[i-1] < opens[i-1] and closes[i] > opens[i-1]:
            patterns.append({
                "pattern": "Order Block haussier (15min)",
                "confiance": "moyenne",
                "type": "achat"
            })
            break

    # --- Double Top / Double Bottom simples ---
    if len(df) >= 10:
        recent_highs = highs[-10:]
        recent_lows = lows[-10:]
        if max(recent_highs[-3:]) < max(recent_highs[:-3]) * 0.999:
            patterns.append({
                "pattern": "Double Top détecté",
                "confiance": "moyenne+",
                "type": "vente"
            })
        if min(recent_lows[-3:]) > min(recent_lows[:-3]) * 1.001:
            patterns.append({
                "pattern": "Double Bottom détecté",
                "confiance": "moyenne+",
                "type": "achat"
            })

    return patterns

# --------------------- 3. CONSTRUCTION DU SIGNAL ---------------------
def build_signal(price, pattern_info):
    if pattern_info["type"] == "achat":
        entree = price * 1.001
        sl = price * 0.993
        tp1 = price * 1.010
        tp2 = price * 1.018
        tp3 = price * 1.027
        tp4 = price * 1.037
    else:
        entree = price * 0.999
        sl = price * 1.007
        tp1 = price * 0.990
        tp2 = price * 0.982
        tp3 = price * 0.973
        tp4 = price * 0.963

    return {
        "pattern": pattern_info["pattern"],
        "prix": round(price, 2),
        "entree": round(entree, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "tp4": round(tp4, 2),
        "timestamp": datetime.now(TZ).strftime("%H:%M")
    }

# --------------------- 4. ENVOI TELEGRAM ---------------------
def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        return
    message = (
        f"🔥 *SIGNAL XAUUSD* 🔥\n"
        f"🕐 {signal['timestamp']}\n\n"
        f"▫️ Pattern : {signal['pattern']}\n"
        f"💵 Prix actuel : {signal['prix']}\n\n"
        f"⬆️ Entrée : {signal['entree']}\n"
        f"🛑 Stop Loss : {signal['sl']}\n"
        f"🎯 TP1 : {signal['tp1']}\n"
        f"🎯 TP2 : {signal['tp2']}\n"
        f"🎯 TP3 : {signal['tp3']}\n"
        f"🎯 TP4 : {signal['tp4']}\n"
    )
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })
        if r.status_code == 200:
            print("✅ Signal envoyé avec succès")
        else:
            print(f"❌ Erreur Telegram : {r.text}")
    except Exception as e:
        print(f"❌ Erreur envoi : {e}")

# --------------------- 5. BOUCLE PRINCIPALE ---------------------
if __name__ == "__main__":
    print("🚀 Bot XAUUSD (patterns) démarré...")
    # Message de bienvenue
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "✅ Bot Patterns XAUUSD en ligne !"}
        )
    except Exception:
        pass

    while True:
        try:
            price = get_spot_price()
df = get_price_and_history()[1]   # On garde l’historique depuis GC=F
            if price is not None:
                patterns = detect_patterns(df)
                for pat in patterns:
                    signal = build_signal(price, pat)
                    send_alert(signal)
                print(f"[{datetime.now(TZ).strftime('%H:%M')}] Scan terminé – {len(patterns)} pattern(s)")
            else:
                print("⚠️ Données de prix indisponibles")
        except Exception as e:
            print(f"❌ Erreur boucle : {e}")
        time.sleep(INTERVAL * 60)
