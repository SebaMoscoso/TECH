"""
InvertirCL — Market Data Module v1.0 (Python)
===============================================
Fuentes de datos (todas gratuitas):
  - mindicador.cl  → UF, dólar observado, IPC, TPM, cobre, euro
  - DolarApi.com   → USD/CLP en tiempo real
  - Yahoo Finance  → IPSA (^IPSA) + acciones chilenas (.SN) con 15min delay

Instalación:
  pip install requests yfinance pandas numpy

USO:
  from market import Market
  data = Market.get_all()
  ipsa = Market.get_ipsa()
  historico = Market.get_historico("^IPSA", period="1y")
  rsi = Market.Indicators.rsi(closes, n=14)
"""

import requests
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False
    print("[Market] yfinance no instalado. Ejecuta: pip install yfinance")


# ══════════════════════════════════════════════════════════════
# CACHÉ INTERNA
# ══════════════════════════════════════════════════════════════
class _Cache:
    def __init__(self):
        self._data = {}

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        value, ts, ttl = item
        if time.time() - ts > ttl:
            return None  # expirado
        return value

    def set(self, key, value, ttl=60):
        self._data[key] = (value, time.time(), ttl)

    def status(self):
        result = {}
        for k, (v, ts, ttl) in self._data.items():
            age = int(time.time() - ts)
            result[k] = {
                "age_s": age,
                "fresh": age < ttl,
                "ttl": ttl,
                "has_value": v is not None,
            }
        return result


_cache = _Cache()

TTL = {
    "usdclp":   30,       # 30s
    "mindicador": 3600,   # 1h (valores diarios)
    "ipsa":     60,       # 1min
    "accion":   60,
    "historico": 3600,
}

FALLBACKS = {
    "usdclp":  {"compra": 927.0, "venta": 930.0, "promedio": 928.5, "fuente": "fallback"},
    "uf":      {"valor": 38741.0, "fuente": "fallback"},
    "ipc":     {"valor": 0.4,    "fuente": "fallback"},
    "tpm":     {"valor": 5.0,    "fuente": "fallback"},
    "cobre":   {"valor": 4.82,   "fuente": "fallback"},
    "euro":    {"valor": 1018.0, "fuente": "fallback"},
    "ipsa":    {"price": 7284.0, "changePct": 0.0, "fuente": "fallback"},
}


# ══════════════════════════════════════════════════════════════
# FETCH HELPER
# ══════════════════════════════════════════════════════════════
def _fetch(url: str, timeout: int = 8) -> dict:
    """GET con timeout y manejo de errores."""
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise RuntimeError(f"Error fetching {url}: {e}")


