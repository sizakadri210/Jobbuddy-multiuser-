# streamlit_app.py

# =============================================================================
# 1. Imports
# =============================================================================

# Standard library imports
import os
import json
import datetime
import re

# Third-party library imports
import pandas as pd
import altair as alt
import streamlit as st
import matplotlib.pyplot as plt # Although not directly used in the provided code, it's a common ML/data viz import.

# Google API client imports for Gmail integration
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

# =============================================================================
# 2. App Configuration and Constants
# =============================================================================

# Configure Streamlit page settings
st.set_page_config(page_title="Job Buddy 1.0", page_icon="💼", layout="wide")

# Define Google API scopes required for Gmail access
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Function to ensure necessary Streamlit secrets are configured
def require_secrets(keys):
    """
    Checks if all specified keys are present in Streamlit secrets.
    If any are missing, displays an error and stops the app.
    """
    missing = [k for k in keys if k not in st.secrets]
    if missing:
        st.error(
            "Missing secrets: " + ", ".join(missing) +
            "\n\nAdd them in Streamlit Cloud → App → Settings → Secrets."
        )
        st.stop()

# Ensure essential Google OAuth secrets are provided in Streamlit Cloud
require_secrets(["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_REDIRECT_URI"])

# Retrieve OAuth credentials from Streamlit secrets
CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
# The redirect URI must be configured in your Google Cloud Console for the OAuth client
# and should typically be your Streamlit app's public URL (e.g., https://your-app-name.streamlit.app)
REDIRECT_URI = st.secrets["OAUTH_REDIRECT_URI"]

# =============================================================================
# 3. Google OAuth Helpers
# =============================================================================

def _client_config():
    """
    Returns the Google OAuth client configuration dictionary.
    This structure is expected by google_auth_oauthlib.flow.
    """
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
    """
    Initializes and returns a Google OAuth2 authorization flow object.
    The 'state' parameter is used to protect against cross-site request forgery.
    """
    return Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )

def begin_google_login():
    """
    Initiates the Google OAuth login process.
    Generates an authorization URL and stores the 'state' in session_state,
    then provides a link for the user to navigate to Google's authentication page.
    """
    flow = get_flow()
    # Request offline access and prompt consent to ensure a refresh token is issued
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    st.session_state["oauth_state"] = state
    st.link_button("Continue to Google", auth_url)

def handle_oauth_callback():
    """
    Handles the redirect back from Google after user authentication.
    Exchanges the authorization 'code' for access and refresh tokens,
    and stores them in Streamlit's session_state.
    """
    params = st.query_params
    code = params.get("code")
    state = params.get("state")

    # If no 'code' parameter, it's not an OAuth callback
    if not code:
        return False

    # Validate the 'state' parameter to prevent CSRF attacks
    expected_state = st.session_state.get("oauth_state")
    if expected_state and state != expected_state:
        st.error("OAuth state mismatch. Please retry login.")
        return False

    try:
        flow = get_flow(state=state)
        # Exchange the authorization code for credentials
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Store credentials in session_state for future use
        st.session_state["token"] = creds.token
        st.session_state["refresh_token"] = creds.refresh_token
        st.session_state["token_uri"] = creds.token_uri
        st.session_state["client_id"] = creds.client_id
        st.session_state["client_secret"] = creds.client_secret
        st.session_state["scopes"] = creds.scopes

        # Clear query parameters to prevent re-triggering the callback on refresh
        try:
            st.query_params.clear()
        except Exception:
            pass # Ignore if clearing query params fails

        return True
    except Exception as e:
        st.error(f"OAuth error: {e}")
        return False

def is_authenticated():
    """
    Checks if the user is currently authenticated by verifying the presence
    of required OAuth tokens in session_state.
    """
    required_keys = ["token", "refresh_token", "client_id", "client_secret", "token_uri", "scopes"]
    return all(k in st.session_state and st.session_state[k] for k in required_keys)

