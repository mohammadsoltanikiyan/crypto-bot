import os
import json
import aiohttp
import asyncio
import numpy as np
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================
# CONFIG
# =========================

TOKEN = "8403952516:AAFJTe8WDl-y6_uaJ6P8yfeN_kwn-kMG8d0"
DATA_FILE = "users_data.json"

scheduler = AsyncIOScheduler()
session = None
user_data = {}
user_jobs = {}
user_states = {}  # برای مدیریت state کاربر

AVAILABLE_SYMBOLS = [
    # بیت‌کوین و اتریوم
    "BTCUSDT", "ETHUSDT",
    # لایه یک
    "SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT", "NEARUSDT",
    "ATOMUSDT", "ALGOUSDT", "FTMUSDT", "INJUSDT", "SUIUSDT", "APTUSDT",
    # DeFi
    "UNIUSDT", "AAVEUSDT", "MKRUSDT", "CRVUSDT", "LDOUSDT",
    # صرافی
    "BNBUSDT", "OKBUSDT",
    # میم‌کوین
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT",
    # پرداخت
    "XRPUSDT", "TRXUSDT", "XLMUSDT", "LTCUSDT",
    # زیرساخت
    "LINKUSDT", "FILUSDT", "ARUSDT", "RENDERUSDT",
    # اکوسیستم TON
    "TONUSDT", "NOTUSDT",
    # AI
    "FETUSDT", "AGIXUSDT", "WLDUSDT",
    # گیمینگ
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "IMXUSDT",
]

TIMEFRAMES = {
    "5m":  {"interval": "5m",  "limit": 100, "label": "۵ دقیقه"},
    "15m": {"interval": "15m", "limit": 100, "label": "۱۵ دقیقه"},
    "1h":  {"interval": "1h",  "limit": 100, "label": "۱ ساعت"},
    "4h":  {"interval": "4h",  "limit": 100, "label": "۴ ساعت"},
    "1d":  {"interval": "1d",  "limit": 100, "label": "روزانه"},
}

# =========================
# DATA
# =========================

def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except:
            user_data = {}
    else:
        user_data = {}

def save_data():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(user_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

def backup_data():
    with open(DATA_FILE + ".bak", "w", encoding="utf-8") as f:
        json.dump(user_data, f, ensure_ascii=False, indent=2)

def init_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "interval": 60,
            "active": True,
            "active_positions": {}
        }
        save_data()

# =========================
# BINANCE DATA
# =========================

async def get_klines(symbol, interval="1h", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return {
                "closes":  np.array([float(c[4]) for c in data]),
                "highs":   np.array([float(c[2]) for c in data]),
                "lows":    np.array([float(c[3]) for c in data]),
                "opens":   np.array([float(c[1]) for c in data]),
                "volumes": np.array([float(c[5]) for c in data]),
            }
    except:
        return None

async def get_ticker(symbol):
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                return None
            d = await resp.json()
            return {
                "price":  float(d["lastPrice"]),
                "change": float(d["priceChangePercent"]),
                "high":   float(d["highPrice"]),
                "low":    float(d["lowPrice"]),
                "volume": float(d["volume"]),
            }
    except:
        return None

async def validate_symbol(symbol):
    """چک می‌کنه آیا این ارز در بایننس وجود داره"""
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    result = await get_ticker(symbol)
    return symbol if result else None

# =========================
# INDICATORS
# =========================

def calc_rsi(closes, period=14):
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def calc_ema(closes, period):
    ema = [np.mean(closes[:period])]
    k = 2 / (period + 1)
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return np.array(ema)

def calc_macd(closes):
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    min_len = min(len(ema12), len(ema26))
    macd_line   = ema12[-min_len:] - ema26[-min_len:]
    signal_line = calc_ema(macd_line, 9)
    histogram   = macd_line[-len(signal_line):] - signal_line
    return {
        "macd":      round(float(macd_line[-1]), 4),
        "signal":    round(float(signal_line[-1]), 4),
        "histogram": round(float(histogram[-1]), 4),
    }

def calc_bollinger(closes, period=20):
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return {
        "upper": round(float(sma + 2 * std), 4),
        "mid":   round(float(sma), 4),
        "lower": round(float(sma - 2 * std), 4),
    }

def calc_atr(highs, lows, closes, period=14):
    tr_list = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        for i in range(1, len(closes))
    ]
    return round(float(np.mean(tr_list[-period:])), 4)