# ══════════════════════════════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════
class Indicators:
    """Cálculo preciso de indicadores técnicos sobre series de precios."""

    @staticmethod
    def rsi(closes: list, n: int = 14) -> Optional[float]:
        """RSI(n) — Relative Strength Index. Retorna valor 0-100."""
        if len(closes) < n + 1:
            return None
        closes = np.array(closes, dtype=float)
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = gains[:n].mean()
        avg_loss = losses[:n].mean()

        for i in range(n, len(deltas)):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 4)

    @staticmethod
    def ema(closes: list, n: int) -> Optional[float]:
        """EMA(n) — Exponential Moving Average."""
        if len(closes) < n:
            return None
        closes = np.array(closes, dtype=float)
        k   = 2 / (n + 1)
        ema = closes[:n].mean()
        for price in closes[n:]:
            ema = price * k + ema * (1 - k)
        return round(float(ema), 4)

    @staticmethod
    def sma(closes: list, n: int) -> Optional[float]:
        """SMA(n) — Simple Moving Average."""
        if len(closes) < n:
            return None
        return round(float(np.mean(closes[-n:])), 4)

    @staticmethod
    def macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
        """MACD(fast, slow, signal) — retorna dict con macd, signal, histogram."""
        if len(closes) < slow + signal:
            return None
        closes = np.array(closes, dtype=float)
        k_fast = 2 / (fast + 1)
        k_slow = 2 / (slow + 1)

        ema_fast = closes[:fast].mean()
        ema_slow = closes[:slow].mean()
        macd_line = []

        for i in range(slow, len(closes)):
            if i >= fast:
                ema_fast = closes[i] * k_fast + ema_fast * (1 - k_fast)
            ema_slow = closes[i] * k_slow + ema_slow * (1 - k_slow)
            macd_line.append(ema_fast - ema_slow)

        macd_arr = np.array(macd_line)
        k_sig    = 2 / (signal + 1)
        sig_val  = macd_arr[:signal].mean()
        for v in macd_arr[signal:]:
            sig_val = v * k_sig + sig_val * (1 - k_sig)

        macd_val = round(float(macd_arr[-1]), 4)
        sig_val  = round(float(sig_val), 4)
        return {
            "macd":      macd_val,
            "signal":    sig_val,
            "histogram": round(macd_val - sig_val, 4),
        }

    @staticmethod
    def bb(closes: list, n: int = 20, std: float = 2.0) -> Optional[dict]:
        """Bandas de Bollinger(n, std). Retorna upper, middle, lower, width."""
        if len(closes) < n:
            return None
        arr    = np.array(closes[-n:], dtype=float)
        sma    = arr.mean()
        sigma  = arr.std()
        return {
            "upper":  round(float(sma + std * sigma), 4),
            "middle": round(float(sma), 4),
            "lower":  round(float(sma - std * sigma), 4),
            "width":  round(float(4 * std * sigma / sma * 100), 4),
        }

    @staticmethod
    def atr(candles: list, n: int = 14) -> Optional[float]:
        """ATR(n) — Average True Range. candles: lista de dicts con high, low, close."""
        if len(candles) < n + 1:
            return None
        trs = []
        for i in range(1, len(candles)):
            c = candles[i]
            p = candles[i - 1]
            trs.append(max(
                c["high"] - c["low"],
                abs(c["high"] - p["close"]),
                abs(c["low"]  - p["close"]),
            ))
        trs = np.array(trs)
        atr = trs[:n].mean()
        for tr in trs[n:]:
            atr = (atr * (n - 1) + tr) / n
        return round(float(atr), 4)

    @staticmethod
    def vwap(candles: list) -> Optional[float]:
        """VWAP — Volume Weighted Average Price."""
        cum_pv  = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in candles)
        cum_vol = sum(c["volume"] for c in candles)
        if cum_vol == 0:
            return None
        return round(cum_pv / cum_vol, 4)

    @staticmethod
    def sharpe(retornos: list, tasa_libre_riesgo: float = 0.05, periodos: int = 252) -> Optional[float]:
        """Sharpe Ratio anualizado."""
        if len(retornos) < 2:
            return None
        r   = np.array(retornos, dtype=float)
        exc = r - tasa_libre_riesgo / periodos
        if r.std() == 0:
            return None
        return round(float(exc.mean() / r.std() * np.sqrt(periodos)), 4)

    @staticmethod
    def sortino(retornos: list, tasa_libre_riesgo: float = 0.05, periodos: int = 252) -> Optional[float]:
        """Sortino Ratio — solo penaliza volatilidad negativa."""
        if len(retornos) < 2:
            return None
        r      = np.array(retornos, dtype=float)
        neg    = r[r < 0]
        if len(neg) == 0 or neg.std() == 0:
            return None
        exc    = r.mean() - tasa_libre_riesgo / periodos
        return round(float(exc / neg.std() * np.sqrt(periodos)), 4)

    @staticmethod
    def max_drawdown(equity_curve: list) -> float:
        """Max Drawdown como porcentaje (0-100)."""
        eq   = np.array(equity_curve, dtype=float)
        peak = np.maximum.accumulate(eq)
        dd   = (peak - eq) / peak * 100
        return round(float(dd.max()), 4)

    @staticmethod
    def profit_factor(trades: list) -> Optional[float]:
        """Profit Factor = suma ganancias / suma pérdidas absolutas."""
        ganancias = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
        perdidas  = sum(-t["pnl"] for t in trades if t.get("pnl", 0) < 0)
        if perdidas == 0:
            return None
        return round(ganancias / perdidas, 4)


