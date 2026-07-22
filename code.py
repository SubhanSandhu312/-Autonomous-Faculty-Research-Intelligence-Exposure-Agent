import requests
import xml.etree.ElementTree as ET
import json
import os
import time
import hashlib
import datetime
from scholarly import scholarly
from dotenv import load_dotenv

from python_vector_store import build_collection, query_publications
# from n8n_alerts import trigger_n8n_alerts, trigger_cfp_alerts
# from email_alerts import send_citation_update_emails, send_cfp_alert_emails
import email_alerts
import auth

from cfp_alerts import get_matched_cfps
import professors

load_dotenv()

CONTACT_EMAIL = os.environ.get("OPENALEX_EMAIL", "your_email@example.com")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")

MAX_OPENALEX_WORKS = 100
MAX_SEMANTIC_SCHOLAR_PAPERS = 100
MAX_ARXIV_RESULTS = 100
MAX_SCHOLARLY_PAPERS = 100
CITATION_ALERT_THRESHOLD = 5


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return None
    positions = []
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions)


def get_openalex_data(author_name, max_works=100):
    try:
        search = requests.get(
            "https://api.openalex.org/authors",
            params={"search": author_name, "per_page": 1, "mailto": CONTACT_EMAIL},
            timeout=15
        )
        search.raise_for_status()
        results = search.json().get("results", [])
    except requests.RequestException as e:
        print(f"FAILED: OpenAlex author search error: {e}")
        return None

    if not results:
        print(f"FAILED: OpenAlex found no author matching '{author_name}'")
        return None

    author = results[0]
    author_id = author["id"].split("/")[-1]
    print(f"SUCCESS: OpenAlex matched author '{author['display_name']}' ({author_id})")

    select_fields = ",".join([
        "id", "title", "publication_year", "publication_date", "cited_by_count",
        "doi", "primary_location", "best_oa_location", "open_access",
        "authorships", "concepts", "abstract_inverted_index",
        "referenced_works"
    ])

    works = []
    cursor = "*"
    try:
        while len(works) < max_works:
            resp = requests.get(
                "https://api.openalex.org/works",
                params={
                    "filter": f"author.id:{author_id}",
                    "per-page": 50,
                    "cursor": cursor,
                    "select": select_fields,
                    "mailto": CONTACT_EMAIL
                },
                timeout=15
            )
            resp.raise_for_status()
            page = resp.json()

            for w in page.get("results", []):
                primary_location = w.get("primary_location") or {}
                best_oa = w.get("best_oa_location") or {}
                open_access = w.get("open_access") or {}

                authors = [
                    (a.get("author") or {}).get("display_name")
                    for a in w.get("authorships", [])
                ]
                fields_of_study = [c.get("display_name") for c in w.get("concepts", [])]
                pdf_url = best_oa.get("pdf_url") or open_access.get("oa_url")
                references = w.get("referenced_works", [])

                works.append({
                    "title": w.get("title"),
                    "year": w.get("publication_year"),
                    "publication_date": w.get("publication_date"),
                    "citations": w.get("cited_by_count"),
                    "doi": w.get("doi"),
                    "venue": (primary_location.get("source") or {}).get("display_name"),
                    "authors": authors,
                    "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
                    "fields_of_study": fields_of_study,
                    "pdf_url": pdf_url,
                    # OpenAlex's own work page -- always present, so it's a
                    # reliable fallback link for the rare paper that has
                    # neither a doi nor an open-access pdf.
                    "link": w.get("id"),
                    "references_count": len(references),
                    "references": references[:20]
                })

            cursor = page.get("meta", {}).get("next_cursor")
            if not cursor or not page.get("results"):
                break
    except requests.RequestException as e:
        print(f"FAILED: OpenAlex works fetch error (kept {len(works)} so far): {e}")

    print(f"SUCCESS: OpenAlex returned {len(works)} works")

    ids = author.get("ids") or {}
    orcid = ids.get("orcid")
    x_concepts = author.get("x_concepts") or []
    research_interests = [c.get("display_name") for c in x_concepts[:10]]

    return {
        "name": author.get("display_name"),
        "affiliation": ((author.get("last_known_institutions") or [{}])[0] or {}).get("display_name"),
        "orcid": orcid,
        "homepage": None,
        "research_interests": research_interests,
        "citations": author.get("cited_by_count"),
        "hindex": (author.get("summary_stats") or {}).get("h_index"),
        "works_count": author.get("works_count"),
        "papers": works[:max_works]
    }


