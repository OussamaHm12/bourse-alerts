from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.models import Alert, News, Signal
from moroccan_stock_intelligence.repository import load_price_frame
from moroccan_stock_intelligence.services.analytics import compute_metrics
from moroccan_stock_intelligence.services.portfolio import load_watchlist
from moroccan_stock_intelligence.services.scoring import score_opportunity

st.set_page_config(page_title="Moroccan Stock Intelligence", layout="wide")


@st.cache_resource
def session_factory():
    engine = get_engine()
    init_db(engine)
    return get_session_factory(engine)


@st.cache_data(ttl=120)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    with session_factory()() as session:
        prices = load_price_frame(session)
        metrics = compute_metrics(prices)
        scores = [score_opportunity(metric) for metric in metrics]
        metric_rows = []
        score_map = {score.symbol: score for score in scores}
        for metric in metrics:
            score = score_map[metric.symbol]
            metric_rows.append(
                {
                    **metric.__dict__,
                    "buy_score": score.buy_score,
                    "watch_score": score.watch_score,
                    "avoid_score": score.avoid_score,
                    "reasons": "; ".join(score.reasons),
                    "risks": "; ".join(score.risks),
                }
            )
        return prices, pd.DataFrame(metric_rows)


prices_df, metrics_df = load_data()

st.title("Moroccan Stock Intelligence")
st.caption("Casablanca Stock Exchange prices may be delayed by public sources.")

pages = [
    "Market Overview",
    "Stock Explorer",
    "Top Opportunities",
    "Signals",
    "Historical Charts",
    "News Feed",
    "Portfolio Watchlist",
]
page = st.sidebar.radio("Navigation", pages)

if prices_df.empty:
    st.warning("No market data yet. Run `python -m moroccan_stock_intelligence.cli run-once`.")
    st.stop()

if page == "Market Overview":
    latest_count = metrics_df["symbol"].nunique() if not metrics_df.empty else 0
    avg_variation = metrics_df["daily_variation"].dropna().mean() if "daily_variation" in metrics_df else None
    total_volume = metrics_df["volume"].dropna().sum() if "volume" in metrics_df else None
    c1, c2, c3 = st.columns(3)
    c1.metric("Tracked Stocks", latest_count)
    c2.metric("Average Daily Variation", f"{avg_variation:.2f}%" if avg_variation is not None else "n/a")
    c3.metric("Total Volume", f"{total_volume:,.0f}" if total_volume is not None else "n/a")
    st.subheader("Latest Market Snapshot")
    st.dataframe(
        metrics_df[
            [
                "symbol",
                "company_name",
                "sector",
                "price",
                "daily_variation",
                "volume",
                "market_cap",
                "buy_score",
            ]
        ].sort_values("buy_score", ascending=False),
        use_container_width=True,
    )

elif page == "Stock Explorer":
    symbol = st.selectbox("Symbol", sorted(prices_df["symbol"].dropna().unique()))
    stock_prices = prices_df[prices_df["symbol"] == symbol].sort_values("observed_at")
    latest = metrics_df[metrics_df["symbol"] == symbol]
    if not latest.empty:
        row = latest.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price", f"{row['price']:.2f} MAD" if pd.notna(row["price"]) else "n/a")
        c2.metric("Daily Var.", f"{row['daily_variation']:.2f}%" if pd.notna(row["daily_variation"]) else "n/a")
        c3.metric("BUY Score", f"{row['buy_score']:.0f}/100")
        c4.metric("Volume Anomaly", f"{row['volume_anomaly']:.1f}x" if pd.notna(row["volume_anomaly"]) else "n/a")
    fig = px.line(stock_prices, x="observed_at", y="current_price", title=f"{symbol} Price History")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(stock_prices.sort_values("observed_at", ascending=False), use_container_width=True)

elif page == "Top Opportunities":
    st.dataframe(
        metrics_df.sort_values("buy_score", ascending=False)[
            ["symbol", "company_name", "sector", "price", "buy_score", "watch_score", "avoid_score", "reasons", "risks"]
        ].head(25),
        use_container_width=True,
    )

elif page == "Signals":
    with session_factory()() as session:
        rows = session.execute(select(Signal).order_by(Signal.generated_at.desc()).limit(200)).scalars().all()
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "generated_at": row.generated_at,
                    "stock_id": row.stock_id,
                    "type": row.signal_type,
                    "score": row.score,
                    "severity": row.severity,
                    "explanation": row.explanation,
                }
                for row in rows
            ]
        ),
        use_container_width=True,
    )
    with session_factory()() as session:
        alerts = session.execute(select(Alert).order_by(Alert.created_at.desc()).limit(100)).scalars().all()
    st.subheader("Alerts")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "created_at": row.created_at,
                    "stock_id": row.stock_id,
                    "type": row.alert_type,
                    "sent": bool(row.sent),
                    "message": row.message,
                }
                for row in alerts
            ]
        ),
        use_container_width=True,
    )

elif page == "Historical Charts":
    symbols = st.multiselect("Symbols", sorted(prices_df["symbol"].unique()), default=sorted(prices_df["symbol"].unique())[:5])
    chart_df = prices_df[prices_df["symbol"].isin(symbols)].sort_values("observed_at")
    st.plotly_chart(
        px.line(chart_df, x="observed_at", y="current_price", color="symbol", title="Historical Prices"),
        use_container_width=True,
    )
    st.plotly_chart(
        px.bar(chart_df, x="observed_at", y="volume", color="symbol", title="Volume"),
        use_container_width=True,
    )

elif page == "News Feed":
    with session_factory()() as session:
        rows = session.execute(select(News).order_by(News.collected_at.desc()).limit(200)).scalars().all()
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "published_at": row.published_at,
                    "source": row.source,
                    "title": row.title,
                    "event_type": row.event_type,
                    "sentiment": row.sentiment,
                    "impact_score": row.impact_score,
                    "url": row.url,
                }
                for row in rows
            ]
        ),
        use_container_width=True,
    )

elif page == "Portfolio Watchlist":
    watchlist = load_watchlist(settings.watchlist_file)
    st.write(f"Configured symbols: {', '.join(watchlist) if watchlist else 'none'}")
    watch_df = metrics_df[metrics_df["symbol"].isin(watchlist)]
    st.dataframe(
        watch_df[
            [
                "symbol",
                "company_name",
                "price",
                "daily_variation",
                "momentum_30d",
                "buy_score",
                "watch_score",
                "avoid_score",
                "reasons",
            ]
        ].sort_values("buy_score", ascending=False),
        use_container_width=True,
    )