# ══════════════════════════════════════════════════════════════
# MATCHING ENGINE
# ══════════════════════════════════════════════════════════════
class MatchingEngine:
    """Simula ejecución real de órdenes con slippage y comisiones."""

    DEFAULT_CONFIG = {
        "slippage_bps": 5,      # 5 basis points
        "comision_pct": 0.1,    # 0.1% comisión corredor
        "spread_pct":   0.02,   # 0.02% spread bid-ask
    }

    @classmethod
    def precio_ejecucion(cls, precio: float, side: str, config: dict = None) -> float:
        """Precio real de ejecución incluyendo slippage y spread."""
        c         = {**cls.DEFAULT_CONFIG, **(config or {})}
        slippage  = precio * c["slippage_bps"] / 10_000
        half_spread = precio * c["spread_pct"] / 2 / 100
        if side == "buy":
            return round(precio + slippage + half_spread, 2)
        else:
            return round(precio - slippage - half_spread, 2)

    @classmethod
    def costos(cls, precio: float, cantidad: int, side: str, config: dict = None) -> dict:
        """Desglose de costos de una operación."""
        c           = {**cls.DEFAULT_CONFIG, **(config or {})}
        precio_ejec = cls.precio_ejecucion(precio, side, config)
        monto_total = precio_ejec * cantidad
        comision    = monto_total * c["comision_pct"] / 100
        slippage_m  = abs(precio_ejec - precio) * cantidad

        return {
            "precio_solicitado": precio,
            "precio_ejecucion":  precio_ejec,
            "cantidad":          cantidad,
            "monto_total":       round(monto_total, 0),
            "comision":          round(comision, 0),
            "slippage":          round(slippage_m, 0),
            "costo_total":       round(comision + slippage_m, 0),
        }

    @classmethod
    def ejecutar(cls, orden: dict, precio_mercado: float) -> dict:
        """
        Ejecutar una orden simulada.
        orden = {
            "symbol": "COPEC",
            "side": "buy" | "sell",
            "cantidad": 100,
            "tipo": "market" | "limit" | "stop",
            "limit_price": float (opcional),
            "stop_price": float (opcional),
        }
        """
        side    = orden["side"]
        tipo    = orden["tipo"]
        cant    = orden["cantidad"]
        symbol  = orden.get("symbol", "")

        precio_ejec = None
        estado      = "pending"

        if tipo == "market":
            precio_ejec = cls.precio_ejecucion(precio_mercado, side)
            estado      = "filled"

        elif tipo == "limit":
            lp = orden.get("limit_price", 0)
            if side == "buy"  and precio_mercado <= lp:
                precio_ejec = lp; estado = "filled"
            if side == "sell" and precio_mercado >= lp:
                precio_ejec = lp; estado = "filled"

        elif tipo == "stop":
            sp = orden.get("stop_price", 0)
            if side == "sell" and precio_mercado <= sp:
                # Slippage extra en stop — simula que el mercado se mueve en tu contra
                import random
                slippage_extra = sp * 0.001 * (1 + random.random())
                precio_ejec    = round(sp - slippage_extra, 2)
                estado         = "filled"

        if estado != "filled":
            return {"estado": estado, "orden": orden}

        costos = cls.costos(precio_ejec, cant, side)
        return {
            "estado":            "filled",
            "symbol":            symbol,
            "side":              side,
            "cantidad":          cant,
            "precio_solicitado": precio_mercado,
            "precio_ejecucion":  costos["precio_ejecucion"],
            "monto_total":       costos["monto_total"],
            "comision":          costos["comision"],
            "slippage":          costos["slippage"],
            "costo_total":       costos["costo_total"],
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }


