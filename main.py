import os
import time
import json
import csv
import requests
import pytz
from datetime import datetime, time as dtime

# ===================== CONFIG =====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY")
TZ = pytz.timezone(os.getenv("TIMEZONE", "Europe/Paris"))

INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
OUTPUTSIZE = int(os.getenv("TWELVE_OUTPUTSIZE", "220"))

# Réglages Premium Order Blocks
MIN_OB_SCORE = int(os.getenv("MIN_OB_SCORE", "75"))
OB_LOOKBACK = int(os.getenv("OB_LOOKBACK", "80"))
SWING_LOOKBACK = int(os.getenv("SWING_LOOKBACK", "10"))
DISPLACEMENT_MULTIPLIER = float(os.getenv("DISPLACEMENT_MULTIPLIER", "1.5"))
OB_RETEST_TOLERANCE_POINTS = float(os.getenv("OB_RETEST_TOLERANCE_POINTS", "1.0"))
OB_SL_BUFFER_POINTS = float(os.getenv("OB_SL_BUFFER_POINTS", "1.0"))
MIN_SECONDS_BETWEEN_SIGNALS = int(os.getenv("MIN_SECONDS_BETWEEN_SIGNALS", "3600"))

# Filtres
USE_TREND_FILTER = os.getenv("USE_TREND_FILTER", "true").lower() in ("1", "true", "yes", "oui")
USE_SESSION_FILTER = os.getenv("USE_SESSION_FILTER", "true").lower() in ("1", "true", "yes", "oui")
ALLOWED_SESSIONS = os.getenv("ALLOWED_SESSIONS", "08:00-11:30,14:30-17:30")
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "false").lower() in ("1", "true", "yes", "oui")

# Money management approximatif XAUUSD
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.25"))
ACCOUNT_CURRENCY = os.getenv("ACCOUNT_CURRENCY", "EUR").upper()
EURUSD_RATE = float(os.getenv("EURUSD_RATE", "1.07"))
CONTRACT_SIZE_OZ = float(os.getenv("XAUUSD_CONTRACT_SIZE_OZ", "100"))
MIN_LOT = float(os.getenv("MIN_LOT", "0.01"))
MAX_LOT = float(os.getenv("MAX_LOT", "2.0"))

STATE_FILE = "last_premium_ob_signal.json"
LOG_FILE = "premium_ob_signals_log.csv"


# ===================== OUTILS =====================
def now_local():
    return datetime.now(TZ)


def now_str():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def parse_hhmm(value):
    hour, minute = value.strip().split(":")
    return dtime(int(hour), int(minute))


def in_allowed_session():
    if not USE_SESSION_FILTER:
        return True, "filtre horaire désactivé"

    current = now_local().time()

    for part in ALLOWED_SESSIONS.split(","):
        if "-" not in part:
            continue

        start_raw, end_raw = part.split("-", 1)
        start = parse_hhmm(start_raw)
        end = parse_hhmm(end_raw)

        if start <= current <= end:
            return True, f"session autorisée {start_raw.strip()}-{end_raw.strip()}"

    return False, f"hors session autorisée ({ALLOWED_SESSIONS})"


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
                "time", "sent", "reason", "side", "score", "entry", "sl",
                "tp1", "tp2", "tp3", "tp4", "zone_low", "zone_high",
                "trend", "session", "lot", "risk_estimated", "details"
            ],
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "time": now_str(),
            "sent": sent,
            "reason": reason,
            "side": signal.get("side", ""),
            "score": signal.get("score", ""),
            "entry": signal.get("entry", ""),
            "sl": signal.get("sl", ""),
            "tp1": signal.get("tp1", ""),
            "tp2": signal.get("tp2", ""),
            "tp3": signal.get("tp3", ""),
            "tp4": signal.get("tp4", ""),
            "zone_low": signal.get("zone_low", ""),
            "zone_high": signal.get("zone_high", ""),
            "trend": signal.get("trend", ""),
            "session": signal.get("session", ""),
            "lot": signal.get("lot", ""),
            "risk_estimated": signal.get("risk_estimated", ""),
            "details": signal.get("details", ""),
        })


def ema(values, period):
    if len(values) < period:
        return None

    alpha = 2 / (period + 1)
    result = values[0]

    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result

    return result


def avg(values):
    if not values:
        return 0
    return sum(values) / len(values)


