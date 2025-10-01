#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, logging, glob, pathlib, csv
from datetime import datetime
from threading import Thread
from time import time

import pandas as pd
import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from binance.client import Client

# ============== ENV & LOG ==============
load_dotenv()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%(Y)s-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wunderbot")

# ============== WT CLIENT ==============
WT_URL = "https://wtalerts.com/bot/custom"

def send_wt(code: str, extra=None):
    """Sadece code gönder. Miktar/lev WT tarafında ayarlı."""
    code = (code or "").strip()
    payload = {"code": code}
    if extra and isinstance(extra, dict):
        payload.update(extra)
    try:
        logger.info(f"📤 WT'ye gönderiliyor: {code}")
        r = requests.post(WT_URL, json=payload, timeout=10)
        body = r.text if hasattr(r, "text") else "<no text>"
        logger.info(f"✅ WT yanıtı [HTTP {r.status_code}]: {body}")
        r.raise_for_status()
    except Exception as e:
        logger.error(f"❌ WT send error: {e}")
        raise

# ============== HELPERS ==============
def _as_bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("true", "1", "yes", "y", "t")

def _as_int(v, default=0):
    try:
        s = str(v).strip()
        if s == "": return default
        s = s.replace(",", ".")
        return int(float(s))
    except Exception:
        return default

def _as_float(v, default=0.0):
    try:
        s = str(v).strip()
        if s == "": return default
        s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default

# ============== CONFIG LOADER ==============
BASE = pathlib.Path(__file__).parent

