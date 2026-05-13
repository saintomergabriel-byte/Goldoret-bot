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
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY", "")

# --------------------- 1. PRIX SPOT VIA TWELVE DATA ---------------------
def get_spot_price():
    """Récupère le prix spot XAU/USD via Twelve Data."""
    if not TWELVE_KEY:
        # Fallback sur Yahoo si pas de clé
        try:
            ticker = yf.Ticker("GC=F")
            price = ticker.history(period="1d", interval="15m")['Close'].iloc[-1]
            if price > 4000:
                price = price / 2
            return price
        except:
            return None

    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_KEY}"
        resp = requests.get(url)
        data = resp.json()
        if "price" in data:
            return float(data["price"])
        else:
            print("Twelve Data error:", data)
            return None
    except Exception as e:
        print(f"Erreur Twelve Data : {e}")
        return None

# --------------------- 2. HISTORIQUE BOUGIES (YAHOO) ---------------------
def get_price_history():
    """Récupère l'historique 15min pour les patterns (via GC=F)."""
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="5d", interval="15m")
        if df.empty:
            return None
        return df
    except:
        return None

# --------------------- 3. DÉTECTION DE PATTERNS ---------------------
def detect_patterns(df):
    patterns = []
    if df is None or len(df) < 10:
        return patterns

    closes = df['Close']
    opens = df['Open']
    highs = df['High']
    lows = df['Low']

    # --- Order Block (engulfing) haussier ET baissier ---
    for i in range(2, len(df)-2):
        # Engulfing haussier
        if (closes[i] > opens[i] and                     # bougie i verte
            closes[i-1] < opens[i-1] and                 # bougie i-1 rouge
            closes[i] > opens[i-1] and                   # la verte avale la rouge
            opens[i] < closes[i-1]):
            patterns.append({
                "pattern": "Order Block haussier (15min)",
                "confiance": "moyenne",
                "type": "achat"
            })
            break   # <-- enlève ce 'break' si tu veux détecter plusieurs signaux à la fois

        # Engulfing baissier
        if (closes[i] < opens[i] and                     # bougie i rouge
            closes[i-1] > opens[i-1] and                 # bougie i-1 verte
            opens[i] > closes[i-1] and                   # la rouge ouvre au-dessus de la clôture verte
            closes[i] < opens[i-1]):                     # et clôture sous l'ouverture verte
            patterns.append({
                "pattern": "Order Block baissier (15min)",
                "confiance": "moyenne",
                "type": "vente"
            })
            break   # idem, optionnel

    # --- Double Top / Double Bottom (inchangé) ---
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

    # --- Order Block haussier simplifié ---
    for i in range(2, len(df)-2):
        if closes[i] > opens[i] and closes[i-1] < opens[i-1] and closes[i] > opens[i-1]:
            patterns.append({
                "pattern": "Order Block haussier (15min)",
                "confiance": "moyenne",
                "type": "achat"
            })
            break

    # --- Double Top / Double Bottom ---
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

# --------------------- 4. CONSTRUCTION DU SIGNAL ---------------------
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

# --------------------- 5. ENVOI TELEGRAM ---------------------
def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        return
    message = (
        f"🔥 *SIGNAL XAUUSD* 🔥\n"
        f"🕐 {signal['timestamp']}\n\n"
        f"▫️ Pattern : {signal['pattern']}\n"
        f"💵 Prix spot : {signal['prix']}\n\n"
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

# --------------------- 6. BOUCLE PRINCIPALE ---------------------
if __name__ == "__main__":
    print("🚀 Bot XAUUSD (Twelve Data spot) démarré...")
    # Message de bienvenue
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "✅ Bot Patterns XAUUSD en ligne (spot via Twelve Data) !"}
        )
    except Exception:
        pass

    while True:
        try:
            # Récupère le prix spot via Twelve Data
            spot_price = get_spot_price()
            # Récupère l'historique depuis Yahoo (GC=F)
            df = get_price_history()

            if spot_price is not None and df is not None:
                patterns = detect_patterns(df)
                for pat in patterns:
                    signal = build_signal(spot_price, pat)
                    send_alert(signal)
                print(f"[{datetime.now(TZ).strftime('%H:%M')}] Prix spot: {spot_price} – {len(patterns)} pattern(s)")
            else:
                print("⚠️ Données indisponibles (prix ou historique)")
        except Exception as e:
            print(f"❌ Erreur boucle : {e}")

        time.sleep(INTERVAL * 60)