def calc_stochastic(highs, lows, closes, k_period=14, d_period=3):
    k_values = []
    for i in range(k_period - 1, len(closes)):
        low_min  = np.min(lows[i - k_period + 1: i + 1])
        high_max = np.max(highs[i - k_period + 1: i + 1])
        k = 100 * (closes[i] - low_min) / (high_max - low_min) if high_max != low_min else 50
        k_values.append(round(k, 2))
    return {"k": k_values[-1], "d": round(float(np.mean(k_values[-d_period:])), 2)}

def calc_vwap(highs, lows, closes, volumes):
    typical = (highs + lows + closes) / 3
    return round(float(np.sum(typical * volumes) / np.sum(volumes)), 4)

def calc_support_resistance(highs, lows, lookback=20):
    return {
        "support":    round(float(np.min(lows[-lookback:])), 4),
        "resistance": round(float(np.max(highs[-lookback:])), 4),
    }

def calc_adx(highs, lows, closes, period=14):
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(closes)):
        h_diff = highs[i]  - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        tr_list.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))

    def smooth(arr, p):
        s = [sum(arr[:p])]
        for v in arr[p:]:
            s.append(s[-1] - s[-1]/p + v)
        return s

    tr_s  = smooth(tr_list, period)
    pdm_s = smooth(plus_dm, period)
    mdm_s = smooth(minus_dm, period)
    pdi = [100 * pdm_s[i] / tr_s[i] if tr_s[i] != 0 else 0 for i in range(len(tr_s))]
    mdi = [100 * mdm_s[i] / tr_s[i] if tr_s[i] != 0 else 0 for i in range(len(tr_s))]
    dx  = [100 * abs(pdi[i]-mdi[i]) / (pdi[i]+mdi[i]) if (pdi[i]+mdi[i]) != 0 else 0 for i in range(len(pdi))]
    return {"adx": round(float(np.mean(dx[-period:])), 2), "+di": round(pdi[-1], 2), "-di": round(mdi[-1], 2)}

def detect_candlestick_pattern(opens, closes, highs, lows):
    patterns = []
    o, c, h, l = opens[-1], closes[-1], highs[-1], lows[-1]
    body  = abs(c - o)
    total = h - l
    upper = h - max(o, c)
    lower = min(o, c) - l

    if total > 0:
        if body / total < 0.1 and upper > body * 2 and lower > body * 2:
            patterns.append("Doji ⚖️")
        if lower > body * 2 and upper < body * 0.3:
            patterns.append("Hammer 🔨" if c > o else "Hanging Man 🪢")
        if upper > body * 2 and lower < body * 0.3:
            patterns.append("Shooting Star ⭐" if c < o else "Inverted Hammer 🔁")
        if body / total > 0.8:
            patterns.append("Marubozu صعودی 💚" if c > o else "Marubozu نزولی 🔴")

    if len(closes) >= 2:
        prev_o, prev_c = opens[-2], closes[-2]
        if c > o and prev_c < prev_o and c > prev_o and o < prev_c:
            patterns.append("Bullish Engulfing 🟢")
        if c < o and prev_c > prev_o and c < prev_o and o > prev_c:
            patterns.append("Bearish Engulfing 🔴")

    return patterns if patterns else ["الگوی خاصی یافت نشد"]

# =========================
# ANALYSIS ENGINE
# =========================