def get_semantic_scholar_data(author_name, max_papers=100):
    headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    try:
        search = requests.get(
            "https://api.semanticscholar.org/graph/v1/author/search",
            params={
                "query": author_name,
                "fields": "name,affiliations,paperCount,citationCount,hIndex,homepage,externalIds"
            },
            headers=headers,
            timeout=15
        )
        search.raise_for_status()
        results = search.json().get("data", [])
    except requests.RequestException as e:
        print(f"FAILED: Semantic Scholar author search error: {e}")
        return None

    if not results:
        print(f"FAILED: Semantic Scholar found no author matching '{author_name}'")
        return None

    author = results[0]
    author_id = author["authorId"]
    print(f"SUCCESS: Semantic Scholar matched author '{author['name']}' ({author_id})")

    fields = ",".join([
        "paperId", "title", "year", "publicationDate", "citationCount", "abstract", "tldr",
        "authors", "fieldsOfStudy", "s2FieldsOfStudy", "openAccessPdf",
        "externalIds", "venue", "references.title", "references.year"
    ])

    try:
        papers_resp = requests.get(
            f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers",
            params={"fields": fields, "limit": max_papers},
            headers=headers,
            timeout=15
        )
        papers_resp.raise_for_status()
        raw_papers = papers_resp.json().get("data", [])
    except requests.RequestException as e:
        print(f"FAILED: Semantic Scholar papers fetch error: {e}")
        raw_papers = []

    papers = []
    for p in raw_papers:
        authors = [a.get("name") for a in (p.get("authors") or [])]
        fields_of_study = p.get("fieldsOfStudy") or []
        pdf_url = (p.get("openAccessPdf") or {}).get("url")
        # externalIds was already being requested from the API but its DOI
        # was never actually read into the paper dict -- every paper had a
        # blank doi field even when Semantic Scholar had one.
        paper_doi = (p.get("externalIds") or {}).get("DOI")
        paper_id = p.get("paperId")
        references = [
            {"title": r.get("title"), "year": r.get("year")}
            for r in (p.get("references") or [])[:20]
        ]

        papers.append({
            "title": p.get("title"),
            "year": p.get("year"),
            "publication_date": p.get("publicationDate"),
            "citations": p.get("citationCount"),
            "doi": paper_doi,
            "abstract": p.get("abstract"),
            "tldr": (p.get("tldr") or {}).get("text"),
            "authors": authors,
            "fields_of_study": fields_of_study,
            "pdf_url": pdf_url,
            # Semantic Scholar's own paper page -- every paper has one, so
            # it's the last-resort fallback when there's no doi and no
            # open-access pdf.
            "link": f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None,
            "references_count": len(p.get("references") or []),
            "references": references
        })

    print(f"SUCCESS: Semantic Scholar returned {len(papers)} papers")

    external_ids = author.get("externalIds") or {}

    return {
        "name": author.get("name"),
        "affiliation": ", ".join(author.get("affiliations") or []),
        "orcid": external_ids.get("ORCID"),
        "homepage": author.get("homepage"),
        "research_interests": [],
        "citations": author.get("citationCount"),
        "hindex": author.get("hIndex"),
        "papers": papers
    }


