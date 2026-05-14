import os
import time
import requests
import pytz
from datetime import datetime, timedelta

# --------------------- CONFIG ---------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
TZ = pytz.timezone(os.getenv("TIMEZONE", "Europe/Paris"))
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY")

# --------------------- 1. RÉCUPÉRATION DES DONNÉES ---------------------
def get_price_and_candles():
    """Récupère le prix spot actuel et les 50 dernières bougies 15min."""
    if not TWELVE_KEY:
        print("❌ Clé Twelve Data manquante !")
        return None, None

    # Prix spot
    try:
        resp = requests.get(f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_KEY}")
        data = resp.json()
        if "price" in data:
            spot = float(data["price"])
        else:
            print("Erreur prix:", data)
            return None, None
    except Exception as e:
        print(f"Erreur requête prix: {e}")
        return None, None

    # Historique 15min (50 bougies = ~12h)
    try:
        resp = requests.get(
            f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=15min&outputsize=50&apikey={TWELVE_KEY}"
        )
        data = resp.json()
        if "values" not in data:
            print("Erreur historique:", data)
            return spot, None
        candles = []
        for bar in data["values"]:
            candles.append({
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"])
            })
        candles.reverse()  # du plus ancien au plus récent
        return spot, candles
    except Exception as e:
        print(f"Erreur historique: {e}")
        return spot, None

# --------------------- 2. DÉTECTION DE PATTERNS ---------------------
def detect_patterns(candles):
    patterns = []
    if not candles or len(candles) < 10:
        return patterns

    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    # --- FVG baissier ---
    for i in range(2, len(candles)-1):
        if lows[i-2] > highs[i]:
            fvg_top = lows[i-2]
            fvg_bottom = highs[i]
            if closes[i] <= fvg_top and closes[i] >= fvg_bottom:
                patterns.append({
                    "pattern": "FVG baissier comblé",
                    "confiance": "élevée",
                    "type": "vente"
                })
                break

    # --- FVG haussier ---
    for i in range(2, len(candles)-1):
        if highs[i-2] < lows[i]:
            fvg_bottom = highs[i-2]
            fvg_top = lows[i]
            if closes[i] >= fvg_bottom and closes[i] <= fvg_top:
                patterns.append({
                    "pattern": "FVG haussier comblé",
                    "confiance": "élevée",
                    "type": "achat"
                })
                break

    # --- Order Block haussier/baissier ---
    for i in range(2, len(candles)-2):
        if (closes[i] > opens[i] and closes[i-1] < opens[i-1] and
            closes[i] > opens[i-1] and opens[i] < closes[i-1]):
            patterns.append({
                "pattern": "Order Block haussier (15min)",
                "confiance": "moyenne",
                "type": "achat"
            })
            break
        if (closes[i] < opens[i] and closes[i-1] > opens[i-1] and
            opens[i] > closes[i-1] and closes[i] < opens[i-1]):
            patterns.append({
                "pattern": "Order Block baissier (15min)",
                "confiance": "moyenne",
                "type": "vente"
            })
            break

    # --- Double Top / Double Bottom ---
    if len(candles) >= 10:
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
        entree = round(price * 1.001, 2)
        sl = round(price * 0.993, 2)
        tp1 = round(price * 1.010, 2)
        tp2 = round(price * 1.018, 2)
        tp3 = round(price * 1.027, 2)
        tp4 = round(price * 1.037, 2)
    else:
        entree = round(price * 0.999, 2)
        sl = round(price * 1.007, 2)
        tp1 = round(price * 0.990, 2)
        tp2 = round(price * 0.982, 2)
        tp3 = round(price * 0.973, 2)
        tp4 = round(price * 0.963, 2)

    return {
        "pattern": pattern_info["pattern"],
        "prix": round(price, 2),
        "entree": entree,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp4": tp4,
        "timestamp": datetime.now(TZ).strftime("%H:%M")
    }

# --------------------- 4. ENVOI TELEGRAM ---------------------
def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        return

    # Déterminer le bandeau
    if signal['entree'] > signal['prix']:
        type_msg = "🟢 ACHAT"
        emoji = "⬆️"
    else:
        type_msg = "🔴 VENTE"
        emoji = "⬇️"

    message = (
        f"🔥 *SIGNAL XAUUSD* 🔥\n"
        f"🕐 {signal['timestamp']}\n"
        f"{type_msg}\n\n"
        f"▫️ Pattern : {signal['pattern']}\n"
        f"💵 Prix spot : {signal['prix']}\n\n"
        f"{emoji} Entrée : {signal['entree']}\n"
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
    print("🚀 Bot XAUUSD (Twelve Data) démarré...")
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "✅ Bot XAUUSD en ligne (spot + patterns via Twelve Data) !"}
        )
    except:
        pass

    while True:
        try:
            price, candles = get_price_and_candles()
            if price is not None and candles is not None:
                patterns = detect_patterns(candles)
                for pat in patterns:
                    signal = build_signal(price, pat)
                    send_alert(signal)
                print(f"[{datetime.now(TZ).strftime('%H:%M')}] Prix: {price} – {len(patterns)} pattern(s)")
            else:
                print("⚠️ Données indisponibles")
        except Exception as e:
            print(f"❌ Erreur boucle : {e}")

        time.sleep(INTERVAL * 60)