# ===================== DONNÉES TWELVE DATA =====================
def get_price_and_candles():
    if not TWELVE_KEY:
        print("Clé Twelve Data manquante.")
        return None, None

    try:
        price_resp = requests.get(
            f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_KEY}",
            timeout=20,
        )
        price_resp.raise_for_status()
        price_data = price_resp.json()

        if "price" not in price_data:
            print("Erreur prix:", price_data)
            return None, None

        spot = float(price_data["price"])

    except Exception as e:
        print(f"Erreur prix Twelve Data: {e}")
        return None, None

    try:
        candles_resp = requests.get(
            f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=15min&outputsize={OUTPUTSIZE}&apikey={TWELVE_KEY}",
            timeout=20,
        )
        candles_resp.raise_for_status()
        data = candles_resp.json()

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

        candles.reverse()
        return spot, candles

    except Exception as e:
        print(f"Erreur bougies Twelve Data: {e}")
        return spot, None


# ===================== TENDANCE =====================
def trend_context(candles):
    closes = [c["close"] for c in candles]
    last_close = closes[-1]
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    if ema50 is None:
        return {
            "trend": "inconnue",
            "ema50": None,
            "ema200": None,
            "allow_buy": True,
            "allow_sell": True,
        }

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


# ===================== OUTILS ORDER BLOCK =====================
def candle_body(candle):
    return abs(candle["close"] - candle["open"])


def is_bullish(candle):
    return candle["close"] > candle["open"]


def is_bearish(candle):
    return candle["close"] < candle["open"]


def has_bullish_fvg(candles, displacement_index):
    if displacement_index < 2:
        return False
    return candles[displacement_index - 2]["high"] < candles[displacement_index]["low"]


def has_bearish_fvg(candles, displacement_index):
    if displacement_index < 2:
        return False
    return candles[displacement_index - 2]["low"] > candles[displacement_index]["high"]


# ===================== MONEY MANAGEMENT =====================
def calculate_lot(entry, sl):
    stop_distance = abs(entry - sl)

    if stop_distance <= 0:
        return 0.0, 0.0, "SL invalide"

    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)

    if ACCOUNT_CURRENCY == "EUR":
        risk_usd = risk_amount * EURUSD_RATE
    else:
        risk_usd = risk_amount

    theoretical_lot = risk_usd / (stop_distance * CONTRACT_SIZE_OZ)
    lot = min(theoretical_lot, MAX_LOT)

    if 0 < lot < MIN_LOT:
        lot = MIN_LOT
        warning = "Lot minimum utilisé : le risque réel peut dépasser le risque cible."
    else:
        warning = "Vérifie le risque affiché chez le courtier avant validation."

    risk_usd_real = lot * stop_distance * CONTRACT_SIZE_OZ

    if ACCOUNT_CURRENCY == "EUR":
        risk_real = risk_usd_real / EURUSD_RATE
    else:
        risk_real = risk_usd_real

    return round(lot, 3), round(risk_real, 2), warning


def make_targets(side, entry, sl):
    risk = abs(entry - sl)

    if side == "BUY":
        return (
            round(entry + 1.0 * risk, 2),
            round(entry + 1.5 * risk, 2),
            round(entry + 2.0 * risk, 2),
            round(entry + 3.0 * risk, 2),
        )

    return (
        round(entry - 1.0 * risk, 2),
        round(entry - 1.5 * risk, 2),
        round(entry - 2.0 * risk, 2),
        round(entry - 3.0 * risk, 2),
    )


