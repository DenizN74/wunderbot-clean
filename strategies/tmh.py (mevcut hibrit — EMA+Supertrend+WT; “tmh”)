def analyze_tmh(df, config):
    import pandas as pd

    if len(df) < 50:
        p = float(df["close"].iloc[-1]) if len(df) else 0
        return {"signal":"HOLD","price":p}

    def ema(s, n): return s.ewm(span=int(n), adjust=False).mean()

    def atr(df_, n=14):
        h,l,c = df_["high"], df_["low"], df_["close"]
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

    ema_fast = int(config.get("ema_fast", 12))
    ema_slow = int(config.get("ema_slow", 26))
    st_per   = int(config.get("supertrend_period", 10))
    st_mult  = float(config.get("supertrend_multiplier", 2.0))

    ema_f = ema(df["close"], ema_fast)
    ema_s = ema(df["close"], ema_slow)
    _, st_dir = supertrend(df, st_per, st_mult)

    close = float(df["close"].iloc[-1])
    emaBull = (ema_f.iloc[-1] > ema_s.iloc[-1]) and (close > ema_f.iloc[-1])
    emaBear = (ema_f.iloc[-1] < ema_s.iloc[-1]) and (close < ema_f.iloc[-1])
    stBull  = st_dir.iloc[-1] == 1
    stBear  = st_dir.iloc[-1] == -1

    bull = sum([emaBull, stBull])
    bear = sum([emaBear, stBear])

    if bull >= 2: return {"signal":"ENTER-LONG", "price": close}
    if bear >= 2: return {"signal":"ENTER-SHORT","price": close}
    if bear >= 1: return {"signal":"EXIT-LONG",  "price": close}
    if bull >= 1: return {"signal":"EXIT-SHORT", "price": close}
    return {"signal":"HOLD", "price": close}
