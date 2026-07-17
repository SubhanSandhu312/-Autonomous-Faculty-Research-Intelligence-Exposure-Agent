"""
test_n8n_webhook.py

Standalone script to test the n8n citation-alert webhook without running
the full scheduler/pipeline. Fires a series of payloads at the webhook
and reports the HTTP response for each.

Usage:
    python test_n8n_webhook.py
"""

import requests

# Replace with your n8n webhook's Production URL (same value as
# N8N_WEBHOOK_URL in scheduler.py).
N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook-test/citation-alerts"

# Use YOUR OWN email here so you can actually check the inbox.
TEST_EMAIL = "subhansandhu312@gmail.com"


def send_test(name, payload):
    print(f"\n--- {name} ---")
    print("Payload:", payload)
    try:
        response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
        print(f"Status: {response.status_code}")
        if response.text:
            print(f"Response body: {response.text}")
        if response.ok:
            print("Sent OK — check the inbox / n8n Executions log to confirm delivery.")
        else:
            print("Non-2xx response — check n8n Executions log for the failing node.")
    except requests.RequestException as e:
        print(f"Request failed: {e}")


def main():
    # 1. Normal case: one recipient, both new papers and citation alerts present.
    send_test("Normal case (1 recipient, both alert types)", {
        "recipients": [TEST_EMAIL],
        "new_paper_count": 2,
        "citation_alert_count": 1,
        "new_papers": ["Test Paper A", "Test Paper B"],
        "citation_alerts": ["Test Paper C"],
    })

    # 2. Multiple recipients: confirms each gets their OWN email, not one
    #    email addressed to everyone.
    # send_test("Multiple recipients", {
    #     "recipients": [TEST_EMAIL, "second-test-address@example.com"],
    #     "new_paper_count": 1,
    #     "citation_alert_count": 0,
    #     "new_papers": ["Multi-Recipient Test Paper"],
    #     "citation_alerts": [],
    # })

    # # 3. Only new papers, no citation alerts: confirms the "Citation
    # #    increases:" section is omitted rather than shown empty.
    # send_test("New papers only", {
    #     "recipients": [TEST_EMAIL],
    #     "new_paper_count": 1,
    #     "citation_alert_count": 0,
    #     "new_papers": ["Only A New Paper"],
    #     "citation_alerts": [],
    # })

    # # 4. Only citation alerts, no new papers: same check, opposite direction.
    # send_test("Citation alerts only", {
    #     "recipients": [TEST_EMAIL],
    #     "new_paper_count": 0,
    #     "citation_alert_count": 1,
    #     "new_papers": [],
    #     "citation_alerts": ["Only A Citation Increase"],
    # })

    # # 5. Empty recipients: should send nothing and not error out.
    # send_test("Empty recipient list", {
    #     "recipients": [],
    #     "new_paper_count": 1,
    #     "citation_alert_count": 0,
    #     "new_papers": ["Nobody Should Get This"],
    #     "citation_alerts": [],
    # })


if __name__ == "__main__":
    main()