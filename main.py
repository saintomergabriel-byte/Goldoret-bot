import os
import time
import json
import csv
import requests
import pytz
from datetime import datetime

# --------------------- CONFIG ---------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
TZ = pytz.timezone(os.getenv("TIMEZONE", "Europe/Paris"))
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY")

# Sécurité / qualité
USE_TREND_FILTER = os.getenv("USE_TREND_FILTER", "true").lower() in ("1", "true", "yes", "oui")
MIN_SCORE = int(os.getenv("MIN_SCORE", "70"))
MIN_SECONDS_BETWEEN_SIGNALS = int(os.getenv("MIN_SECONDS_BETWEEN_SIGNALS", "1800"))
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "false").lower() in ("1", "true", "yes", "oui")

# Historique : 220 bougies permet EMA200. Si ton plan Twelve Data bloque, mets 80 et USE_TREND_FILTER=false.
OUTPUTSIZE = int(os.getenv("TWELVE_OUTPUTSIZE", "220"))

# Distances façon Davo (en pips, 1 pip = 0.01 $ pour XAUUSD)
SL_PIPS = int(os.getenv("SL_PIPS", "3000"))        # 30 points
TP1_PIPS = int(os.getenv("TP1_PIPS", "200"))       # 2 points
TP2_PIPS = int(os.getenv("TP2_PIPS", "400"))       # 4 points
TP3_PIPS = int(os.getenv("TP3_PIPS", "800"))       # 8 points
TP4_PIPS = int(os.getenv("TP4_PIPS", "1200"))      # 12 points
ENTRY_OFFSET_PIPS = int(os.getenv("ENTRY_OFFSET_PIPS", "50"))

# Money management approximatif XAUUSD
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "1000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))
ACCOUNT_CURRENCY = os.getenv("ACCOUNT_CURRENCY", "EUR").upper()
EURUSD_RATE = float(os.getenv("EURUSD_RATE", "1.07"))
CONTRACT_SIZE_OZ = float(os.getenv("XAUUSD_CONTRACT_SIZE_OZ", "100"))
MIN_LOT = float(os.getenv("MIN_LOT", "0.01"))
MAX_LOT = float(os.getenv("MAX_LOT", "2.0"))

STATE_FILE = "last_signal.json"
LOG_FILE = "signals_log.csv"


# --------------------- OUTILS ---------------------
def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_signal(signal, sent, reason=""):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "time", "sent", "reason", "pattern", "type", "score", "prix", "entree",
                "sl", "tp1", "tp2", "tp3", "tp4", "lot", "risk_estimated"
            ]
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "time": now_str(),
            "sent": sent,
            "reason": reason,
            "pattern": signal.get("pattern", ""),
            "type": signal.get("type", ""),
            "score": signal.get("score", ""),
            "prix": signal.get("prix", ""),
            "entree": signal.get("entree", ""),
            "sl": signal.get("sl", ""),
            "tp1": signal.get("tp1", ""),
            "tp2": signal.get("tp2", ""),
            "tp3": signal.get("tp3", ""),
            "tp4": signal.get("tp4", ""),
            "lot": signal.get("lot", ""),
            "risk_estimated": signal.get("risk_estimated", ""),
        })


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