def get_arxiv_data(author_name, max_results=20):
    query_name = author_name.replace(" ", "_")
    try:
        resp = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"au:{query_name}", "max_results": max_results},
            timeout=15
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"FAILED: arXiv fetch error: {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ns)

    preprints = []
    for entry in entries:
        authors = [a.findtext("atom:name", default="", namespaces=ns) for a in entry.findall("atom:author", ns)]
        preprints.append({
            "title": entry.findtext("atom:title", default="", namespaces=ns).strip(),
            "published": entry.findtext("atom:published", default="", namespaces=ns),
            "summary": entry.findtext("atom:summary", default="", namespaces=ns).strip(),
            "authors": authors,
            "link": entry.findtext("atom:id", default="", namespaces=ns)
        })

    print(f"SUCCESS: arXiv returned {len(preprints)} preprints")
    return preprints


def get_scholar_data(name_or_url, max_papers=50):
    print("Falling back to scholarly (Google Scholar)...")
    import re

    match = re.search(r"user=([\w-]+)", name_or_url)

    try:
        if match:
            author = scholarly.search_author_id(match.group(1))
        else:
            author = next(scholarly.search_author(name_or_url))
    except (StopIteration, Exception) as e:
        print(f"FAILED: scholarly could not find author: {e}")
        return None

    try:
        author = scholarly.fill(author)
    except Exception as e:
        print(f"FAILED: scholarly could not load author details: {e}")
        return None

    print(f"SUCCESS: scholarly matched author '{author.get('name')}'")

    papers = []
    for i, pub in enumerate(author["publications"][:max_papers], start=1):
        try:
            filled = scholarly.fill(pub)
            pub_year = filled["bib"].get("pub_year")
            papers.append({
                "title": filled["bib"].get("title"),
                "year": pub_year,
                # scholarly never exposes a full publication date, only a
                # year -- store a year-precision ISO date anyway so every
                # paper across every source has SOME publication_date to
                # embed and filter on, instead of this source being the
                # one gap. It's coarser than OpenAlex/Semantic Scholar's
                # day-level dates, but still far more useful for RAG
                # questions like "what did they publish in 2022" than
                # nothing at all.
                "publication_date": f"{pub_year}-01-01" if pub_year else None,
                "citations": filled.get("num_citations"),
                "authors": filled["bib"].get("author"),
                "abstract": filled["bib"].get("abstract"),
                # scholarly usually exposes the publisher/listing page as
                # pub_url (not always present) -- last-resort link for a
                # source that never gives a doi or a direct pdf.
                "link": filled.get("pub_url") or filled.get("eprint_url")
            })
            print(f"  SUCCESS [{i}/{max_papers}]: {filled['bib'].get('title')}")
        except Exception as e:
            print(f"  FAILED [{i}/{max_papers}]: {e}")
        time.sleep(1.5)

    print(f"SUCCESS: scholarly returned {len(papers)} papers")

    return {
        "name": author.get("name"),
        "affiliation": author.get("affiliation"),
        "orcid": None,
        "homepage": None,
        "research_interests": author.get("interests") or [],
        "citations": author.get("citedby"),
        "hindex": author.get("hindex"),
        "papers": papers
    }


