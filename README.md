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
- **Error handling:** Records ambiguous or unexpected lookup responses as `CHECK_ERROR` without opening the removal form.
- **Result logging:** Records processing history in `results.csv`.
- **Persistent state:** Stores the latest processing state in `agent.db`.
- **Environment-based configuration:** Loads contact details and request information from a local `.env` file.

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

| Lookup result | Agent behavior |
|---|---|
| `NOT_LISTED` | Leaves the lookup result visible and continues to the next IP. |
| `LISTED` | Opens the removal form, fills the configured contact details, and attempts to submit the request automatically. |
| `CHECK_ERROR` | Records the uncertain result and does not open the removal form. |
| Form or submission error | Records the error when supported by the implementation; inspect the browser and logs to determine what happened. |

Automatic submission is an attempt to send a request, not proof that Barracuda accepted it or removed the IP from the list.

## Output Files

| File | Description |
|---|---|
| `results.csv` | Records processing timestamps, IP addresses, statuses, and details. |
| `agent.db` | SQLite database containing persistent processing state. |
| `input_ips.csv` | Input list of IPv4 addresses to check. |
| `.env` | Local configuration containing contact and request details. |

The exact fields and state transitions depend on the implementation. Review the output files to understand the recorded outcomes.

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

A click on the Submit button does not guarantee successful submission. Check the resulting browser response and any confirmation message before treating the request as accepted. Avoid blindly retrying a request whose outcome is unknown.

## Security and Responsible Use

- Use the agent only for IP addresses and contact details you are authorized to manage.
- Verify the configured contact information and removal reason before enabling automated submissions.
- Keep `.env`, `input_ips.csv`, `results.csv`, and `agent.db` private if they contain sensitive information.
- Exclude virtual environments, private configuration, and runtime data from Git.
- Respect Barracuda's applicable terms, policies, and rate limits.
- Review error states and unexpected browser behavior carefully.

## Limitations

The agent depends on the current structure and wording of the Barracuda website. Changes to HTML, selectors, form fields, or result messages may require code updates.

Reputation classification may fail when the page returns unexpected content. The agent cannot guarantee that a removal request will be accepted or that an IP address will be delisted.

## Contributing

Bug reports and improvements are welcome. When sharing errors, logs, or screenshots, remove sensitive IP information, contact details, and other private data.

## License

No license is specified in this repository. Add a `LICENSE` file if you intend to grant others explicit permission to reuse, modify, or redistribute the project.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by Barracuda Networks. Use it responsibly and verify the results of automated actions.
