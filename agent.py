import csv
import ipaddress
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()

LOOKUP_URL = os.getenv("BARRACUDA_LOOKUP_URL", "https://barracudacentral.org/lookups/lookup-reputation")
FORM_URL = os.getenv("BARRACUDA_FORM_URL", "https://www.barracudacentral.org/rbl/removal-request")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CUSTOMER_NAME = os.getenv("CUSTOMER_NAME", "").strip()
CUSTOMER_EMAIL = os.getenv("CUSTOMER_EMAIL", "").strip()
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "").strip()
REMOVAL_REASON = os.getenv("REMOVAL_REASON", "").strip()

INPUT_PATH = "input_ips.csv"
RESULTS_PATH = "results.csv"
DB_PATH = "agent.db"

TIMEOUT_MS = 30000
CONFIRMATION_TIMEOUT_MS = 180000  # 3 minutes
CONFIRMATION_POLL_MS = 1000


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ip_jobs (
            ip TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            detail TEXT,
            updated_at TEXT NOT NULL
        )""")


def get_status(ip):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status FROM ip_jobs WHERE ip=?", (ip,)
        ).fetchone()
    return row[0] if row else None


def record(ip, status, detail=""):
    timestamp = now()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO ip_jobs(ip,status,detail,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                status=excluded.status,
                detail=excluded.detail,
                updated_at=excluded.updated_at""",
            (ip, status, detail, timestamp),
        )

    new_file = (
        not os.path.exists(RESULTS_PATH)
        or os.path.getsize(RESULTS_PATH) == 0
    )

    with open(RESULTS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if new_file:
            writer.writerow(["timestamp_utc", "ip", "status", "detail"])

        writer.writerow([timestamp, ip, status, detail])


def load_ips():
    if not os.path.exists(INPUT_PATH):
        raise FileNotFoundError(f"Missing {INPUT_PATH}")

    ips, seen = [], set()

    with open(INPUT_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = (row.get("ip") or "").strip()

            if not raw or raw.startswith("#"):
                continue

            try:
                parsed = ipaddress.ip_address(raw)
            except ValueError:
                record(raw, "INVALID_IP", "Input is not a valid IP address")
                print(f"Skipping invalid IP entry: {raw!r}")
                continue

            if parsed.version != 4:
                record(
                    str(parsed),
                    "UNSUPPORTED_IP_VERSION",
                    "Only IPv4 is supported",
                )
                continue

            ip = str(parsed)

            if ip not in seen:
                seen.add(ip)
                ips.append(ip)

    return ips


def fill_lookup_ip(page, ip):
    try:
        field = page.get_by_label("IP or Domain", exact=False)

        if field.count():
            field.first.fill(ip)
            return True
    except Exception:
        pass

    fields = page.locator("input:visible")

    for i in range(fields.count()):
        field = fields.nth(i)

        try:
            if (field.get_attribute("type") or "text").lower() in {
                "text", "search"
            }:
                field.fill(ip)
                return True
        except Exception:
            continue

    return False


def click_lookup(page):
    try:
        button = page.get_by_role(
            "button", name="Check Reputation", exact=False
        )

        if button.count():
            button.first.click()
            return True
    except Exception:
        pass

    try:
        button = page.get_by_text("Check Reputation", exact=False)

        if button.count():
            button.first.click()
            return True
    except Exception:
        pass

    return False


def classify_page(page, ip):
    text = re.sub(
        r"\s+", " ", page.locator("body").inner_text(timeout=10000)
    ).lower()

    ip_re = re.escape(ip.lower())

    if re.search(
        rf"{ip_re}.{{0,160}}not currently listed as\s+['\"]?poor",
        text,
    ):
        return "NOT_LISTED", (
            "Official lookup page says the IP is not currently listed as poor."
        )

    if re.search(
        rf"{ip_re}.{{0,160}}(?:is currently listed as|is listed as)\s+['\"]?poor",
        text,
    ):
        return "LISTED", (
            "Official lookup page says the IP is listed as poor."
        )

    return "CHECK_ERROR", (
        "Could not confidently identify a final result for this IP "
        "on the lookup page."
    )


def website_lookup(page, ip):
    page.goto(
        LOOKUP_URL,
        wait_until="domcontentloaded",
        timeout=TIMEOUT_MS,
    )

    if not fill_lookup_ip(page, ip):
        return "CHECK_ERROR", "Could not locate the IP or Domain field."

    if not click_lookup(page):
        return "CHECK_ERROR", "Could not locate the Check Reputation button."

    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(1000)
    return classify_page(page, ip)


def fill_removal_form(page, ip):
    missing = [
        key
        for key, value in {
            "CONTACT_EMAIL": CONTACT_EMAIL,
            "CUSTOMER_NAME": CUSTOMER_NAME,
            "CUSTOMER_EMAIL": CUSTOMER_EMAIL,
            "PHONE_NUMBER": PHONE_NUMBER,
            "REMOVAL_REASON": REMOVAL_REASON,
        }.items()
        if not value
    ]

    if missing:
        return "CONFIG_ERROR", "Missing .env values: " + ", ".join(missing)

    try:
        page.goto(
            FORM_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT_MS,
        )

        fields = [
            ("ip", ip),
            ("email", CONTACT_EMAIL),
            ("name", CUSTOMER_NAME),
            ("customer email", CUSTOMER_EMAIL),
            ("phone", PHONE_NUMBER),
        ]

        inputs = page.locator("input:visible")
        text_inputs = []

        for i in range(inputs.count()):
            item = inputs.nth(i)
            input_type = (item.get_attribute("type") or "text").lower()

            if input_type in {
                "text", "email", "tel", "number", "search"
            }:
                text_inputs.append(item)

        if len(text_inputs) < 5:
            return (
                "FORM_FILL_ERROR",
                f"Expected 5 text inputs; found {len(text_inputs)}.",
            )

        for locator, (_, value) in zip(text_inputs[:5], fields):
            locator.fill(value)

        textareas = page.locator("textarea:visible")

        if textareas.count() != 1:
            return (
                "FORM_FILL_ERROR",
                f"Expected one reason textarea; found {textareas.count()}.",
            )

        textareas.first.fill(REMOVAL_REASON)

        if text_inputs[0].input_value().strip() != ip:
            return "FORM_FILL_ERROR", "IP verification failed."

        print(f"Submitting removal request for listed IP: {ip}")

        form = text_inputs[0].locator("xpath=ancestor::form")
        submit_button = form.get_by_role(
            "button", name=re.compile(r"submit|send request", re.I)
        )

        if submit_button.count() != 1:
            return (
                "SUBMISSION_NOT_ATTEMPTED",
                "Could not uniquely identify the form's Submit button.",
            )

        record(
            ip,
            "SUBMISSION_ATTEMPTED",
            "Form validated; submission is about to be attempted.",
        )

        # Click without waiting for navigation to finish.
        submit_button.click(
            timeout=TIMEOUT_MS,
            no_wait_after=True,
        )

        confirmation_phrases = (
            "request received",
            "request has been submitted",
            "request was submitted",
            "successfully submitted",
            "submission successful",
        )

        deadline = time.monotonic() + CONFIRMATION_TIMEOUT_MS / 1000

        while time.monotonic() < deadline:
            try:
                response_text = re.sub(
                    r"\s+",
                    " ",
                    page.locator("body").inner_text(timeout=5000),
                ).strip()

                response_lower = response_text.lower()

                if any(
                    phrase in response_lower
                    for phrase in confirmation_phrases
                ):
                    match = re.search(
                        r"confirmation number is\s+([A-Z0-9-]+)",
                        response_text,
                        re.IGNORECASE,
                    )

                    confirmation_number = (
                        match.group(1) if match else None
                    )

                    if confirmation_number:
                        detail = (
                            "Barracuda confirmed receipt. "
                            f"Confirmation number: {confirmation_number}"
                        )
                    else:
                        detail = (
                            "Barracuda displayed a request-received "
                            "confirmation, but no confirmation number "
                            "was extracted."
                        )

                    return "SUBMISSION_CONFIRMED", detail

            except PlaywrightTimeoutError:
                pass

            remaining = deadline - time.monotonic()

            if remaining > 0:
                page.wait_for_timeout(
                    min(
                        CONFIRMATION_POLL_MS,
                        max(1, int(remaining * 1000)),
                    )
                )

        return (
            "SUBMISSION_UNCONFIRMED",
            "No recognized confirmation appeared within 3 minutes. "
            "Verify manually before retrying.",
        )

    except PlaywrightTimeoutError as exc:
        return (
            "SUBMISSION_UNCONFIRMED",
            f"Timeout during form processing; verify manually before retrying: {exc}",
        )

    except Exception as exc:
        return (
            "SUBMISSION_UNCONFIRMED",
            f"{type(exc).__name__}: {exc}. Verify manually before retrying.",
        )


def main():
    init_db()

    try:
        ips = load_ips()
    except (OSError, csv.Error) as exc:
        print(f"Input error: {exc}")
        return

    if not ips:
        print("No valid IPv4 addresses found in input_ips.csv.")
        return

    print(f"Loaded {len(ips)} unique IPv4 address(es).")
    print("Checking reputation and processing listed IPs.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            for idx, ip in enumerate(ips, start=1):
                print(f"\n[{idx}/{len(ips)}] Checking {ip}")

                if get_status(ip) in {
                    "SUBMISSION_ATTEMPTED",
                    "SUBMISSION_CONFIRMED",
                    "SUBMISSION_UNCONFIRMED",
                    "SUBMITTED",
                }:
                    print(
                        "Skipping IP with a previous submission attempt; "
                        "verify its status first."
                    )
                    continue

                try:
                    status, detail = website_lookup(page, ip)
                except PlaywrightTimeoutError as exc:
                    status, detail = "CHECK_ERROR", f"Lookup timed out: {exc}"
                except Exception as exc:
                    status, detail = (
                        "CHECK_ERROR",
                        f"{type(exc).__name__}: {exc}",
                    )

                print(f"Website result: {status} — {detail}")
                record(ip, status, detail)

                if status == "NOT_LISTED":
                    print(
                        "Unlisted: leaving lookup result visible; "
                        "no removal form will open."
                    )

                elif status == "LISTED":
                    form_status, form_detail = fill_removal_form(page, ip)

                    # Save the final status and confirmation number before
                    # proceeding to the next IP.
                    record(ip, form_status, form_detail)
                    print(f"Removal form: {form_status} — {form_detail}")

                    if form_status == "SUBMISSION_UNCONFIRMED":
                        print(
                            "Submission is unconfirmed. Waiting for the "
                            "confirmation deadline before continuing."
                        )

                else:
                    print("Uncertain result: no removal form will open.")

                time.sleep(2)

        finally:
            browser.close()

    print(f"\nFinished. Results: {RESULTS_PATH}; database: {DB_PATH}")


if __name__ == "__main__":
    main()