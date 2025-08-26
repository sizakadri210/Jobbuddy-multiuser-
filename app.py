# streamlit_app.py
import os
import json
import datetime
import re
import pandas as pd
import altair as alt
import streamlit as st
import matplotlib.pyplot as plt

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

# -----------------------
# App Config
# -----------------------
st.set_page_config(page_title="Job Buddy 1.0", page_icon="💼", layout="wide")

# -----------------------
# Constants / Settings
# -----------------------
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# We read OAuth settings from Streamlit Secrets so nothing sensitive is in code.
# Add these in Streamlit Cloud → App → Settings → Secrets:
# GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OAUTH_REDIRECT_URI
def require_secrets(keys):
    missing = [k for k in keys if k not in st.secrets]
    if missing:
        st.error(
            "Missing secrets: " + ", ".join(missing) +
            "\n\nAdd them in Streamlit Cloud → App → Settings → Secrets."
        )
        st.stop()

require_secrets(["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_REDIRECT_URI"])

CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
REDIRECT_URI = st.secrets["OAUTH_REDIRECT_URI"]  # e.g. https://your-app-name.streamlit.app

# -----------------------
# OAuth Helpers
# -----------------------
def _client_config():
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }

def get_flow(state=None):
    return Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )

def begin_google_login():
    flow = get_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # ensures refresh_token
    )
    st.session_state["oauth_state"] = state
    # Send user to Google
    st.link_button("Continue to Google", auth_url)

def handle_oauth_callback():
    """If ?code= is present, exchange for tokens."""
    params = st.query_params
    code = params.get("code")
    state = params.get("state")
    if not code:
        return False

    expected_state = st.session_state.get("oauth_state")
    if expected_state and state != expected_state:
        st.error("OAuth state mismatch. Please retry login.")
        return False

    try:
        flow = get_flow(state=state)
        # Option A: pass the one-time code directly
        flow.fetch_token(code=code)
        creds = flow.credentials

        st.session_state["token"] = creds.token
        st.session_state["refresh_token"] = creds.refresh_token
        st.session_state["token_uri"] = creds.token_uri
        st.session_state["client_id"] = creds.client_id
        st.session_state["client_secret"] = creds.client_secret
        st.session_state["scopes"] = creds.scopes

        # Clear code/state from URL so refreshes don't re-trigger callback
        try:
            st.query_params.clear()
        except Exception:
            pass

        return True
    except Exception as e:
        st.error(f"OAuth error: {e}")
        return False

def is_authenticated():
    for k in ["token", "refresh_token", "client_id", "client_secret", "token_uri", "scopes"]:
        if k not in st.session_state or not st.session_state[k]:
            return False
    return True

def logout():
    for k in ["token", "refresh_token", "client_id", "client_secret", "token_uri", "scopes", "oauth_state"]:
        st.session_state.pop(k, None)
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.rerun()

def get_gmail_service():
    """Builds a Gmail API client using tokens stored in session_state."""
    creds = Credentials(
        token=st.session_state.get("token"),
        refresh_token=st.session_state.get("refresh_token"),
        token_uri=st.session_state.get("token_uri"),
        client_id=st.session_state.get("client_id"),
        client_secret=st.session_state.get("client_secret"),
        scopes=st.session_state.get("scopes", SCOPES),
    )

    if creds.expired:
        if creds.refresh_token:
            creds.refresh(Request())
            # persist updated access token
            st.session_state["token"] = creds.token
        else:
            raise RuntimeError("Access token expired and no refresh token available.")

    return build("gmail", "v1", credentials=creds)

# -----------------------
# Data / Gmail
# -----------------------
def fetch_job_emails():
    """Query Gmail for common 'application received' threads and return metadata."""
    try:
        service = get_gmail_service()
        q = (
            'subject:"Thank you for Applying" '
            'OR "Thank you for your expression" '
            'OR "Thank you for applying" '
            'OR "Your application was sent" '
            'OR "Thank you for your application" '
            'OR "We have received your application"'
        )
        result = service.users().messages().list(userId="me", q=q, maxResults=50).execute()
        messages = result.get("messages", [])

        items = []
        for m in messages:
            msg = service.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            items.append(
                {
                    "Subject": headers.get("Subject", ""),
                    "From": headers.get("From", ""),
                    "Date": headers.get("Date", ""),
                }
            )

        return items
    except HttpError as e:
        if e.resp.status in (401, 403):
            st.warning("Authorization expired. Please login again.")
            logout()
        else:
            st.error(f"Gmail API error: {e}")
        return []
    except Exception as e:
        st.error(f"Error fetching emails: {e}")
        return []

