from importlib import import_module

# Basit strateji kayıt defteri. Yeni strateji eklemek = dosya eklemek + burada ad vermek.
REGISTRY = {
    "ema_st_wt": ("strategies.ssl_compat", "analyze_ema_st_wt"),  # mevcut analizini buraya taşıyabilirsin (opsiyonel)
    "ssl_channel": ("strategies.ssl_channel", "analyze_ssl_channel"),
    "wt_cross": ("strategies.wt_cross", "analyze_wt_cross"),
}

def run(name: str, df, config: dict):
    key = (name or "ema_st_wt").lower()
    if key not in REGISTRY:
        # bilinmeyen tip -> HOLD
        return {"signal": "HOLD", "price": float(df['close'].iloc[-1])}
    mod_name, fn_name = REGISTRY[key]
    mod = import_module(mod_name)
    fn = getattr(mod, fn_name)
    return fn(df, config)
