import os
import time
import json
import csv
import requests
import pytz
from datetime import datetime, time as dtime

# ==========================================================
# 🔥 GOLDORET BOT — XAUUSD + BTCUSD — SIGNALS QUALITY MODE
# ==========================================================
# Signaux informatifs uniquement. Aucun trade automatique.
# Objectif : moins de signaux faibles, plus de contexte, SL cohérent.
# ==========================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY")
TZ = pytz.timezone(os.getenv("TIMEZONE", "Europe/Paris"))

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
TIMEFRAME = os.getenv("TWELVE_INTERVAL", "15min")
OUTPUTSIZE = int(os.getenv("TWELVE_OUTPUTSIZE", "220"))
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "XAU/USD,BTC/USD").split(",") if s.strip()]

# --------------------- Filtres qualité ---------------------
MIN_SCORE = int(os.getenv("MIN_SCORE", "75"))
USE_TREND_FILTER = os.getenv("USE_TREND_FILTER", "true").lower() in ("1", "true", "yes", "oui")
USE_SESSION_FILTER = os.getenv("USE_SESSION_FILTER", "true").lower() in ("1", "true", "yes", "oui")
ALLOWED_SESSIONS = os.getenv("ALLOWED_SESSIONS", "08:00-11:30,14:30-17:30")
MIN_SECONDS_BETWEEN_SIGNALS = int(os.getenv("MIN_SECONDS_BETWEEN_SIGNALS", "3600"))
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "false").lower() in ("1", "true", "yes", "oui")

# --------------------- Détection patterns ---------------------
OB_LOOKBACK = int(os.getenv("OB_LOOKBACK", "80"))
SWING_LOOKBACK = int(os.getenv("SWING_LOOKBACK", "10"))
DISPLACEMENT_MULTIPLIER = float(os.getenv("DISPLACEMENT_MULTIPLIER", "1.5"))
RETEST_TOLERANCE_MULT = float(os.getenv("RETEST_TOLERANCE_MULT", "0.20"))

# --------------------- Money management ---------------------
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.25"))
ACCOUNT_CURRENCY = os.getenv("ACCOUNT_CURRENCY", "EUR").upper()
EURUSD_RATE = float(os.getenv("EURUSD_RATE", "1.07"))

STATE_FILE = "goldoret_multi_asset_state.json"
LOG_FILE = "goldoret_multi_asset_log.csv"

ASSET_CONFIG = {
    "XAU/USD": {
        "label": "XAUUSD",
        "icon": "🥇",
        "sl": float(os.getenv("XAU_SL_POINTS", "30")),
        "tp1": float(os.getenv("XAU_TP1_POINTS", "30")),
        "tp2": float(os.getenv("XAU_TP2_POINTS", "60")),
        "tp3": float(os.getenv("XAU_TP3_POINTS", "90")),
        "tp4": float(os.getenv("XAU_TP4_POINTS", "120")),
        "max_distance": float(os.getenv("XAU_MAX_DISTANCE_POINTS", "5")),
        "contract_size": float(os.getenv("XAUUSD_CONTRACT_SIZE_OZ", "100")),
        "min_lot": float(os.getenv("XAU_MIN_LOT", "0.01")),
        "max_lot": float(os.getenv("XAU_MAX_LOT", "2.0")),
    },
    "BTC/USD": {
        "label": "BTCUSD",
        "icon": "₿",
        "sl": float(os.getenv("BTC_SL_POINTS", "300")),
        "tp1": float(os.getenv("BTC_TP1_POINTS", "300")),
        "tp2": float(os.getenv("BTC_TP2_POINTS", "600")),
        "tp3": float(os.getenv("BTC_TP3_POINTS", "900")),
        "tp4": float(os.getenv("BTC_TP4_POINTS", "1200")),
        "max_distance": float(os.getenv("BTC_MAX_DISTANCE_POINTS", "80")),
        "contract_size": float(os.getenv("BTCUSD_CONTRACT_SIZE", "1")),
        "min_lot": float(os.getenv("BTC_MIN_LOT", "0.01")),
        "max_lot": float(os.getenv("BTC_MAX_LOT", "1.0")),
    },
}

