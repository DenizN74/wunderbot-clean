#!/usr/bin/env python3
"""
WunderBot - Automated Trading Signal Generator
Sıfırdan yazılmış, temiz, basit versiyon
"""

import os
import json
import time
import logging
from datetime import datetime
from threading import Thread
from binance.client import Client
import pandas as pd
import numpy as np
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# Environment
load_dotenv()
WEBHOOK_URL = os.getenv('WUNDERTRADING_WEBHOOK_URL', '')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))
BINANCE_KEY = os.getenv('BINANCE_API_KEY', '')
BINANCE_SECRET = os.getenv('BINANCE_API_SECRET', '')

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('wunderbot')

# Flask app (sadece health check için)
app = Flask(__name__)

# Global state
bot_state = {
    'running': False,
    'start_time': None,
    'total_signals': 0,
    'last_check': None,
    'positions': {}
}

# ═══════════════════════════════════════════════════════════════════════
# BINANCE CLIENT
# ═══════════════════════════════════════════════════════════════════════

binance_client = Client(BINANCE_KEY, BINANCE_SECRET) if BINANCE_KEY else Client()

def get_klines(symbol, timeframe, limit=200):
    """Binance'den mum verileri çeker"""
    try:
        interval_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '12h': '12h',
            '1d': '1d', '3d': '3d', '1w': '1w'
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
        
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df.set_index('timestamp', inplace=True)
        
        return df
    except Exception as e:
        logger.error(f"❌ {symbol} veri çekme hatası: {e}")
        return pd.DataFrame()

# ═══════════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════════

def ema(series, period):
    """Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    """Average True Range"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def supertrend(df, period=10, multiplier=2.0):
    """Supertrend Indicator"""
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
    """Wave Trend Oscillator"""
    hlc3 = (df['high'] + df['low'] + df['close']) / 3
    esa = hlc3.ewm(span=channel, adjust=False).mean()
    d = (hlc3 - esa).abs().ewm(span=channel, adjust=False).mean()
    ci = (hlc3 - esa) / (0.015 * d)
    ci = ci.fillna(0)
    
    wt1 = ci.ewm(span=average, adjust=False).mean()
    wt2 = wt1.rolling(window=4).mean()
    
    return wt1, wt2

# ═══════════════════════════════════════════════════════════════════════
# STRATEGY
# ═══════════════════════════════════════════════════════════════════════

def analyze(df, config):
    """Strateji analizi yapar ve sinyal üretir"""
    if len(df) < 50:
        return {'signal': 'HOLD', 'reason': 'Yetersiz veri'}
    
    # İndikatörleri hesapla
    ema_fast = ema(df['close'], config['ema_fast'])
    ema_slow = ema(df['close'], config['ema_slow'])
    st_line, st_dir, atr_val = supertrend(df, config['supertrend_period'], config['supertrend_multiplier'])
    wt1, wt2 = wave_trend(df, config['wt_channel'], config['wt_average'], config['wt_overbought'], config['wt_oversold'])
    
    # Son değerler
    close = df['close'].iloc[-1]
    ema_f = ema_fast.iloc[-1]
    ema_s = ema_slow.iloc[-1]
    st_direction = st_dir.iloc[-1]
    wt1_curr = wt1.iloc[-1]
    wt2_curr = wt2.iloc[-1]
    wt1_prev = wt1.iloc[-2]
    wt2_prev = wt2.iloc[-2]
    
    # EMA sinyalleri
    ema_bull = (ema_f > ema_s) and (close > ema_f)
    ema_bear = (ema_f < ema_s) and (close < ema_f)
    
    # Supertrend sinyalleri
    st_bull = st_direction == 1
    st_bear = st_direction == -1
    
    # Wave Trend sinyalleri
    wt_cross_up = (wt1_prev <= wt2_prev) and (wt1_curr > wt2_curr)
    wt_cross_down = (wt1_prev >= wt2_prev) and (wt1_curr < wt2_curr)
    wt_bull = wt_cross_up and (wt1_curr <= config['wt_oversold'])
    wt_bear = wt_cross_down and (wt1_curr >= config['wt_overbought'])
    
    # Onay sayıları
    bull_count = sum([ema_bull, st_bull, wt_bull])
    bear_count = sum([ema_bear, st_bear, wt_bear])
    
    # Gerekli onay
    mode = config['confirmation_mode']
    required = 1 if mode == 'Any 1 of 3' else (3 if mode == 'All 3 Required' else 2)
    
    # Sinyal üret
    if bull_count >= required:
        return {'signal': 'ENTER-LONG', 'price': close, 'atr': atr_val.iloc[-1], 'reason': f'Bull: {bull_count}/{required}'}
    elif bear_count >= required:
        return {'signal': 'ENTER-SHORT', 'price': close, 'atr': atr_val.iloc[-1], 'reason': f'Bear: {bear_count}/{required}'}
    
    # Exit kontrolleri
    if bear_count >= 2 or close < ema_s:
        return {'signal': 'EXIT-LONG', 'price': close, 'atr': atr_val.iloc[-1], 'reason': 'Exit sinyali'}
    
    if bull_count >= 2 or close > ema_s:
        return {'signal': 'EXIT-SHORT', 'price': close, 'atr': atr_val.iloc[-1], 'reason': 'Exit sinyali'}
    
    return {'signal': 'HOLD', 'price': close, 'atr': atr_val.iloc[-1]}

