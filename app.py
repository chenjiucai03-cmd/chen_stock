import streamlit as st
import pandas as pd
import akshare as ak
import time
from datetime import datetime, timedelta

st.set_page_config(page_title="隔夜战法", layout="wide")

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


# ── Cached data fetcher with retry ──
@st.cache_data(ttl=300)
def fetch_spot():
    for i in range(3):
        try:
            df = ak.stock_zh_a_spot_em()
            return df
        except Exception:
            if i < 2:
                time.sleep(2)
            else:
                raise
    return None


def fetch_kline(code):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    return ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
    )


# ── Main action ──
if st.button("🚀 一键选股", type="primary", use_container_width=True):
    bar = st.progress(0, "正在获取实时行情…")

    # ── Step 1: real-time screening ──
    try:
        spot = fetch_spot()
    except Exception as e:
        bar.empty()
        st.error(f"❌ 行情获取失败: {e}")
        st.stop()

    if spot is None or spot.empty:
        bar.empty()
        st.error("❌ 获取数据为空")
        st.stop()

    bar.progress(18, "正在初筛股票…")

    df = spot.copy()
    df = df[~df["名称"].str.contains(r"ST|退", na=False)]

    for col in ["涨跌幅", "量比", "换手率", "总市值"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    mask = (
        df["涨跌幅"].between(2.8, 5.5)
        & (df["量比"] > 1.1)
        & df["换手率"].between(2.5, 12.0)
        & df["总市值"].between(30e8, 200e8)
    )
    screened = df[mask]

    if screened.empty:
        bar.empty()
        st.info("📭 今日无符合条件标的，建议空仓休息 ☕")
        st.stop()

    bar.progress(30, f"初筛通过 {len(screened)} 只，深度分析K线…")

    # ── Step 2: historical K-line confirmation ──
    results = []
    n = len(screened)

    for idx, (_, row) in enumerate(screened.iterrows()):
        code, name = row["代码"], row["名称"]
        pct = 30 + int(65 * (idx + 1) / n)
        bar.progress(pct, f"分析 {name}({code})  [{idx + 1}/{n}]")

        try:
            hist = fetch_kline(code)
            if hist is None or len(hist) < 20:
                time.sleep(0.2)
                continue

            hist = hist.sort_values("日期").reset_index(drop=True)
            close = hist["收盘"]

            # Bullish alignment: close > MA5 > MA10 > MA20
            c = close.iloc[-1]
            ma5 = close.rolling(5).mean().iloc[-1]
            ma10 = close.rolling(10).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]

            if not (c > ma5 > ma10 > ma20):
                time.sleep(0.2)
                continue

            # Strong gene: >= 9.5% in last 20 trading days
            tail = hist.tail(20)
            if "涨跌幅" in tail.columns:
                max_up = tail["涨跌幅"].max()
            else:
                max_up = tail["收盘"].pct_change().max() * 100

            if max_up < 9.5:
                time.sleep(0.2)
                continue

            results.append(
                {
                    "name": name,
                    "code": code,
                    "price": row["最新价"],
                    "chg": row["涨跌幅"],
                    "ratio": row["量比"],
                    "turnover": row["换手率"],
                }
            )
        except Exception:
            pass

        time.sleep(0.2)

    # ── Render results ──
    bar.progress(99, "渲染结果…")
    time.sleep(0.2)
    bar.empty()

    if not results:
        st.info("📭 今日无符合条件标的，建议空仓休息 ☕")
    else:
        st.success(f"🎉 共发现 {len(results)} 只符合条件的标的")

        # Card layout: 2 columns for mobile
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
                                f"量比 {float(r['ratio']):.2f} ｜ "
                                f"换手 {float(r['turnover']):.2f}%",
                                unsafe_allow_html=True,
                            )
