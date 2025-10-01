from importlib import import_module

REGISTRY = {
    "tmh":        ("strategies.tmh", "analyze_tmh"),           # senin mevcut hibrit
    "wt_cross":   ("strategies.wt_cross", "analyze_wt_cross"),  # LazyBear
    "ssl_channel":("strategies.ssl_channel", "analyze_ssl_channel"),
}

def run(name: str, df, config: dict):
    key = (name or "tmh").lower()
    if key not in REGISTRY:
        return {"signal":"HOLD", "price": float(df["close"].iloc[-1])}
    mod_name, fn_name = REGISTRY[key]
    mod = import_module(mod_name)
    fn  = getattr(mod, fn_name)
    return fn(df, config)