def fingerprint(paper):
    relevant = {k: v for k, v in paper.items() if k not in ("last_checked", "last_updated")}
    encoded = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stamp_papers(papers, previous_papers, source_label=""):
    today = datetime.date.today().isoformat()
    prev_lookup = {(p.get("title") or "").strip().lower(): p for p in (previous_papers or [])}
    notifications = []

    for paper in papers:
        new_fp = fingerprint(paper)
        key = (paper.get("title") or "").strip().lower()
        prev = prev_lookup.get(key)

        is_new_paper = prev is None
        citation_alert = False

        if prev:
            if prev.get("_fingerprint") == new_fp:
                paper["last_updated"] = prev.get("last_updated", today)
            else:
                paper["last_updated"] = today

            prev_citations = prev.get("citations")
            new_citations = paper.get("citations")
            if isinstance(prev_citations, (int, float)) and isinstance(new_citations, (int, float)):
                increase = new_citations - prev_citations
                if increase >= CITATION_ALERT_THRESHOLD:
                    citation_alert = True
                    notifications.append({
                        "type": "citation_alert",
                        "source": source_label,
                        "title": paper.get("title"),
                        "old_citations": prev_citations,
                        "new_citations": new_citations,
                        "increase": increase
                    })
        else:
            paper["last_updated"] = today
            notifications.append({
                "type": "new_paper",
                "source": source_label,
                "title": paper.get("title"),
                "year": paper.get("year"),
                "citations": paper.get("citations")
            })

        paper["last_checked"] = today
        paper["is_new_paper"] = is_new_paper
        paper["citation_alert"] = citation_alert
        paper["_fingerprint"] = new_fp

    return papers, notifications