async def full_analysis(symbol):
    ticker = await get_ticker(symbol)
    if not ticker:
        return None

    price   = ticker["price"]
    signals = []
    reasons = []
    results = {}

    for tf_key, tf_info in TIMEFRAMES.items():
        kdata = await get_klines(symbol, tf_info["interval"], tf_info["limit"])
        if not kdata:
            continue

        closes  = kdata["closes"]
        highs   = kdata["highs"]
        lows    = kdata["lows"]
        opens   = kdata["opens"]
        volumes = kdata["volumes"]

        rsi   = calc_rsi(closes)
        macd  = calc_macd(closes)
        bb    = calc_bollinger(closes)
        atr   = calc_atr(highs, lows, closes)
        stoch = calc_stochastic(highs, lows, closes)
        vwap  = calc_vwap(highs, lows, closes, volumes)
        sr    = calc_support_resistance(highs, lows)
        adx   = calc_adx(highs, lows, closes)
        ema20 = float(calc_ema(closes, 20)[-1])
        ema50 = float(calc_ema(closes, 50)[-1]) if len(closes) >= 50 else None
        pats  = detect_candlestick_pattern(opens, closes, highs, lows)

        results[tf_key] = {
            "label": tf_info["label"], "rsi": rsi, "macd": macd,
            "bb": bb, "atr": atr, "stoch": stoch, "vwap": vwap,
            "sr": sr, "adx": adx, "ema20": round(ema20, 4),
            "ema50": round(ema50, 4) if ema50 else None,
            "patterns": pats,
        }

        if tf_key in ("1h", "4h"):
            w = 2 if tf_key == "4h" else 1

            if rsi < 35:
                signals.append(+1 * w); reasons.append(f"RSI اشباع فروش ({rsi}) | {tf_info['label']}")
            elif rsi > 65:
                signals.append(-1 * w); reasons.append(f"RSI اشباع خرید ({rsi}) | {tf_info['label']}")

            if macd["histogram"] > 0:
                signals.append(+1 * w); reasons.append(f"MACD صعودی | {tf_info['label']}")
            else:
                signals.append(-1 * w); reasons.append(f"MACD نزولی | {tf_info['label']}")

            if price < bb["lower"]:
                signals.append(+2 * w); reasons.append(f"زیر باند بولینگر | {tf_info['label']}")
            elif price > bb["upper"]:
                signals.append(-2 * w); reasons.append(f"بالای باند بولینگر | {tf_info['label']}")

            signals.append(+1 * w if price > vwap else -1 * w)

            if adx["adx"] > 25:
                if adx["+di"] > adx["-di"]:
                    signals.append(+2 * w); reasons.append(f"ADX قوی صعودی | {tf_info['label']}")
                else:
                    signals.append(-2 * w); reasons.append(f"ADX قوی نزولی | {tf_info['label']}")

            if stoch["k"] < 20:
                signals.append(+1 * w); reasons.append(f"Stochastic اشباع فروش | {tf_info['label']}")
            elif stoch["k"] > 80:
                signals.append(-1 * w); reasons.append(f"Stochastic اشباع خرید | {tf_info['label']}")

            if ema50:
                signals.append(+1 * w if ema20 > ema50 else -1 * w)

    score = sum(signals)
    h1    = results.get("1h", {})
    atr_v = h1.get("atr", price * 0.01)
    sr_v  = h1.get("sr", {"support": price * 0.97, "resistance": price * 1.03})

    if score >= 5:
        direction  = "LONG 🟢"
        sl         = round(max(price - atr_v * 1.5, sr_v["support"] * 0.998), 4)
        tp1        = round(price + atr_v * 1.5, 4)
        tp2        = round(price + atr_v * 3.0, 4)
        tp3        = round(sr_v["resistance"] * 0.998, 4)
        hold_hours = 4 if score >= 8 else 8
        confidence = "بالا 🔥" if score >= 8 else "متوسط ✅"
    elif score <= -5:
        direction  = "SHORT 🔴"
        sl         = round(min(price + atr_v * 1.5, sr_v["resistance"] * 1.002), 4)
        tp1        = round(price - atr_v * 1.5, 4)
        tp2        = round(price - atr_v * 3.0, 4)
        tp3        = round(sr_v["support"] * 1.002, 4)
        hold_hours = 4 if score <= -8 else 8
        confidence = "بالا 🔥" if score <= -8 else "متوسط ✅"
    else:
        direction  = "NEUTRAL ⚪"
        sl = tp1 = tp2 = tp3 = None
        hold_hours = 0
        confidence = "سیگنال ضعیف ⚠️"

    expiry = (datetime.now() + timedelta(hours=hold_hours)).isoformat() if hold_hours > 0 else None

    return {
        "symbol": symbol, "price": price, "ticker": ticker,
        "direction": direction, "score": score, "confidence": confidence,
        "entry": price, "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "hold_hours": hold_hours, "expiry": expiry,
        "reasons": reasons[:6], "timeframes": results,
    }

