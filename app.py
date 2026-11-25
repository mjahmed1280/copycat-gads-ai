"""Flask API wrapping the Copycat Google Ads copy generator.

Default values and logic are sourced from the existing UI event handlers in
py/copycat/ui/event_handlers.py and states.py — not guessed.

Prerequisites:
  - GCP Application Default Credentials already configured
      gcloud auth application-default login
  - pip install flask google-auth
  - pip install -e ./py          # installs the copycat package from this repo
  - Set GOOGLE_CLOUD_PROJECT env var (fixed per deployment; the UI no longer asks
    for it), or pass gcp_project in a request body
  - GCP_LOCATION (default us-central1) sets the Vertex AI region
  - COPYCAT_MODEL_FILE sets where the trained model is stored (mount a bucket
    there on Cloud Run so it survives restarts)

Endpoints:
  GET  /health          -- liveness check; shows whether a model is loaded
  POST /train           -- build vectorstore from existing ads (slow, saves to disk)
  POST /style-guide     -- generate a brand style guide from exemplar ads
  POST /generate        -- generate new ad copies (simple or Campaign/AdGroup format)
"""

import json
import logging
import os
from pathlib import Path

import pandas as pd
import vertexai
from flask import Flask, jsonify, request

from copycat import copycat as _copycat_mod
from copycat import (
    ALL_SAFETY_SETTINGS_ONLY_HIGH,
    Copycat,
    EmbeddingModelName,
    ModelName,
)
from copycat.data import utils as data_utils

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Gemini model ids. Google retires models (2.5 went on 2026-10-20), so keep these configurable.
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", ModelName.GEMINI_3_8_FLASH.value)
STYLE_GUIDE_MODEL = os.environ.get("STYLE_GUIDE_MODEL", ModelName.GEMINI_3_8_FLASH.value)
MODEL_FILE = Path(os.environ.get("COPYCAT_MODEL_FILE", "copycat_model.json"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory singleton; loaded from disk on startup when the file exists.
_copycat_instance: Copycat | None = None

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_project(body: dict) -> str:
    project = (
        body.get("gcp_project")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
    )
    if not project:
        try:
            import google.auth
            _, project = google.auth.default()
        except Exception:
            pass
    if not project:
        raise ValueError(
            "GCP project not found. Set the GOOGLE_CLOUD_PROJECT environment "
            "variable or pass 'gcp_project' in the request body."
        )
    return project


def _init_vertex(body: dict) -> None:
    project = _resolve_project(body)
    location = body.get("gcp_location") or os.environ.get("GCP_LOCATION", "us-central1")
    vertexai.init(project=project, location=location)


def _load_model_from_disk() -> Copycat | None:
    if MODEL_FILE.exists():
        logger.info("Loading Copycat model from %s", MODEL_FILE)
        try:
            return Copycat.from_json(MODEL_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load model from disk; starting fresh.")
    return None


def _save_model_to_disk(model: Copycat) -> None:
    MODEL_FILE.write_text(model.to_json(), encoding="utf-8")
    logger.info("Copycat model saved to %s", MODEL_FILE)


def _prepare_training_df(training_data_raw: list[dict]) -> pd.DataFrame:
    """Convert raw dicts to a DataFrame ready for Copycat.create_from_pandas.

    Supports two input shapes:
      1. Already-shaped: each row has 'headlines' (list), 'descriptions' (list),
         'keywords' (str).
      2. Google Ads CSV export: rows have 'Headline 1', 'Headline 2', ... columns
         alongside 'Keywords'.
    """
    df = pd.DataFrame(training_data_raw)

    # Google Ads CSV export format has "Headline N" / "Description N" columns.
    if any(c.startswith("Headline ") for c in df.columns):
        df = data_utils.collapse_headlines_and_descriptions(df)

    # Normalise column name variants.
    if "Keywords" in df.columns and "keywords" not in df.columns:
        df = df.rename(columns={"Keywords": "keywords"})

    # Google Ads UI exports use "Ad group" (no Keywords column).
    # Fall back to Ad group name as the keyword string so training still works.
    if "keywords" not in df.columns:
        for col in ("Ad group", "Ad Group", "Ad_group"):
            if col in df.columns:
                df = df.rename(columns={col: "keywords"})
                break

    # Mirror what the UI does: keep only the three required columns and drop
    # rows that have no headlines at all (event_handlers.py line ~755).
    if "headlines" in df.columns:
        df = df[df["headlines"].apply(len) > 0]

    return df


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "model_loaded": _copycat_instance is not None,
        "model_file": str(MODEL_FILE),
        "n_exemplars": (
            _copycat_instance.ad_copy_vectorstore.n_exemplars
            if _copycat_instance
            else None
        ),
    })


@app.post("/train")
def train():
    """Build (or rebuild) the Copycat vectorstore from existing ad data.

    Request body (JSON):
      training_data                   list[dict]  Required.
                                                   Rows with 'headlines' (list),
                                                   'descriptions' (list),
                                                   'keywords' (str).  OR the raw
                                                   Google Ads CSV row format
                                                   ('Headline 1', 'Headline 2', ...).
      gcp_project                     str
      gcp_location                    str          Default: us-central1
      ad_format                       str          responsive_search_ad (default)
                                                   or 'custom' (needs max_headlines
                                                   and max_descriptions too)
      max_headlines                   int          Used when ad_format == 'custom'
      max_descriptions                int          Used when ad_format == 'custom'
      on_invalid_ad                   str          drop | skip | raise (default: drop)
      embedding_model_name            str          text-embedding-005 (default)
      embedding_model_dimensionality  int          Default: 768
      embedding_model_batch_size      int          Default: 50
      max_initial_ads                 int          Default: 2000
      max_exemplar_ads                int          Default: 200
      affinity_preference             float        Optional; None = auto
      exemplar_selection_method       str          affinity_propagation (default) | random
      replace_special_variables       bool         Default: false
    """
    global _copycat_instance

    body = request.get_json(force=True) or {}

    training_data_raw = body.get("training_data")
    if not training_data_raw:
        return jsonify({"error": "'training_data' is required"}), 400

    try:
        _init_vertex(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    df = _prepare_training_df(training_data_raw)
    missing = {"headlines", "descriptions", "keywords"} - set(df.columns)
    if missing:
        return jsonify({
            "error": f"training_data is missing required columns: {sorted(missing)}",
            "hint": (
                "Each row needs 'headlines' (list), 'descriptions' (list), "
                "'keywords' (str). Or use the Google Ads CSV export format."
            ),
        }), 400

    # Build the ad format — mirrors event_handlers.build_new_copycat_instance.
    ad_format_name = body.get("ad_format", "responsive_search_ad")
    if ad_format_name == "custom":
        ad_format = _copycat_mod.google_ads.GoogleAdFormat(
            name="custom",
            max_headlines=int(body.get("max_headlines", 15)),
            max_descriptions=int(body.get("max_descriptions", 4)),
            min_headlines=1,
            min_descriptions=1,
            max_headline_length=30,
            max_description_length=90,
        )
    else:
        ad_format = _copycat_mod.google_ads.get_google_ad_format(ad_format_name)

    affinity_preference = (
        float(body["affinity_preference"])
        if body.get("affinity_preference") is not None
        else None
    )

    try:
        _copycat_instance = Copycat.create_from_pandas(
            training_data=df[["headlines", "descriptions", "keywords"]],
            ad_format=ad_format,
            on_invalid_ad=body.get("on_invalid_ad", "drop"),
            embedding_model_name=body.get(
                "embedding_model_name", EmbeddingModelName.TEXT_EMBEDDING.value
            ),
            embedding_model_dimensionality=int(
                body.get("embedding_model_dimensionality", 768)
            ),
            embedding_model_batch_size=int(
                body.get("embedding_model_batch_size", 50)
            ),
            vectorstore_max_initial_ads=int(body.get("max_initial_ads", 2000)),
            vectorstore_max_exemplar_ads=int(body.get("max_exemplar_ads", 200)),
            vectorstore_affinity_preference=affinity_preference,
            vectorstore_exemplar_selection_method=body.get(
                "exemplar_selection_method", "affinity_propagation"
            ),
            replace_special_variables_with_default=bool(
                body.get("replace_special_variables", False)
            ),
        )
        _save_model_to_disk(_copycat_instance)
    except Exception as e:
        logger.exception("Training failed")
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "status": "ok",
        "n_training_rows": len(df),
        "n_exemplars": _copycat_instance.ad_copy_vectorstore.n_exemplars,
    })