def stamp_source(source_data, previous_source_data, source_label=""):
    if not source_data:
        return source_data, []

    today = datetime.date.today().isoformat()
    previous_source_data = previous_source_data or {}

    papers, notifications = stamp_papers(
        source_data.get("papers", []),
        previous_source_data.get("papers", []),
        source_label
    )
    source_data["papers"] = papers

    stats_fields = {
        k: v for k, v in source_data.items()
        if k in ("citations", "hindex", "works_count")
    }
    stats_fp = hashlib.sha256(
        json.dumps(stats_fields, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    if previous_source_data.get("_stats_fingerprint") == stats_fp:
        source_data["stats_last_updated"] = previous_source_data.get("stats_last_updated", today)
    else:
        source_data["stats_last_updated"] = today

    source_data["stats_last_checked"] = today
    source_data["_stats_fingerprint"] = stats_fp

    return source_data, notifications


def strip_identity_fields(source_data):
    if not source_data:
        return source_data
    for field in ("name", "affiliation", "orcid", "homepage", "research_interests"):
        source_data.pop(field, None)
    return source_data


def stamp_profile(profile, previous_profile):
    today = datetime.date.today().isoformat()
    previous_profile = previous_profile or {}

    fp = hashlib.sha256(
        json.dumps(
            {k: v for k, v in profile.items() if k not in ("last_checked", "last_updated")},
            sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()

    if previous_profile.get("_fingerprint") == fp:
        profile["last_updated"] = previous_profile.get("last_updated", today)
    else:
        profile["last_updated"] = today

    profile["last_checked"] = today
    profile["_fingerprint"] = fp
    return profile


def load_previous_data(data_path):
    if not os.path.exists(data_path):
        return {}
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: could not read previous data file, treating as first run: {e}")
        return {}


def build_merged_profile(sources):
    profile = {"name": None, "affiliation": None, "orcid": None, "homepage": None, "research_interests": []}
    for source in sources:
        if not source:
            continue
        for field in profile:
            if not profile[field] and source.get(field):
                profile[field] = source.get(field)
    return profile


def print_paper(i, p):
    print(f"  {i}. {p.get('title')} ({p.get('year')})")
    print(f"     Citations: {p.get('citations')}")
    if p.get("authors"):
        print(f"     Authors: {p.get('authors')}")
    if p.get("publication_date"):
        print(f"     Published: {p.get('publication_date')}")
    if p.get("fields_of_study"):
        print(f"     Fields of study: {p.get('fields_of_study')}")
    if p.get("pdf_url"):
        print(f"     PDF: {p.get('pdf_url')}")
    if p.get("references_count") is not None:
        print(f"     References: {p.get('references_count')}")
    if p.get("abstract"):
        print(f"     Abstract: {p.get('abstract')}")
    if p.get("tldr"):
        print(f"     TLDR: {p.get('tldr')}")
    if p.get("last_checked"):
        print(f"     Last checked: {p.get('last_checked')} | Last updated: {p.get('last_updated')}")
    if p.get("is_new_paper"):
        print(f"     NEW PAPER")
    if p.get("citation_alert"):
        print(f"     CITATION ALERT (gained {CITATION_ALERT_THRESHOLD}+ citations since last check)")


def print_section(title, data):
    print(f"\n===== {title} =====")
    if not data:
        print("No data.")
        print(f"===== END {title} =====\n")
        return
    for key, value in data.items():
        if key == "papers":
            print(f"Papers ({len(value)}):")
            for i, p in enumerate(value, start=1):
                print_paper(i, p)
        elif key.startswith("_"):
            continue
        else:
            print(f"{key}: {value}")
    print(f"===== END {title} =====\n")


def print_notifications(notifications):
    print(f"\n===== NOTIFICATIONS ({len(notifications)}) =====")
    if not notifications:
        print("No new papers or citation spikes since the last run.")
    for n in notifications:
        if n["type"] == "new_paper":
            print(f"[{n['source']}] New paper found: '{n['title']}' ({n.get('year')}), {n.get('citations')} citations")
        elif n["type"] == "citation_alert":
            print(f"[{n['source']}] Citation increase: '{n['title']}' went from "
                  f"{n['old_citations']} to {n['new_citations']} citations (+{n['increase']})")
    print("===== END NOTIFICATIONS =====\n")


def process_professor(professor):
    """Runs the full fetch -> stamp -> save pipeline for ONE professor.
    Returns (profile, all_notifications, all_papers_for_cfp_matching).

    Writes ONLY to that professor's own
    data/professors/<id>/professor.json. There is no more legacy
    data/professor.json bridge -- python_vector_store.py now reads every
    professor's own file directly, so the old single-file compatibility
    copy is no longer needed."""
    author_name = professor["name"]
    scholar_url = professor.get("scholar_url") or ""
    data_dir = professors.get_professor_data_dir(professor["id"])
    data_path = os.path.join(data_dir, "professor.json")

    print(f"\n########## Processing professor: {author_name} ##########")

    previous = load_previous_data(data_path)
    all_notifications = []

    print("Fetching from OpenAlex...")
    openalex_data = get_openalex_data(author_name, max_works=MAX_OPENALEX_WORKS)
    openalex_data, notifications = stamp_source(openalex_data, previous.get("openalex"), "openalex")
    all_notifications.extend(notifications)
    print_section("OPENALEX", openalex_data)

    print("Fetching from Semantic Scholar...")
    semantic_data = get_semantic_scholar_data(author_name, max_papers=MAX_SEMANTIC_SCHOLAR_PAPERS)
    semantic_data, notifications = stamp_source(semantic_data, previous.get("semantic_scholar"), "semantic_scholar")
    all_notifications.extend(notifications)
    print_section("SEMANTIC SCHOLAR", semantic_data)

    print("Fetching from arXiv...")
    arxiv_data = get_arxiv_data(author_name, max_results=MAX_ARXIV_RESULTS)
    arxiv_data, notifications = stamp_papers(arxiv_data, previous.get("arxiv", []), "arxiv")
    all_notifications.extend(notifications)
    print(f"\n===== ARXIV ({len(arxiv_data)} preprints) =====")
    for i, p in enumerate(arxiv_data, start=1):
        print_paper(i, p)
    print("===== END ARXIV =====\n")

    scholar_data = None
    if not openalex_data and not semantic_data:
        scholar_data = get_scholar_data(scholar_url or author_name, max_papers=MAX_SCHOLARLY_PAPERS)
        scholar_data, notifications = stamp_source(scholar_data, previous.get("scholarly_fallback"), "scholarly")
        all_notifications.extend(notifications)
        print_section("SCHOLARLY (fallback)", scholar_data)
    else:
        print("Skipping scholarly fallback - OpenAlex/Semantic Scholar already returned data.")

    profile = build_merged_profile([openalex_data, semantic_data, scholar_data])
    profile = stamp_profile(profile, previous.get("profile"))
    print_section("MERGED PROFILE", profile)

    print_notifications(all_notifications)

    all_papers_for_cfp_matching = (
        (openalex_data or {}).get("papers", [])
        + (semantic_data or {}).get("papers", [])
        + (scholar_data or {}).get("papers", [])
    )

    strip_identity_fields(openalex_data)
    strip_identity_fields(semantic_data)
    strip_identity_fields(scholar_data)

    combined = {
        "professor_id": professor["id"],
        "professor_name": author_name,
        "profile": profile,
        "openalex": openalex_data,
        "semantic_scholar": semantic_data,
        "arxiv": arxiv_data,
        "scholarly_fallback": scholar_data,
        "has_notifications": len(all_notifications) > 0,
        "notifications": all_notifications
    }

    os.makedirs(data_dir, exist_ok=True)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=4, ensure_ascii=False)

    print(f"Done with {author_name}! Saved to {data_path}")

    return profile, all_notifications, all_papers_for_cfp_matching


def main():
    all_professors = professors.get_all_professors()

    if not all_professors:
        print("No professors registered yet -- nothing to fetch. "
              "Add a professor from the app first.")
        return

    print(f"Tracking {len(all_professors)} professor(s) across all users.")

    results_by_professor = {}

    for professor in all_professors:
        profile, notifications, papers_for_cfp = process_professor(professor)
        results_by_professor[professor["id"]] = {
            "profile": profile,
            "notifications": notifications,
            "papers": papers_for_cfp,
            "name": professor["name"],
        }

    # --- Build per-user notification bundles across ALL professors each
    # user tracks, so someone following 3 professors gets ONE combined
    # citation-update email and ONE combined CFP email this run, not three
    # of each. ---
    user_citation_bundles = {}  # user_email -> [{"professor_name", "new_papers", "citation_alerts"}, ...]
    user_cfp_bundles = {}       # user_email -> [{"professor_name", "cfps"}, ...]

    for pid, result in results_by_professor.items():
        subscribers = professors.get_subscribers_for_professor(pid)
        if not subscribers:
            continue

        new_papers = [n["title"] for n in result["notifications"] if n["type"] == "new_paper"]
        citation_alerts = [n["title"] for n in result["notifications"] if n["type"] == "citation_alert"]
        matched_cfps = get_matched_cfps(result["profile"], result["papers"])

        for user_email in subscribers:
            if new_papers or citation_alerts:
                user_citation_bundles.setdefault(user_email, []).append({
                    "professor_name": result["name"],
                    "new_papers": new_papers,
                    "citation_alerts": citation_alerts,
                })
            if matched_cfps:
                user_cfp_bundles.setdefault(user_email, []).append({
                    "professor_name": result["name"],
                    "cfps": matched_cfps,
                })

    # trigger_n8n_alerts(user_citation_bundles)
    # trigger_cfp_alerts(user_cfp_bundles)

    names = {s["email"]: s["name"] for s in auth.get_all_subscribers()}
    email_alerts.send_citation_update_emails(user_citation_bundles, names)
    email_alerts.send_cfp_alert_emails(user_cfp_bundles, names)
 
    # Everyone who tracks at least one professor but got nothing in
    # EITHER bundle above -- gated inside send_no_update_emails() by the
    # NOTIFY_ON_NO_UPDATES flag in email_alerts.py.
    all_subscribed_emails = set()
    for pid in results_by_professor:
        all_subscribed_emails.update(professors.get_subscribers_for_professor(pid))
 
    users_with_no_updates = [
        email for email in all_subscribed_emails
        if email not in user_citation_bundles and email not in user_cfp_bundles
    ]
    email_alerts.send_no_update_emails(users_with_no_updates, names)
    print("All professors processed.")

    print("Updating ChromaDB...")
    build_collection()

    print("\nTesting retrieval...")
    query_publications("deep learning research")


if __name__ == "__main__":
    main()