# ===================== DÉTECTION PREMIUM ORDER BLOCK =====================
def detect_premium_order_block(candles, spot):
    if not candles or len(candles) < 60:
        return None, "Pas assez de bougies pour un Order Block premium."

    session_ok, session_reason = in_allowed_session()

    if not session_ok:
        return None, session_reason

    trend = trend_context(candles)
    bodies = [candle_body(c) for c in candles]
    candidates = []
    last = candles[-1]
    last_close = last["close"]
    start = max(SWING_LOOKBACK + 2, len(candles) - OB_LOOKBACK)

    for i in range(len(candles) - 4, start - 1, -1):
        ob = candles[i]
        after = candles[i + 1]
        previous = candles[max(0, i - SWING_LOOKBACK):i]
        previous_bodies = bodies[max(0, i - 20):i]
        avg_body = avg(previous_bodies)

        if not previous or avg_body <= 0:
            continue

        previous_high = max(c["high"] for c in previous)
        previous_low = min(c["low"] for c in previous)
        displacement_body = candle_body(after)
        strong_displacement = displacement_body >= avg_body * DISPLACEMENT_MULTIPLIER

        # ---------- BUY : OB haussier ----------
        if is_bearish(ob) and is_bullish(after):
            bos = after["close"] > previous_high
            zone_low = ob["low"]
            zone_high = ob["open"]
            retest = (
                last["low"] <= zone_high + OB_RETEST_TOLERANCE_POINTS
                and last_close >= zone_low - OB_RETEST_TOLERANCE_POINTS
            )

            if bos and strong_displacement and retest:
                fvg = has_bullish_fvg(candles, i + 1)

                score = 45
                score += 18 if strong_displacement else 0
                score += 15 if bos else 0
                score += 10 if fvg else 0
                score += 10 if trend["allow_buy"] else -20
                score += 7 if session_ok else 0
                score = max(0, min(100, score))

                if not USE_TREND_FILTER or trend["allow_buy"]:
                    entry = round((zone_low + zone_high) / 2, 2)
                    sl = round(zone_low - OB_SL_BUFFER_POINTS, 2)
                    tp1, tp2, tp3, tp4 = make_targets("BUY", entry, sl)
                    lot, risk_estimated, warning = calculate_lot(entry, sl)

                    candidates.append({
                        "side": "BUY",
                        "score": score,
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp3": tp3,
                        "tp4": tp4,
                        "zone_low": round(zone_low, 2),
                        "zone_high": round(zone_high, 2),
                        "spot": round(spot, 2),
                        "trend": trend["trend"],
                        "session": session_reason,
                        "lot": lot,
                        "micro_lots": round(lot * 100, 1),
                        "risk_estimated": risk_estimated,
                        "risk_warning": warning,
                        "details": f"OB haussier + BOS au-dessus {previous_high:.2f} + déplacement {displacement_body:.2f}/{avg_body:.2f} + FVG={fvg}",
                        "signature": f"BUY|{round(zone_low, 2)}|{round(zone_high, 2)}|{ob.get('datetime', i)}",
                    })

        # ---------- SELL : OB baissier ----------
        if is_bullish(ob) and is_bearish(after):
            bos = after["close"] < previous_low
            zone_low = ob["open"]
            zone_high = ob["high"]
            retest = (
                last["high"] >= zone_low - OB_RETEST_TOLERANCE_POINTS
                and last_close <= zone_high + OB_RETEST_TOLERANCE_POINTS
            )

            if bos and strong_displacement and retest:
                fvg = has_bearish_fvg(candles, i + 1)

                score = 45
                score += 18 if strong_displacement else 0
                score += 15 if bos else 0
                score += 10 if fvg else 0
                score += 10 if trend["allow_sell"] else -20
                score += 7 if session_ok else 0
                score = max(0, min(100, score))

                if not USE_TREND_FILTER or trend["allow_sell"]:
                    entry = round((zone_low + zone_high) / 2, 2)
                    sl = round(zone_high + OB_SL_BUFFER_POINTS, 2)
                    tp1, tp2, tp3, tp4 = make_targets("SELL", entry, sl)
                    lot, risk_estimated, warning = calculate_lot(entry, sl)

                    candidates.append({
                        "side": "SELL",
                        "score": score,
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp3": tp3,
                        "tp4": tp4,
                        "zone_low": round(zone_low, 2),
                        "zone_high": round(zone_high, 2),
                        "spot": round(spot, 2),
                        "trend": trend["trend"],
                        "session": session_reason,
                        "lot": lot,
                        "micro_lots": round(lot * 100, 1),
                        "risk_estimated": risk_estimated,
                        "risk_warning": warning,
                        "details": f"OB baissier + BOS sous {previous_low:.2f} + déplacement {displacement_body:.2f}/{avg_body:.2f} + FVG={fvg}",
                        "signature": f"SELL|{round(zone_low, 2)}|{round(zone_high, 2)}|{ob.get('datetime', i)}",
                    })

    if not candidates:
        return None, f"Aucun Order Block premium valide. Tendance={trend['trend']}, session={session_reason}"

    best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]

    if best["score"] < MIN_OB_SCORE:
        return None, f"Meilleur OB trouvé mais score insuffisant: {best['score']}/{MIN_OB_SCORE}"

    return best, "OK"