# ==========================================================
# 🧰 OUTILS
# ==========================================================
def log_print(text):
    print(text, flush=True)


def now_local():
    return datetime.now(TZ)


def parse_hhmm(value):
    h, m = value.strip().split(":")
    return dtime(int(h), int(m))


def in_allowed_session():
    if not USE_SESSION_FILTER:
        return True, "filtre horaire désactivé"
    current = now_local().time()
    for part in ALLOWED_SESSIONS.split(","):
        if "-" not in part:
            continue
        raw_start, raw_end = part.split("-", 1)
        start = parse_hhmm(raw_start)
        end = parse_hhmm(raw_end)
        if start <= current <= end:
            return True, f"session autorisée {raw_start.strip()}-{raw_end.strip()}"
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


def log_signal(signal, sent, reason):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        fields = [
            "time", "sent", "reason", "symbol", "pattern", "side", "score",
            "spot", "entry", "sl", "tp1", "tp2", "tp3", "tp4",
            "distance", "lot", "risk"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "time": now_local().strftime("%Y-%m-%d %H:%M:%S"),
            "sent": sent,
            "reason": reason,
            "symbol": signal.get("symbol", ""),
            "pattern": signal.get("pattern", ""),
            "side": signal.get("side", ""),
            "score": signal.get("score", ""),
            "spot": signal.get("spot", ""),
            "entry": signal.get("entry", ""),
            "sl": signal.get("sl", ""),
            "tp1": signal.get("tp1", ""),
            "tp2": signal.get("tp2", ""),
            "tp3": signal.get("tp3", ""),
            "tp4": signal.get("tp4", ""),
            "distance": signal.get("distance", ""),
            "lot": signal.get("lot", ""),
            "risk": signal.get("risk_estimated", ""),
        })


def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def avg(values):
    return sum(values) / len(values) if values else 0


def body(c):
    return abs(c["close"] - c["open"])


def bull(c):
    return c["close"] > c["open"]


def bear(c):
    return c["close"] < c["open"]

# ==========================================================
# 📡 DONNÉES
# ==========================================================
def fetch_symbol(symbol):
    if not TWELVE_KEY:
        return None, None, "Clé Twelve Data manquante"
    try:
        r = requests.get(
            f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVE_KEY}",
            timeout=20
         )
        r.raise_for_status()
        price_data = r.json()
        if "price" not in price_data:
            return None, None, f"Erreur prix: {price_data}"
        spot = float(price_data["price"])

        r = requests.get(
            f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={TIMEFRAME}&outputsize={OUTPUTSIZE}&apikey={TWELVE_KEY}",
            timeout=20,
         )
        r.raise_for_status()
        data = r.json()
        if "values" not in data:
            return spot, None, f"Erreur historique: {data}"
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
        return spot, candles, "OK"
    except Exception as e:
        return None, None, str(e)

# ==========================================================
# 📊 CONTEXTE
# ==========================================================
def trend_context(candles):
    closes = [c["close"] for c in candles]
    last = closes[-1]
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    if e50 is None:
        return {
            "trend": "inconnue",
            "allow_buy": True,
            "allow_sell": True,
            "ema50": None,
            "ema200": None,
        }
    if e200 is None:
        return {
            "trend": "hausse" if last >= e50 else "baisse",
            "allow_buy": last >= e50,
            "allow_sell": last <= e50,
            "ema50": e50,
            "ema200": None,
        }
    buy = last >= e50 >= e200
    sell = last <= e50 <= e200
    return {
        "trend": "hausse" if buy else "baisse" if sell else "range",
        "allow_buy": buy,
        "allow_sell": sell,
        "ema50": e50,
        "ema200": e200,
    }

# ==========================================================
# 💼 MONEY MANAGEMENT
# ==========================================================
def calculate_lot(entry, sl, cfg):
    dist = abs(entry - sl)
    if dist <= 0:
        return 0.0, 0.0, "SL invalide"
    risk_account = ACCOUNT_BALANCE * RISK_PERCENT / 100
    risk_usd = risk_account * EURUSD_RATE if ACCOUNT_CURRENCY == "EUR" else risk_account
    lot = risk_usd / (dist * cfg["contract_size"])
    lot = min(lot, cfg["max_lot"])
    warning = "Vérifie le risque affiché chez le courtier avant validation."
    if 0 < lot < cfg["min_lot"]:
        lot = cfg["min_lot"]
        warning = "Lot minimum utilisé : le risque réel peut dépasser la cible."
    real_usd = lot * dist * cfg["contract_size"]
    real_account = real_usd / EURUSD_RATE if ACCOUNT_CURRENCY == "EUR" else real_usd
    return round(lot, 3), round(real_account, 2), warning


