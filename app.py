import streamlit as st
import chromadb
import subprocess
import sys
import os
import json
import datetime
from dotenv import load_dotenv
import auth
import chat_store
import professors
import cfp_alerts

load_dotenv()

CHROMA_DIR = "chroma_db"
COLLECTION_NAME = "publications"
OPENROUTER_MODEL = "openrouter/free"
MAX_RELEVANT_DISTANCE = 1.5

st.set_page_config(page_title="Faculty Research Portal", layout="wide")


def add_and_track_professor(user_email, name, scholar_url):
    """Adds a professor to the global registry (or reuses the existing
    entry if one already matches this name/Scholar URL) and subscribes
    this user to it. Returns (professor_id, was_duplicate) so the caller
    can tell the user whether this reused an existing profile (no
    re-fetching needed) or created a brand new one."""
    professor_id = professors.make_professor_id(name, scholar_url)
    was_duplicate = professors.get_professor(professor_id) is not None
    professors.add_professor(name, scholar_url)
    professors.subscribe(user_email, professor_id)
    return professor_id, was_duplicate



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

    st.divider()

    if "current_chat_id" not in st.session_state:
        st.session_state.current_chat_id = None

    if st.button("+ New Chat", use_container_width=True):
        # Don't persist anything yet — a chat only gets saved (and shows up
        # in History) once the first message is actually sent. Otherwise
        # every click leaves an empty "New Chat" stub in the history list.
        st.session_state.current_chat_id = None
        st.rerun()

    st.caption("History")
    user_chats = chat_store.get_user_chats(st.session_state.user_email)

    if not user_chats:
        st.caption("No conversations yet.")
    else:
        for c in user_chats:
            is_current = c["chat_id"] == st.session_state.current_chat_id
            label = ("➤ " if is_current else "") + c["title"]
            if st.button(label, key=f"history_{c['chat_id']}", use_container_width=True):
                st.session_state.current_chat_id = c["chat_id"]
                st.rerun()
# --- End login gate ---


# --- Onboarding gate: there is no default/hardcoded professor anymore.
# Every user must add at least one professor before they see Ask/Browse —
# only after that does it make sense to fetch publications and build the
# knowledge base. ---
my_professors = professors.get_user_professors(st.session_state.user_email)

if not my_professors:
    st.title("Faculty Research Portal")
    st.subheader("Add your first professor to get started")
    st.caption(
        "Once you add a professor, we'll fetch their publications and set "
        "up the chatbot and paper browser for them. You can add more "
        "professors any time from the My Professors tab."
    )
    with st.form("first_professor_form"):
        prof_name = st.text_input("Professor Name *")
        prof_scholar_url = st.text_input("Google Scholar Profile URL")
        prof_email = st.text_input("Professor Email (optional)")
        prof_university = st.text_input("University (optional)")
        if st.form_submit_button("Add & Track"):
            if not prof_name.strip():
                st.error("Enter a professor name.")
            else:
                _, was_duplicate = add_and_track_professor(
                    st.session_state.user_email, prof_name, prof_scholar_url
                )
                if was_duplicate:
                    st.success(
                        f"{prof_name} is already being tracked in the system — "
                        f"you're now subscribed to their existing profile, no re-fetching needed."
                    )
                else:
                    st.success(f"Added {prof_name}. Fetching their publications now — this can take a few minutes.")
                st.rerun()
    st.stop()
# --- End onboarding gate ---


