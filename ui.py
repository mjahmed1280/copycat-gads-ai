"""Streamlit UI for the Copycat Flask API.

Local dev (no login):
    python app.py          # terminal 1 — API on :5000
    streamlit run ui.py    # terminal 2 — UI on :8501

Cloud Run (see start.sh): gunicorn serves the API on 127.0.0.1:5000 (internal
only) and Streamlit serves this UI on $PORT behind a login.

Environment:
    AUTH_MODE       basic -> require login (fail closed); anything else -> no login
    APP_USERS_JSON  {"email": "password", ...}  (Secret Manager: pmax-app-users)
    ADMIN_EMAILS    comma-separated emails that may Train / write the Style Guide
    COMPANY_NAME    default advertiser name (e.g. "Acme Paints")
    GENERATION_MODEL / STYLE_GUIDE_MODEL   Gemini ids (default gemini-3.8-flash)
    API_URL         Flask API base URL (default http://127.0.0.1:5000)
"""

import hmac
import json
import os
import time

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://127.0.0.1:5000")
AUTH_MODE = os.environ.get("AUTH_MODE", "none").lower()
DEFAULT_COMPANY = os.environ.get("COMPANY_NAME", "")
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "gemini-3.8-flash")
STYLE_GUIDE_MODEL = os.environ.get("STYLE_GUIDE_MODEL", "gemini-3.8-flash")
ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Copycat — Google Ads Generator",
    page_icon="📢",
    layout="wide",
)


# ── Authentication (basic: email + password from Secret Manager) ───────────────
def _load_users() -> dict[str, str]:
    raw = os.environ.get("APP_USERS_JSON", "")
    try:
        users = json.loads(raw) if raw else {}
        return {str(k).strip().lower(): str(v) for k, v in users.items()}
    except (ValueError, AttributeError):
        return {}


def _check_login(email: str, password: str, users: dict[str, str]) -> bool:
    """Constant-time check; always compares against a value to avoid timing leaks."""
    expected = users.get(email.strip().lower(), "")
    ok = hmac.compare_digest(password.encode(), expected.encode())
    return ok and bool(expected)


def _require_login() -> tuple[str, bool]:
    """Returns (user_email, is_admin). Stops the script until logged in."""
    if AUTH_MODE != "basic":
        return "local", True  # local development: no login, full access

    users = _load_users()
    if not users:
        st.error("Login is not configured (APP_USERS_JSON is empty or invalid).")
        st.stop()

    if "user" not in st.session_state:
        st.title("📢 Copycat")
        st.caption("Sign in to continue")
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            if _check_login(email, password, users):
                st.session_state["user"] = email.strip().lower()
                st.rerun()
            time.sleep(1.5)  # slow down guessing
            st.error("Invalid email or password.")
        st.stop()

    user = st.session_state["user"]
    return user, user in ADMIN_EMAILS  # admin = reserved for future sensitive features


USER, IS_ADMIN = _require_login()


# ── API helpers ────────────────────────────────────────────────────────────────
def _api_health() -> dict | None:
    try:
        return requests.get(f"{API_URL}/health", timeout=3).json()
    except Exception:
        return None


health = _api_health()
MODEL_LOADED = bool(health and health.get("model_loaded")) or st.session_state.get(
    "model_ready", False
)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📢 Copycat")
    st.caption("Google Ads copy generator powered by Vertex AI")
    if AUTH_MODE == "basic":
        st.caption(f"Signed in as **{USER}**{' (admin)' if IS_ADMIN else ''}")
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    st.divider()

    company_name = st.text_input(
        "Company Name", value=DEFAULT_COMPANY, placeholder="Your brand"
    )
    language = st.selectbox(
        "Language",
        [
            "English", "Hindi", "Spanish", "French", "German",
            "Italian", "Portuguese", "Dutch",
        ],
    )

    with st.expander("Model settings"):
        st.caption("Gemini model ids. Change if Google retires one.")
        generation_model = st.text_input("Generation model", value=GENERATION_MODEL)
        style_guide_model = st.text_input("Style guide model", value=STYLE_GUIDE_MODEL)

    st.divider()
    st.subheader("Model status")
    if health is None:
        st.error("API not reachable")
    elif MODEL_LOADED:
        st.success("Model loaded")
        st.caption(f"{health.get('n_exemplars', 0)} exemplar ads")
    else:
        st.warning("No model loaded")
        st.caption(
            "Go to the Train tab first."
        )
    if health and health.get("project"):
        st.caption(f"Vertex project: `{health['project']}`")
    if st.button("Refresh", use_container_width=True):
        st.rerun()