def make_levels(side, entry, cfg):
    if side == "BUY":
        return (
            round(entry - cfg["sl"], 2),
            round(entry + cfg["tp1"], 2),
            round(entry + cfg["tp2"], 2),
            round(entry + cfg["tp3"], 2),
            round(entry + cfg["tp4"], 2),
        )
    return (
        round(entry + cfg["sl"], 2),
        round(entry - cfg["tp1"], 2),
        round(entry - cfg["tp2"], 2),
        round(entry - cfg["tp3"], 2),
        round(entry - cfg["tp4"], 2),
    )

# ==========================================================
# 🔎 CONSTRUCTION SIGNAL
# ==========================================================
def build_signal(symbol, spot, side, pattern, score, entry, details, trend, session_reason, cfg):
    sl, tp1, tp2, tp3, tp4 = make_levels(side, entry, cfg)
    lot, risk, warning = calculate_lot(entry, sl, cfg)
    return {
        "symbol": cfg["label"],
        "icon": cfg["icon"],
        "side": side,
        "pattern": pattern,
        "score": max(0, min(100, int(score))),
        "spot": round(spot, 2),
        "entry": round(entry, 2),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp4": tp4,
        "distance": round(abs(spot - entry), 2),
        "trend": trend["trend"],
        "session": session_reason,
        "details": details,
        "lot": lot,
        "micro_lots": round(lot * 100, 1),
        "risk_estimated": risk,
        "risk_warning": warning,
        "signature": f"{cfg['label']}|{side}|{pattern}|{round(entry, 2)}",
    }

# ==========================================================
# 🧱 PATTERN 1 : ORDER BLOCK PREMIUM
# ==========================================================
def detect_order_block(symbol, spot, candles, trend, session_reason, cfg):
    candidates = []
    bodies = [body(c) for c in candles]
    last = candles[-1]
    start = max(SWING_LOOKBACK + 2, len(candles) - OB_LOOKBACK)
    for i in range(len(candles) - 4, start - 1, -1):
        ob = candles[i]
        after = candles[i + 1]
        previous = candles[max(0, i - SWING_LOOKBACK):i]
        avg_body = avg(bodies[max(0, i - 20):i])
        if not previous or avg_body <= 0:
            continue
        prev_high = max(c["high"] for c in previous)
        prev_low = min(c["low"] for c in previous)
        strong = body(after) >= avg_body * DISPLACEMENT_MULTIPLIER
        tol = cfg["sl"] * RETEST_TOLERANCE_MULT

        if bear(ob) and bull(after):
            bos = after["close"] > prev_high
            zone_low, zone_high = ob["low"], ob["open"]
            retest = last["low"] <= zone_high + tol and last["close"] >= zone_low - tol
            if bos and strong and retest and (not USE_TREND_FILTER or trend["allow_buy"]):
                score = 55 + 20 + (15 if trend["allow_buy"] else -15) + 5
                entry = round((zone_low + zone_high) / 2, 2)
                candidates.append(build_signal(symbol, spot, "BUY", "Order Block Premium", score, entry, f"OB haussier + BOS au-dessus {prev_high:.2f} + impulsion {body(after):.2f}/{avg_body:.2f}", trend, session_reason, cfg))

        if bull(ob) and bear(after):
            bos = after["close"] < prev_low
            zone_low, zone_high = ob["open"], ob["high"]
            retest = last["high"] >= zone_low - tol and last["close"] <= zone_high + tol
            if bos and strong and retest and (not USE_TREND_FILTER or trend["allow_sell"]):
                score = 55 + 20 + (15 if trend["allow_sell"] else -15) + 5
                entry = round((zone_low + zone_high) / 2, 2)
                candidates.append(build_signal(symbol, spot, "SELL", "Order Block Premium", score, entry, f"OB baissier + BOS sous {prev_low:.2f} + impulsion {body(after):.2f}/{avg_body:.2f}", trend, session_reason, cfg))
    return candidates

