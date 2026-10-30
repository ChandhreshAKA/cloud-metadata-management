"""OPTIONAL Streamlit page. It only CALLS the API: every number shown comes from the backend.

    pip install streamlit            (see requirements-optional.txt; not needed by the API)
    streamlit run dashboard.py       (the API must be running; set API_URL if it is not on port 8000)

SIMULATION MODE: storage is local folders; prices, latencies and scores are illustrative.
"""

import os

import pandas as pd
import requests
import streamlit as st

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="Multi-Cloud Metadata Manager", layout="wide")
st.title("Multi-Cloud Metadata Manager")
st.warning("SIMULATION MODE: cloud storage is local folders. Prices and scores are illustrative.")


def api(method, path, **kwargs):
    token = st.session_state.get("token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.request(method, API + path, headers=headers, timeout=30, **kwargs)
        return r.status_code, r.json()
    except requests.RequestException as exc:
        return 0, {"detail": f"API not reachable at {API}: {exc}"}


with st.sidebar:
    st.header("Log in")
    email = st.text_input("E-mail")
    password = st.text_input("Password", type="password")
    if st.button("Log in"):
        status, body = api("POST", "/auth/login", json={"email": email, "password": password})
        st.session_state["token"] = body.get("access_token") if status == 200 else None
        if status != 200:
            st.error(body.get("detail", "login failed"))
    if st.session_state.get("token"):
        st.success("Logged in")

if not st.session_state.get("token"):
    st.info("Log in from the sidebar (an account is created by POST /auth/register).")
    st.stop()

files_tab, why_tab, dup_tab, cold_tab = st.tabs(["My files", "Why this provider", "Duplicates", "Cold files"])

with files_tab:
    status, body = api("GET", "/files")
    if status == 200:
        st.dataframe(pd.DataFrame(body["files"]), width="stretch")
    else:
        st.error(body.get("detail", "could not load files (this page is for USER accounts)"))

with why_tab:
    file_id = st.number_input("File id", min_value=1, step=1, value=int(body["files"][0]["file_id"]) if status == 200 and body["files"] else 1)  # noqa: E501
    status, placed = api("GET", f"/files/{int(file_id)}/placement")
    if status == 200:
        sel = placed["selected"]
        st.subheader(f"Selected: {sel['provider_code']} / {sel['region_code']} (score {sel['composite']})")
        st.write(placed["explanation"])
        st.dataframe(pd.DataFrame(placed["ranked"]).drop(columns=["raw"]), width="stretch")
        if placed["rejected"]:
            st.caption("Rejected before scoring")
            st.dataframe(pd.DataFrame(placed["rejected"]), width="stretch")
    else:
        st.error(placed.get("detail", "no placement found"))

with dup_tab:
    status, report = api("GET", "/reports/duplicates")
    if status == 200:
        st.caption(report["notes"][1] + " Prices are illustrative.")
        rows = [{"hash": g["content_sha256"][:12], "copies": g["copies"], "wasted_bytes": g["wasted_bytes"], "est_monthly_waste_usd": g["estimated_monthly_waste_usd"], "files": ", ".join(f["file_name"] for f in g["files"])} for g in report["groups"]]  # noqa: E501
        st.dataframe(pd.DataFrame(rows), width="stretch")

with cold_tab:
    idle = st.number_input("Idle for more than (days)", min_value=0, value=90)
    few = st.number_input("Fewer than (downloads in window)", min_value=1, value=3)
    status, report = api("GET", "/reports/cold-files", params={"idle_days": int(idle), "max_accesses": int(few)})
    if status == 200:
        st.caption("Candidates for archival review only. Nothing is moved or deleted.")
        st.dataframe(pd.DataFrame(report["files"]).drop(columns=["owner_id"], errors="ignore"), width="stretch")
