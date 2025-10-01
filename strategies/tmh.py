def analyze_tmh(df, config):
    """
    TMH (Triple Moving Hybrid) Strategy
    - EMA Cross (Fast vs Slow)
    - Supertrend Direction
    - Confirmation logic: any_2_of_3, all_3, supertrend_only
    """
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

    # Config parametreleri
    ema_fast = int(config.get("ema_fast", 12))
    ema_slow = int(config.get("ema_slow", 26))
    st_per   = int(config.get("supertrend_period", 10))
    st_mult  = float(config.get("supertrend_multiplier", 2.0))
    confirmation_mode = (config.get("confirmation_mode") or "any_2_of_3").lower()

    # İndikatör hesaplamaları
    ema_f = ema(df["close"], ema_fast)
    ema_s = ema(df["close"], ema_slow)
    _, st_dir = supertrend(df, st_per, st_mult)

    close = float(df["close"].iloc[-1])
    
    # Bullish/Bearish koşullar
    emaBull = (ema_f.iloc[-1] > ema_s.iloc[-1]) and (close > ema_f.iloc[-1])
    emaBear = (ema_f.iloc[-1] < ema_s.iloc[-1]) and (close < ema_f.iloc[-1])
    stBull  = st_dir.iloc[-1] == 1
    stBear  = st_dir.iloc[-1] == -1

    bull_count = sum([emaBull, stBull])
    bear_count = sum([emaBear, stBear])

    # Confirmation mode'a göre sinyal üret
    if confirmation_mode == "supertrend_only":
        # Sadece Supertrend'e göre karar ver
        if stBull and not stBear:
            return {"signal":"ENTER-LONG", "price": close}
        if stBear and not stBull:
            return {"signal":"ENTER-SHORT","price": close}
        # Exit: Supertrend ters yöne döndüğünde
        if stBear:
            return {"signal":"EXIT-LONG", "price": close}
        if stBull:
            return {"signal":"EXIT-SHORT", "price": close}
    
    elif confirmation_mode == "all_3":
        # Tüm indikatörler aynı yönde olmalı (en katı)
        # Not: 3. indikatör eklenebilir (örn: RSI, MACD)
        if bull_count >= 2:  # Şimdilik 2 üzerinden
            return {"signal":"ENTER-LONG", "price": close}
        if bear_count >= 2:
            return {"signal":"ENTER-SHORT","price": close}
        # Exit: Herhangi biri ters yöne döndüğünde
        if bear_count >= 1:
            return {"signal":"EXIT-LONG", "price": close}
        if bull_count >= 1:
            return {"signal":"EXIT-SHORT", "price": close}
    
    else:  # any_2_of_3 (varsayılan)
        # En az 2 indikatör aynı yönde olmalı
        if bull_count >= 2:
            return {"signal":"ENTER-LONG", "price": close}
        if bear_count >= 2:
            return {"signal":"ENTER-SHORT","price": close}
        # Exit: En az 2 indikatör ters yöne döndüğünde (ENTER ile aynı mantık)
        if bear_count >= 2:
            return {"signal":"EXIT-LONG", "price": close}
        if bull_count >= 2:
            return {"signal":"EXIT-SHORT", "price": close}

    return {"signal":"HOLD", "price": close}
