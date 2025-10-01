#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, logging, glob, pathlib
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
        print("WT payload:", json.dumps(payload, ensure_ascii=False))
        r = requests.post(WT_URL, json=payload, timeout=10)
        body = r.text if hasattr(r, "text") else "<no text>"
        print("WT status:", r.status_code, "resp:", body)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"WT send error: {e}")
        raise

# ============== CONFIG LOADER ==============
BASE = pathlib.Path(__file__).parent

def load_pairs():
    """
    1) configs/*.json varsa: onları yükler
    2) yoksa: legacy pairs.json'dan okur
    """
    cfg_dir = BASE / "configs"
    pairs = []
    if cfg_dir.exists():
        for pth in sorted(glob.glob(str(cfg_dir / "*.json"))):
            try:
                with open(pth, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if obj.get("enabled", True):
                    pairs.append(obj)
            except Exception as e:
                logger.error(f"[config] {pth} okunamadı: {e}")
    if not pairs:
        legacy = BASE / "pairs.json"
        if legacy.exists():
            with open(legacy, "r", encoding="utf-8") as f:
                data = json.load(f)
            pairs = [p for p in data.get("pairs", []) if p.get("enabled", True)]
    return pairs

# ============== STRATEGY DISPATCHER ==============
try:
    # strategies/ package’ı varsa kullan
    from strategies import run as run_strategy
except Exception:
    run_strategy = None

def analyze_dispatch(df: pd.DataFrame, config: dict):
    """config['type']'a göre ilgili stratejiyi çağırır.
       strategies yoksa 'tmh' yerinde basit FALLBACK kullanır."""
    stype = (config.get("type") or "tmh").lower()

    if run_strategy:  # modüler yol
        return run_strategy(stype, df, config)

    # ---- FALLBACK (strategies klasörü yoksa) ----
    # Basitleştirilmiş “tmh” (EMA+Supertrend).
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
# pair_key -> {"pos": "NONE|LONG|SHORT", "last_sig": "ENTER-LONG|EXIT-LONG|...", "ts": epoch}
pos_state = {}

def key_of(pair):
    return f"{pair['symbol']}@{pair['timeframe']}"

def can_send(pair_key, signal, cooldown_sec=90):
    """Aynı sinyali belirli süre içinde tekrar yollama (spam önleyici)."""
    st = pos_state.get(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
    if st.get("last_sig") == signal and (time() - st.get("ts",0)) < cooldown_sec:
        return False
    return True

def update_after_send(pair_key, signal):
    st = pos_state.setdefault(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
    st["last_sig"] = signal
    st["ts"] = time()
    # pozisyon değişimi
    if signal == "ENTER-LONG":
        st["pos"] = "LONG"
    elif signal == "ENTER-SHORT":
        st["pos"] = "SHORT"
    elif signal in ("EXIT-LONG","EXIT-SHORT","EXIT-ALL"):
        st["pos"] = "NONE"

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

        # Bar kapanışında mı tetikleyeceğiz?
        use_closed = bool(config.get("signal_on_close", True))  # default: kapanışta
        df_in = df.iloc[:-1] if (use_closed and len(df) > 1) else df

        result = analyze_dispatch(df_in, config)
        signal = result["signal"]
        price  = float(result.get("price", df_in["close"].iloc[-1]))
        logger.info(f"📊 {symbol} [{stype}] | {signal} @ ${price:.4f}")

        pair_key = key_of(pair)
        st = pos_state.setdefault(pair_key, {"pos":"NONE","last_sig":None,"ts":0})
        pos = st["pos"]

        if signal == "ENTER-LONG" and "enter_long" in alerts:
            if pos != "LONG" and can_send(pair_key, signal):
                send_wt(alerts["enter_long"]); update_after_send(pair_key, signal)

        elif signal == "ENTER-SHORT" and "enter_short" in alerts:
            if pos != "SHORT" and can_send(pair_key, signal):
                send_wt(alerts["enter_short"]); update_after_send(pair_key, signal)

        elif signal == "EXIT-LONG" and "exit_long" in alerts:
            if pos == "LONG" and can_send(pair_key, signal):
                send_wt(alerts["exit_long"]); update_after_send(pair_key, signal)

        elif signal == "EXIT-SHORT" and "exit_short" in alerts:
            if pos == "SHORT" and can_send(pair_key, signal):
                send_wt(alerts["exit_short"]); update_after_send(pair_key, signal)

        # EXIT-ALL kullanırsan şuna benzer guard ekleyebilirsin:
        # elif signal == "EXIT-ALL" and "exit_all" in alerts:
        #     if pos != "NONE" and can_send(pair_key, signal):
        #         send_wt(alerts["exit_all"]); update_after_send(pair_key, signal)

    except Exception as e:
        logger.error(f"❌ {symbol} error: {e}")

def check_all_pairs():
    try:
        pairs = load_pairs()
        if not pairs:
            logger.warning("Aktif parite bulunamadı"); return
        logger.info(f"🔄 {len(pairs)} parite kontrol ediliyor...")
        bot_state["last_check"] = datetime.now().isoformat()
        threads = []
        for p in pairs:
            t = Thread(target=check_pair, args=(p,)); t.start(); threads.append(t)
        for t in threads: t.join()
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
        return jsonify({"pairs": load_pairs()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def start_scheduler():
    sch = BackgroundScheduler()
    sch.add_job(func=check_all_pairs, trigger="interval", seconds=CHECK_INTERVAL,
                id="check_pairs", replace_existing=True)
    sch.start()
    logger.info(f"⏰ Scheduler başlatıldı ({CHECK_INTERVAL}s)")

if __name__ == "__main__":
    logger.info("="*60); logger.info("🤖 WunderBot Starting..."); logger.info("="*60)
    bot_state["running"] = True
    bot_state["start_time"] = datetime.now().isoformat()
    check_all_pairs()
    start_scheduler()
    port = int(os.getenv("PORT", 5000))
    logger.info(f"🌐 Server: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
