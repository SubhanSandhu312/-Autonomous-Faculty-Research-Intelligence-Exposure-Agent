import streamlit as st
import chromadb
import subprocess
import sys
import os
import json
from dotenv import load_dotenv
import auth

load_dotenv()

CHROMA_DIR = "chroma_db"
COLLECTION_NAME = "publications"
OPENROUTER_MODEL = "openrouter/free"
MAX_RELEVANT_DISTANCE = 1.5
DATA_PATH = "data/professor.json"

st.set_page_config(page_title="Faculty Research Portal", layout="wide")

# --- Login gate: nothing below this loads until the user is authenticated ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user_email = None

if not st.session_state.logged_in:
    st.title("Faculty Research Portal")
    login_tab, signup_tab = st.tabs(["Log In", "Sign Up"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In"):
                if auth.verify_user(email, password):
                    st.session_state.logged_in = True
                    st.session_state.user_email = email.strip().lower()
                    st.rerun()
                else:
                    st.error("Invalid email or password.")

    with signup_tab:
        with st.form("signup_form"):
            new_name = st.text_input("Name", key="signup_name")
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password (min 8 characters)", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm password", type="password", key="signup_confirm")
            if st.form_submit_button("Sign Up"):
                if new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    success, message = auth.register_user(new_email, new_password, new_name)
                    (st.success if success else st.error)(message)

    st.stop()

with st.sidebar:
    st.write(f"Signed in as **{st.session_state.user_email}**")
    if st.button("Log Out"):
        st.session_state.logged_in = False
        st.session_state.user_email = None
        st.rerun()
# --- End login gate ---


def load_data_file():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@st.cache_resource
def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_collection(COLLECTION_NAME)


def generate_answer(question, context_chunks):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    context_text = "\n\n".join(context_chunks)

    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[
            {"role": "system", "content": "Answer using only the provided research paper excerpts. Mention paper titles you used. If the excerpts don't answer the question, say so."},
            {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {question}"}
        ]
    )
    return response.choices[0].message.content


try:
    collection = get_collection()
except Exception:
    st.info("No data found yet. Running the pipeline to fetch and build it — this can take a few minutes.")
    with st.spinner("Fetching papers and building the database..."):
        try:
            subprocess.run([sys.executable, "code.py"], check=True)
        except subprocess.CalledProcessError:
            st.error("code.py failed to run. Check your terminal for the error.")
            st.stop()
    collection = get_collection()

st.title("Faculty Research Portal")

data_file = load_data_file()
profile = data_file.get("profile", {})

if profile:
    with st.container(border=True):
        st.subheader(profile.get("name") or "Unknown Researcher")
        if profile.get("affiliation"):
            st.write(profile["affiliation"])
        if profile.get("orcid"):
            st.write(f"ORCID: {profile['orcid']}")
        if profile.get("homepage"):
            st.markdown(f"[Homepage]({profile['homepage']})")
        interests = profile.get("research_interests")
        if interests:
            st.write("Research interests: " + ", ".join(interests))

tab_ask, tab_browse = st.tabs(["Ask", "Browse Papers"])

with tab_ask:
    question = st.text_input("Ask a question about the professor's research")

    if question:
        results = collection.query(query_texts=[question], n_results=5)
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        if not distances or min(distances) > MAX_RELEVANT_DISTANCE:
            st.write("No closely related papers found for that question.")
        else:
            answer = generate_answer(question, documents)

            if answer:
                st.subheader("Answer")
                st.write(answer)
            else:
                st.caption("Set OPENROUTER_API_KEY to generate a written answer. Showing matching papers below.")

            st.subheader("Related Papers")
            for meta in metadatas:
                with st.expander(meta["title"]):
                    st.write(f"Authors: {meta['authors']}")
                    st.write(f"Source: {meta['source']}")
                    if meta.get("doi"):
                        doi = meta["doi"]
                        doi_url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
                        st.markdown(f"[DOI: {doi}]({doi_url})")
                    if meta.get("pdf_url"):
                        st.markdown(f"[View PDF]({meta['pdf_url']})")

with tab_browse:
    all_data = collection.get()
    papers = all_data["metadatas"]

    col1, col2 = st.columns([1, 2])
    with col1:
        sources = sorted(set(p["source"] for p in papers))
        selected_source = st.selectbox("Source", ["All"] + sources)
    with col2:
        search_text = st.text_input("Search by title")

    filtered = papers
    if selected_source != "All":
        filtered = [p for p in filtered if p["source"] == selected_source]
    if search_text:
        filtered = [p for p in filtered if search_text.lower() in p["title"].lower()]

    # Sorting still uses citation counts internally (most-cited first),
    # it's just no longer displayed anywhere.
    filtered = sorted(filtered, key=lambda p: p.get("citations", 0), reverse=True)

    st.write(f"{len(filtered)} papers")

    for paper in filtered:
        label = paper["title"]
        if paper.get("is_new_paper"):
            label += "  •  New"
        if paper.get("citation_alert"):
            label += "  •  Citation increase"

        with st.expander(label):
            st.write(f"Authors: {paper['authors']}")
            st.write(f"Venue: {paper.get('venue', '')}")
            st.write(f"Source: {paper['source']}")
            if paper.get("doi"):
                doi = paper["doi"]
                doi_url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
                st.markdown(f"[DOI: {doi}]({doi_url})")
            if paper.get("pdf_url"):
                st.markdown(f"[View PDF]({paper['pdf_url']})")