def logout():
    """
    Clears all OAuth-related information from session_state, effectively logging
    the user out, and then reruns the app to reflect the logged-out state.
    """
    keys_to_clear = ["token", "refresh_token", "client_id", "client_secret", "token_uri", "scopes", "oauth_state"]
    for k in keys_to_clear:
        st.session_state.pop(k, None) # Safely remove key if it exists
    try:
        st.query_params.clear() # Clear URL parameters
    except Exception:
        pass
    st.rerun() # Force a rerun to update UI

def get_gmail_service():
    """
    Builds and returns a Gmail API service client.
    Refreshes the access token if it's expired using the refresh token.
    Raises an error if the token is expired and no refresh token is available.
    """
    creds = Credentials(
        token=st.session_state.get("token"),
        refresh_token=st.session_state.get("refresh_token"),
        token_uri=st.session_state.get("token_uri"),
        client_id=st.session_state.get("client_id"),
        client_secret=st.session_state.get("client_secret"),
        scopes=st.session_state.get("scopes", SCOPES),
    )

    # If the access token is expired, try to refresh it
    if creds.expired:
        if creds.refresh_token:
            creds.refresh(Request())
            # Persist the newly obtained access token
            st.session_state["token"] = creds.token
        else:
            raise RuntimeError("Access token expired and no refresh token available. Please log in again.")

    return build("gmail", "v1", credentials=creds)

# =============================================================================
# 4. Data Fetching and Processing (Gmail Integration)
# =============================================================================

def fetch_job_emails():
    """
    Queries Gmail for emails typically indicating a job application confirmation.
    Fetches the subject, sender, and date of these emails.
    Handles potential API errors like authorization expiry.
    """
    try:
        service = get_gmail_service()
        # Define a broad query to capture various "application received" emails
        q = (
            'subject:"Thank you for Applying" '
            'OR "Thank you for your expression" '
            'OR "Thank you for applying" '
            'OR "Your application was sent" '
            'OR "Thank you for your application" '
            'OR "We have received your application"'
        )
        # List up to 50 relevant messages
        result = service.users().messages().list(userId="me", q=q, maxResults=50).execute()
        messages = result.get("messages", [])

        items = []
        for m in messages:
            # For each message, fetch its metadata (Subject, From, Date headers)
            msg = service.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            # Extract headers into a dictionary
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
        # Handle 401/403 errors by prompting re-login
        if e.resp.status in (401, 403):
            st.warning("Authorization expired or denied. Please log in again.")
            logout()
        else:
            st.error(f"Gmail API error: {e}")
        return []
    except Exception as e:
        st.error(f"Error fetching emails: {e}")
        return []

# =============================================================================
# 5. Visualization Helpers
# =============================================================================

