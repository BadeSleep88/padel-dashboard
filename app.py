"""Padel Insights - run with:  streamlit run app.py"""

import asyncio
import json
import sys
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from allowlist import is_allowed
from dashboard import render_dashboard
from scraper import MatchpointScraper, ScrapeError

if sys.platform == "win32":  # Playwright needs the Proactor event loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

st.set_page_config(page_title="Padel Insights", page_icon="🎾", layout="wide")


def secret(key, default):
    try:
        return st.secrets[key]
    except Exception:  # no secrets file / key not set
        return default


CLUB_URL = secret("club_base_url", "https://stratfordpadelclub.matchpoint.com.es")
MAX_AT_ONCE = int(secret("max_concurrent_scrapes", 1))  # each scrape runs a browser; free hosting is small


@st.cache_resource
def guards():  # shared by every visitor
    return {"slots": threading.BoundedSemaphore(MAX_AT_ONCE), "tries": defaultdict(list)}


st.title("🎾 Padel Insights")

# ---------------------------------------------------------------- report view
if "report" in st.session_state:
    html, meta = st.session_state["report"]
    st.caption(f"{meta['me']} · {meta['sessions']} sessions · {meta['span']}")
    c1, c2, _ = st.columns([1, 1, 4])
    c1.download_button(
        "⬇ Download HTML report",
        html,
        mime="text/html",
        file_name=f"padel-report-{date.today():%Y-%m-%d}.html",
    )
    if c2.button("Clear my data"):
        del st.session_state["report"]
        st.rerun()
    components.html(html, height=8000, scrolling=False)
    st.stop()

# ------------------------------------------------------------------ login view
st.write("Sign in with your club account to build your personal padel dashboard.")
with st.form("login", clear_on_submit=True):
    email = st.text_input("Club email")
    password = st.text_input("Club password", type="password")
    agree = st.checkbox(
        "I agree to use my club login once to fetch my own session history. "
        "My password is not stored and my report disappears when I close this page."
    )
    go = st.form_submit_button("Build my dashboard")

if st.button("See a demo with sample data"):
    st.session_state["report"] = render_dashboard(
        json.loads(Path("demo_data.json").read_text(encoding="utf-8"))
    )
    st.rerun()

if go:
    email = email.strip().lower()
    if not is_allowed(email, list(secret("allowed_emails", []))):
        st.error("This email isn't on the access list yet. Ask the organiser to add it.")
        st.stop()
    if not password or not agree:
        st.error("Enter your club password and tick the box to continue.")
        st.stop()

    g, now = guards(), time.time()
    g["tries"][email] = [t for t in g["tries"][email] if now - t < 3600]
    if len(g["tries"][email]) >= 3:  # protects people's club accounts from repeated logins
        st.error("Too many attempts for this email. Please try again in an hour.")
        st.stop()
    g["tries"][email].append(now)
    if not g["slots"].acquire(blocking=False):
        st.warning("The server is busy with another dashboard. Please try again in a minute or two.")
        st.stop()

    bar, note = st.progress(0), st.empty()
    try:
        payload = MatchpointScraper(
            email,
            password,
            CLUB_URL,
            deadline=time.time() + 900,
            progress=lambda stage, pct: (bar.progress(pct / 100), note.write(stage)),
        ).run()
        st.session_state["report"] = render_dashboard(payload)
    except (ScrapeError, ValueError) as e:
        bar.empty()
        note.empty()
        st.error(str(e))
    except Exception:
        bar.empty()
        note.empty()
        st.error("Something went wrong while fetching your data. Please try again.")
    finally:
        g["slots"].release()
        password = None
    if "report" in st.session_state:
        st.rerun()
