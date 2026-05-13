import os
import time
import requests
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
INTERVAL = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))

def send_test():
    try:
        ticker = yf.Ticker("GC=F")
        price = ticker.history(period="1d", interval="15m")['Close'].iloc[-1]
        msg = f"🚀 Bot test OK\n Prix XAUUSD: {price:.2f}"
    except:
        msg = "🚀 Bot test OK (prix non disponible)"
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

if name == "main":
    send_test()
    while True:
        time.sleep(INTERVAL * 60)
        send_test()