def plot_interactive_calendar(df):
    """
    Generates and displays an interactive calendar heatmap of job applications
    per day using Altair.
    """
    df = df.copy() # Work on a copy to avoid modifying the original DataFrame
    df["Date_Only"] = pd.to_datetime(df["Date_Only"])

    # Count applications per unique date
    by_date = df.groupby("Date_Only").size().reset_index(name="Applications")
    by_date["Day_Num"] = by_date["Date_Only"].dt.weekday           # 0=Mon, ..., 6=Sun
    by_date["Month_Num"] = by_date["Date_Only"].dt.month           # 1=Jan, ..., 12=Dec
    by_date["Month"] = by_date["Date_Only"].dt.strftime("%b")      # Jan, Feb, etc.

    # Aggregate applications by Month and Day-of-week
    agg = (
        by_date.groupby(["Month_Num", "Month", "Day_Num"], as_index=False)["Applications"]
        .sum()
    )

    # Create a complete grid of all months and days to ensure all cells are present,
    # even if no applications were made on a specific day/month combination.
    all_months = (
        agg[["Month_Num", "Month"]]
        .drop_duplicates()
        .sort_values("Month_Num")
        .reset_index(drop=True)
    )
    all_days = pd.DataFrame({"Day_Num": list(range(7))})
    grid = (
        all_months.assign(key=1)
        .merge(all_days.assign(key=1), on="key", how="outer")
        .drop(columns="key")
    )
    # Merge the aggregated data onto the complete grid, filling missing values with 0
    grid = grid.merge(agg, on=["Month_Num","Month","Day_Num"], how="left")
    grid["Applications"] = grid["Applications"].fillna(0).astype(int)

    max_apps = int(grid["Applications"].max())

    # Dynamically generate legend tick values for clarity, always including 0
    if max_apps <= 7:
        tick_values = list(range(0, max_apps + 1))
    else:
        step = max(1, round(max_apps / 5)) # Aim for ~5 ticks
        tick_values = list(range(0, max_apps + 1, step))
        if tick_values[-1] != max_apps: # Ensure max value is always included
            tick_values.append(max_apps)

    # Base chart definition with X and Y axes
    base = alt.Chart(grid).encode(
        x=alt.X(
            "Day_Num:O", # Ordinal scale for day number
            title="Day of Week",
            axis=alt.Axis(
                labelExpr="{'0':'Mon','1':'Tue','2':'Wed','3':'Thu','4':'Fri','5':'Sat','6':'Sun'}[datum.label]"
            ), # Custom labels for days
        ),
        y=alt.Y(
            "Month:O", # Ordinal scale for month
            title="Month",
            sort=alt.EncodingSortField(field="Month_Num", order="ascending"), # Sort months correctly
        ),
    )

    # Heatmap layer for the application counts
    heatmap = base.mark_rect().encode(
        # Conditional coloring: white for 0 applications, green scale otherwise
        color=alt.condition(
            alt.datum.Applications == 0,
            alt.value("#ffffff"), # White for zero applications
            alt.Color(
                "Applications:Q", # Quantitative scale for applications
                scale=alt.Scale(scheme="greens", domain=[0, max_apps], nice=False),
                legend=alt.Legend(values=tick_values, title="Applications"),
            ),
        ),
        tooltip=[ # Tooltip shows Month, Day of Week, and Applications
            alt.Tooltip("Month:N", title="Month"),
            alt.Tooltip("Day_Num:O", title="Day of Week"),
            alt.Tooltip("Applications:Q", title="Applications"),
        ],
    )

    # Add borders to the heatmap cells for better visual separation
    borders = base.mark_rect(fillOpacity=0, stroke="black", strokeWidth=0.5)

    # Combine heatmap and borders, set chart properties
    chart = (heatmap + borders).properties(
        width=700, height=400, title="Calendar Heatmap of Applications"
    )

    st.altair_chart(chart, use_container_width=True)

# =============================================================================
# 6. Streamlit Page Render Functions
# =============================================================================