@app.post("/style-guide")
def style_guide():
    """Generate a brand style guide from the trained exemplar ads.

    Request body (JSON):
      company_name                    str    Required.
      gcp_project                     str
      gcp_location                    str    Default: us-central1
      additional_style_instructions   str
      model_name                      str    Default: $GENERATION_MODEL / $STYLE_GUIDE_MODEL (gemini-3.8-flash)
      temperature                     float  Default: 0.95
      top_k                           int    Default: 40
      top_p                           float  Default: 0.95
      use_exemplar_ads                bool   Default: true
      files_uri                       str    GCS URI for brand asset files
    """
    global _copycat_instance

    if _copycat_instance is None:
        return jsonify({"error": "No model loaded. Call POST /train first."}), 400

    body = request.get_json(force=True) or {}
    company_name = body.get("company_name")
    if not company_name:
        return jsonify({"error": "'company_name' is required"}), 400

    try:
        _init_vertex(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        guide = _copycat_instance.generate_style_guide(
            company_name=company_name,
            additional_style_instructions=body.get(
                "additional_style_instructions", ""
            ),
            model_name=body.get("model_name") or STYLE_GUIDE_MODEL,
            safety_settings=ALL_SAFETY_SETTINGS_ONLY_HIGH,
            temperature=float(body.get("temperature", 0.95)),
            top_k=int(body.get("top_k", 40)),
            top_p=float(body.get("top_p", 0.95)),
            use_exemplar_ads=bool(body.get("use_exemplar_ads", True)),
            files_uri=body.get("files_uri", ""),
        )
        _save_model_to_disk(_copycat_instance)
    except Exception as e:
        logger.exception("Style guide generation failed")
        return jsonify({"error": str(e)}), 500

    return jsonify({"style_guide": guide})


@app.post("/generate")
def generate():
    """Generate new ad copies.

    Supports two input formats for keywords:

    --- Simple format ---
    Pass 'keywords' as a list of strings. Each string is a comma-separated
    keyword group for one ad.

      {
        "keywords": ["sofa, couch, sectional", "mattress, king mattress"],
        "company_name": "Furniture Land",
        "language": "English"
      }

    --- Structured format (matches Google Ads campaign structure) ---
    Pass 'new_keywords' as a list of rows, each with 'Campaign ID', 'Ad Group',
    and 'Keyword'. Keywords are grouped into ads by Campaign ID + Ad Group.
    Optionally also pass 'extra_instructions' (list of rows with 'Campaign ID',
    'Ad Group', 'Version', 'Extra Instructions') and 'n_versions' (int).

      {
        "new_keywords": [
          {"Campaign ID": "3", "Ad Group": "Sofas", "Keyword": "sofa"},
          {"Campaign ID": "3", "Ad Group": "Sofas", "Keyword": "couch"}
        ],
        "company_name": "Furniture Land"
      }

    Common parameters:
      company_name                    str    Used in system instruction.
      language                        str    Default: English
      gcp_project                     str
      gcp_location                    str    Default: us-central1
      keywords_specific_instructions  list[str]  Per-keyword extra context (simple format only).
      model_name                      str    Default: $GENERATION_MODEL / $STYLE_GUIDE_MODEL (gemini-3.8-flash)
      temperature                     float  Default: 0.95
      top_k                           int    Default: 40
      top_p                           float  Default: 0.95
      num_in_context_examples         int    Default: 5
      allow_memorised_headlines       bool   Default: false
      allow_memorised_descriptions    bool   Default: false
      use_style_guide                 bool   Default: true (uses stored style guide if present)
      batch_size                      int    Default: 15
      n_versions                      int    Default: 1 (structured format only)

    Response:
      { "results": [ { "keywords", "success", "headlines", "descriptions",
                       "error_message", "warning_message", "evaluation" }, ... ] }
    """
    if _copycat_instance is None:
        return jsonify({"error": "No model loaded. Call POST /train first."}), 400

    body = request.get_json(force=True) or {}

    try:
        _init_vertex(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    generation_params = dict(
        system_instruction_kwargs=dict(
            company_name=body.get("company_name", "the advertiser"),
            language=body.get("language", "English"),
        ),
        model_name=body.get("model_name") or GENERATION_MODEL,
        temperature=float(body.get("temperature", 0.95)),
        top_k=int(body.get("top_k", 40)),
        top_p=float(body.get("top_p", 0.95)),
        num_in_context_examples=int(body.get("num_in_context_examples", 5)),
        allow_memorised_headlines=bool(body.get("allow_memorised_headlines", False)),
        allow_memorised_descriptions=bool(body.get("allow_memorised_descriptions", False)),
        safety_settings=ALL_SAFETY_SETTINGS_ONLY_HIGH,
        style_guide=(
            _copycat_instance.style_guide
            if body.get("use_style_guide", True)
            else ""
        ),
    )

    # ------------------------------------------------------------------
    # Simple format: keywords is a flat list of strings
    # ------------------------------------------------------------------
    if "keywords" in body:
        keywords = body["keywords"]
        if not keywords or not isinstance(keywords, list):
            return jsonify(
                {"error": "'keywords' must be a non-empty list of strings"}
            ), 400

        try:
            responses = _copycat_instance.generate_new_ad_copy(
                keywords=keywords,
                keywords_specific_instructions=body.get(
                    "keywords_specific_instructions"
                ),
                **generation_params,
            )
        except Exception as e:
            logger.exception("Generation failed")
            return jsonify({"error": str(e)}), 500

        return jsonify({"results": _format_responses(responses)})

    # ------------------------------------------------------------------
    # Structured format: new_keywords is a list of Campaign/AdGroup rows
    # ------------------------------------------------------------------
    if "new_keywords" in body:
        new_keywords_raw = body["new_keywords"]
        if not new_keywords_raw:
            return jsonify({"error": "'new_keywords' must be a non-empty list"}), 400

        new_keywords_df = pd.DataFrame(new_keywords_raw)
        index_columns = ["Campaign ID", "Ad Group"]
        missing_idx = [c for c in index_columns if c not in new_keywords_df.columns]
        if missing_idx or "Keyword" not in new_keywords_df.columns:
            return jsonify({
                "error": (
                    "Structured format requires 'Campaign ID', 'Ad Group', "
                    "and 'Keyword' columns in each new_keywords row."
                )
            }), 400

        new_keywords_df = new_keywords_df.set_index(index_columns)

        extra_instructions_df = None
        if body.get("extra_instructions"):
            extra_df = pd.DataFrame(body["extra_instructions"])
            idx_cols = ["Campaign ID", "Ad Group", "Version"]
            if all(c in extra_df.columns for c in idx_cols):
                extra_df["Version"] = extra_df["Version"].astype(str)
                extra_df = extra_df.set_index(idx_cols)
                extra_instructions_df = extra_df

        try:
            generation_data = data_utils.construct_generation_data(
                new_keywords_data=new_keywords_df,
                additional_instructions_data=extra_instructions_df,
                n_versions=int(body.get("n_versions", 1)),
                keyword_column="Keyword",
                version_column="Version",
                additional_instructions_column="Extra Instructions",
            )
        except Exception as e:
            logger.exception("Failed to construct generation data")
            return jsonify({"error": str(e)}), 500

        batch_size = int(body.get("batch_size", 15))
        all_responses = []

        try:
            for batch in data_utils.iterate_over_batches(
                generation_data, batch_size=batch_size
            ):
                responses = _copycat_instance.generate_new_ad_copy_for_dataframe(
                    data=batch,
                    keywords_specific_instructions_column="additional_instructions",
                    **generation_params,
                )
                for idx, response in responses.items():
                    entry = _format_response(response)
                    # Include the Campaign ID / Ad Group / Version index
                    entry["index"] = dict(zip(generation_data.index.names, idx))
                    all_responses.append(entry)
        except Exception as e:
            logger.exception("Generation failed")
            return jsonify({"error": str(e)}), 500

        return jsonify({"results": all_responses})

    return jsonify({
        "error": "Request body must contain either 'keywords' (simple) or 'new_keywords' (structured)."
    }), 400


# ---------------------------------------------------------------------------
# Response serialisation helpers
# ---------------------------------------------------------------------------


def _format_response(r) -> dict:
    return {
        "keywords": r.keywords,
        "success": r.success,
        "headlines": r.google_ad.headlines,
        "descriptions": r.google_ad.descriptions,
        "error_message": r.error_message,
        "warning_message": r.warning_message,
        "evaluation": {
            "headlines_are_memorised": r.evaluation_results.headlines_are_memorised,
            "descriptions_are_memorised": r.evaluation_results.descriptions_are_memorised,
            "style_similarity": r.evaluation_results.style_similarity,
            "keyword_similarity": r.evaluation_results.keyword_similarity,
        },
    }


def _format_responses(responses: list) -> list[dict]:
    return [_format_response(r) for r in responses]


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

# Runs on import so it also works under gunicorn (Cloud Run), not just
# `python app.py`. Use a single gunicorn worker: the model is an in-memory singleton.
_copycat_instance = _load_model_from_disk()

if __name__ == "__main__":
    try:
        _init_vertex({})
        logger.info("Vertex AI initialised on startup.")
    except ValueError:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not set — pass 'gcp_project' in each request body."
        )

    app.run(
        # Cloud Run: keep 127.0.0.1 so only the Streamlit UI (same container) can
        # reach the API; set API_HOST=0.0.0.0 only if you need it exposed.
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", 5000)),
        debug=False,
    )
