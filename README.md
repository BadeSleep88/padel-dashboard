# Padel Insights (Streamlit)

Sign in with your club email and password. If the email is on the allowlist, the app logs in with a headless
browser, scrapes your session history and shows your dashboard, with a download button for the HTML report.

```
padel-streamlit/
├── app.py               the Streamlit page (login form, progress, report view)
├── scraper.py           club login + history scraper
├── dashboard.py         analysis + HTML dashboard
├── allowlist.py         email allowlist check
├── allowed_emails.txt   allowed emails (optional, see secrets below)
├── demo_data.json       fake data for the "See a demo" button
├── requirements.txt     Python packages
├── packages.txt         system package for Streamlit Cloud (chromium)
└── .streamlit/secrets.toml.example
```

## Run locally
```bash
pip install -r requirements.txt
playwright install chromium
cp .streamlit/secrets.toml.example .streamlit/secrets.toml     # put your allowed emails in it
streamlit run app.py
```

## Put it on Streamlit Community Cloud
1. Push this folder to a GitHub repo.
2. On share.streamlit.io choose the repo and `app.py` as the main file.
3. In **Settings → Secrets**, paste: `allowed_emails = ["alice@example.com", "@mypadelgroup.org"]`
   (use secrets, not `allowed_emails.txt`, if the repo is public).
4. Deploy. `packages.txt` installs Chromium for you.

## Good to know
- The password is used once for sign-in and not saved. The report lives in the visitor's session only, and **Clear my data** removes it.
- Only one dashboard builds at a time (free hosting has little memory). Raise `max_concurrent_scrapes` in secrets on a bigger server.
- Each email gets 3 attempts per hour, so repeated wrong passwords can't lock people out of the club site.
- The scraper depends on the club site's HTML; update `scraper.py` if the site changes.
- Check the club allows automated logins before sharing this with members.
