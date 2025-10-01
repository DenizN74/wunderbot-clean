#!/usr/bin/env python3
"""
WunderBot - WT entegrasyonlu versiyon
"""

import os
import json
import logging
from datetime import datetime
from threading import Thread
from binance.client import Client
import pandas as pd
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import pathlib

# --- WT Client ---
WT_URL = "https://wtalerts.com/bot/custom"

BASE = pathlib.Path(__file__).parent
PAIRS_PATH = BASE / "pairs.json"

with open(PAIRS_PATH, "r", encoding="utf-8") as f:
    PAIRS_CFG = json.load(f)

def get_alert(pair_symbol: str, key: str) -> str:
    for p in PAIRS_CFG.get("pairs", []):
        if p.get("symbol") == pair_symbol and p.get("enabled", True):
            return p["alerts"][key]
    raise ValueError(f"Alert not found: {pair_symbol} / {key}")

def send_wt(code: str, quote_amount: float = 10):
    payload = {
        "code": code,
        "orderType": "market",
        "amountPerTradeType": "quote",
        "amountPerTrade": float(quote_amount),
    }
    print("WT payload:", json.dumps(payload, ensure_ascii=False))
    r = requests.post(WT_URL, json=payload, timeout=10)
    print("WT status:", r.status_code, "resp:", r.text)
    r.raise_for_status()
# --- /WT Client ---

# Environment
load_dotenv()
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('wunderbot')

# Flask app
app = Flask(__name__)

# Binance client
binance_client = Client()

# Global state
bot_state = {
    'running': False,
    'start_time': None,
    'total_signals': 0,
    'last_check': None
}

# Gönderilen alertleri takip et (tekrar göndermemek için)
sent_alerts = {}

# === INDICATORS ===
def get_klines(symbol, timeframe, limit=200):
    try:
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '4h': '4h', '1d': '1d'
        }
        interval = interval_map.get(timeframe, '15m')
        klines = binance_client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].set_index('timestamp')
    except Exception as e:
        logger.error(f"Veri çekme hatası ({symbol}): {e}")
        return pd.DataFrame()

def ema(series, period): return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def supertrend(df, period=10, multiplier=2.0):
    hl2 = (df['high'] + df['low']) / 2
    atr_values = atr(df, period)
    upper_band = hl2 + (multiplier * atr_values)
    lower_band = hl2 - (multiplier * atr_values)
    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)
    st.iloc[0] = upper_band.iloc[0]
    direction.iloc[0] = 1
    for i in range(1, len(df)):
        if pd.isna(atr_values.iloc[i]):
            st.iloc[i] = st.iloc[i-1]
            direction.iloc[i] = direction.iloc[i-1]
            continue
        if df['close'].iloc[i] <= upper_band.iloc[i]:
            st.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = -1
        else:
            st.iloc[i] = lower_band.iloc[i]
            direction.iloc[i] = 1
    return st, direction, atr_values

def wave_trend(df, channel=9, average=12, overbought=60, oversold=-60):
    hlc3 = (df['high'] + df['low'] + df['close']) / 3
    esa = hlc3.ewm(span=channel, adjust=False).mean()
    d = (hlc3 - esa).abs().ewm(span=channel, adjust=False).mean()
    ci = (hlc3 - esa) / (0.015 * d)
    ci = ci.fillna(0)
    wt1 = ci.ewm(span=average, adjust=False).mean()
    wt2 = wt1.rolling(window=4).mean()
    return wt1, wt2