def load_professor_profile(professor_id):
    data_dir = professors.get_professor_data_dir(professor_id)
    path = os.path.join(data_dir, "professor.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("profile", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@st.cache_resource
def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_collection(COLLECTION_NAME)


def generate_answer(question, context_chunks, chat_history=None):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    context_text = "\n\n".join(context_chunks) if context_chunks else "(no paper excerpts retrieved for this question)"

    messages = [
        {"role": "system", "content": (
            "You are a research assistant with two jobs, depending on the question:\n"
            "1. If the question asks about a professor's research (papers, topics, "
            "findings), answer using ONLY the provided research paper excerpts below. "
            "Mention paper titles you used, and which professor each came from if more "
            "than one professor is represented in the excerpts. If the excerpts don't "
            "cover it, say so.\n"
            "2. If the question is instead about THIS CONVERSATION itself (e.g. 'what "
            "did I just ask', 'summarize what we've discussed', 'what was your first "
            "answer'), answer directly from the conversation history — you do not need "
            "paper excerpts for this, and there may be none provided."
        )}
    ]
    # Fold in the FULL prior history of this chat (not just the last few
    # turns) so follow-up questions can resolve against anything said
    # earlier in this same conversation, not just the most recent exchange.
    for turn in (chat_history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {question}"})

    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=messages
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
    get_collection.clear()
    collection = get_collection()

st.title("Faculty Research Portal")

# --- Professor selector: applies to both Ask and Browse Papers below ---
professor_options = {p["id"]: p["name"] for p in my_professors}
ALL_LABEL = "All my professors"
selector_labels = [ALL_LABEL] + list(professor_options.values())
selected_label = st.selectbox("Professor", selector_labels)
show_all = selected_label == ALL_LABEL

if show_all:
    selected_professor_ids = list(professor_options.keys())
else:
    selected_professor_ids = [pid for pid, name in professor_options.items() if name == selected_label]

# Show the profile card only when viewing exactly one professor — a
# merged profile across several professors wouldn't mean much.
if len(selected_professor_ids) == 1:
    profile = load_professor_profile(selected_professor_ids[0])
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

            # CFP matches for this professor -- same matching logic used
            # to decide alert emails (cfp_alerts.py), shown here live so
            # you don't have to wait for an email to see it.
            cfp_matches = cfp_alerts.get_upcoming_cfp_matches(profile)
            if cfp_matches:
                st.write(f"**Upcoming CFP matches ({len(cfp_matches)})**")
                for m in cfp_matches:
                    topics = ", ".join(m["matched_topics"]) if m["matched_topics"] else ""
                    line = f"- **{m['venue']}** — deadline {m['deadline']} ({m['days_left']} days left)"
                    if topics:
                        line += f" — matched: {topics}"
                    st.markdown(line)
                    if m.get("link"):
                        st.markdown(f"  [CFP link]({m['link']})")

tab_ask, tab_browse, tab_professors = st.tabs(["Ask", "Browse Papers", "My Professors"])

with tab_ask:
    time_range = st.selectbox(
        "Time range",
        ["Any time", "Last 6 months", "Last year", "Last 2 years", "Custom year range"]
    )

    current_year = datetime.date.today().year
    year_filter = None

    if time_range == "Last 6 months":
        # Metadata only carries publication year (see python_vector_store.py's
        # metadata schema), not a full date, so "last 6 months" is approximated
        # as the current year. If publication_date is later added to the
        # metadata, swap this for a real day-level cutoff.
        year_filter = {"year": {"$gte": current_year}}
    elif time_range == "Last year":
        year_filter = {"year": {"$gte": current_year - 1}}
    elif time_range == "Last 2 years":
        year_filter = {"year": {"$gte": current_year - 2}}
    elif time_range == "Custom year range":
        col_a, col_b = st.columns(2)
        with col_a:
            start_year = st.number_input("From year", min_value=1950, max_value=current_year, value=current_year - 1)
        with col_b:
            end_year = st.number_input("To year", min_value=1950, max_value=current_year, value=current_year)
        year_filter = {"$and": [{"year": {"$gte": int(start_year)}}, {"year": {"$lte": int(end_year)}}]}

    # Always scope to selected_professor_ids -- it's already correctly
    # populated in BOTH branches above (the user's full own list when
    # "All my professors" is chosen, or just the chosen subset otherwise).
    # Skipping this when show_all was true used to mean NO professor
    # filter at all, which pulled from the entire shared Chroma collection
    # across every user in the app, not just this user's own professors --
    # that was the actual bug behind seeing other users' professors.
    professor_filter = None
    if len(selected_professor_ids) == 1:
        professor_filter = {"professor_id": selected_professor_ids[0]}
    elif selected_professor_ids:
        professor_filter = {"professor_id": {"$in": selected_professor_ids}}

    clauses = [c for c in (year_filter, professor_filter) if c]
    if len(clauses) == 0:
        where_filter = None
    elif len(clauses) == 1:
        where_filter = clauses[0]
    else:
        where_filter = {"$and": clauses}

    # current_chat_id is None for a brand-new, not-yet-saved conversation —
    # nothing to load yet, that's expected, not an error state.
    if st.session_state.current_chat_id is None:
        prior_messages = []
    else:
        prior_messages = chat_store.get_chat_messages(st.session_state.user_email, st.session_state.current_chat_id)

    for turn in prior_messages:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    question = st.chat_input("Ask a question about the professor's research")

    if question:
        # First message of a brand-new chat: create (and thus persist) it
        # now, not before — this is the point it earns a real title and a
        # spot in the History list.
        if st.session_state.current_chat_id is None:
            st.session_state.current_chat_id = chat_store.create_chat(st.session_state.user_email)

        chat_store.append_message(st.session_state.user_email, st.session_state.current_chat_id, "user", question)

        with st.chat_message("user"):
            st.write(question)

        query_kwargs = {"query_texts": [question], "n_results": 5}
        if where_filter:
            query_kwargs["where"] = where_filter

        results = collection.query(**query_kwargs)
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        with st.chat_message("assistant"):
            is_relevant = bool(distances) and min(distances) <= MAX_RELEVANT_DISTANCE

            # Always call the LLM with the full conversation history — even
            # when no papers are relevant, e.g. meta-questions like "what
            # did I just ask" have nothing to do with paper content at all,
            # but still need an answer grounded in the chat history.
            answer = generate_answer(question, documents if is_relevant else [], prior_messages)
            answer_text = answer or "Set OPENROUTER_API_KEY to generate a written answer."
            st.write(answer_text)

            if is_relevant:
                st.subheader("Related Papers")
                for meta in metadatas:
                    expander_label = meta["title"]
                    if show_all or len(selected_professor_ids) > 1:
                        expander_label += f"  —  {meta.get('professor_name', '')}"
                    with st.expander(expander_label):
                        st.write(f"Authors: {meta['authors']}")
                        st.write(f"Source: {meta['source']}")
                        if meta.get("professor_name"):
                            st.write(f"Professor: {meta['professor_name']}")
                        if meta.get("publication_date"):
                            st.write(f"Published: {meta['publication_date']}")
                        # Not every source gives a DOI -- arxiv and the
                        # scholarly fallback never do -- so `link` is
                        # whatever best URL was available (DOI > direct
                        # PDF > source page), computed in
                        # python_vector_store.py's make_link().
                        if meta.get("doi"):
                            doi = meta["doi"]
                            doi_url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
                            st.markdown(f"[DOI: {doi}]({doi_url})")
                        elif meta.get("link"):
                            st.markdown(f"[View Paper]({meta['link']})")
                        if meta.get("pdf_url") and meta.get("pdf_url") != meta.get("link"):
                            st.markdown(f"[View PDF]({meta['pdf_url']})")

        chat_store.append_message(st.session_state.user_email, st.session_state.current_chat_id, "assistant", answer_text)
        st.rerun()

with tab_browse:
    all_data = collection.get()
    papers = all_data["metadatas"]

    # Always restrict to selected_professor_ids -- correctly scoped to
    # this user's own professors in both the "All" and specific case (see
    # the same fix and explanation in the Ask tab above).
    papers = [p for p in papers if p.get("professor_id") in selected_professor_ids]

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
        if show_all or len(selected_professor_ids) > 1:
            label += f"  —  {paper.get('professor_name', '')}"
        if paper.get("is_new_paper"):
            label += "  •  New"
        if paper.get("citation_alert"):
            label += "  •  Citation increase"

        with st.expander(label):
            st.write(f"Authors: {paper['authors']}")
            st.write(f"Venue: {paper.get('venue', '')}")
            st.write(f"Source: {paper['source']}")
            if paper.get("professor_name"):
                st.write(f"Professor: {paper['professor_name']}")
            if paper.get("publication_date"):
                st.write(f"Published: {paper['publication_date']}")
            # Same DOI > link > pdf_url fallback as the Ask tab -- arxiv
            # and scholarly-fallback papers have no DOI, so without this
            # they'd show no link at all.
            if paper.get("doi"):
                doi = paper["doi"]
                doi_url = doi if doi.startswith("http") else f"https://doi.org/{doi}"
                st.markdown(f"[DOI: {doi}]({doi_url})")
            elif paper.get("link"):
                st.markdown(f"[View Paper]({paper['link']})")
            if paper.get("pdf_url") and paper.get("pdf_url") != paper.get("link"):
                st.markdown(f"[View PDF]({paper['pdf_url']})")

with tab_professors:
    st.subheader("Add a Professor")
    st.caption(
        "Adding a professor here registers them for tracking. They'll be "
        "fetched on the next scheduled run (or the next time code.py runs)."
    )
    with st.form("add_professor_form"):
        prof_name = st.text_input("Professor Name")
        prof_scholar_url = st.text_input("Google Scholar Profile URL (optional but recommended)")
        if st.form_submit_button("Add & Track"):
            if not prof_name.strip():
                st.error("Enter a professor name.")
            else:
                _, was_duplicate = add_and_track_professor(
                    st.session_state.user_email, prof_name, prof_scholar_url
                )
                if was_duplicate:
                    st.success(
                        f"{prof_name} is already being tracked in the system — "
                        f"you're now subscribed to their existing profile, no re-fetching needed."
                    )
                else:
                    st.success(f"Added and now tracking {prof_name}.")
                st.rerun()

    st.divider()

    st.subheader("My Tracked Professors")
    st.caption("You'll get citation/new-paper and CFP alert emails for everyone in this list.")

    if not my_professors:
        st.caption("You're not tracking any professors yet. Add one above.")
    else:
        for p in my_professors:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"**{p['name']}**")
                if p.get("scholar_url"):
                    st.caption(p["scholar_url"])
            with col2:
                if st.button("Remove", key=f"unsub_{p['id']}"):
                    professors.unsubscribe(st.session_state.user_email, p["id"])
                    st.rerun()