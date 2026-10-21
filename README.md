# Barracuda IP Removal Agent

A project for checking IPv4 reputation using Barracuda's official Reputation Block List (BRBL) lookup page and managing removal-request workflows for IP addresses reported as listed.

This repository provides **two separate ways to use the project**:

1. **Windows desktop app (`BarracudaIPAgent.exe`)** — a graphical interface for looking up single IPs, ranges, or pasted lists, viewing results and logs, and exporting records. This is the easiest option for most Windows users.
2. **Python CLI (`agent.py`)** — the original Playwright-based browser automation script, configured through a local `.env` file and an `input_ips.csv` input list.

The GUI app and CLI are separate implementations. Follow the instructions for the one you choose.

## Features

- Look up IPv4 addresses using Barracuda's official reputation lookup page.
- Use the Windows GUI to enter a single address, an inclusive IP range, or paste multiple IPs.
- View statuses, timestamps, messages, and confirmation details.
- Export results to CSV and search/export application logs.
- Save processing history locally and protect against duplicate submissions.
- Configure contact details and removal reasons locally rather than embedding them in the shared executable.
- Handle uncertain outcomes cautiously. A request-received confirmation does **not** mean the IP has been delisted.

## Option 1: Windows desktop application (recommended)

### Requirements

- Windows 10 or Windows 11, 64-bit.
- Microsoft Edge installed; Google Chrome can be used as a fallback.
- Internet access.
- IP addresses and contact details you are authorized to manage.

**Python is not required** to run the packaged EXE. The application uses a browser installed on your computer; it does not bundle Chromium.

### How to use the EXE

1. Download `BarracudaIPAgent.exe` from the download section at the end of this README.
2. Save it in a folder you control and double-click it.
3. **Set up your profile first.** Enter your own contact email, customer name, customer email, phone number, and removal reason in the Profile window.
4. Click **Save Profile** to open the dashboard.
5. Choose the input tab:
   - **Single:** enter one IPv4 address.
   - **Range:** enter a starting IP and ending IP; both endpoints are included.
   - **Random:** paste one or more IPv4 addresses using supported separators such as new lines or commas. You do not need to create a CSV file.
6. Review your entries and click **Start**.
7. Check the Results table and status messages. A `LISTED` result means the lookup page reported that the IP is listed; it does not mean the IP has already been removed.
8. Click **Export Results CSV** to export the displayed results. Click **View Logs** to search logs and export log records.
9. Use **Pause/Resume** or **Stop** as needed. Allow the current operation to reach a safe checkpoint before closing the app.

### Files created by the EXE

The application stores runtime files beside the executable:

| File | Purpose |
|---|---|
| `.env` | Saved profile and configuration. Keep it private. |
| `results.csv` | Result records and exported run details. |
| `agent.db` | SQLite processing state and duplicate-submission protection. |
| `agent_errors.log` | Diagnostic messages, including errors and access restrictions. |

Create your own profile on your computer. Do not upload or share `.env`, `agent.db`, results, input lists, or logs if they contain personal or confidential information. Back up important output files before moving or replacing the EXE.

### EXE troubleshooting and safe use

- HTTP **403/429** responses, access challenges, and uncertain lookup results stop processing. The app does not bypass access restrictions or automatically retry an ambiguous submission.
- If a removal request returns an error or no confirmation appears, manually verify whether the request was received before submitting again.
- A request-received confirmation does **not** prove that an IP address was delisted.
- If the browser does not open, confirm Microsoft Edge or Google Chrome is installed.
- If required profile fields are missing, use **Edit Profile**, enter your details, and save the profile.
- If Windows shows a security warning, verify that the executable came from a source you trust and is the intended build. Do not ignore warnings for unknown files.

## Option 2: Python CLI (`agent.py`)

The CLI is a separate Playwright-based implementation. Use this section only if you want to run the Python script instead of the Windows GUI.

### Requirements

- Python 3.11 or a compatible Python version.
- Git.
- An internet connection.
- Chromium installed through Playwright.
- IPv4 addresses you are authorized to manage.

### 1. Clone the repository

```powershell
git clone https://github.com/Tarunsingh465/barracuda-delisting-agent.git
cd barracuda-delisting-agent
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use the virtual environment's Python executable directly.

### 3. Install dependencies and Chromium

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### 4. Configure `.env`

Create a `.env` file in the project root alongside `agent.py`. Use the configuration keys expected by the current code. Example placeholders:

```dotenv
BARRACUDA_LOOKUP_URL=https://barracudacentral.org/lookups/lookup-reputation
BARRACUDA_FORM_URL=https://www.barracudacentral.org/rbl/removal-request