# ── Helper ─────────────────────────────────────────────────────────────────────
def _read_csv(file) -> pd.DataFrame:
    """Try common encodings so Windows-exported CSVs don't crash."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            file.seek(0)
            return pd.read_csv(file, encoding=enc)
        except UnicodeDecodeError:
            continue
    file.seek(0)
    return pd.read_csv(file, encoding="latin-1", encoding_errors="replace")



# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_train, tab_style, tab_generate = st.tabs(["🏋️ Train", "✍️ Style Guide", "⚡ Generate"])

# ══════════════════════════════════════════════════════════════════════════════
# TRAIN
# ══════════════════════════════════════════════════════════════════════════════
with tab_train:
    st.header("Train Model")
    st.markdown(
        "Upload your existing Google Ads export CSV to build the similarity vectorstore. "
        "This runs once and saves the shared model that all users generate from."
    )

    uploaded = st.file_uploader(
        "Google Ads CSV export",
        type=["csv"],
        help="Expects columns: Headline 1, Headline 2 …, Description 1 …, Keywords",
    )

    if uploaded:
        preview_df = _read_csv(uploaded)
        st.caption(f"{len(preview_df)} rows loaded")
        with st.expander("Preview data"):
            st.dataframe(preview_df.head(5), use_container_width=True)
        uploaded.seek(0)

    with st.expander("Advanced parameters"):
        col1, col2 = st.columns(2)
        with col1:
            embedding_dim = st.number_input("Embedding Dimensionality", value=768, step=64)
            max_initial_ads = st.number_input("Max Initial Ads", value=2000, step=100)
            on_invalid = st.selectbox("On Invalid Ad", ["drop", "skip", "raise"])
        with col2:
            max_exemplar_ads = st.number_input("Max Exemplar Ads", value=200, step=10)
            exemplar_method = st.selectbox(
                "Exemplar Selection", ["affinity_propagation", "random"]
            )
            replace_special = st.checkbox("Replace Special Variables with Default", value=False)

    if st.button("Train", type="primary", disabled=uploaded is None, use_container_width=True):
        uploaded.seek(0)
        df = _read_csv(uploaded)
        training_data = json.loads(df.to_json(orient="records"))

        payload = {
                "training_data": training_data,
            "embedding_model_dimensionality": int(embedding_dim),
            "max_initial_ads": int(max_initial_ads),
            "max_exemplar_ads": int(max_exemplar_ads),
            "exemplar_selection_method": exemplar_method,
            "on_invalid_ad": on_invalid,
            "replace_special_variables": replace_special,
        }

        with st.spinner("Training… this can take 5–10 minutes for large datasets."):
            try:
                r = requests.post(f"{API_URL}/train", json=payload, timeout=900)
                result = r.json()
                if r.ok:
                    st.session_state["model_ready"] = True
                    st.success(
                        f"Training complete — {result['n_training_rows']} rows, "
                        f"{result['n_exemplars']} exemplar ads selected."
                    )
                else:
                    st.error(result.get("error", "Unknown error"))
            except requests.exceptions.Timeout:
                st.error("Request timed out. Training may still be running — check the API terminal.")
            except Exception as e:
                st.error(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# STYLE GUIDE
# ══════════════════════════════════════════════════════════════════════════════
with tab_style:
    st.header("Style Guide")
    st.markdown(
        "Gemini reads your exemplar ads and writes brand copy guidelines. "
        "The guide is stored in the model and used automatically during generation."
    )

    if not company_name:
        st.info("Set **Company Name** in the sidebar first.")

    additional_instructions = st.text_area(
        "Additional instructions (optional)",
        placeholder="Focus on comfort and quality. Avoid mentioning specific prices.",
        height=100,
    )
    use_exemplar_ads = st.checkbox("Include exemplar ads in style guide prompt", value=True)

    if st.button(
        "Generate Style Guide",
        type="primary",
        disabled=not company_name,
        use_container_width=True,
    ):
        payload = {
                "company_name": company_name,
            "additional_style_instructions": additional_instructions,
            "use_exemplar_ads": use_exemplar_ads,
            "model_name": style_guide_model,
        }
        with st.spinner("Generating style guide…"):
            try:
                r = requests.post(f"{API_URL}/style-guide", json=payload, timeout=300)
                result = r.json()
                if r.ok:
                    st.session_state["style_guide"] = result["style_guide"]
                    st.success("Style guide generated and saved to model.")
                else:
                    st.error(result.get("error", "Unknown error"))
            except Exception as e:
                st.error(str(e))

    if "style_guide" in st.session_state:
        st.divider()
        st.subheader("Current Style Guide")
        st.markdown(st.session_state["style_guide"])


# ══════════════════════════════════════════════════════════════════════════════
# GENERATE
# ══════════════════════════════════════════════════════════════════════════════
with tab_generate:
    st.header("Generate Ad Copy")

    if not MODEL_LOADED:
        st.warning(
            "No trained model is loaded yet. "
            + "Train one in the Train tab first."
        )

    mode = st.radio(
        "Input mode",
        ["Simple — keyword strings", "Structured — Campaign / Ad Group CSV"],
        horizontal=True,
    )

    payload_keywords: dict = {}

    if mode == "Simple — keyword strings":
        st.markdown(
            "One ad group per line. Use commas within a line to group related keywords together."
        )
        keywords_input = st.text_area(
            "Keywords",
            placeholder="sofa, couch, sleeper sofa\nmattress, king mattress, memory foam\ndesk chair, ergonomic chair",
            height=160,
        )
        keywords = [k.strip() for k in keywords_input.strip().splitlines() if k.strip()]
        if keywords:
            payload_keywords = {"keywords": keywords}
            st.caption(f"{len(keywords)} ad group(s)")

    else:
        st.markdown(
            "Upload a CSV with columns `Campaign ID`, `Ad Group`, `Keyword` — one keyword per row. "
            "Keywords in the same Campaign ID + Ad Group are merged into one ad."
        )
        kw_file = st.file_uploader("New Keywords CSV", type=["csv"], key="kw_upload")
        if kw_file:
            kw_df = _read_csv(kw_file)
            st.caption(f"{len(kw_df)} keyword rows")
            with st.expander("Preview"):
                st.dataframe(kw_df.head(10), use_container_width=True)
            payload_keywords = {"new_keywords": json.loads(kw_df.to_json(orient="records"))}

        ei_file = st.file_uploader(
            "Extra Instructions CSV (optional — Campaign ID, Ad Group, Version, Extra Instructions)",
            type=["csv"],
            key="ei_upload",
        )
        if ei_file:
            ei_df = _read_csv(ei_file)
            payload_keywords["extra_instructions"] = json.loads(ei_df.to_json(orient="records"))

    st.divider()

    with st.expander("Generation parameters"):
        col1, col2, col3 = st.columns(3)
        with col1:
            use_style_guide = st.checkbox("Use style guide", value=True)
            allow_mem_headlines = st.checkbox("Allow memorised headlines", value=False)
            allow_mem_desc = st.checkbox("Allow memorised descriptions", value=False)
        with col2:
            num_examples = st.slider("In-context examples", 1, 10, 5)
            temperature = st.slider("Temperature", 0.0, 1.0, 0.95, step=0.05)
        with col3:
            n_versions = st.number_input("Versions per ad group", min_value=1, max_value=5, value=1)
            batch_size = st.number_input("Batch size", min_value=1, max_value=30, value=15)

    can_generate = bool(
        payload_keywords.get("keywords") or payload_keywords.get("new_keywords")
    )

    if not company_name:
        st.info("Set **Company Name** in the sidebar before generating.")

    if st.button(
        "Generate",
        type="primary",
        disabled=not (can_generate and company_name and MODEL_LOADED),
        use_container_width=True,
    ):
        payload = {
            **payload_keywords,
            "company_name": company_name,
            "language": language,
            "model_name": generation_model,
            "use_style_guide": use_style_guide,
            "num_in_context_examples": int(num_examples),
            "temperature": float(temperature),
            "allow_memorised_headlines": allow_mem_headlines,
            "allow_memorised_descriptions": allow_mem_desc,
            "n_versions": int(n_versions),
            "batch_size": int(batch_size),
        }
        with st.spinner("Generating ad copy…"):
            try:
                r = requests.post(f"{API_URL}/generate", json=payload, timeout=600)
                result = r.json()
                if r.ok:
                    st.session_state["results"] = result["results"]
                else:
                    st.error(result.get("error", "Unknown error"))
            except requests.exceptions.Timeout:
                st.error("Request timed out. Try a smaller batch.")
            except Exception as e:
                st.error(str(e))

    # ── Results ──────────────────────────────────────────────────────────────
    if "results" in st.session_state:
        results = st.session_state["results"]
        success_count = sum(1 for r in results if r.get("success"))
        st.divider()
        st.subheader(f"Results — {success_count}/{len(results)} successful")

        for i, ad in enumerate(results):
            ev = ad.get("evaluation", {})
            style_score = ev.get("style_similarity", 0)
            kw_score = ev.get("keyword_similarity", 0)
            status = "✅" if ad.get("success") else "❌"
            label = ad.get("keywords") or str(ad.get("index", f"Ad {i+1}"))

            with st.expander(
                f"{status} {label[:80]}  —  style {style_score:.2f} · keyword {kw_score:.2f}",
                expanded=(i == 0),
            ):
                if not ad.get("success"):
                    st.error(ad.get("error_message", "Generation failed"))
                    continue

                if ad.get("warning_message"):
                    st.warning(ad["warning_message"])

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Headlines**")
                    for h in ad.get("headlines", []):
                        st.markdown(f"- {h}")
                with col2:
                    st.markdown("**Descriptions**")
                    for d in ad.get("descriptions", []):
                        st.markdown(f"- {d}")

                if ev.get("headlines_are_memorised") or ev.get("descriptions_are_memorised"):
                    st.warning("Some copy appears to be memorised from training data.")

        # ── Download ─────────────────────────────────────────────────────────
        st.divider()
        rows = []
        for ad in results:
            if not ad.get("success"):
                continue
            ev = ad.get("evaluation", {})
            row: dict = {"keywords": ad.get("keywords", "")}
            for j, h in enumerate(ad.get("headlines", []), 1):
                row[f"Headline {j}"] = h
            for j, d in enumerate(ad.get("descriptions", []), 1):
                row[f"Description {j}"] = d
            row["style_similarity"] = ev.get("style_similarity", "")
            row["keyword_similarity"] = ev.get("keyword_similarity", "")
            rows.append(row)

        if rows:
            dl_df = pd.DataFrame(rows)
            st.download_button(
                "⬇️ Download results as CSV",
                data=dl_df.to_csv(index=False),
                file_name="generated_ads.csv",
                mime="text/csv",
                use_container_width=True,
            )