# -----------------------
# Visualization Helpers
# -----------------------
def plot_interactive_calendar(df):
    df = df.copy()
    df["Date_Only"] = pd.to_datetime(df["Date_Only"])
    hm = df.groupby("Date_Only").size().reset_index(name="Applications")
    hm["Day_Num"] = hm["Date_Only"].dt.weekday
    hm["Month_Num"] = hm["Date_Only"].dt.month
    hm["Month"] = hm["Date_Only"].dt.strftime("%b")
    hm["Day_Label"] = hm["Date_Only"].dt.day_name()

    base = alt.Chart(hm).encode(
        x=alt.X(
            "Day_Num:O",
            title="Day of Week",
            axis=alt.Axis(
                labelExpr="{'0':'Mon','1':'Tue','2':'Wed','3':'Thu','4':'Fri','5':'Sat','6':'Sun'}[datum.label]"
            ),
        ),
        y=alt.Y("Month:O", title="Month", sort=alt.EncodingSortField(field="Month_Num", order="ascending")),
    )

    heatmap = base.mark_rect().encode(
        color=alt.Color("Applications:Q", scale=alt.Scale(scheme="greens")),
        tooltip=[alt.Tooltip("Date_Only:T", title="Date"), alt.Tooltip("Applications:Q")],
    )

    borders = base.mark_rect(fillOpacity=0, stroke="black", strokeWidth=0.5)

    chart = (heatmap + borders).properties(width=700, height=400)
    st.altair_chart(chart, use_container_width=True)

# -----------------------
# Pages
# -----------------------
def render_home():
    st.title("💼 Welcome to Job Buddy 1.0 — Your Job Search Companion")
    st.write("This app helps you track and analyze your job applications automatically from Gmail.")
    quotes = [
        "Believe you can and you're halfway there. – Theodore Roosevelt",
        "Your limitation—it’s only your imagination.",
        "Push yourself, because no one else is going to do it for you.",
        "Great things never come from comfort zones.",
        "Dream it. Wish it. Do it.",
        "Success doesn’t just find you. You have to go out and get it.",
        "The harder you work for something, the greater you’ll feel when you achieve it.",
        "Don’t watch the clock; do what it does. Keep going. – Sam Levenson",
        "Stay positive, work hard, make it happen.",
        "The future depends on what you do today. – Mahatma Gandhi",
    ]
    today = datetime.date.today()
    st.info(f"✨ Daily Motivational Quote: “{quotes[today.toordinal() % len(quotes)]}”")

