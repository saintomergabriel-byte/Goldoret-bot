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
    # Récupération des distances en pips depuis les variables d'environnement
    sl_pips = float(os.getenv("SL_PIPS", "50"))
    tp1_pips = float(os.getenv("TP1_PIPS", "100"))
    tp2_pips = float(os.getenv("TP2_PIPS", "150"))
    tp3_pips = float(os.getenv("TP3_PIPS", "180"))
    tp4_pips = float(os.getenv("TP4_PIPS", "250"))
    entry_offset = float(os.getenv("ENTRY_OFFSET_PIPS", "20"))

    # Conversion pips → dollars (1 pip = 0.01 pour XAUUSD)
    sl_dist = sl_pips * 0.01
    tp1_dist = tp1_pips * 0.01
    tp2_dist = tp2_pips * 0.01
    tp3_dist = tp3_pips * 0.01
    tp4_dist = tp4_pips * 0.01
    entry_dist = entry_offset * 0.01

    if pattern_info["type"] == "achat":
        entree = round(price + entry_dist, 2)
        sl = round(entree - sl_dist, 2)
        tp1 = round(entree + tp1_dist, 2)
        tp2 = round(entree + tp2_dist, 2)
        tp3 = round(entree + tp3_dist, 2)
        tp4 = round(entree + tp4_dist, 2)
    else:
        entree = round(price - entry_dist, 2)
        sl = round(entree + sl_dist, 2)
        tp1 = round(entree - tp1_dist, 2)
        tp2 = round(entree - tp2_dist, 2)
        tp3 = round(entree - tp3_dist, 2)
        tp4 = round(entree - tp4_dist, 2)

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
