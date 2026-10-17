# Barracuda Delisting Agent

A Python-based browser automation agent that checks IPv4 reputation using the official Barracuda Reputation Block List (BRBL) lookup page and automates the removal-request workflow for IP addresses clearly identified as listed.

The agent uses Playwright to interact with the browser, loads contact details from a local `.env` file, and records processing results in CSV and SQLite files.

## Features

- **Automated reputation lookup:** Opens the official Barracuda lookup page in a visible Chromium browser for every IPv4 address.
- **Automatic IP entry:** Enters the IP address and clicks **Check Reputation**.
- **Result classification:** Distinguishes clearly recognized listed and not-listed results.
- **Unlisted IP handling:** Leaves the lookup result visible and continues to the next IP when the address is clearly not listed as poor.
- **Removal form automation:** Opens the removal form when the lookup clearly reports a poor listing.
- **Automatic form submission:** Fills in the configured contact details and submits the removal request automatically.
- **Confirmation detection:** Recognizes Barracuda's request-received confirmation message.
- **Confirmation number extraction:** Extracts the confirmation number when it appears in the response and stores it in the existing result detail.
- **Confirmation waiting:** Checks for a confirmation for up to three minutes after the submit click. When a recognized confirmation appears, the result is recorded and the agent continues to the next IP. If none appears before the deadline, the result is recorded as `SUBMISSION_UNCONFIRMED` and processing continues without an automatic retry.
- **Error handling:** Records ambiguous or unexpected lookup responses as `CHECK_ERROR` without opening the removal form.
- **Result logging:** Records timestamps, IP addresses, statuses, and details in `results.csv`.
- **Persistent state:** Stores the latest processing state and result detail in `agent.db`.
- **Environment-based configuration:** Loads contact details and request information from a local `.env` file.
- **Duplicate protection:** Skips IPs that already have a recorded submission attempt or outcome, so they can be verified before any further attempt.

## Requirements

- Python 3.11 or a compatible Python version.
- Git.
- Visual Studio Code or another Python-compatible editor.
- An internet connection.
- Chromium installed through Playwright.
- IPv4 addresses you are authorized to manage.

## Installation

### 1. Clone the repository

```powershell
git clone https://github.com/Tarunsingh465/barracuda-delisting-agent.git
cd barracuda-delisting-agent
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks environment activation, use the virtual environment's Python executable directly.

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the Chromium browser required by Playwright:

```powershell
python -m playwright install chromium
```

## Configuration

### 1. Create the environment file

Create a `.env` file in the project root directory, alongside `agent.py`.

Add the environment variables expected by your implementation. For example:

```dotenv
BARRACUDA_LOOKUP_URL=https://barracudacentral.org/lookups/lookup-reputation
BARRACUDA_FORM_URL=https://www.barracudacentral.org/rbl/removal-request

