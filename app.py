"""
隔夜战法选股工具（基于 Tushare Pro）

============================================================
 Streamlit Cloud Secrets 配置方法：
   后台 → Settings → Secrets → 添加以下内容：

     tushare_token = "你的Tushare Pro Token"

   Token 可在 https://tushare.pro/register 注册获取（需实名认证）
============================================================
"""

import streamlit as st
import pandas as pd
import tushare as ts
import time
import pytz
from datetime import datetime, timedelta

st.set_page_config(page_title="隔夜战法", layout="wide")

BEIJING_TZ = pytz.timezone("Asia/Shanghai")

# ── Hide Streamlit chrome ──
st.markdown(
    """<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding: 0.5rem 0.8rem;}
.stApp > header {display: none;}
div[data-testid="stToolbar"] {display: none;}
</style>""",
    unsafe_allow_html=True,
)

st.title("📈 隔夜战法选股")


# ═══════════════════════════════════════════════════════════
#  时间模式 ── 实时模式（14:30-14:55）/ 复盘模式
# ═══════════════════════════════════════════════════════════

START_HOUR, START_MIN = 14, 30
END_HOUR, END_MIN = 14, 55

now_bj = datetime.now(BEIJING_TZ)
current_code = now_bj.hour * 100 + now_bj.minute
start_code = START_HOUR * 100 + START_MIN
end_code = END_HOUR * 100 + END_MIN

is_in_window = start_code <= current_code < end_code

if is_in_window:
    mode_label = "🟢 实时模式"
    mode_desc = "基于今日盘中数据选股"
else:
    mode_label = "🔵 复盘模式"
    mode_desc = "基于最近交易日收盘数据选股"

st.info(
    f"{mode_label}：{mode_desc}　"
    f"🕐 {now_bj.strftime('%H:%M:%S')}"
)


# ═══════════════════════════════════════════════════════════
#  Tushare 初始化
# ═══════════════════════════════════════════════════════════

try:
    token = st.secrets["tushare_token"]
    pro = ts.pro_api(token, timeout=30)
except (KeyError, AttributeError):
    st.warning("⚠️ 请先配置 Tushare Token")
    st.markdown(
        "1. 前往 [Tushare Pro 注册](https://tushare.pro/register) 获取 Token（需实名认证）\n\n"
        "2. 在 Streamlit Cloud 后台 → Settings → Secrets 添加：\n\n"
        "   ```\n   tushare_token = \"你的Token\"\n   ```"
    )
    st.stop()
except Exception as e:
    st.error(f"❌ Tushare 初始化失败: {e}")
    st.stop()


# ═══════════════════════════════════════════════════════════
#  缓存数据获取函数（仅使用 daily / stock_basic / pro_bar）
# ═══════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def get_stock_names():
    """获取全市场股票名称列表，用于剔除 ST / *ST / 退市股"""
    for _ in range(3):
        try:
            df = pro.stock_basic(
                exchange="", list_status="L", fields="ts_code,symbol,name"
            )
            if df is not None and not df.empty:
                return df
        except Exception:
            time.sleep(1)
    return pd.DataFrame()


@st.cache_data(ttl=300)
def get_latest_trade_date():
    """获取最近一个交易日（UTC+8）"""
    now_bj = datetime.now(BEIJING_TZ)
    today = now_bj.strftime("%Y%m%d")
    start = (now_bj - timedelta(days=15)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today)
        if cal is None or cal.empty:
            return None
        trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
        return trade_dates[-1] if trade_dates else None
    except Exception:
        return None


@st.cache_data(ttl=300)
def get_prev_trade_date():
    """获取上一个交易日（相对今天）"""
    now_bj = datetime.now(BEIJING_TZ)
    today = now_bj.strftime("%Y%m%d")
    start = (now_bj - timedelta(days=15)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today)
        if cal is None or cal.empty:
            return None
        trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
        if len(trade_dates) >= 2:
            return trade_dates[-2]
        return trade_dates[-1] if trade_dates else None
    except Exception:
        return None


@st.cache_data(ttl=300)
def get_daily_all(trade_date):
    """获取指定交易日全市场行情（仅 daily 接口，无 daily_basic）"""
    for _ in range(3):
        try:
            df = pro.daily(
                trade_date=trade_date,
                fields="ts_code,close,pct_chg,vol",
            )
            if df is not None and not df.empty:
                return df
        except Exception:
            time.sleep(1)
    return pd.DataFrame()


def get_hist_bars(ts_code, start, end):
    """获取单只股票前复权日线数据"""
    for _ in range(2):
        try:
            df = ts.pro_bar(
                ts_code=ts_code,
                adj="qfq",
                start_date=start,
                end_date=end,
                freq="D",
                retry_count=3,
            )
            if df is not None and not df.empty:
                return df
        except Exception:
            time.sleep(0.5)
    return None


# ═══════════════════════════════════════════════════════════
#  选股结果缓存（按模式分立TTL）
# ═══════════════════════════════════════════════════════════