def render_home():
    """Renders the Home page of the Job Buddy app."""
    st.title("💼 Welcome to Job Buddy 1.0 — Your Job Search Companion")
    st.write("This app helps you track and analyze your job applications automatically from Gmail.")

    # List of motivational quotes
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
    # Select a quote based on the day of the year for variety
    quote_of_the_day = quotes[today.toordinal() % len(quotes)]

    # Display the daily motivational quote with custom styling
    st.markdown(
        f"""
        <div style="background-color:#DFF6FF; padding:20px; border-radius:10px; margin-bottom:20px;">
            <h3 style="color:#007ACC; text-align:center;">
            ✨ Daily Motivational Quote ✨</h3>
            <p style="font-style:italic; font-size:20px; text-align:center;
            ">"{quote_of_the_day}"</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    

def render_dashboard():
    """Renders the Dashboard page, showing application summaries and trends."""
    st.title("🪞 Job Application: Reflection")

    data = fetch_job_emails()
    df = pd.DataFrame(data)

    if df.empty:
        st.warning("No job-related emails found. Please ensure you have applied for jobs "
                   "and that your Gmail inbox contains 'application received' confirmations.")
        return

    st.success(f"✅ Found {len(df)} application confirmation emails.")

    # Data cleaning and preparation
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    df = df.dropna(subset=["Date"]) # Remove rows where date parsing failed

    df["Year"] = df["Date"].dt.year
    df["Week_Num"] = df["Date"].dt.isocalendar().week
    df["Date_Only"] = df["Date"].dt.date # Extract just the date part for daily comparisons

    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    last_7_days = today - datetime.timedelta(days=7)

    # Calculate metrics for recent applications
    jobs_today = df[df["Date_Only"] == today]
    jobs_yesterday = df[df["Date_Only"] == yesterday]
    jobs_last_7_days = df[df["Date_Only"] >= last_7_days]

    # Display key metrics using Streamlit columns
    col1, col2, col3 = st.columns(3)
    col1.metric("🟢 Jobs Applied Today", len(jobs_today))
    col2.metric("🕒 Jobs Applied Yesterday", len(jobs_yesterday))
    col3.metric("📆 Jobs Applied Last 7 Days", len(jobs_last_7_days))

    # Calculate and display average applications per day
    daily_counts = df.groupby("Date_Only").size()
    avg_per_day = int(round(daily_counts.mean())) if not daily_counts.empty else 0
    st.metric("📊 Avg Jobs/Day", f"{avg_per_day}")

    # Provide feedback based on today's application count versus average
    jobs_today_count = len(jobs_today)
    if jobs_today_count > avg_per_day:
        st.success(f"👏 You're on fire! {jobs_today_count} today (> avg {avg_per_day}). Keep going! 🚀")
    elif jobs_today_count < avg_per_day:
        st.info(f"🌱 {jobs_today_count} today (< avg {avg_per_day}). Small steps add up! 💪")
    else:
        st.warning(f"🎯 On track! Today matches your average of {avg_per_day}.")

    st.markdown("---") # Visual separator

    # Define date ranges for filtering daily trends
    start_of_this_month = today.replace(day=1)
    start_of_last_month = (start_of_this_month - datetime.timedelta(days=1)).replace(day=1)
    end_of_last_month = start_of_this_month - datetime.timedelta(days=1)
    two_weeks_ago = today - datetime.timedelta(days=14)

    st.markdown("### 📅 Filter Daily Trend by Time Range")
    date_filter = st.selectbox("Select Time Range", ["Last 2 Weeks", "This Month", "Last Month", "All Time"])

    # Filter DataFrame based on selected time range
    if date_filter == "Last 2 Weeks":
        df_filtered = df[df["Date_Only"] >= two_weeks_ago]
    elif date_filter == "This Month":
        df_filtered = df[df["Date_Only"] >= start_of_this_month]
    elif date_filter == "Last Month":
        df_filtered = df[(df["Date_Only"] >= start_of_last_month) & (df["Date_Only"] <= end_of_last_month)]
    else: # "All Time"
        df_filtered = df

    # Group by date to get daily application counts for charting
    daily_trend = df_filtered.groupby("Date_Only").size().reset_index(name="Applications").sort_values("Date_Only")

    # Create and display an Altair line chart for daily application trends
    chart = (
        alt.Chart(daily_trend)
        .mark_line(point=True) # Line with points at each data point
        .encode(
            x=alt.X("Date_Only:T", title="Date", axis=alt.Axis(labelAngle=0)), # Time scale for date
            y=alt.Y("Applications", title="Jobs Applied"), # Quantitative scale for applications
            tooltip=["Date_Only:T", "Applications"], # Show details on hover
        )
        .properties(title=f"📈 Daily Job Application Trend ({date_filter})", width=700, height=300)
    )
    st.altair_chart(chart, use_container_width=True)

    # Provide option to download raw data
    csv = df.to_csv(index=False).encode('utf-8') # Encode to utf-8 for download
    st.download_button("📥 Download Job Data as CSV", csv, "job_applications.csv", "text/csv")

    # Expandable section to show raw email data
    with st.expander("🔍 Raw Email Data"):
        st.dataframe(df[["Date", "Subject", "From"]].sort_values(by="Date", ascending=False), use_container_width=True)

def render_more_analysis():
    """Renders the More Analysis page, including weekly goals and a calendar heatmap."""
    st.title("📈 More Analysis")
    data = fetch_job_emails()
    df = pd.DataFrame(data)

    if df.empty:
        st.warning("No job-related emails found to perform more analysis. Please login and fetch your emails.")
        return

    # Data cleaning and preparation
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    df = df.dropna(subset=["Date"])
    df["Date_Only"] = df["Date"].dt.date
    today = datetime.date.today()

    st.markdown("## 📆 Weekly Job Application Goal & Progress")
    # Sidebar input for setting a weekly goal
    weekly_goal = st.sidebar.number_input(
        "Set your weekly job application goal:", min_value=1, max_value=100, value=10, step=1
    )

    # Calculate applications for the current week
    df["Year_Week"] = df["Date"].dt.strftime("%G-W%V") # Format Year-Week (e.g., "2023-W40")
    current_week = today.isocalendar()
    current_year_week = f"{current_week[0]}-W{str(current_week[1]).zfill(2)}"
    weekly_apps = df[df["Year_Week"] == current_year_week]
    count_weekly_apps = len(weekly_apps)

    # Display progress towards the weekly goal
    progress_percent = int((count_weekly_apps / weekly_goal) * 100) if weekly_goal > 0 else 0
    progress_percent = min(progress_percent, 100) # Cap at 100%
    st.markdown("### 🏁 Weekly Application Goal Tracker")
    st.progress(progress_percent)
    st.info(f"📅 This Week: {count_weekly_apps} / {weekly_goal} applications")

    # Display a summary of applications for the last 5 weeks
    weekly_summary = df.groupby("Year_Week").size().reset_index(name="Applications").sort_values("Year_Week", ascending=False).head(5)
    with st.expander("📊 Weekly History (Last 5 Weeks)"):
        st.dataframe(weekly_summary, use_container_width=True)

    st.markdown("## 🗓️ Calendar Heatmap of Applications")
    plot_interactive_calendar(df) # Call the helper function to plot the heatmap

def render_tracking():
    """Renders the Job Application Tracking page (placeholder)."""
    st.title("📆 Job Application & Status")
    st.info("🚧 This feature is coming soon! Here you'll be able to manually track job statuses.")
    
    
def render_resume_analyzer():
    """Renders the Resume vs Job Description Analyzer page (placeholder)."""
    st.title("🕵️‍♂️ Resume vs Job Description Analyzer ")
    st.info("🚧 This feature is coming soon! This section will help you tailor your resume to job descriptions.")
    

# =============================================================================
# 7. Main Application Logic
# =============================================================================

def main():
    """
    The main function that orchestrates the Streamlit application.
    Handles authentication flow and navigates between different pages.
    """
    # Attempt to handle OAuth callback if redirected from Google
    handle_oauth_callback()

    # Sidebar navigation for different app pages
    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Go to", ["🏠 Home", "📊 Dashboard", "📈 More Analysis", "📆 Tracking", "🕵️‍♂️ Resume Analyzer"])

    # Authentication check: if not authenticated, prompt user to log in
    if not is_authenticated():
        st.info("🔐 Please login with Google to fetch and analyze your job application emails.")
        if st.button("Login with Google"):
            begin_google_login() # Initiate OAuth flow
        st.stop() # Stop further execution until authenticated

    # If authenticated, display a success message and logout button
    st.sidebar.success("✅ You are authenticated!")
    if st.sidebar.button("Logout"): # Logout button in sidebar
        logout()

    # Render the selected page
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

# Entry point for the Streamlit application
if __name__ == "__main__":
    main()