# =========================
# MESSAGE BUILDER
# =========================

def build_message(a):
    if not a:
        return "❌ خطا در دریافت داده"
    sym = a["symbol"]
    tv  = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}"
    now = datetime.now().strftime("%H:%M:%S")

    msg  = f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📊 {sym} | {now}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"💵 قیمت: {a['price']:,.4f}\n"
    msg += f"📈 تغییر ۲۴h: {a['ticker']['change']:+.2f}%\n\n"
    msg += f"🎯 سیگنال: {a['direction']}\n"
    msg += f"💪 اطمینان: {a['confidence']} (امتیاز: {a['score']})\n\n"

    if a["direction"] != "NEUTRAL ⚪":
        sl_pct  = abs(a["entry"] - a["stop_loss"]) / a["entry"] * 100
        tp1_pct = abs(a["tp1"] - a["entry"]) / a["entry"] * 100
        rr      = round(tp1_pct / sl_pct, 2) if sl_pct > 0 else 0

        msg += f"🔰 ورود:      {a['entry']:,.4f}\n"
        msg += f"🛑 Stop Loss: {a['stop_loss']:,.4f}  ({sl_pct:.2f}%)\n"
        msg += f"🎯 TP1:       {a['tp1']:,.4f}  ({tp1_pct:.2f}%)\n"
        msg += f"🎯 TP2:       {a['tp2']:,.4f}\n"
        msg += f"🎯 TP3:       {a['tp3']:,.4f}\n"
        msg += f"⚖️ R/R:       {rr}\n\n"

        if a["expiry"]:
            exp = datetime.fromisoformat(a["expiry"]).strftime("%H:%M  %Y-%m-%d")
            msg += f"⏰ مدت: {a['hold_hours']} ساعت  |  انقضا: {exp}\n\n"

    msg += "📋 دلایل:\n"
    for r in a["reasons"]:
        msg += f"  • {r}\n"

    h1 = a["timeframes"].get("1h", {})
    if h1:
        msg += f"\n📉 اندیکاتور (۱h):\n"
        msg += f"  RSI: {h1['rsi']}  |  Stoch: {h1['stoch']['k']}\n"
        msg += f"  MACD: {h1['macd']['macd']}  |  ADX: {h1['adx']['adx']}\n"
        msg += f"  BB: {h1['bb']['lower']} ↔ {h1['bb']['upper']}\n"
        msg += f"  VWAP: {h1['vwap']}\n"
        msg += f"  S: {h1['sr']['support']}  |  R: {h1['sr']['resistance']}\n"
        msg += f"  الگو: {', '.join(h1['patterns'])}\n"

    msg += f"\n🔗 {tv}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━"
    return msg

# =========================
# MENUS
# =========================