def render_dashboard():
    st.title("📊 Job Application: Reflection")

    data = fetch_job_emails()
    df = pd.DataFrame(data)

    if df.empty:
        st.warning("No job-related emails found.")
        return

    st.success(f"✅ Found {len(df)} emails.")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    df = df.dropna(subset=["Date"])

    df["Year"] = df["Date"].dt.year
    df["Week_Num"] = df["Date"].dt.isocalendar().week
    df["Date_Only"] = df["Date"].dt.date

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    last_7_days = today - datetime.timedelta(days=7)

    jobs_today = df[df["Date_Only"] == today]
    jobs_yesterday = df[df["Date_Only"] == yesterday]
    jobs_last_7_days = df[df["Date_Only"] >= last_7_days]

    col1, col2, col3 = st.columns(3)
    col1.metric("🟢 Jobs Applied Today", len(jobs_today))
    col2.metric("🕒 Jobs Applied Yesterday", len(jobs_yesterday))
    col3.metric("📆 Jobs Applied Last 7 Days", len(jobs_last_7_days))

    daily_counts = df.groupby("Date_Only").size()
    avg_per_day = int(round(daily_counts.mean())) if not daily_counts.empty else 0
    st.metric("📊 Avg Jobs/Day", f"{avg_per_day}")

    jobs_today_count = len(jobs_today)
    if jobs_today_count > avg_per_day:
        st.success(f"👏 You're on fire! {jobs_today_count} today (> avg {avg_per_day}). Keep going! 🚀")
    elif jobs_today_count < avg_per_day:
        st.info(f"🌱 {jobs_today_count} today (< avg {avg_per_day}). Small steps add up! 💪")
    else:
        st.warning(f"🎯 On track! Today matches your average of {avg_per_day}.")

    st.markdown("---")

    start_of_this_month = today.replace(day=1)
    start_of_last_month = (start_of_this_month - datetime.timedelta(days=1)).replace(day=1)
    end_of_last_month = start_of_this_month - datetime.timedelta(days=1)
    two_weeks_ago = today - datetime.timedelta(days=14)

    st.markdown("### 📅 Filter Daily Trend by Time Range")
    date_filter = st.selectbox("Select Time Range", ["Last 2 Weeks", "This Month", "Last Month", "All Time"])

    if date_filter == "Last 2 Weeks":
        df_filtered = df[df["Date_Only"] >= two_weeks_ago]
    elif date_filter == "This Month":
        df_filtered = df[df["Date_Only"] >= start_of_this_month]
    elif date_filter == "Last Month":
        df_filtered = df[(df["Date_Only"] >= start_of_last_month) & (df["Date_Only"] <= end_of_last_month)]
    else:
        df_filtered = df

    daily_trend = df_filtered.groupby("Date_Only").size().reset_index(name="Applications").sort_values("Date_Only")

    chart = (
        alt.Chart(daily_trend)
        .mark_line(point=True)
        .encode(
            x=alt.X("Date_Only:T", title="Date", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Applications", title="Jobs Applied"),
            tooltip=["Date_Only:T", "Applications"],
        )
        .properties(title=f"📈 Daily Job Application Trend ({date_filter})", width=700, height=300)
    )
    st.altair_chart(chart, use_container_width=True)

    csv = df.to_csv(index=False)
    st.download_button("📥 Download Job Data as CSV", csv, "job_applications.csv", "text/csv")

    with st.expander("🔍 Raw Email Data"):
        st.dataframe(df[["Date", "Subject", "From"]].sort_values(by="Date", ascending=False), use_container_width=True)

def render_more_analysis():
    st.title("📈 More Analysis")
    data = fetch_job_emails()
    df = pd.DataFrame(data)
    if df.empty:
        st.warning("No job-related emails found.")
        return

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    df = df.dropna(subset=["Date"])
    df["Date_Only"] = df["Date"].dt.date
    today = datetime.date.today()

    st.markdown("## 📆 Weekly Job Application Goal & Progress")
    weekly_goal = st.sidebar.number_input(
        "Set your weekly job application goal:", min_value=1, max_value=100, value=10, step=1
    )

    df["Year_Week"] = df["Date"].dt.strftime("%G-W%V")
    current_week = today.isocalendar()
    current_year_week = f"{current_week[0]}-W{str(current_week[1]).zfill(2)}"
    weekly_apps = df[df["Year_Week"] == current_year_week]
    count_weekly_apps = len(weekly_apps)

    progress_percent = int((count_weekly_apps / weekly_goal) * 100) if weekly_goal > 0 else 0
    progress_percent = min(progress_percent, 100)
    st.markdown("### 🏁 Weekly Application Goal Tracker")
    st.progress(progress_percent)
    st.info(f"📅 This Week: {count_weekly_apps} / {weekly_goal} applications")

    weekly_summary = df.groupby("Year_Week").size().reset_index(name="Applications").sort_values("Year_Week", ascending=False).head(5)
    with st.expander("📊 Weekly History (Last 5 Weeks)"):
        st.dataframe(weekly_summary, use_container_width=True)

    st.markdown("## 🗓️ Calendar Heatmap of Applications")
    plot_interactive_calendar(df)

def render_tracking():
    st.title("📆 Job Application & Status")
    st.info("🚧 This feature is coming soon!")

def render_resume_analyzer():

    st.title("🕵️‍♂️ Resume vs Job Description Analyzer")

    st.info("🚧 This feature is coming soon!")

# -----------------------
# Main
# -----------------------
def main():
    # If Google just redirected back, finish the OAuth flow
    handle_oauth_callback()

    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Go to", ["🏠 Home", "📊 Dashboard", "📈 More Analysis", "📆 Tracking", "🕵️‍♂️ Resume Analyzer"])

    if not is_authenticated():
        st.info("🔐 Please login with Google to fetch your job emails.")
        if st.button("Login with Google"):
            begin_google_login()
        st.stop()

    st.success("✅ You are authenticated!")
    if st.button("Logout"):
        logout()

    if page == "🏠 Home":
        render_home()
    elif page == "📊 Dashboard":
        render_dashboard()
    elif page == "📈 More Analysis":
        render_more_analysis()
    elif page == "📆 Tracking":
        render_tracking()
    elif page == "🕵️‍♂️ Resume Analyzer":
        render_resume_analyzer()

if __name__ == "__main__":
    main()