CONTACT_EMAIL=your-authorized-contact@example.com
CUSTOMER_NAME=Your Authorized Contact
CUSTOMER_EMAIL=your-authorized-customer@example.com
PHONE_NUMBER=your-authorized-phone-number
REMOVAL_REASON="Describe the corrective actions taken to address the listing."
```

Replace the placeholder values with valid information you are authorized to use. Never commit your real `.env` file, private contact information, credentials, or other secrets to a public repository. Use `.env.example` with placeholder values when sharing setup instructions.

### 5. Prepare `input_ips.csv`

Create `input_ips.csv` in the project root with an `ip` column and one IPv4 address per row:

```csv
ip
203.0.113.10
198.51.100.20
```

These IPs are documentation-only examples from reserved ranges. Replace them with addresses you are authorized to check. Start with one address to verify your configuration and browser behavior.

### 6. Run the CLI

```powershell
python agent.py
```

The script opens a visible Chromium browser and processes the input addresses according to its lookup and form-handling logic.

### CLI result statuses

| Status or outcome | Meaning |
|---|---|
| `NOT_LISTED` | The lookup result was recognized as not listed; the agent records the result and continues. |
| `LISTED` | The lookup result was recognized as listed; the agent opens and fills the removal form. |
| `SUBMISSION_CONFIRMED` | A recognized request-received confirmation was found. A confirmation number is saved when available. |
| `SUBMISSION_UNCONFIRMED` | No recognized confirmation appeared within the waiting period. The request may still have been received; verify manually before retrying. |
| `CHECK_ERROR` | The lookup response was ambiguous or unexpected; the agent does not treat it as a confirmed listing. |
| Form/submission error | Inspect the browser and logs to understand the outcome. |

The CLI waits up to three minutes for a recognized confirmation after the submit click. If no confirmation is found, it records `SUBMISSION_UNCONFIRMED` and does not automatically retry. A recognized receipt confirmation means Barracuda displayed a request-received message; it does not mean the IP has been delisted.

### CLI output files

| File | Description |
|---|---|
| `results.csv` | Processing timestamps, IP addresses, statuses, and details. |
| `agent.db` | SQLite database containing the latest recorded status and detail for each IP. |
| `input_ips.csv` | Input IP list. |
| `.env` | Local contact and request configuration; keep it private. |

The CLI stores a confirmation number in the existing result detail when it is available. Duplicate protection skips IPs that already have a recorded submission attempt or outcome so that the status can be checked before any further attempt.

### CLI troubleshooting

- **Chromium does not launch:** run `python -m playwright install chromium`.
- **`CHECK_ERROR`:** the website may be unavailable, the lookup may have timed out, or the page structure or result wording may have changed. Check the lookup manually rather than guessing the outcome.
- **Missing environment variables:** verify that `.env` exists in the project root, the keys match the Python code, and required values are not empty.
- **Removal form does not open:** confirm the lookup response clearly matches the listed condition and inspect the browser/logs.
- **Uncertain submission:** check the site manually before retrying. A missing confirmation does not prove the request was not received.
- **Missing confirmation number:** the page may not have shown a number or its format may not match the current code.

## Security and responsible use

- Use this project only for IP addresses and contact details you are authorized to manage.
- Verify the contact information and removal reason before submitting requests.
- Keep `.env`, input lists, databases, results, and logs private when they contain sensitive information.
- Exclude virtual environments, private configuration, and runtime data from Git.
- Respect Barracuda's applicable terms, policies, and rate limits.
- If the website presents a CAPTCHA, rate-limit message, access-denied page, or another security challenge, stop and investigate. Do not attempt to bypass it.
- Review unexpected browser behavior and uncertain submission outcomes carefully.

## Limitations

The project depends on the current structure and wording of the Barracuda website. Changes to HTML, selectors, form fields, confirmation messages, or confirmation-number formatting may require code updates. Reputation classification can fail when the page returns unexpected content. Neither implementation can guarantee that a removal request will be accepted or that an IP address will be delisted.

## Contributing

Bug reports and improvements are welcome. When sharing errors, logs, or screenshots, remove sensitive IP information, contact details, and other private data.

## License

No license is specified in this repository. Add a `LICENSE` file if you intend to grant others explicit permission to reuse, modify, or redistribute the project.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by Barracuda Networks. Use it responsibly and verify important lookup and submission outcomes.

---

## Download the Windows EXE

**[Download BarracudaIPAgent.exe](https://raw.githubusercontent.com/Tarunsingh465/barracuda-delisting-agent/main/BarracudaIPAgent.exe)**

For this download link to work, place the executable in the repository's root folder with the exact filename `BarracudaIPAgent.exe`, then commit and push it to the repository's `main` branch alongside this `README.md`. This link downloads the EXE; GitHub cannot launch the program directly from a README.