# ==========================================================
# 🔁 PATTERN 2 : BREAK & RETEST
# ==========================================================
def detect_break_retest(symbol, spot, candles, trend, session_reason, cfg):
    candidates = []
    recent = candles[-30:]
    previous = candles[-45:-15]
    if len(previous) < 10 or len(recent) < 10:
        return candidates
    level_high = max(c["high"] for c in previous)
    level_low = min(c["low"] for c in previous)
    tol = cfg["sl"] * 0.15
    last = candles[-1]
    broke_up = any(c["close"] > level_high for c in recent[:-1])
    broke_down = any(c["close"] < level_low for c in recent[:-1])

    if broke_up and trend["allow_buy"] and last["low"] <= level_high + tol and last["close"] >= level_high:
        entry = round(level_high, 2)
        score = 70 + (15 if trend["allow_buy"] else 0)
        candidates.append(build_signal(symbol, spot, "BUY", "Break & Retest", score, entry, f"Cassure haussière puis retest du niveau {level_high:.2f}", trend, session_reason, cfg))

    if broke_down and trend["allow_sell"] and last["high"] >= level_low - tol and last["close"] <= level_low:
        entry = round(level_low, 2)
        score = 70 + (15 if trend["allow_sell"] else 0)
        candidates.append(build_signal(symbol, spot, "SELL", "Break & Retest", score, entry, f"Cassure baissière puis retest du niveau {level_low:.2f}", trend, session_reason, cfg))

    return candidates

# ==========================================================
# 🧹 PATTERN 3 : LIQUIDITY SWEEP
# ==========================================================
def detect_liquidity_sweep(symbol, spot, candles, trend, session_reason, cfg):
    candidates = []
    if len(candles) < 30:
        return candidates
    previous = candles[-25:-2]
    last = candles[-1]
    prior_high = max(c["high"] for c in previous)
    prior_low = min(c["low"] for c in previous)
    sweep_size = cfg["sl"] * 0.10

    if last["high"] > prior_high + sweep_size and last["close"] < prior_high and (not USE_TREND_FILTER or trend["allow_sell"]):
        entry = round(prior_high, 2)
        score = 72 + (13 if trend["allow_sell"] else 0)
        candidates.append(build_signal(symbol, spot, "SELL", "Liquidity Sweep", score, entry, f"Balayage liquidité au-dessus {prior_high:.2f} puis réintégration", trend, session_reason, cfg))

    if last["low"] < prior_low - sweep_size and last["close"] > prior_low and (not USE_TREND_FILTER or trend["allow_buy"]):
        entry = round(prior_low, 2)
        score = 72 + (13 if trend["allow_buy"] else 0)
        candidates.append(build_signal(symbol, spot, "BUY", "Liquidity Sweep", score, entry, f"Balayage liquidité sous {prior_low:.2f} puis réintégration", trend, session_reason, cfg))

    return candidates

# ==========================================================
# 🏆 CHOISIR LE MEILLEUR SIGNAL
# ==========================================================
def detect_best_signal(symbol, spot, candles):
    cfg = ASSET_CONFIG.get(symbol)
    if not cfg:
        return None, f"Symbole non configuré: {symbol}"
    if not candles or len(candles) < 60:
        return None, "Pas assez de bougies"

    session_ok, session_reason = in_allowed_session()
    if not session_ok:
        return None, session_reason

    trend = trend_context(candles)
    candidates = []
    candidates += detect_order_block(symbol, spot, candles, trend, session_reason, cfg)
    candidates += detect_break_retest(symbol, spot, candles, trend, session_reason, cfg)
    candidates += detect_liquidity_sweep(symbol, spot, candles, trend, session_reason, cfg)

    if not candidates:
        return None, f"Aucun pattern de qualité. Tendance={trend['trend']}"

    candidates = [c for c in candidates if c["score"] >= MIN_SCORE and c["distance"] <= cfg["max_distance"]]
    if not candidates:
        return None, "Pattern trouvé mais score insuffisant ou prix trop loin de l’entrée"

    best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
    return best, "OK"