# ===================== ANTI-SPAM =====================
def can_send(signal):
    state = load_state()
    last_signature = state.get("signature")
    last_time = float(state.get("time", 0))
    elapsed = time.time() - last_time

    if signal["signature"] == last_signature and elapsed < MIN_SECONDS_BETWEEN_SIGNALS:
        return False, f"Signal identique bloqué anti-spam ({int(elapsed)}s)."

    save_state({
        "signature": signal["signature"],
        "time": time.time(),
        "last_signal": signal,
    })

    return True, "OK"


# ===================== TELEGRAM =====================
def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        print("TOKEN ou CHAT_ID manquant.")
        return False

    if signal["side"] == "BUY":
        side_icon = "🟢"
        side_text = "ACHAT"
        action_text = "Chercher une entrée BUY sur retest"
    else:
        side_icon = "🔴"
        side_text = "VENTE"
        action_text = "Chercher une entrée SELL sur retest"

    message = (
        f"🔥 XAUUSD PREMIUM ORDER BLOCK 🔥\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{side_icon} Signal : {side_text}\n"
        f"🕐 Heure : {now_local().strftime('%H:%M')}\n"
        f"⭐ Score : {signal['score']}/100\n\n"

        f"📍 ZONE DE TRADING\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Prix spot : {signal['spot']}\n"
        f"🧱 Zone OB : {signal['zone_low']} → {signal['zone_high']}\n"
        f"🎯 Entrée indicative : {signal['entry']}\n"
        f"🛑 Stop-loss : {signal['sl']}\n\n"

        f"🎯 OBJECTIFS\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 TP1 : {signal['tp1']}\n"
        f"🥈 TP2 : {signal['tp2']}\n"
        f"🥉 TP3 : {signal['tp3']}\n"
        f"🏆 TP4 : {signal['tp4']}\n\n"

        f"📊 CONTEXTE\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 Tendance : {signal['trend']}\n"
        f"⏰ Session : {signal['session']}\n"
        f"🧠 Raison : {signal['details']}\n\n"

        f"💼 MONEY MANAGEMENT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Lot théorique : {signal['lot']} lot\n"
        f"🔹 Micro-lots : {signal['micro_lots']}\n"
        f"🧮 Risque estimé : {signal['risk_estimated']} {ACCOUNT_CURRENCY}\n"
        f"⚠️ Sécurité : {signal['risk_warning']}\n\n"

        f"✅ PLAN\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"➡️ Action : {action_text}\n"
        f"🚫 Ne pas entrer si le prix est déjà trop loin.\n"
        f"👀 Vérifie le risque chez le courtier avant validation.\n\n"

        f"⚠️ Signal informatif, aucune garantie de profit."
    )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=20,
        )

        if response.status_code == 200:
            print("Signal premium OB envoyé.")
            return True

        print(f"Erreur Telegram: {response.text}")
        return False

    except Exception as e:
        print(f"Erreur envoi Telegram: {e}")
        return False


# ===================== BOUCLE PRINCIPALE =====================
if __name__ == "__main__":
    print("Bot XAUUSD Premium Order Blocks démarré.")
    print(
        f"Réglages: interval={INTERVAL}min, "
        f"score_min={MIN_OB_SCORE}, "
        f"sessions={ALLOWED_SESSIONS}, "
        f"trend_filter={USE_TREND_FILTER}"
    )

    if SEND_STARTUP_MESSAGE and TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": "Bot Premium Order Blocks en ligne."},
                timeout=20,
            )
        except Exception:
            pass

    while True:
        try:
            spot, candles = get_price_and_candles()

            if spot is None or candles is None:
                print("Données indisponibles.")
            else:
                signal, reason = detect_premium_order_block(candles, spot)

                if signal is None:
                    print(f"[{now_local().strftime('%H:%M')}] Prix={spot} — pas de signal: {reason}")
                else:
                    allowed, spam_reason = can_send(signal)

                    if not allowed:
                        print(f"[{now_local().strftime('%H:%M')}] {spam_reason}")
                        log_signal(signal, sent=False, reason=spam_reason)
                    else:
                        sent = send_alert(signal)
                        log_signal(signal, sent=sent, reason="envoyé" if sent else "erreur envoi")

        except Exception as e:
            print(f"Erreur boucle principale: {e}")

        time.sleep(INTERVAL * 60)
              