def main_menu(chat_id):
    """منوی اصلی با وضعیت فعال/متوقف"""
    is_active = user_data.get(chat_id, {}).get("active", True)
    symbols   = user_data.get(chat_id, {}).get("symbols", [])

    toggle_btn = (
        InlineKeyboardButton("⏹ توقف ارسال", callback_data="toggle_off")
        if is_active else
        InlineKeyboardButton("▶️ شروع ارسال", callback_data="toggle_on")
    )

    keyboard = [
        [InlineKeyboardButton("📊 تحلیل همین الان", callback_data="do_analysis")],
        [toggle_btn],
        [
            InlineKeyboardButton("➕ افزودن ارز",  callback_data="menu_add"),
            InlineKeyboardButton("➖ حذف ارز",     callback_data="menu_remove"),
        ],
        [InlineKeyboardButton("📋 ارزهای فعال",    callback_data="menu_list")],
        [InlineKeyboardButton("📁 پوزیشن‌های فعال", callback_data="menu_positions")],
        [InlineKeyboardButton("⏱ تنظیم بازه",      callback_data="menu_interval")],
    ]

    status = "🟢 فعال" if is_active else "🔴 متوقف"
    text   = (
        f"🤖 ربات تحلیل ارز\n"
        f"وضعیت: {status}\n"
        f"ارزهای انتخابی: {len(symbols)} عدد\n\n"
        f"یک گزینه انتخاب کن:"
    )
    return text, InlineKeyboardMarkup(keyboard)

def add_symbol_menu(chat_id):
    """منوی افزودن ارز - نمایش ارزهایی که هنوز اضافه نشدن"""
    current = set(user_data.get(chat_id, {}).get("symbols", []))
    keyboard = []
    row = []
    for sym in AVAILABLE_SYMBOLS:
        if sym not in current:
            row.append(InlineKeyboardButton(sym.replace("USDT",""), callback_data=f"add_{sym}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✏️ جستجوی ارز دلخواه", callback_data="add_custom")])
    keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="back_main")])
    return "➕ کدوم ارز رو اضافه کنی؟", InlineKeyboardMarkup(keyboard)

def remove_symbol_menu(chat_id):
    """منوی حذف ارز - نمایش ارزهای فعلی"""
    current = user_data.get(chat_id, {}).get("symbols", [])
    keyboard = []
    row = []
    for sym in current:
        row.append(InlineKeyboardButton(f"❌ {sym.replace('USDT','')}", callback_data=f"rem_{sym}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="back_main")])
    return "➖ کدوم ارز رو حذف کنی؟", InlineKeyboardMarkup(keyboard)

# =========================
# POSITION TRACKER
# =========================

async def check_expired_positions(bot):
    now = datetime.now()
    for chat_id, udata in user_data.items():
        positions = udata.get("active_positions", {})
        expired = [s for s, p in positions.items() if p.get("expiry") and now >= datetime.fromisoformat(p["expiry"])]

        for sym in expired:
            pos    = positions.pop(sym)
            ticker = await get_ticker(sym)
            if not ticker:
                continue
            current   = ticker["price"]
            entry     = pos["entry"]
            direction = pos["direction"]
            tp1       = pos.get("tp1")
            sl        = pos.get("stop_loss")
            pnl_pct   = (current - entry) / entry * 100 if "LONG" in direction else (entry - current) / entry * 100

            if tp1 and ((direction == "LONG 🟢" and current >= tp1) or (direction == "SHORT 🔴" and current <= tp1)):
                result = "✅ موفق — به TP1 رسید!"
            elif sl and ((direction == "LONG 🟢" and current <= sl) or (direction == "SHORT 🔴" and current >= sl)):
                result = "❌ استاپ لاس خورد"
            elif pnl_pct > 0:
                result = f"🟡 سود جزئی ({pnl_pct:+.2f}%)"
            else:
                result = f"🟠 ضرر جزئی ({pnl_pct:+.2f}%)"

            try:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        f"⏰ پوزیشن {sym} منقضی شد!\n\n"
                        f"جهت: {direction}\n"
                        f"ورود: {entry:,.4f}\n"
                        f"قیمت الان: {current:,.4f}\n"
                        f"P&L: {pnl_pct:+.2f}%\n\n"
                        f"نتیجه: {result}"
                    )
                )
            except:
                pass
        save_data()

# =========================
# CORE SENDER
# =========================