def _screening_pipeline(trade_date):
    """核心选股流水线（仅依赖 daily + stock_basic + pro_bar）"""
    now_bj = datetime.now(BEIJING_TZ)
    today_str = now_bj.strftime("%Y%m%d")
    start_hist = (now_bj - timedelta(days=120)).strftime("%Y%m%d")

    # ── 获取股票名称列表 ──
    stock_df = get_stock_names()
    if stock_df.empty:
        return []

    # ── 获取指定交易日全市场行情（daily 接口） ──
    daily_all = get_daily_all(trade_date)
    if daily_all.empty:
        return []

    # 合并名称，剔除 ST / *ST / 退
    df = daily_all.merge(
        stock_df[["ts_code", "name"]], on="ts_code", how="left"
    )
    df = df[~df["name"].str.contains(r"ST|退", na=False)]

    # 类型转换
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")

    # Step 1 筛选：涨跌幅 2.8% ~ 5.5%
    df_step1 = df[df["pct_chg"].between(2.8, 5.5)]

    if df_step1.empty:
        return []

    # ── Step 2：逐只股票深度分析 ──
    results = []
    for _, row in df_step1.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        code_short = ts_code[:6]

        try:
            hist = get_hist_bars(ts_code, start_hist, today_str)
            if hist is None or len(hist) < 25:
                time.sleep(0.3)
                continue

            hist = hist.sort_values("trade_date").reset_index(drop=True)
            close = hist["close"]
            vol = hist["vol"] if "vol" in hist.columns else None

            if len(close) < 20:
                time.sleep(0.3)
                continue

            # ── 量比：当日成交量 / 过去5日平均成交量 > 1.1 ──
            if vol is not None and len(vol) >= 6:
                avg_5d = vol.iloc[-6:-1].mean()
                vol_ratio = vol.iloc[-1] / avg_5d if avg_5d > 0 else 0
            else:
                vol_ratio = 0

            if vol_ratio <= 1.1:
                time.sleep(0.3)
                continue

            # ── 均线多头排列（无未来函数） ──
            c = close.iloc[-1]
            ma5 = close.rolling(5).mean().iloc[-1]
            ma10 = close.rolling(10).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]

            if not (c > ma5 > ma10 > ma20):
                time.sleep(0.3)
                continue

            # ── 强势基因：最近20日曾涨幅 >= 9.5%（无未来函数） ──
            recent = hist.tail(20)
            if "pct_chg" in recent.columns:
                max_up = recent["pct_chg"].max()
            else:
                max_up = recent["close"].pct_change().max() * 100

            if max_up < 9.5:
                time.sleep(0.3)
                continue

            results.append(
                {
                    "name": name,
                    "code": code_short,
                    "price": row["close"],
                    "chg": row["pct_chg"],
                    "ratio": round(float(vol_ratio), 2),
                }
            )
        except Exception:
            pass

        time.sleep(0.3)

    return results


@st.cache_data(ttl=60)
def screening_realtime(trade_date):
    """实时模式缓存（60秒）"""
    now_bj = datetime.now(BEIJING_TZ)
    results = _screening_pipeline(trade_date)
    return results, now_bj


@st.cache_data(ttl=86400)
def screening_review(trade_date):
    """复盘模式缓存（24小时）"""
    now_bj = datetime.now(BEIJING_TZ)
    results = _screening_pipeline(trade_date)
    return results, now_bj


# ═══════════════════════════════════════════════════════════
#  主流程（按钮始终可用）
# ═══════════════════════════════════════════════════════════

if st.button("🚀 一键选股", type="primary", use_container_width=True):
    bar = st.progress(0, "正在获取交易日信息…")

    # ── 确定交易日 ──
    trade_date = get_latest_trade_date()
    if trade_date is None:
        bar.empty()
        st.error("❌ 无法获取交易日信息，请检查网络或 Tushare 积分")
        st.stop()

    today_str = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
    now_hour = datetime.now(BEIJING_TZ).hour

    if is_in_window:
        # 实时模式：使用当天盘中数据
        pass
    else:
        # 复盘模式：使用最近一个完整交易日
        if trade_date == today_str and now_hour < 15:
            prev = get_prev_trade_date()
            if prev:
                trade_date = prev

    # ── 调用缓存选股 ──
    bar.progress(20, "正在下载行情数据…")

    if is_in_window:
        results, cache_time = screening_realtime(trade_date)
    else:
        results, cache_time = screening_review(trade_date)

    bar.progress(95, "渲染结果…")
    time.sleep(0.2)
    bar.empty()

    # ── 显示缓存信息 ──
    st.info(
        f"📦 使用缓存结果，上次更新时间：{cache_time.strftime('%H:%M:%S')}"
    )

    # ── 显示结果 ──
    if not results:
        st.info("📭 今日无符合条件标的，建议空仓休息 ☕")
    else:
        st.success(f"🎉 共发现 {len(results)} 只符合条件的标的")

        for i in range(0, len(results), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j < len(results):
                    r = results[i + j]
                    with cols[j]:
                        with st.container(border=True):
                            st.markdown(
                                f"**{r['name']}** `{r['code']}`\n\n"
                                f"**{float(r['price']):.2f}**  "
                                f"<span style='color:#e74c3c'>↑ {float(r['chg']):.2f}%</span>\n\n"
                                f"量比 {float(r['ratio']):.2f}",
                                unsafe_allow_html=True,
                            )