def analyze(df, config):
    if len(df) < 50:
        return {'signal': 'HOLD', 'reason': 'Yetersiz veri', 'price': 0}
    ema_fast = ema(df['close'], config.get('ema_fast', 12))
    ema_slow = ema(df['close'], config.get('ema_slow', 26))
    st_line, st_dir, atr_val = supertrend(df, config.get('supertrend_period', 10), config.get('supertrend_multiplier', 2.0))
    wt1, wt2 = wave_trend(df, config.get('wt_channel', 9), config.get('wt_average', 12))
    close = df['close'].iloc[-1]
    ema_f = ema_fast.iloc[-1]; ema_s = ema_slow.iloc[-1]
    st_direction = st_dir.iloc[-1]
    wt1_curr, wt2_curr = wt1.iloc[-1], wt2.iloc[-1]
    wt1_prev, wt2_prev = wt1.iloc[-2], wt2.iloc[-2]
    ema_bull = (ema_f > ema_s) and (close > ema_f)
    ema_bear = (ema_f < ema_s) and (close < ema_f)
    st_bull = st_direction == 1
    st_bear = st_direction == -1
    wt_cross_up = (wt1_prev <= wt2_prev) and (wt1_curr > wt2_curr)
    wt_cross_down = (wt1_prev >= wt2_prev) and (wt1_curr < wt2_curr)
    wt_bull = wt_cross_up and (wt1_curr <= config.get('wt_oversold', -60))
    wt_bear = wt_cross_down and (wt1_curr >= config.get('wt_overbought', 60))
    bull_count = sum([ema_bull, st_bull, wt_bull])
    bear_count = sum([ema_bear, st_bear, wt_bear])
    mode = config.get('confirmation_mode', 'Any 2 of 3')
    required = 1 if mode == 'Any 1 of 3' else (3 if mode == 'All 3 Required' else 2)
    if bull_count >= required:
        return {'signal': 'ENTER-LONG', 'price': close}
    elif bear_count >= required:
        return {'signal': 'ENTER-SHORT', 'price': close}
    elif bear_count >= 2 or close < ema_s:
        return {'signal': 'EXIT-LONG', 'price': close}
    elif bull_count >= 2 or close > ema_s:
        return {'signal': 'EXIT-SHORT', 'price': close}
    return {'signal': 'HOLD', 'price': close}

# === MAIN LOOP ===
def check_pair(pair):
    symbol = pair['symbol']
    timeframe = pair['timeframe']
    alerts = pair.get('alerts', {})
    config = pair.get('strategy', {})
    try:
        df = get_klines(symbol, timeframe)
        if df.empty: return
        result = analyze(df, config)
        signal = result['signal']; price = result.get('price', 0)
        logger.info(f"📊 {symbol} | {signal} @ ${price:.4f}")
        if signal == 'ENTER-LONG' and 'enter_long' in alerts:
            send_wt(alerts['enter_long'])
        elif signal == 'EXIT-LONG' and 'exit_long' in alerts:
            send_wt(alerts['exit_long'])
        elif signal == 'ENTER-SHORT' and 'enter_short' in alerts:
            send_wt(alerts['enter_short'])
        elif signal == 'EXIT-SHORT' and 'exit_short' in alerts:
            send_wt(alerts['exit_short'])
    except Exception as e:
        logger.error(f"❌ {symbol} error: {e}")

def check_all_pairs():
    try:
        with open('pairs.json', 'r') as f:
            data = json.load(f)
        pairs = [p for p in data['pairs'] if p.get('enabled', True)]
        if not pairs:
            logger.warning("Aktif parite bulunamadı"); return
        logger.info(f"🔄 {len(pairs)} parite kontrol ediliyor...")
        bot_state['last_check'] = datetime.now().isoformat()
        threads = []
        for pair in pairs:
            t = Thread(target=check_pair, args=(pair,))
            t.start(); threads.append(t)
        for t in threads: t.join()
        logger.info("✅ Kontrol tamamlandı")
    except Exception as e:
        logger.error(f"❌ Genel hata: {e}")

# === FLASK ROUTES ===
@app.route('/health')
def health(): return jsonify({'status': 'ok', 'running': bot_state['running']})

@app.route('/status')
def status(): return jsonify(bot_state)

@app.route('/pairs')
def pairs():
    try:
        with open('pairs.json', 'r') as f: data = json.load(f)
        return jsonify(data)
    except: return jsonify({'error': 'pairs.json okunamadı'}), 500

# === SCHEDULER ===
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_all_pairs, trigger="interval", seconds=CHECK_INTERVAL,
                      id='check_pairs', replace_existing=True)
    scheduler.start()
    logger.info(f"⏰ Scheduler başlatıldı ({CHECK_INTERVAL} saniye)")

# === MAIN ===
if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🤖 WunderBot Starting...")
    logger.info("=" * 60)
    bot_state['running'] = True
    bot_state['start_time'] = datetime.now().isoformat()
    logger.info("🔍 İlk kontrol...")
    check_all_pairs()
    start_scheduler()
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🌐 Server: http://0.0.0.0:{port}")
    logger.info("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