async def send_analysis(bot, chat_id, symbols):
    if not user_data.get(chat_id, {}).get("active", True):
        return
    for symbol in symbols:
        analysis = await full_analysis(symbol)
        if not analysis:
            await bot.send_message(chat_id=int(chat_id), text=f"❌ خطا در تحلیل {symbol}")
            continue
        if analysis["direction"] != "NEUTRAL ⚪" and analysis["expiry"]:
            if "active_positions" not in user_data[chat_id]:
                user_data[chat_id]["active_positions"] = {}
            user_data[chat_id]["active_positions"][symbol] = {
                "direction": analysis["direction"], "entry": analysis["entry"],
                "stop_loss": analysis["stop_loss"], "tp1": analysis["tp1"],
                "expiry": analysis["expiry"],
            }
            save_data()
        try:
            await bot.send_message(chat_id=int(chat_id), text=build_message(analysis), disable_web_page_preview=True)
        except Exception as e:
            print("send error:", e)

# =========================
# JOB SYSTEM
# =========================

def schedule_user_job(app, chat_id):
    chat_id = str(chat_id)
    if chat_id in user_jobs:
        try:
            user_jobs[chat_id].remove()
        except:
            pass
    interval = user_data[chat_id]["interval"]
    job = scheduler.add_job(
        send_analysis, "interval", minutes=interval,
        args=[app.bot, chat_id, user_data[chat_id]["symbols"]],
        id=f"user_{chat_id}", replace_existing=True
    )
    user_jobs[chat_id] = job

