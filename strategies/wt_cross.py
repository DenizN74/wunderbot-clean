def analyze_wt_cross(df, config):
    import numpy as np
    if len(df) < 50:
        p = float(df['close'].iloc[-1]) if len(df) else 0
        return {"signal":"HOLD","price":p}
    n1 = int(config.get("n1", 10))
    n2 = int(config.get("n2", 21))
    ob2 = float(config.get("obLevel2", 53))
    os2 = float(config.get("osLevel2", -53))
    mode = (config.get("mode") or "basic").lower()  # basic|oversold_bullish|overbought_bearish|dual_filtered
    enter_exit = bool(config.get("enter_exit", False))

    ap = (df["high"] + df["low"] + df["close"]) / 3.0
    esa = ap.ewm(span=n1, adjust=False).mean()
    d = (ap - esa).abs().ewm(span=n1, adjust=False).mean()
    ci = (ap - esa) / (0.015 * d.replace(0, np.nan))
    wt1 = ci.ewm(span=n2, adjust=False).mean()
    wt2 = wt1.rolling(window=4, min_periods=1).mean()

    bull = (wt1.shift(1) <= wt2.shift(1)) & (wt1 > wt2)
    bear = (wt1.shift(1) >= wt2.shift(1)) & (wt1 < wt2)

    last_bull = bool(bull.iloc[-1])
    last_bear = bool(bear.iloc[-1])
    last_wt2  = float(wt2.iloc[-1])
    price = float(df["close"].iloc[-1])

    def bull_ok():
        if mode in ("oversold_bullish","dual_filtered"):
            return last_bull and (last_wt2 < os2)
        return last_bull

    def bear_ok():
        if mode in ("overbought_bearish","dual_filtered"):
            return last_bear and (last_wt2 > ob2)
        return last_bear

    if bull_ok():
        return {"signal": ("EXIT-SHORT" if enter_exit else "ENTER-LONG"), "price": price}
    if bear_ok():
        return {"signal": ("EXIT-LONG" if enter_exit else "ENTER-SHORT"), "price": price}
    return {"signal":"HOLD","price":price}
