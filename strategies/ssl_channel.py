def analyze_ssl_channel(df, config):
    import numpy as np, pandas as pd
    if len(df) < 50:
        p = float(df['close'].iloc[-1]) if len(df) else 0
        return {'signal':'HOLD','price':p}
    period = int(config.get('ssl_period', config.get('period', 10)))
    smaHigh = df['high'].rolling(window=period).mean()
    smaLow  = df['low'].rolling(window=period).mean()
    close = df['close']
    hlv = [1]
    for i in range(1, len(df)):
        hi = smaHigh.iloc[i]; lo = smaLow.iloc[i]
        if close.iloc[i] > (hi if hi==hi else close.iloc[i]):  # hi==hi -> not NaN
            hlv.append(1)
        elif close.iloc[i] < (lo if lo==lo else close.iloc[i]):
            hlv.append(-1)
        else:
            hlv.append(hlv[-1])
    import numpy as np
    hlv = np.array(hlv)
    sslDown = (hlv < 0) * smaHigh + (hlv >= 0) * smaLow
    sslUp   = (hlv < 0) * smaLow  + (hlv >= 0) * smaHigh
    sslDown = pd.Series(sslDown, index=df.index)
    sslUp   = pd.Series(sslUp,   index=df.index)
    cross_up   = (sslUp.shift(1) <= sslDown.shift(1)) & (sslUp > sslDown)
    cross_down = (sslUp.shift(1) >= sslDown.shift(1)) & (sslUp < sslDown)
    price = float(close.iloc[-1])
    enter_exit = bool(config.get('enter_exit', False))
    if cross_up.iloc[-1]:
        return {'signal': ('EXIT-SHORT' if enter_exit else 'ENTER-LONG'), 'price': price}
    if cross_down.iloc[-1]:
        return {'signal': ('EXIT-LONG' if enter_exit else 'ENTER-SHORT'), 'price': price}
    return {'signal':'HOLD','price':price}