# --------------------- 1. RÉCUPÉRATION DES DONNÉES ---------------------
def get_price_and_candles():
    """Récupère le prix spot actuel et les bougies 15min via Twelve Data."""
    if not TWELVE_KEY:
        print("❌ Clé Twelve Data manquante !")
        return None, None

    try:
        resp = requests.get(
            f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_KEY}",
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if "price" in data:
            spot = float(data["price"])
        else:
            print("Erreur prix:", data)
            return None, None
    except Exception as e:
        print(f"Erreur requête prix: {e}")
        return None, None

    try:
        resp = requests.get(
            f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=15min&outputsize={OUTPUTSIZE}&apikey={TWELVE_KEY}",
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if "values" not in data:
            print("Erreur historique:", data)
            return spot, None

        candles = []
        for bar in data["values"]:
            candles.append({
                "datetime": bar.get("datetime", ""),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
            })
        candles.reverse()  # du plus ancien au plus récent
        return spot, candles
    except Exception as e:
        print(f"Erreur historique: {e}")
        return spot, None


# --------------------- 2. FILTRE DE TENDANCE ---------------------
def trend_context(candles):
    closes = [c["close"] for c in candles]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    last_close = closes[-1]

    if ema50 is None:
        return {"trend": "inconnue", "ema50": None, "ema200": None, "allow_buy": True, "allow_sell": True}

    # Si EMA200 indisponible, on utilise seulement EMA50.
    if ema200 is None:
        return {
            "trend": "hausse" if last_close >= ema50 else "baisse",
            "ema50": ema50,
            "ema200": None,
            "allow_buy": last_close >= ema50,
            "allow_sell": last_close <= ema50,
        }

    bullish = last_close >= ema50 >= ema200
    bearish = last_close <= ema50 <= ema200
    return {
        "trend": "hausse" if bullish else "baisse" if bearish else "range",
        "ema50": ema50,
        "ema200": ema200,
        "allow_buy": bullish,
        "allow_sell": bearish,
    }


# --------------------- 3. DÉTECTION DE PATTERNS ---------------------
def detect_patterns(candles):
    patterns = []
    if not candles or len(candles) < 10:
        return patterns

    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last_close = closes[-1]
    last_high = highs[-1]
    last_low = lows[-1]

    # On cherche d'abord les FVG récents, pas les anciens.
    start = max(2, len(candles) - 25)
    for i in range(len(candles) - 2, start - 1, -1):
        # FVG baissier : zone entre high[i] et low[i-2]
        if lows[i - 2] > highs[i]:
            fvg_top = lows[i - 2]
            fvg_bottom = highs[i]
            if last_high >= fvg_bottom and last_close <= fvg_top:
                patterns.append({
                    "pattern": "FVG baissier comblé",
                    "confiance": "élevée",
                    "type": "vente",
                    "base_score": 76,
                })
                break

        # FVG haussier : zone entre high[i-2] et low[i]
        if highs[i - 2] < lows[i]:
            fvg_bottom = highs[i - 2]
            fvg_top = lows[i]
            if last_low <= fvg_top and last_close >= fvg_bottom:
                patterns.append({
                    "pattern": "FVG haussier comblé",
                    "confiance": "élevée",
                    "type": "achat",
                    "base_score": 76,
                })
                break

    # Order Block / engulfing sur les deux dernières bougies.
    i = len(candles) - 1
    if (closes[i] < opens[i] and closes[i - 1] > opens[i - 1]
            and opens[i] >= closes[i - 1] and closes[i] <= opens[i - 1]):
        patterns.append({
            "pattern": "Order Block baissier",
            "confiance": "moyenne+",
            "type": "vente",
            "base_score": 72,
        })

    if (closes[i] > opens[i] and closes[i - 1] < opens[i - 1]
            and closes[i] >= opens[i - 1] and opens[i] <= closes[i - 1]):
        patterns.append({
            "pattern": "Order Block haussier",
            "confiance": "moyenne+",
            "type": "achat",
            "base_score": 72,
        })

    # Double Top / Double Bottom sur les 12 dernières bougies.
    recent_highs = highs[-12:]
    recent_lows = lows[-12:]
    tolerance = float(os.getenv("DOUBLE_TOLERANCE_POINTS", "1.5"))

    previous_top = max(recent_highs[:-3])
    current_top = max(recent_highs[-3:])
    if abs(previous_top - current_top) <= tolerance and closes[-1] < opens[-1]:
        patterns.append({
            "pattern": "Double Top",
            "confiance": "moyenne+",
            "type": "vente",
            "base_score": 68,
        })

    previous_bottom = min(recent_lows[:-3])
    current_bottom = min(recent_lows[-3:])
    if abs(previous_bottom - current_bottom) <= tolerance and closes[-1] > opens[-1]:
        patterns.append({
            "pattern": "Double Bottom",
            "confiance": "moyenne+",
            "type": "achat",
            "base_score": 68,
        })

    return patterns


# --------------------- 4. SCORE ET MONEY MANAGEMENT ---------------------
def score_pattern(pattern_info, trend):
    score = int(pattern_info.get("base_score", 60))
    if not USE_TREND_FILTER:
        return score

    if pattern_info["type"] == "achat" and trend["allow_buy"]:
        score += 12
    elif pattern_info["type"] == "vente" and trend["allow_sell"]:
        score += 12
    else:
        score -= 25
    return max(0, min(100, score))


def calculate_lot(entry, sl):
    stop_distance = abs(entry - sl)
    if stop_distance <= 0:
        return 0.0, 0.0, "SL invalide"

    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    risk_usd = risk_amount * EURUSD_RATE if ACCOUNT_CURRENCY == "EUR" else risk_amount
    theoretical_lot = risk_usd / (stop_distance * CONTRACT_SIZE_OZ)

    lot = min(theoretical_lot, MAX_LOT)
    if 0 < lot < MIN_LOT:
        lot = MIN_LOT
        warning = "⚠️ Lot minimum utilisé : le risque réel peut dépasser le risque cible."
    else:
        warning = "Vérifie le risque affiché chez le courtier avant validation."

    risk_usd_real = lot * stop_distance * CONTRACT_SIZE_OZ
    risk_real = risk_usd_real / EURUSD_RATE if ACCOUNT_CURRENCY == "EUR" else risk_usd_real
    return round(lot, 3), round(risk_real, 2), warning


# --------------------- 5. CONSTRUCTION DU SIGNAL ---------------------
def build_signal(price, pattern_info, trend):
    sl_dist = SL_PIPS * 0.01
    tp1_dist = TP1_PIPS * 0.01
    tp2_dist = TP2_PIPS * 0.01
    tp3_dist = TP3_PIPS * 0.01
    tp4_dist = TP4_PIPS * 0.01
    entry_dist = ENTRY_OFFSET_PIPS * 0.01

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

    score = score_pattern(pattern_info, trend)
    lot, risk_estimated, warning = calculate_lot(entree, sl)

    return {
        "pattern": pattern_info["pattern"],
        "type": pattern_info["type"],
        "confiance": pattern_info["confiance"],
        "score": score,
        "prix": round(price, 2),
        "entree": entree,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp4": tp4,
        "lot": lot,
        "micro_lots": round(lot * 100, 1),
        "risk_estimated": risk_estimated,
        "risk_warning": warning,
        "trend": trend["trend"],
        "ema50": None if trend["ema50"] is None else round(trend["ema50"], 2),
        "ema200": None if trend["ema200"] is None else round(trend["ema200"], 2),
        "timestamp": datetime.now(TZ).strftime("%H:%M"),
    }


# --------------------- 6. ANTI-SPAM ---------------------
def can_send(signal):
    state = load_state()
    signature = f"{signal['type']}|{signal['pattern']}|{signal['entree']}|{signal['sl']}"
    last_signature = state.get("signature")
    last_time = float(state.get("time", 0))
    elapsed = time.time() - last_time

    if signature == last_signature and elapsed < MIN_SECONDS_BETWEEN_SIGNALS:
        return False, f"Signal identique bloqué anti-spam ({int(elapsed)}s depuis le dernier)."

    save_state({"signature": signature, "time": time.time(), "last_signal": signal})
    return True, "OK"


# --------------------- 7. ENVOI TELEGRAM ---------------------
def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        print("❌ TOKEN ou CHAT_ID manquant")
        return False

    emoji = "🟢 ACHAT" if signal["type"] == "achat" else "🔴 VENTE"
    message = (
        f"🔥 *SIGNAL XAUUSD* 🔥\n"
        f"🕐 {signal['timestamp']}\n\n"
        f"{emoji}\n"
        f"▫️ Pattern : {signal['pattern']}\n"
        f"⭐ Score : {signal['score']}/100\n"
        f"📈 Tendance : {signal['trend']}\n"
        f"💵 Prix spot : {signal['prix']}\n\n"
        f"➡️ Entrée : {signal['entree']}\n"
        f"🛑 Stop Loss : {signal['sl']}\n"
        f"🎯 TP1 : {signal['tp1']}\n"
        f"🎯 TP2 : {signal['tp2']}\n"
        f"🎯 TP3 : {signal['tp3']}\n"
        f"🎯 TP4 : {signal['tp4']}\n\n"
        f"💼 Lot théorique : {signal['lot']} lot ≈ {signal['micro_lots']} micro-lots\n"
        f"🧮 Risque estimé : {signal['risk_estimated']} {ACCOUNT_CURRENCY}\n"
        f"⚠️ {signal['risk_warning']}\n\n"
        f"Signal informatif, aucune garantie de profit."
    )
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=20,
        )
        if r.status_code == 200:
            print("✅ Signal envoyé avec succès")
            return True
        print(f"❌ Erreur Telegram : {r.text}")
        return False
    except Exception as e:
        print(f"❌ Erreur envoi : {e}")
        return False