# =========================
# HANDLERS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    init_user(chat_id)
    text, markup = main_menu(chat_id)
    await update.message.reply_text(text, reply_markup=markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    data    = query.data
    init_user(chat_id)

    # --- توقف / شروع ---
    if data == "toggle_off":
        user_data[chat_id]["active"] = False
        save_data()
        text, markup = main_menu(chat_id)
        await query.edit_message_text("⏹ ارسال خودکار متوقف شد.\n\n" + text, reply_markup=markup)
        return

    if data == "toggle_on":
        user_data[chat_id]["active"] = True
        save_data()
        schedule_user_job(context.application, chat_id)
        text, markup = main_menu(chat_id)
        await query.edit_message_text("▶️ ارسال خودکار فعال شد.\n\n" + text, reply_markup=markup)
        return

    # --- تحلیل ---
    if data == "do_analysis":
        await query.edit_message_text("⏳ در حال تحلیل...")
        await send_analysis(context.bot, chat_id, user_data[chat_id]["symbols"])
        text, markup = main_menu(chat_id)
        await context.bot.send_message(chat_id=int(chat_id), text=text, reply_markup=markup)
        return

    # --- منوی افزودن ---
    if data == "menu_add":
        text, markup = add_symbol_menu(chat_id)
        await query.edit_message_text(text, reply_markup=markup)
        return

    if data.startswith("add_"):
        sym = data[4:]
        if sym not in user_data[chat_id]["symbols"]:
            user_data[chat_id]["symbols"].append(sym)
            save_data()
            schedule_user_job(context.application, chat_id)
        text, markup = add_symbol_menu(chat_id)
        await query.edit_message_text(f"✅ {sym} اضافه شد!\n\n" + text, reply_markup=markup)
        return

    if data == "add_custom":
        user_states[chat_id] = "waiting_custom_symbol"
        await query.edit_message_text(
            "✏️ نام ارز رو بنویس (مثلاً: LINK یا LINKUSDT)\n\n"
            "برای لغو /cancel بزن"
        )
        return

    # --- منوی حذف ---
    if data == "menu_remove":
        text, markup = remove_symbol_menu(chat_id)
        await query.edit_message_text(text, reply_markup=markup)
        return

    if data.startswith("rem_"):
        sym     = data[4:]
        symbols = user_data[chat_id]["symbols"]
        if len(symbols) == 1:
            await query.answer("حداقل یک ارز باید فعال باشد!", show_alert=True)
            return
        if sym in symbols:
            symbols.remove(sym)
            save_data()
            schedule_user_job(context.application, chat_id)
        text, markup = remove_symbol_menu(chat_id)
        await query.edit_message_text(f"🗑 {sym} حذف شد.\n\n" + text, reply_markup=markup)
        return

    # --- لیست ارزها ---
    if data == "menu_list":
        syms = user_data[chat_id]["symbols"]
        text = "📋 ارزهای فعال:\n\n" + "\n".join(f"  • {s}" for s in syms)
        keyboard = [[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # --- پوزیشن‌ها ---
    if data == "menu_positions":
        positions = user_data[chat_id].get("active_positions", {})
        if not positions:
            text = "هیچ پوزیشن فعالی نداری"
        else:
            text = "📋 پوزیشن‌های فعال:\n\n"
            for sym, pos in positions.items():
                exp  = datetime.fromisoformat(pos["expiry"]).strftime("%H:%M")
                text += f"• {sym} | {pos['direction']}\n"
                text += f"  ورود: {pos['entry']:,.4f} | SL: {pos['stop_loss']:,.4f}\n"
                text += f"  TP1: {pos['tp1']:,.4f} | انقضا: {exp}\n\n"
        keyboard = [[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # --- بازه زمانی ---
    if data == "menu_interval":
        keyboard = [
            [
                InlineKeyboardButton("۳۰ دقیقه",  callback_data="setint_30"),
                InlineKeyboardButton("۱ ساعت",    callback_data="setint_60"),
                InlineKeyboardButton("۲ ساعت",    callback_data="setint_120"),
            ],
            [
                InlineKeyboardButton("۴ ساعت",    callback_data="setint_240"),
                InlineKeyboardButton("۸ ساعت",    callback_data="setint_480"),
                InlineKeyboardButton("۱۲ ساعت",   callback_data="setint_720"),
            ],
            [InlineKeyboardButton("🔙 برگشت",     callback_data="back_main")],
        ]
        await query.edit_message_text("⏱ بازه ارسال خودکار رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("setint_"):
        minutes = int(data[7:])
        user_data[chat_id]["interval"] = minutes
        save_data()
        schedule_user_job(context.application, chat_id)
        text, markup = main_menu(chat_id)
        await query.edit_message_text(f"✅ بازه روی {minutes} دقیقه تنظیم شد.\n\n" + text, reply_markup=markup)
        return

    # --- برگشت ---
    if data == "back_main":
        text, markup = main_menu(chat_id)
        await query.edit_message_text(text, reply_markup=markup)
        return

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text    = update.message.text or ""
    state   = user_states.get(chat_id)

    # جستجوی ارز دلخواه
    if state == "waiting_custom_symbol":
        user_states.pop(chat_id, None)
        await update.message.reply_text("⏳ در حال بررسی ارز...")
        valid_sym = await validate_symbol(text.strip())
        if valid_sym:
            if valid_sym not in user_data[chat_id]["symbols"]:
                user_data[chat_id]["symbols"].append(valid_sym)
                save_data()
                schedule_user_job(context.application, chat_id)
            menu_text, markup = main_menu(chat_id)
            await update.message.reply_text(f"✅ {valid_sym} با موفقیت اضافه شد!\n\n" + menu_text, reply_markup=markup)
        else:
            await update.message.reply_text(f"❌ ارز «{text}» در بایننس پیدا نشد. دوباره امتحان کن.")
        return

    # منشن ربات
    me = await context.bot.get_me()
    if me.username and f"@{me.username}" in text:
        init_user(chat_id)
        await update.message.reply_text("⏳ در حال تحلیل...")
        await send_analysis(context.bot, chat_id, user_data[chat_id]["symbols"])

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_states.pop(chat_id, None)
    text, markup = main_menu(chat_id)
    await update.message.reply_text("لغو شد.\n\n" + text, reply_markup=markup)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    backup_data()
    await update.message.reply_text("✅ بکاپ ساخته شد")

# =========================
# STARTUP
# =========================

async def post_init(app: Application):
    global session
    session = aiohttp.ClientSession()
    scheduler.start()
    load_data()
    for chat_id in user_data:
        if user_data[chat_id].get("active", True):
            schedule_user_job(app, chat_id)
    scheduler.add_job(check_expired_positions, "interval", minutes=5,  args=[app.bot])
    scheduler.add_job(backup_data,             "interval", minutes=10)
    print("Bot started")

# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
