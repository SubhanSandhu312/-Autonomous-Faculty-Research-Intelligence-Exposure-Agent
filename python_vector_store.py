"""
python_vector_store.py

Module 2 -- Knowledge Base (multi-professor).

Previously this read a single hardcoded data/professor.json. It now loops
through EVERY professor in the registry (professors.py) and embeds each
professor's papers from their own data/professors/<id>/professor.json
into ONE shared Chroma collection, tagging every paper's metadata with
professor_id and professor_name so app.py can filter Ask/Browse by
professor (all professors, or a chosen subset).

Nothing about the embedding format changes for a single professor -- this
is a strict generalization of the old single-file version.
"""

import json
import os
import hashlib
import chromadb

import professors

CHROMA_DIR = "chroma_db"
COLLECTION_NAME = "publications"


def load_professor_data(professor_id):
    data_dir = professors.get_professor_data_dir(professor_id)
    path = os.path.join(data_dir, "professor.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def collect_all_papers(data):
    all_papers = []

    for source in ["openalex", "semantic_scholar", "scholarly_fallback"]:
        source_data = data.get(source)
        if source_data:
            for paper in source_data.get("papers", []):
                paper = dict(paper)
                paper["source"] = source
                all_papers.append(paper)

    for paper in data.get("arxiv", []):
        paper = dict(paper)
        paper["source"] = "arxiv"
        # arxiv entries use "published" instead of "publication_date" and
        # have a "link" (the arxiv.org abstract page) instead of a doi --
        # normalize so make_metadata()/make_document() can treat every
        # source the same way.
        if not paper.get("publication_date") and paper.get("published"):
            paper["publication_date"] = paper["published"]
        all_papers.append(paper)

    return all_papers


def dedupe_papers(papers):
    deduped = {}
    for paper in papers:
        title = (paper.get("title") or "").strip().lower()
        if not title:
            continue
        if title not in deduped or paper.get("abstract"):
            deduped[title] = paper
    return list(deduped.values())


def make_id(professor_id, title):
    # Prefixed with professor_id so the SAME paper title tracked under two
    # different professors (e.g. co-authored work) gets two distinct
    # Chroma entries instead of one overwriting the other.
    basis = f"{professor_id}:{title.strip().lower()}"
    return hashlib.md5(basis.encode("utf-8")).hexdigest()


def make_document(paper):
    title = paper.get("title") or ""
    authors = paper.get("authors")
    authors_text = ", ".join(authors) if isinstance(authors, list) else (authors or "")
    abstract = paper.get("abstract") or paper.get("summary") or ""
    published = paper.get("publication_date") or (str(paper.get("year")) if paper.get("year") else "")
    venue = paper.get("venue") or ""
    # Published date and venue are folded into the embedded document text
    # (not just metadata) so the RAG chatbot can actually answer
    # "when was X published" / "what did they publish in venue Y" style
    # questions from the retrieved excerpt itself, not just show it as a
    # side field.
    return (
        f"Title: {title}\n"
        f"Authors: {authors_text}\n"
        f"Published: {published}\n"
        f"Venue: {venue}\n"
        f"Abstract: {abstract}"
    )


def make_link(paper):
    """Best available URL to the actual paper, in priority order:
    DOI > direct PDF > source page link (e.g. the arxiv abstract page).
    Not every source gives a DOI -- arxiv papers and the scholarly
    fallback never have one -- so without this fallback those papers
    showed no link at all in the UI."""
    doi = paper.get("doi")
    if doi:
        return doi if str(doi).startswith("http") else f"https://doi.org/{doi}"
    if paper.get("pdf_url"):
        return paper["pdf_url"]
    if paper.get("link"):
        return paper["link"]
    return ""


def make_metadata(paper, professor_id, professor_name):
    authors = paper.get("authors")
    authors_text = ", ".join(authors) if isinstance(authors, list) else (authors or "")
    fields = paper.get("fields_of_study")
    fields_text = ", ".join(fields) if isinstance(fields, list) else ""

    return {
        "title": paper.get("title") or "",
        "authors": authors_text,
        "year": paper.get("year") or 0,
        "publication_date": paper.get("publication_date") or "",
        "citations": paper.get("citations") or 0,
        "venue": paper.get("venue") or "",
        "doi": paper.get("doi") or "",
        "pdf_url": paper.get("pdf_url") or "",
        "link": make_link(paper),
        "source": paper.get("source") or "",
        "fields_of_study": fields_text,
        "last_updated": paper.get("last_updated") or "",
        "is_new_paper": bool(paper.get("is_new_paper")),
        "citation_alert": bool(paper.get("citation_alert")),
        "professor_id": professor_id,
        "professor_name": professor_name,
    }


def build_collection():
    """Rebuilds the shared collection from EVERY professor in the
    registry, not just one. Safe to call repeatedly (upsert)."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(COLLECTION_NAME)

    all_professors = professors.get_all_professors()

    if not all_professors:
        print("No professors registered yet -- nothing to embed.")
        return collection

    total_upserted = 0

    for professor in all_professors:
        professor_id = professor["id"]
        professor_name = professor["name"]

        data = load_professor_data(professor_id)
        if not data:
            print(f"  Skipping {professor_name} -- no professor.json yet.")
            continue

        papers = collect_all_papers(data)
        papers = dedupe_papers(papers)

        if not papers:
            print(f"  Skipping {professor_name} -- no papers found.")
            continue

        ids = [make_id(professor_id, p["title"]) for p in papers]
        documents = [make_document(p) for p in papers]
        metadatas = [make_metadata(p, professor_id, professor_name) for p in papers]

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        total_upserted += len(ids)
        print(f"  {professor_name}: upserted {len(ids)} deduped papers")

    print(
        f"SUCCESS: upserted {total_upserted} papers total into "
        f"'{COLLECTION_NAME}' across {len(all_professors)} professor(s)"
    )
    return collection


def query_publications(question, n_results=5, professor_ids=None):
    """professor_ids: optional list of professor_id strings to restrict
    the search to (e.g. from a Streamlit multiselect/selectbox in
    app.py). None or empty = search across every professor."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_collection(COLLECTION_NAME)

    query_kwargs = {"query_texts": [question], "n_results": n_results}
    if professor_ids:
        if len(professor_ids) == 1:
            query_kwargs["where"] = {"professor_id": professor_ids[0]}
        else:
            query_kwargs["where"] = {"professor_id": {"$in": professor_ids}}

    results = collection.query(**query_kwargs)

    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        print(
            f"{i+1}. {meta['title']} ({meta['year']}) - {meta['citations']} citations "
            f"[{meta.get('professor_name', '')}]"
        )

    return results


if __name__ == "__main__":
    build_collection()
    print("\nTest query:")
    query_publications("deep learning research")