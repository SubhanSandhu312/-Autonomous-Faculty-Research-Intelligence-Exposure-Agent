import schedule
import time
import subprocess
import sys
import json
import datetime
import requests
from plyer import notification
import auth

RUN_DAY = "monday"
RUN_TIME = "09:00"
TEST_MODE_INTERVAL_MINUTES = 2
RUN_IMMEDIATELY_ON_START = True
LOG_PATH = "scheduler.log"
DATA_PATH = "data/professor.json"

# Replace with the "Production URL" shown on your n8n Webhook node.
N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook-test/citation-alerts"


def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_notification(title, message):
    try:
        notification.notify(title=title, message=message, timeout=10)
    except Exception as e:
        log(f"Could not show desktop notification: {e}")


def send_email_alert(notifications):
    """Posts the run's findings to the n8n webhook, which fans out a Gmail
    send to every registered subscriber."""
    recipients = auth.get_all_subscriber_emails()
    if not recipients:
        log("No registered subscribers — skipping email alert.")
        return

    new_papers = [n for n in notifications if n["type"] == "new_paper"]
    citation_alerts = [n for n in notifications if n["type"] == "citation_alert"]

    payload = {
        "recipients": recipients,
        "new_paper_count": len(new_papers),
        "citation_alert_count": len(citation_alerts),
        "new_papers": [n.get("title") for n in new_papers],
        "citation_alerts": [n.get("title") for n in citation_alerts],
    }

    try:
        response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
        response.raise_for_status()
        log(f"n8n webhook triggered for {len(recipients)} subscriber(s).")
    except requests.RequestException as e:
        log(f"Failed to trigger n8n webhook: {e}")


def notify_run_results():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        send_notification("Research Agent", "Weekly update finished, but results could not be read.")
        return

    notifications = data.get("notifications", [])

    if not notifications:
        send_notification("Research Agent", "Weekly update finished. No new papers or citation changes.")
        return

    new_count = sum(1 for n in notifications if n["type"] == "new_paper")
    alert_count = sum(1 for n in notifications if n["type"] == "citation_alert")

    message = f"{new_count} new paper(s), {alert_count} citation increase(s) found."
    send_notification("Research Agent - Update Found", message)
    log(message)

    send_email_alert(notifications)


def run_pipeline():
    log("Starting scheduled run...")
    try:
        subprocess.run([sys.executable, "code.py"], check=True)
        log("Run finished successfully.")
        notify_run_results()
    except subprocess.CalledProcessError as e:
        log(f"Run failed: {e}")
        send_notification("Research Agent - Run Failed", "The weekly update failed. Check scheduler.log for details.")


if TEST_MODE_INTERVAL_MINUTES:
    job = schedule.every(TEST_MODE_INTERVAL_MINUTES).minutes.do(run_pipeline)
    log(f"TEST MODE: running every {TEST_MODE_INTERVAL_MINUTES} minute(s).")
else:
    job = getattr(schedule.every(), RUN_DAY).at(RUN_TIME).do(run_pipeline)
    log(f"Scheduler started. Will run every {RUN_DAY} at {RUN_TIME}.")

log(f"Next scheduled run: {job.next_run}")

if RUN_IMMEDIATELY_ON_START:
    run_pipeline()

last_logged_next_run = job.next_run

while True:
    schedule.run_pending()
    if job.next_run != last_logged_next_run:
        log(f"Next scheduled run: {job.next_run}")
        last_logged_next_run = job.next_run
    time.sleep(60)