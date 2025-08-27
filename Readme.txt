Job Buddy 1.0 — Gmail-powered Job Application Tracker

Track and visualize your job applications automatically from your Gmail inbox.
This Streamlit app connects (read-only) to your Gmail, detects “application received / thank you for applying” emails, and turns them into simple dashboards and trends.

✅ OAuth is handled entirely in the Streamlit app (no Flask server needed).
🔒 Uses gmail.readonly scope only.

📸 Demo 

![Job Buddy Demo](assets/demo.gif)

✨ Features

Google Sign-in (OAuth) — secure login via your Google account

Automatic email fetch — pulls recent application confirmation emails

Dashboards — daily/weekly counts, time-range filter, calendar heatmap

Read-only — the app never modifies your Gmail

🧩 Tech Stack

Frontend/App: Streamlit

Data: pandas, Altair

Google APIs: google-api-python-client, google-auth, google-auth-oauthlib

Scope: https://www.googleapis.com/auth/gmail.readonly

📂 Project Structure
.
├─ streamlit_app.py        # main app
├─ requirements.txt
├─ .streamlit/             # local-only secrets (not committed)
│  └─ secrets.toml
└─ .gitignore


.gitignore (recommended):

__pycache__/
*.pyc
.venv/
.streamlit/secrets.toml

🔑 Prerequisites

Google Cloud Project

Enable Gmail API (APIs & Services → Library → Gmail API → Enable).

Create OAuth consent screen:

User type: External

Status: Testing (add your Google account under Test users).

Create OAuth 2.0 Client ID:

Application type: Web application

Authorized redirect URIs:

For local dev: http://localhost:8501

For Cloud: https://<your-app-name>.streamlit.app

Python 3.9+ and pip

🧪 Run Locally

Create & activate a virtualenv, then install deps:

python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt


Create .streamlit/secrets.toml with your OAuth values:

GOOGLE_CLIENT_ID = "YOUR_CLIENT_ID.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
OAUTH_REDIRECT_URI = "http://localhost:8501"


If you see an OAuth error about HTTP redirect, set:

Windows (PowerShell): setx OAUTHLIB_INSECURE_TRANSPORT 1 then open a new shell

macOS/Linux: export OAUTHLIB_INSECURE_TRANSPORT=1

Run the app:

streamlit run streamlit_app.py


Open http://localhost:8501, click Login with Google, and you’re in 🎉

🧯 Troubleshooting

redirect_uri_mismatch
The redirect URI in GCP must match exactly what you set in secrets (no trailing slash).

invalid_client / unauthorized_client
Ensure you created a Web application OAuth client and copied the correct ID/secret.

Login loops / 401 after a while
In Testing mode, refresh tokens can rotate sooner. Click Logout and sign in again.

Module not found
Make sure all imports exist in requirements.txt and restart the app.

📦 Requirements

requirements.txt:

streamlit>=1.32
pandas
altair
matplotlib
google-api-python-client
google-auth
google-auth-oauthlib

🔐 Privacy & Security

Read-only Gmail scope; no email modifications.

Tokens live in your Streamlit session state (not persisted server-side).

Keep your Client Secret safe; rotate it in GCP if exposed.

🗺️ Roadmap 

CSV export with richer fields (company, inferred source)

Customizable Gmail query builder

Application status tracking

Rate-limit aware caching

🤝 Contributing

Issues and pull requests welcome! Please open an issue to discuss larger changes.

📄 License

Choose a license for your repo (MIT is common for apps like this). If you add a LICENSE file, reference it here.

🙌 Credits

Built by Siza Kadri.
Powered by Streamlit, Google APIs, pandas, Altair, and a lot of ☕.