# --------------------- 8. BOUCLE PRINCIPALE ---------------------
if __name__ == "__main__":
    print("🚀 Bot XAUUSD Goldoret amélioré démarré...")
    print(f"Réglages: interval={INTERVAL}min, trend_filter={USE_TREND_FILTER}, min_score={MIN_SCORE}")

    if SEND_STARTUP_MESSAGE and TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": "✅ Bot Goldoret amélioré en ligne !"},
                timeout=20,
            )
        except Exception:
            pass

    while True:
        try:
            price, candles = get_price_and_candles()
            if price is not None and candles is not None:
                trend = trend_context(candles)
                patterns = detect_patterns(candles)
                sent_count = 0

                for pat in patterns:
                    signal = build_signal(price, pat, trend)

                    if signal["score"] < MIN_SCORE:
                        reason = f"Score trop bas: {signal['score']}/{MIN_SCORE}"
                        print(f"⏸️ {reason} — {signal['pattern']}")
                        log_signal(signal, sent=False, reason=reason)
                        continue

                    allowed, reason = can_send(signal)
                    if not allowed:
                        print(f"⏸️ {reason}")
                        log_signal(signal, sent=False, reason=reason)
                        continue

                    if send_alert(signal):
                        sent_count += 1
                        log_signal(signal, sent=True, reason="envoyé")

                print(
                    f"[{datetime.now(TZ).strftime('%H:%M')}] Prix: {price} – "
                    f"patterns={len(patterns)} – envoyés={sent_count} – tendance={trend['trend']}"
                )
            else:
                print("⚠️ Données indisponibles")
        except Exception as e:
            print(f"❌ Erreur boucle : {e}")

        time.sleep(INTERVAL * 60)
                