CONTACT_EMAIL=your-authorized-contact@example.com
CUSTOMER_NAME=Your Authorized Contact
CUSTOMER_EMAIL=your-authorized-customer@example.com
PHONE_NUMBER=your-authorized-phone-number
REMOVAL_REASON="Describe the corrective actions taken to address the listing."
```

Replace the example values with the actual configuration required by your code and valid contact information you are authorized to use.

**Security note:** Never commit your real `.env` file, private contact information, credentials, or other secrets to a public repository. Use `.env.example` with placeholder values when sharing configuration instructions.

### 2. Prepare the input file

Create `input_ips.csv` in the project root. The file must contain an `ip` column with one IPv4 address per row.

```csv
ip
203.0.113.10
198.51.100.20
```

The addresses above are documentation-only examples from reserved IP ranges. Replace them with IPv4 addresses you are authorized to check.

Start with one authorized IPv4 address to verify your configuration and browser behavior.

## Usage

### Run the agent

Activate your virtual environment, navigate to the project root, and run:

```powershell
python agent.py
```

The agent opens a visible Chromium browser and processes the input addresses according to its lookup and form-handling logic.

### Expected behavior

| Lookup result or form outcome | Agent behavior |
|---|---|
| `NOT_LISTED` | Records the result and continues to the next IP. |
| `LISTED` | Opens the removal form, fills the configured contact details, and clicks Submit. |
| `SUBMISSION_CONFIRMED` | Records the recognized confirmation and extracted confirmation number, when available, in the result detail; then continues to the next IP. |
| `SUBMISSION_UNCONFIRMED` | Records that no recognized confirmation appeared within three minutes. The request may still have been received, so verify manually before retrying; the agent does not automatically retry it. |
| `CHECK_ERROR` | Records the uncertain lookup result and does not open the removal form. |
| Form or submission error | Records the outcome when supported by the implementation; inspect the browser and logs to understand what happened. |

A recognized request-received message confirms that Barracuda displayed a receipt confirmation. It does not establish that the IP has been delisted.

## Output Files

| File | Description |
|---|---|
| `results.csv` | Appends processing timestamps, IP addresses, statuses, and details. Successful submission details include the extracted confirmation number when available. |
| `agent.db` | SQLite database containing the latest recorded processing status and detail for each IP. |
| `input_ips.csv` | Input list of IPv4 addresses to check. |
| `.env` | Local configuration containing contact and request details. Keep it private. |

The confirmation number is stored in the existing `detail` field in both `results.csv` and `agent.db`; no extra database column is required.

## Troubleshooting

### Chromium does not launch

Install the Playwright browser:

```powershell
python -m playwright install chromium
```

### The agent reports `CHECK_ERROR`

The website may be unavailable, the lookup may have timed out, or the page structure or result wording may have changed.

Open the official lookup page manually and inspect the result. An ambiguous response should not be interpreted as either a confirmed listing or a confirmed clean result.

### Environment variables are missing

- Verify that `.env` exists in the project root.
- Check that variable names match those referenced in the Python code.
- Confirm that required values are not empty.

### The removal form does not open

Verify that the lookup result clearly matches the recognized listed condition. Inspect the browser and logs rather than assuming the IP is listed.

### The request outcome is uncertain

The agent waits up to three minutes for a recognized confirmation. If one is not detected, it records `SUBMISSION_UNCONFIRMED`. A missing confirmation does not prove that Barracuda failed to receive the request. Verify the status manually before retrying, since the agent skips IPs with previous submission statuses.

### The confirmation number is missing

The number is extracted only when it appears in a format recognized by the current code. If the page wording changes or no number is shown, the agent may record the confirmation without a number. Check the visible browser response and confirm the result manually.

## Security and Responsible Use

- Use the agent only for IP addresses and contact details you are authorized to manage.
- Verify the configured contact information and removal reason before enabling automated submissions.
- Keep `.env`, `input_ips.csv`, `results.csv`, and `agent.db` private if they contain sensitive information.
- Exclude virtual environments, private configuration, and runtime data from Git.
- Respect Barracuda's applicable terms, policies, and rate limits.
- Stop and investigate if the website presents a CAPTCHA, rate-limit message, access-denied page, or another security challenge; do not attempt to bypass it.
- Review error states and unexpected browser behavior carefully.

## Limitations

The agent depends on the current structure and wording of the Barracuda website. Changes to HTML, selectors, form fields, confirmation messages, or confirmation-number formatting may require code updates.

Reputation classification may fail when the page returns unexpected content. The agent cannot guarantee that a removal request will be accepted or that an IP address will be delisted. A submission confirmation indicates receipt confirmation, not successful delisting.

## Contributing

Bug reports and improvements are welcome. When sharing errors, logs, or screenshots, remove sensitive IP information, contact details, and other private data.

## License

No license is specified in this repository. Add a `LICENSE` file if you intend to grant others explicit permission to reuse, modify, or redistribute the project.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by Barracuda Networks. Use it responsibly and verify the results of automated actions.