def load_pairs():
    """
    Öncelik: Google Sheets (SHEET_URL)
    Sonra  : configs/*.json
    En son : legacy pairs.json
    """
    pairs = []

    # 1) Google Sheets (CSV) — yeni arayüz
    sheet_url = os.getenv("SHEET_URL")
    if sheet_url:
        try:
            logger.info(f"📥 Google Sheets'den config çekiliyor: {sheet_url[:60]}...")
            resp = requests.get(sheet_url, timeout=10)
            resp.raise_for_status()
            reader = csv.DictReader(resp.text.splitlines())

            row_count = 0
            enabled_count = 0
            
            for row in reader:
                row_count += 1
                symbol = (row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                if not _as_bool(row.get("enabled"), False):
                    continue

                enabled_count += 1
                timeframe = (row.get("timeframe") or "15m").strip()
                
                # Başlangıç pozisyonu (yeni sütun)
                initial_pos = (row.get("initial_position") or "NONE").strip().upper()
                if initial_pos not in ["LONG", "SHORT", "NONE"]:
                    initial_pos = "NONE"

                strategy = {
                    "type": (row.get("strategy.type") or "tmh").strip().lower(),
                    # TMH / hibrit alanları
                    "ema_fast":              _as_int(row.get("ema_fast"), 12),
                    "ema_slow":              _as_int(row.get("ema_slow"), 26),
                    "supertrend_period":     _as_int(row.get("supertrend_period"), 10),
                    "supertrend_multiplier": _as_float(row.get("supertrend_multiplier"), 2.0),
                    "wt_channel":            _as_int(row.get("wt_channel"), 9),
                    "wt_average":            _as_int(row.get("wt_average"), 12),
                    "wt_overbought":         _as_float(row.get("wt_overbought"), 60.0),
                    "wt_oversold":           _as_float(row.get("wt_oversold"), -60.0),
                    # SSL
                    "ssl_period":            _as_int(row.get("ssl_period"), 10),
                    # WT_CROSS
                    "n1":                    _as_int(row.get("n1"), 10),
                    "n2":                    _as_int(row.get("n2"), 21),
                    "obLevel2":              _as_float(row.get("obLevel2"), 53.0),
                    "osLevel2":              _as_float(row.get("osLevel2"), -53.0),
                    "mode":                 (row.get("mode") or "").strip() or None,
                    "enter_exit":            _as_bool(row.get("enter_exit"), False),
                    # Genel
                    "confirmation_mode":    (row.get("confirmation_mode") or None),
                    "signal_on_close":       _as_bool(row.get("signal_on_close"), True),
                }

                alerts = {
                    "enter_long":  (row.get("alerts.enter_long")  or "").strip() or None,
                    "exit_long":   (row.get("alerts.exit_long")   or "").strip() or None,
                    "enter_short": (row.get("alerts.enter_short") or "").strip() or None,
                    "exit_short":  (row.get("alerts.exit_short")  or "").strip() or None,
                    "exit_all":    (row.get("alerts.exit_all")    or "").strip() or None,
                }

                pairs.append({
                    "symbol":           symbol,
                    "timeframe":        timeframe,
                    "enabled":          True,
                    "initial_position": initial_pos,  # ← YENİ ALAN
                    "strategy":         strategy,
                    "alerts":           alerts,
                })

            logger.info(f"✅ Google Sheets: {row_count} satır okundu, {enabled_count} parite aktif")
            
            if pairs:
                return pairs
            else:
                logger.warning("⚠️  Google Sheets boş veya hiç 'enabled=TRUE' satır yok. configs/ → pairs.json'a düşüyorum.")

        except Exception as e:
            logger.error(f"❌ Google Sheets okuma hatası: {e}")
            logger.warning("⚠️  configs/ dizinine düşülüyor...")

    # 2) configs/*.json — mevcut sistem
    cfg_dir = BASE / "configs"
    if cfg_dir.exists():
        json_files = sorted(glob.glob(str(cfg_dir / "*.json")))
        if json_files:
            logger.info(f"📂 configs/ dizininden {len(json_files)} JSON dosyası okunuyor...")
        for pth in json_files:
            try:
                with open(pth, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if obj.get("enabled", True):
                    # JSON'da initial_position yoksa NONE varsayılan
                    if "initial_position" not in obj:
                        obj["initial_position"] = "NONE"
                    pairs.append(obj)
            except Exception as e:
                logger.error(f"[config] {pth} okunamadı: {e}")
    if pairs:
        return pairs

    # 3) legacy pairs.json — en son fallback
    legacy = BASE / "pairs.json"
    if legacy.exists():
        try:
            logger.info("📄 Legacy pairs.json dosyası okunuyor...")
            with open(legacy, "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("pairs", []):
                if p.get("enabled", True):
                    if "initial_position" not in p:
                        p["initial_position"] = "NONE"
                    pairs.append(p)
            return pairs
        except Exception as e:
            logger.error(f"[pairs.json] okunamadı: {e}")

    return pairs

# ============== STRATEGY DISPATCHER ==============
try:
    from strategies import run as run_strategy
except Exception:
    run_strategy = None

def analyze_dispatch(df: pd.DataFrame, config: dict):
    """config['type']'a göre ilgili stratejiyi çağırır."""
    stype = (config.get("type") or "tmh").lower()

    if run_strategy:
        return run_strategy(stype, df, config)

    # ---- FALLBACK (strategies klasörü yoksa) ----
    def ema(s, n): return s.ewm(span=n, adjust=False).mean()

    def atr(df_, n=14):
        h, l, c = df_["high"], df_["low"], df_["close"]
        tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(window=n).mean()

    def supertrend(df_, period=10, mult=2.0):
        hl2 = (df_["high"] + df_["low"]) / 2
        atrv = atr(df_, period)
        up = hl2 + mult*atrv
        dn = hl2 - mult*atrv
        st = pd.Series(index=df_.index, dtype=float)
        dirn = pd.Series(index=df_.index, dtype=int)
        st.iloc[0] = up.iloc[0]; dirn.iloc[0] = 1
        for i in range(1, len(df_)):
            if pd.isna(atrv.iloc[i]):
                st.iloc[i] = st.iloc[i-1]; dirn.iloc[i] = dirn.iloc[i-1]; continue
            if df_["close"].iloc[i] <= up.iloc[i]:
                st.iloc[i] = up.iloc[i]; dirn.iloc[i] = -1
            else:
                st.iloc[i] = dn.iloc[i]; dirn.iloc[i] = 1
        return st, dirn

    if len(df) < 50:
        return {"signal":"HOLD", "price": float(df["close"].iloc[-1]) if len(df) else 0}

    ema_f = ema(df["close"], int(config.get("ema_fast", 12)))
    ema_s = ema(df["close"], int(config.get("ema_slow", 26)))
    _, st_dir = supertrend(df, int(config.get("supertrend_period", 10)), float(config.get("supertrend_multiplier", 2.0)))

    close = float(df["close"].iloc[-1])
    emaBull = (ema_f.iloc[-1] > ema_s.iloc[-1]) and (close > ema_f.iloc[-1])
    emaBear = (ema_f.iloc[-1] < ema_s.iloc[-1]) and (close < ema_f.iloc[-1])
    stBull  = st_dir.iloc[-1] == 1
    stBear  = st_dir.iloc[-1] == -1

    bull = sum([emaBull, stBull])
    bear = sum([emaBear, stBear])

    if bull >= 2: return {"signal":"ENTER-LONG", "price": close}
    if bear >= 2: return {"signal":"ENTER-SHORT","price": close}
    if bear >= 1: return {"signal":"EXIT-LONG", "price": close}
    if bull >= 1: return {"signal":"EXIT-SHORT","price": close}
    return {"signal":"HOLD", "price": close}

# ============== DATA ==============
binance_client = Client()

def get_klines(symbol, timeframe, limit=200):
    try:
        interval = {
            "1m":"1m","5m":"5m","15m":"15m","30m":"30m",
            "1h":"1h","4h":"4h","1d":"1d"
        }.get(timeframe, "15m")
        ks = binance_client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(ks, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_volume","trades","taker_buy_base",
            "taker_buy_quote","ignore"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        return df[["timestamp","open","high","low","close","volume"]].set_index("timestamp")
    except Exception as e:
        logger.error(f"Veri çekme hatası ({symbol}): {e}")
        return pd.DataFrame()

# ============== POSITION STATE & DEBOUNCE ==============
pos_state = {}

def key_of(pair):
    return f"{pair['symbol']}@{pair['timeframe']}"

def can_send(pair_key, signal, cooldown_sec=90):
    """Aynı sinyali belirli süre içinde tekrar yollama."""
    st = pos_state.get(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
    if st.get("last_sig") == signal and (time() - st.get("ts",0)) < cooldown_sec:
        return False
    return True

def update_after_send(pair_key, signal):
    st = pos_state.setdefault(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
    st["last_sig"] = signal
    st["ts"] = time()
    if signal == "ENTER-LONG":
        st["pos"] = "LONG"
    elif signal == "ENTER-SHORT":
        st["pos"] = "SHORT"
    elif signal in ("EXIT-LONG","EXIT-SHORT","EXIT-ALL"):
        st["pos"] = "NONE"

# ============== POZISYON SENKRONIZASYONU ==============
def sync_positions_from_sheet():
    """
    Google Sheets'deki 'initial_position' sütununu okuyarak
    bot başlangıç pozisyonlarını otomatik olarak ayarla.
    """
    try:
        pairs = load_pairs()
        if not pairs:
            logger.warning("⚠️  Hiç parite bulunamadı, pozisyon senkronizasyonu atlanıyor.")
            return

        logger.info("🔄 Başlangıç pozisyonları Google Sheets'den yükleniyor...")
        
        synced = 0
        for pair in pairs:
            pair_key = key_of(pair)
            initial_pos = pair.get("initial_position", "NONE")
            
            pos_state[pair_key] = {
                "pos": initial_pos,
                "last_sig": None,
                "ts": 0
            }
            
            if initial_pos != "NONE":
                logger.info(f"  ├─ {pair_key}: {initial_pos} ✓")
                synced += 1
            else:
                logger.info(f"  ├─ {pair_key}: NONE")
        
        logger.info(f"✅ Pozisyon senkronizasyonu tamamlandı ({synced}/{len(pairs)} pozisyon aktif)")
        
    except Exception as e:
        logger.error(f"❌ Pozisyon senkronizasyonu hatası: {e}")

# ============== CORE LOOP ==============
app = Flask(__name__)
bot_state = {"running": False, "start_time": None, "last_check": None}

def check_pair(pair: dict):
    symbol   = pair["symbol"]
    tf       = pair["timeframe"]
    alerts   = pair.get("alerts", {})
    config   = pair.get("strategy", {}) or {}
    stype    = (config.get("type") or "tmh").lower()

    try:
        df = get_klines(symbol, tf)
        if df.empty:
            return

        use_closed = bool(config.get("signal_on_close", True))
        df_in = df.iloc[:-1] if (use_closed and len(df) > 1) else df

        result = analyze_dispatch(df_in, config)
        signal = result["signal"]
        price  = float(result.get("price", df_in["close"].iloc[-1]))
        
        pair_key = key_of(pair)
        st = pos_state.setdefault(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
        pos = st["pos"]
        
        logger.info(f"📊 {symbol} [{stype}] | {signal} @ ${price:.4f} | Pozisyon: {pos}")

        # Alert kontrolü
        if signal == "ENTER-LONG" and alerts.get("enter_long"):
            if pos != "LONG" and can_send(pair_key, signal):
                send_wt(alerts["enter_long"])
                update_after_send(pair_key, signal)

        elif signal == "ENTER-SHORT" and alerts.get("enter_short"):
            if pos != "SHORT" and can_send(pair_key, signal):
                send_wt(alerts["enter_short"])
                update_after_send(pair_key, signal)

        elif signal == "EXIT-LONG" and alerts.get("exit_long"):
            if pos == "LONG" and can_send(pair_key, signal):
                send_wt(alerts["exit_long"])
                update_after_send(pair_key, signal)

        elif signal == "EXIT-SHORT" and alerts.get("exit_short"):
            if pos == "SHORT" and can_send(pair_key, signal):
                send_wt(alerts["exit_short"])
                update_after_send(pair_key, signal)

    except Exception as e:
        logger.error(f"❌ {symbol} hatası: {e}")

def check_all_pairs():
    try:
        pairs = load_pairs()
        if not pairs:
            logger.warning("⚠️  Aktif parite bulunamadı")
            return
            
        logger.info(f"🔄 {len(pairs)} parite kontrol ediliyor...")
        bot_state["last_check"] = datetime.now().isoformat()
        
        threads = []
        for p in pairs:
            t = Thread(target=check_pair, args=(p,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
            
        logger.info("✅ Kontrol tamamlandı")
    except Exception as e:
        logger.error(f"❌ Genel hata: {e}")

# ============== FLASK & SCHEDULER ==============
@app.get("/health")
def health():
    return jsonify({"status":"ok", "running": bot_state["running"]})

@app.get("/status")
def status():
    return jsonify(bot_state)

@app.get("/pairs")
def pairs_view():
    try:
        pairs = load_pairs()
        return jsonify({
            "count": len(pairs),
            "pairs": pairs
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/positions")
def positions_view():
    """Mevcut pozisyon durumlarını göster"""
    try:
        return jsonify({
            "count": len(pos_state),
            "positions": pos_state
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def start_scheduler():
    sch = BackgroundScheduler()
    sch.add_job(
        func=check_all_pairs,
        trigger="interval",
        seconds=CHECK_INTERVAL,
        id="check_pairs",
        replace_existing=True
    )
    sch.start()
    logger.info(f"⏰ Scheduler başlatıldı ({CHECK_INTERVAL}s)")

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("🤖 WunderBot Starting...")
    logger.info("="*60)
    
    bot_state["running"] = True
    bot_state["start_time"] = datetime.now().isoformat()
    
    # Başlangıç pozisyonlarını Google Sheets'den oku
    sync_positions_from_sheet()
    
    # İlk kontrol
    check_all_pairs()
    
    # Scheduler başlat
    start_scheduler()
    
    port = int(os.getenv("PORT", 5000))
    logger.info(f"🌐 Server başlatıldı: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
