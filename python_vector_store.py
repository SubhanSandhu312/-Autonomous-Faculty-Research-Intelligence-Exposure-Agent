import json
import hashlib
import chromadb

DATA_PATH = "data/professor.json"
CHROMA_DIR = "chroma_db"
COLLECTION_NAME = "publications"


def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_all_papers(data):
    all_papers = []

    for source in ["openalex", "semantic_scholar", "scholarly_fallback"]:
        source_data = data.get(source)
        if source_data:
            for paper in source_data.get("papers", []):
                paper["source"] = source
                all_papers.append(paper)

    for paper in data.get("arxiv", []):
        paper["source"] = "arxiv"
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


def make_id(title):
    return hashlib.md5(title.strip().lower().encode("utf-8")).hexdigest()


def make_document(paper):
    title = paper.get("title") or ""
    authors = paper.get("authors")
    authors_text = ", ".join(authors) if isinstance(authors, list) else (authors or "")
    abstract = paper.get("abstract") or paper.get("summary") or ""
    return f"Title: {title}\nAuthors: {authors_text}\nAbstract: {abstract}"


def make_metadata(paper):
    authors = paper.get("authors")
    authors_text = ", ".join(authors) if isinstance(authors, list) else (authors or "")
    fields = paper.get("fields_of_study")
    fields_text = ", ".join(fields) if isinstance(fields, list) else ""

    return {
        "title": paper.get("title") or "",
        "authors": authors_text,
        "year": paper.get("year") or 0,
        "citations": paper.get("citations") or 0,
        "venue": paper.get("venue") or "",
        "doi": paper.get("doi") or "",
        "pdf_url": paper.get("pdf_url") or "",
        "source": paper.get("source") or "",
        "fields_of_study": fields_text,
        "last_updated": paper.get("last_updated") or "",
        "is_new_paper": bool(paper.get("is_new_paper")),
        "citation_alert": bool(paper.get("citation_alert"))
    }


def build_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)     #builds database 
    collection = client.get_or_create_collection(COLLECTION_NAME)

    data = load_data()
    papers = collect_all_papers(data)
    papers = dedupe_papers(papers)

    ids = [make_id(p["title"]) for p in papers]
    documents = [make_document(p) for p in papers]
    metadatas = [make_metadata(p) for p in papers]

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    print(f"SUCCESS: upserted {len(ids)} deduped papers into '{COLLECTION_NAME}'")
    return collection


def query_publications(question, n_results=5):
    client = chromadb.PersistentClient(path=CHROMA_DIR)    #connects to database
    collection = client.get_collection(COLLECTION_NAME)

    results = collection.query(query_texts=[question], n_results=n_results)

    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        print(f"{i+1}. {meta['title']} ({meta['year']}) - {meta['citations']} citations")


if __name__ == "__main__":
    build_collection()
    print("\nTest query:")
    query_publications("deep learning research")

    