# ══════════════════════════════════════════════════════════════
# MARKET — API PÚBLICA
# ══════════════════════════════════════════════════════════════
class Market:

    Indicators     = Indicators
    MatchingEngine = MatchingEngine

    # ── 1. USD/CLP — DolarApi.com ──────────────────────────
    @staticmethod
    def get_usdclp() -> dict:
        cached = _cache.get("usdclp")
        if cached:
            return cached
        try:
            data = _fetch("https://cl.dolarapi.com/v1/cotizaciones/usd")
            value = {
                "compra":   data["compra"],
                "venta":    data["venta"],
                "promedio": round((data["compra"] + data["venta"]) / 2, 2),
                "fecha":    data.get("fechaActualizacion"),
                "fuente":   "DolarApi.com",
                "live":     True,
            }
            _cache.set("usdclp", value, TTL["usdclp"])
            return value
        except Exception as e:
            print(f"[Market] USD/CLP fallback: {e}")
            return FALLBACKS["usdclp"]

    # ── 2. INDICADORES CHILE — mindicador.cl ───────────────
    @staticmethod
    def _fetch_mindicador():
        cached = _cache.get("mindicador")
        if cached:
            return cached
        try:
            data = _fetch("https://mindicador.cl/api")
            result = {
                "uf":    {"valor": data["uf"]["valor"],          "fecha": data["uf"]["fecha"],          "unidad": "CLP",    "fuente": "mindicador.cl"},
                "ipc":   {"valor": data["ipc"]["valor"],         "fecha": data["ipc"]["fecha"],         "unidad": "%",      "fuente": "mindicador.cl"},
                "tpm":   {"valor": data["tpm"]["valor"],         "fecha": data["tpm"]["fecha"],         "unidad": "%",      "fuente": "mindicador.cl"},
                "cobre": {"valor": data["libra_cobre"]["valor"], "fecha": data["libra_cobre"]["fecha"], "unidad": "USD/lb", "fuente": "mindicador.cl"},
                "euro":  {"valor": data["euro"]["valor"],        "fecha": data["euro"]["fecha"],        "unidad": "CLP",    "fuente": "mindicador.cl"},
            }
            _cache.set("mindicador", result, TTL["mindicador"])
            return result
        except Exception as e:
            print(f"[Market] mindicador.cl fallback: {e}")
            return {k: FALLBACKS.get(k, {}) for k in ["uf","ipc","tpm","cobre","euro"]}

    @classmethod
    def get_uf(cls)    -> dict: return cls._fetch_mindicador().get("uf",    FALLBACKS["uf"])
    @classmethod
    def get_ipc(cls)   -> dict: return cls._fetch_mindicador().get("ipc",   FALLBACKS["ipc"])
    @classmethod
    def get_tpm(cls)   -> dict: return cls._fetch_mindicador().get("tpm",   FALLBACKS["tpm"])
    @classmethod
    def get_cobre(cls) -> dict: return cls._fetch_mindicador().get("cobre", FALLBACKS["cobre"])
    @classmethod
    def get_euro(cls)  -> dict: return cls._fetch_mindicador().get("euro",  FALLBACKS["euro"])

    # ── 3. IPSA + ACCIONES — yfinance ─────────────────────
    @staticmethod
    def get_ipsa() -> dict:
        cached = _cache.get("ipsa")
        if cached:
            return cached
        if not YFINANCE_OK:
            return FALLBACKS["ipsa"]
        try:
            ticker = yf.Ticker("^IPSA")
            info   = ticker.fast_info
            price  = round(float(info.last_price), 2)
            prev   = round(float(info.previous_close), 2)
            chg    = round(price - prev, 2)
            chgPct = round((chg / prev) * 100, 2)
            value  = {
                "symbol":    "^IPSA",
                "price":     price,
                "prevClose": prev,
                "change":    chg,
                "changePct": chgPct,
                "fuente":    "Yahoo Finance (15min delay)",
                "live":      False,
            }
            _cache.set("ipsa", value, TTL["ipsa"])
            return value
        except Exception as e:
            print(f"[Market] IPSA fallback: {e}")
            return FALLBACKS["ipsa"]

    @staticmethod
    def get_accion(nemo: str) -> dict:
        """
        Acción chilena por nemotécnico.
        Agrega .SN automáticamente si no tiene extensión.
        Ejemplos: 'COPEC' → 'COPEC.SN', 'SQM-B' → 'SQM-B.SN'
        """
        symbol = nemo if "." in nemo or nemo.startswith("^") else f"{nemo}.SN"
        cache_key = f"accion_{symbol}"
        cached = _cache.get(cache_key)
        if cached:
            return cached
        if not YFINANCE_OK:
            return {"symbol": symbol, "fuente": "fallback"}
        try:
            ticker = yf.Ticker(symbol)
            info   = ticker.fast_info
            price  = round(float(info.last_price), 2)
            prev   = round(float(info.previous_close), 2)
            chg    = round(price - prev, 2)
            chgPct = round((chg / prev) * 100, 2)
            value  = {
                "symbol":    symbol,
                "nemo":      nemo,
                "price":     price,
                "prevClose": prev,
                "change":    chg,
                "changePct": chgPct,
                "high":      round(float(info.day_high), 2),
                "low":       round(float(info.day_low), 2),
                "volume":    int(info.shares),
                "fuente":    "Yahoo Finance (15min delay)",
                "live":      False,
            }
            _cache.set(cache_key, value, TTL["accion"])
            return value
        except Exception as e:
            print(f"[Market] Acción {symbol} fallback: {e}")
            return {"symbol": symbol, "error": str(e)}

    # ── 4. HISTÓRICO — yfinance ────────────────────────────
    @staticmethod
    def get_historico(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """
        Datos históricos de precios.
        symbol:   "^IPSA", "COPEC.SN", "SQM-B.SN", etc.
        period:   "1mo", "3mo", "6mo", "1y", "2y", "5y"
        interval: "1d", "1wk", "1mo"

        Retorna DataFrame con columnas: Date, Open, High, Low, Close, Volume
        """
        sym = symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.SN"
        cache_key = f"hist_{sym}_{period}_{interval}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        if not YFINANCE_OK:
            return pd.DataFrame()
        try:
            df = yf.download(sym, period=period, interval=interval, progress=False)
            df = df.reset_index()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df.rename(columns={"Date":"date","Open":"open","High":"high",
                                    "Low":"low","Close":"close","Volume":"volume"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["close"])
            _cache.set(cache_key, df, TTL["historico"])
            return df
        except Exception as e:
            print(f"[Market] Histórico {sym} error: {e}")
            return pd.DataFrame()

    # ── 5. GET ALL ─────────────────────────────────────────
    @classmethod
    def get_all(cls) -> dict:
        """Obtener todos los indicadores en una sola llamada."""
        mindicador = cls._fetch_mindicador()
        return {
            "usdclp": cls.get_usdclp(),
            "uf":     mindicador.get("uf"),
            "ipc":    mindicador.get("ipc"),
            "tpm":    mindicador.get("tpm"),
            "cobre":  mindicador.get("cobre"),
            "euro":   mindicador.get("euro"),
            "ipsa":   cls.get_ipsa(),
        }

    @staticmethod
    def cache_status() -> dict:
        return _cache.status()


# ══════════════════════════════════════════════════════════════
# TEST RÁPIDO — ejecutar directamente
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import json

    print("\n" + "="*60)
    print("  InvertirCL — Market Data Module v1.0 (Python)")
    print("="*60)

    tests = [
        ("USD/CLP",   Market.get_usdclp),
        ("UF",        Market.get_uf),
        ("IPC",       Market.get_ipc),
        ("Cobre",     Market.get_cobre),
        ("IPSA",      Market.get_ipsa),
    ]

    for name, fn in tests:
        try:
            result = fn()
            print(f"\n✅ {name}:")
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        except Exception as e:
            print(f"\n❌ {name}: {e}")

    # Test acciones
    print("\n📈 Acciones chilenas:")
    for nemo in ["COPEC", "SQM-B", "BCI"]:
        try:
            r = Market.get_accion(nemo)
            print(f"  {nemo}: ${r.get('price', 'N/A')} ({r.get('changePct', 'N/A')}%)")
        except Exception as e:
            print(f"  {nemo}: ERROR - {e}")

    # Test indicadores
    print("\n🔬 Indicadores técnicos (IPSA 3 meses):")
    try:
        df = Market.get_historico("^IPSA", period="3mo")
        if not df.empty:
            closes  = df["close"].tolist()
            candles = df[["high","low","close","volume"]].rename(
                columns={"high":"high","low":"low","close":"close","volume":"volume"}
            ).to_dict("records")
            print(f"  RSI(14):  {Indicators.rsi(closes, 14)}")
            print(f"  EMA(9):   {Indicators.ema(closes, 9)}")
            print(f"  EMA(21):  {Indicators.ema(closes, 21)}")
            print(f"  MACD:     {Indicators.macd(closes)}")
            print(f"  BB(20,2): {Indicators.bb(closes)}")
            print(f"  ATR(14):  {Indicators.atr(candles)}")
    except Exception as e:
        print(f"  Error: {e}")

    # Test Matching Engine
    print("\n⚙️  Matching Engine:")
    costos = MatchingEngine.costos(8420, 100, "buy")
    print(f"  Compra 100 COPEC @ $8.420:")
    for k, v in costos.items():
        print(f"    {k}: {v}")

    print("\n" + "="*60)
    print("  Cache status:")
    import json
    print(json.dumps(Market.cache_status(), indent=2))
