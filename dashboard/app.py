import os
import time
import httpx
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
REFRESH_INTERVAL = max(5, int(os.getenv("REFRESH_INTERVAL_SECONDS", "10")))

st.set_page_config(page_title="Store Intelligence Dashboard", layout="wide")
st.title("Store Intelligence Dashboard")


def fetch(path: str):
    try:
        resp = httpx.get(f"{API_BASE_URL}{path}", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"API error for {path}: {e}")
        return None


# --- Health Panel ---
st.header("System Health")
health = fetch("/health")
if health:
    status_color = "🟢" if health.get("status") == "ok" else "🔴"
    st.write(f"{status_color} Status: **{health.get('status', 'unknown')}** | DB: **{health.get('db', 'unknown')}**")

    stores = health.get("stores", [])
    if stores:
        for store in stores:
            feed_status = store.get("feed_status", "unknown")
            if feed_status == "STALE_FEED":
                st.warning(f"⚠️ Store **{store['store_id']}**: STALE_FEED — last event: {store.get('last_event_timestamp', 'N/A')}")
            else:
                st.success(f"✅ Store **{store['store_id']}**: Feed OK")
    else:
        st.info("No stores reporting yet.")

# Get list of stores from health endpoint
store_ids = [s["store_id"] for s in (health.get("stores", []) if health else [])]

if not store_ids:
    st.info("No stores found. Ingest some events to see analytics.")
else:
    selected_store = st.selectbox("Select Store", store_ids)

    col1, col2 = st.columns(2)

    # --- Metrics Panel ---
    with col1:
        st.header("Store Metrics")
        metrics = fetch(f"/stores/{selected_store}/metrics")
        if metrics:
            m1, m2, m3 = st.columns(3)
            m1.metric("Unique Visitors", metrics.get("unique_visitors", 0))
            m2.metric("Conversion Rate", f"{metrics.get('conversion_rate', 0.0):.1%}")
            m3.metric("Avg Dwell (s)", f"{metrics.get('avg_dwell_seconds', 0.0):.1f}")

            m4, m5 = st.columns(2)
            m4.metric("Queue Depth", metrics.get("queue_depth", 0))
            m5.metric("Abandonment Rate", f"{metrics.get('abandonment_rate', 0.0):.1%}")

    # --- Funnel Panel ---
    with col2:
        st.header("Conversion Funnel")
        funnel = fetch(f"/stores/{selected_store}/funnel")
        if funnel and funnel.get("stages"):
            for stage in funnel["stages"]:
                drop = stage.get("drop_off_pct")
                drop_str = f" (↓ {drop:.1f}%)" if drop is not None else ""
                st.write(f"**{stage['stage']}**: {stage['count']}{drop_str}")

    # --- Heatmap Panel ---
    st.header("Zone Heatmap")
    heatmap = fetch(f"/stores/{selected_store}/heatmap")
    if heatmap and heatmap.get("zones"):
        zones = heatmap["zones"]
        cols = st.columns(min(len(zones), 4))
        for i, zone in enumerate(zones):
            intensity = zone.get("intensity", 0)
            # Color based on intensity: green (low) → yellow → red (high)
            color = f"hsl({int(120 - intensity * 1.2)}, 70%, 50%)"
            with cols[i % 4]:
                st.markdown(
                    f'<div style="background:{color};padding:10px;border-radius:5px;text-align:center;">'
                    f'<b>{zone["zone_id"]}</b><br>'
                    f'Intensity: {intensity:.0f}<br>'
                    f'Visits: {zone["visit_count"]}<br>'
                    f'Dwell: {zone["avg_dwell_seconds"]:.1f}s'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("No heatmap data available.")

    # --- Anomalies Panel ---
    st.header("Anomalies")
    anomalies_data = fetch(f"/stores/{selected_store}/anomalies")
    if anomalies_data:
        anomalies = anomalies_data.get("anomalies", [])
        if anomalies:
            for anomaly in anomalies:
                severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(anomaly.get("severity", ""), "⚪")
                st.warning(f"{severity_icon} **{anomaly['type']}** ({anomaly['severity']}) — {anomaly['description']}")
        else:
            st.success("No anomalies detected.")

# Auto-refresh
st.caption(f"Auto-refreshing every {REFRESH_INTERVAL} seconds...")
time.sleep(REFRESH_INTERVAL)
st.rerun()
