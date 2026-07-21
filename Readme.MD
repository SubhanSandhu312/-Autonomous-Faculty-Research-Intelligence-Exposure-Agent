# Faculty Research Intelligence & Exposure Agent

An autonomous, multi-user platform that tracks academic researchers'
publications, citations, and collaborations from open web sources, lets
you ask questions about their work through a RAG-powered chatbot, and
proactively surfaces upcoming Calls for Papers (CFPs) that match their
research interests.

Originally built for a single hardcoded professor, then migrated into a
multi-professor, multi-user platform — any signed-up user can track any
number of professors, with fully isolated chat history, subscriptions,
and alerts per account.

## Features

- **Multi-source publication fetching** — OpenAlex, Semantic Scholar, and
  arXiv as primary sources, with Google Scholar (via `scholarly`) as a
  fallback when the others come up empty (Scholar has no official API and
  gets rate-limited/blocked unpredictably, so it's treated as a
  last-resort source, not primary).
- **Semantic search over publications** via ChromaDB — papers are deduped
  across sources, embedded locally (no API cost), and searchable by
  meaning, not just keywords.
- **RAG chatbot** — ask natural-language questions about a professor's
  research; answers are grounded only in retrieved paper excerpts, with a
  relevance cutoff so it won't force an answer when nothing relevant was
  found. Supports full per-chat conversation history, so follow-up
  questions ("what did I just ask") resolve correctly.
- **Multi-professor, multi-user** — each user tracks their own subset of
  professors. Adding a professor another user already tracks reuses the
  existing data instead of re-fetching.
- **New paper / citation spike detection** — every fetch is diffed
  against the previous run; new publications and significant citation
  increases are flagged and bundled into per-user notification emails.
- **CFP matching** — upcoming conference/journal calls for papers are
  matched against each professor's research interests and surfaced both
  in the UI and via alert email.
- **Scheduled autonomous runs** — a background scheduler re-fetches every
  tracked professor on a weekly cadence (configurable), with desktop
  notifications summarizing what changed.
- **Email alerts via n8n** — citation/new-paper and CFP notifications are
  sent through an n8n webhook workflow, bundled per user across all of
  their tracked professors (one email covering everyone they follow, not
  one email per professor).

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │   OpenAlex · Semantic Scholar · arXiv │
                    │      Google Scholar (fallback)        │
                    └───────────────────┬───────────────────┘
                                        │
                              code.py (per professor)
                       fetch → dedupe → stamp → detect changes
                                        │
                    data/professors/<id>/professor.json
                          (system of record, one per professor)
                                        │
                          python_vector_store.py
                    dedupe across sources → embed → upsert
                                        │
                         chroma_db/  (shared collection,
                        professor_id tagged in metadata)
                                        │
                    ┌───────────────────┴───────────────────┐
                    │              app.py (Streamlit)         │
                    │  Auth · Onboarding · Ask (RAG) · Browse  │
                    │      CFP matches · Chat history          │
                    └───────────────────┬───────────────────┘
                                        │
                    cfp_alerts.py + n8n_alerts.py
                    matched CFPs / notifications → n8n webhook → email

                          scheduler.py
              triggers code.py on a weekly schedule,
                  desktop notification per run
```

## Project structure

```
.
├── app.py                      # Streamlit UI: auth, onboarding, Ask/Browse/My Professors
├── code.py                     # Fetch pipeline — runs per professor, detects changes
├── python_vector_store.py      # Dedupes across sources, embeds into ChromaDB
├── auth.py                     # User accounts + each user's own tracked-professor list
├── professors.py               # Global professor registry (shared across users)
├── chat_store.py                # Per-user, per-chat conversation history
├── cfp_alerts.py                # Matches professor profiles against CFP venues
├── n8n_alerts.py                 # Bundles + sends notification emails via n8n webhook
├── scheduler.py                 # Weekly autonomous trigger + desktop notifications
├── migrate_user_professors.py   # One-time migration script (see Open Items below)
├── requirements.txt
├── .env.example
└── data/
    ├── professors.json                  # Global professor registry
    ├── professors/<professor_id>/
    │   └── professor.json               # One professor's full fetched data
    ├── users.json                       # Accounts + each user's own subscriptions
    ├── chats.json                       # Per-user chat history
    ├── cfp_venues.json                  # Manually curated CFP list (no free API exists)
    └── cfp_alerts_sent.json             # De-dup log for already-sent CFP alerts
```

## Setup

```bash
git clone <this-repo>
cd <this-repo>
python -m venv venv
source venv/bin/activate      # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes, for chatbot answers | Powers the RAG chatbot's generated answers via OpenRouter |
| `OPENALEX_EMAIL` | Recommended | OpenAlex's "polite pool" — higher rate limits when you provide a contact email |
| `SEMANTIC_SCHOLAR_API_KEY` | Optional | Higher rate limits on Semantic Scholar's API |
| `N8N_WEBHOOK_URL` | Optional | Where citation/new-paper and CFP alert emails get sent |
| `N8N_DRY_RUN` | Optional | Set to `1` to print alert payloads instead of POSTing to n8n, for debugging |

## Usage

**First run / normal use:**
```bash
streamlit run app.py
```
Sign up, log in, and add your first professor (by name and, ideally, their
Google Scholar profile URL). The app will fetch and build the knowledge
base automatically on first use — this can take a few minutes depending
on how many publications the professor has.

**Manually re-fetch every tracked professor:**
```bash
python code.py
```
This re-fetches all professors in the global registry, detects new
papers/citation changes, and rebuilds the ChromaDB index.

**Run the autonomous weekly scheduler:**
```bash
python scheduler.py
```
Configure `RUN_DAY`/`RUN_TIME` at the top of the file for your preferred
schedule, or set `TEST_MODE_INTERVAL_MINUTES` to a small number to test
it fires correctly without waiting a week.

## Known limitations / open items

- **ChromaDB needs a manual rebuild after certain schema changes.** Since
  `python_vector_store.py` uses `upsert()`, old-format entries aren't
  automatically cleaned up when the document/metadata schema changes.
  Delete `chroma_db/` and rerun `code.py` if papers look stale or
  duplicated after an update.
- **The n8n webhook URL may need switching** from n8n's test endpoint to
  its production endpoint once the workflow is confirmed activated (test
  endpoints only fire once while the workflow is open in the n8n editor).
- **`data/cfp_venues.json` is manually curated** — no free, reliable CFP
  API exists, so new venues/deadlines need to be added by hand.
- **Research interests only come from OpenAlex** in the merged profile.
  If OpenAlex fails to match a professor, CFP matching and the displayed
  research interests will be empty even if other sources returned data.
- **`migrate_user_professors.py`** is a one-time script for migrating off
  an earlier storage format (`data/user_professors.json`) — run it once
  if upgrading from that version, then it can be deleted along with the
  old file.

## Notes for future development

- Don't add functions to fix an import error without confirming they
  don't already exist elsewhere under a different name — adapt calling
  code to what's actually there first.
- Keep fixes scoped to what's broken; avoid rewriting unrelated logic in
  the same change.
- Any change to what gets embedded or stored as Chroma metadata requires
  a full `chroma_db/` rebuild — `upsert()` never removes stale fields
  from old entries.