# ==========================================================
# 🔒 ANTI-SPAM + TELEGRAM
# ==========================================================
def can_send(signal):
    state = load_state()
    key = signal["signature"]
    last = state.get(key, 0)
    elapsed = time.time() - float(last)
    if elapsed < MIN_SECONDS_BETWEEN_SIGNALS:
        return False, f"Anti-spam actif ({int(elapsed)}s depuis signal similaire)"
    state[key] = time.time()
    save_state(state)
    return True, "OK"


def send_alert(signal):
    if not TOKEN or not CHAT_ID:
        log_print("❌ TOKEN ou CHAT_ID manquant")
        return False
    side_icon = "🟢" if signal["side"] == "BUY" else "🔴"
    action = "Achat uniquement si le prix reste proche de l’entrée" if signal["side"] == "BUY" else "Vente uniquement si le prix reste proche de l’entrée"
    message = (
        f"🔥 {signal['icon']} {signal['symbol']} SIGNAL QUALITÉ 🔥\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{side_icon} Signal : {signal['side']}\n"
        f"🧩 Pattern : {signal['pattern']}\n"
        f"🕐 Heure : {now_local().strftime('%H:%M')}\n"
        f"⭐ Score : {signal['score']}/100\n\n"
        f"📍 ZONE DE TRADING\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Prix spot : {signal['spot']}\n"
        f"🎯 Entrée indicative : {signal['entry']}\n"
        f"📏 Distance prix/entrée : {signal['distance']} pts\n"
        f"🛑 Stop-loss : {signal['sl']}\n\n"
        f"🎯 OBJECTIFS LARGES\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 TP1 : {signal['tp1']}\n"
        f"🥈 TP2 : {signal['tp2']}\n"
        f"🥉 TP3 : {signal['tp3']}\n"
        f"🏆 TP4 : {signal['tp4']}\n\n"
        f"📊 CONTEXTE\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Tendance : {signal['trend']}\n"
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
        f"➡️ {action}\n"
        f"🚫 Ne pas courir après le prix.\n"
        f"⚠️ Signal informatif, vérification manuelle obligatoire."
    )
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=20
         )
        if r.status_code == 200:
            log_print(f"✅ Signal envoyé: {signal['symbol']} {signal['side']} {signal['pattern']}")
            return True
        log_print(f"❌ Erreur Telegram: {r.text}")
        return False
    except Exception as e:
        log_print(f"❌ Erreur Telegram: {e}")
        return False

# ==========================================================
# 🚀 BOUCLE PRINCIPALE
# ==========================================================
if __name__ == "__main__":
    log_print("🚀 Goldoret Multi-Actifs démarré : XAUUSD + BTCUSD")
    log_print(f"⚙️ Symboles={SYMBOLS} | interval={CHECK_INTERVAL_MINUTES}min | score_min={MIN_SCORE} | sessions={ALLOWED_SESSIONS}")

    if SEND_STARTUP_MESSAGE and TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": "✅ Goldoret Multi-Actifs en ligne : XAUUSD + BTCUSD"},
                timeout=20
             )
        except Exception:
            pass

    while True:
        for symbol in SYMBOLS:
            try:
                spot, candles, status = fetch_symbol(symbol)
                if spot is None or candles is None:
                    log_print(f"[{now_local().strftime('%H:%M')}] {symbol} ⚠️ Données indisponibles: {status}")
                    continue

                signal, reason = detect_best_signal(symbol, spot, candles)
                if signal is None:
                    log_print(f"[{now_local().strftime('%H:%M')}] {symbol} Prix={spot} — pas de signal: {reason}")
                    continue

                ok, spam_reason = can_send(signal)
                if not ok:
                    log_print(f"[{now_local().strftime('%H:%M')}] {symbol} ⏸️ {spam_reason}")
                    log_signal(signal, sent=False, reason=spam_reason)
                    continue

                sent = send_alert(signal)
                log_signal(signal, sent=sent, reason="envoyé" if sent else "erreur envoi")

            except Exception as e:
                log_print(f"❌ Erreur boucle {symbol}: {e}")

        time.sleep(CHECK_INTERVAL_MINUTES * 60)
