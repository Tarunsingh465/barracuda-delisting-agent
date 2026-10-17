# Barracuda Delisting Agent — clean replacement

- Opens the official Barracuda lookup page in a visible browser for every IPv4.
- Enters the IP and clicks Check Reputation.
- If the page clearly reports the IP is not currently listed as poor, leaves that lookup result visible and moves on.
- If the page clearly reports a poor listing, opens the removal form and fills the contact details from `.env`.
- Ambiguous results are recorded as `CHECK_ERROR`; the removal form is not opened.
- It does not click Submit automatically.
- Records results in `results.csv` and current state in `agent.db`.

## Install in VS Code PowerShell
```powershell
python -m pip install -r requirements.txt
playwright install chromium
```

Keep `.env` private and out of GitHub. Test with one authorized IPv4 first. Website wording/HTML may change; uncertain results are deliberately classified as `CHECK_ERROR`.