# ═══════════════════════════════════════════════════════════════════════
# ALERT SENDER
# ═══════════════════════════════════════════════════════════════════════

def send_alert(symbol, signal, alert_text, price):
    """WunderTrading'e alert gönderir"""
    if not WEBHOOK_URL:
        logger.warning(f"⚠️  Webhook URL tanımlı değil!")
        return False
    
    if signal == 'HOLD':
        return False
    
    try:
        # Form format için sadece alert parametresi
        payload = {'alert': alert_text}
        
        response = requests.post(
            WEBHOOK_URL,
            data=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            logger.info(f"✅ {symbol} | {signal} @ ${price:.4f} | Alert: {alert_text}")
            return True
        else:
            logger.error(f"❌ Alert hatası: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Alert gönderme hatası: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════

def check_pair(pair):
    """Bir parite için kontrol yapar"""
    symbol = pair['symbol']
    timeframe = pair['timeframe']
    alerts = pair['alerts']
    config = pair['strategy']
    
    try:
        # Veri çek
        df = get_klines(symbol, timeframe)
        if df.empty:
            return
        
        # Analiz
        result = analyze(df, config)
        signal = result['signal']
        price = result.get('price', 0)
        
        # Pozisyon takibi
        current_position = bot_state['positions'].get(symbol)
        
        # Log
        if signal != 'HOLD':
            logger.info(f"📊 {symbol} | {signal} @ ${price:.4f} | {result.get('reason', '')}")
        
        # Alert gönder
        alert_sent = False
        
        if signal == 'ENTER-LONG' and current_position != 'LONG':
            alert_sent = send_alert(symbol, signal, alerts['enter_long'], price)
            if alert_sent:
                bot_state['positions'][symbol] = 'LONG'
                bot_state['total_signals'] += 1
        
        elif signal == 'EXIT-LONG' and current_position == 'LONG':
            alert_sent = send_alert(symbol, signal, alerts['exit_long'], price)
            if alert_sent:
                bot_state['positions'][symbol] = None
                bot_state['total_signals'] += 1
        
        elif signal == 'ENTER-SHORT' and current_position != 'SHORT':
            alert_sent = send_alert(symbol, signal, alerts['enter_short'], price)
            if alert_sent:
                bot_state['positions'][symbol] = 'SHORT'
                bot_state['total_signals'] += 1
        
        elif signal == 'EXIT-SHORT' and current_position == 'SHORT':
            alert_sent = send_alert(symbol, signal, alerts['exit_short'], price)
            if alert_sent:
                bot_state['positions'][symbol] = None
                bot_state['total_signals'] += 1
        
    except Exception as e:
        logger.error(f"❌ {symbol} hata: {e}")

def check_all_pairs():
    """Tüm pariteleri kontrol eder"""
    try:
        with open('pairs.json', 'r') as f:
            data = json.load(f)
        
        pairs = [p for p in data['pairs'] if p.get('enabled', True)]
        
        if not pairs:
            logger.warning("⚠️  Aktif parite bulunamadı")
            return
        
        logger.info(f"🔄 {len(pairs)} parite kontrol ediliyor...")
        bot_state['last_check'] = datetime.now().isoformat()
        
        threads = []
        for pair in pairs:
            t = Thread(target=check_pair, args=(pair,))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        logger.info("✅ Kontrol tamamlandı")
        
    except Exception as e:
        logger.error(f"❌ Genel hata: {e}")

# ═══════════════════════════════════════════════════════════════════════
# FLASK ROUTES (Sadece monitoring)
# ═══════════════════════════════════════════════════════════════════════

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'running': bot_state['running']})

@app.route('/status')
def status():
    """Bot durumu"""
    return jsonify(bot_state)

@app.route('/pairs')
def pairs():
    """Aktif pariteler"""
    try:
        with open('pairs.json', 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except:
        return jsonify({'error': 'pairs.json okunamadı'}), 500

# ═══════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═══════════════════════════════════════════════════════════════════════

def start_scheduler():
    """Scheduler başlatır"""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=check_all_pairs,
        trigger="interval",
        seconds=CHECK_INTERVAL,
        id='check_pairs',
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"⏰ Scheduler başlatıldı ({CHECK_INTERVAL} saniye)")

# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🤖 WunderBot Starting...")
    logger.info("=" * 60)
    
    bot_state['running'] = True
    bot_state['start_time'] = datetime.now().isoformat()
    
    # İlk kontrol
    logger.info("🔍 İlk kontrol...")
    check_all_pairs()
    
    # Scheduler başlat
    start_scheduler()
    
    # Flask başlat
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🌐 Server: http://0.0.0.0:{port}")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=False)
