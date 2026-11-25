

 


The ASICS Women's Performance Running Capri Tight is a high-quality, versatile tight perfect for running, yoga, and other athletic activities. The tight is made with soft, stretchy fabric that wicks away sweat to keep you cool and dry. It also features reflective details for added visibility at night. The tight has a secure pocket for storing small items such as keys or a credit card. The ASICS Women's Performance Running Capri Tight is available in black and white and comes in sizes small to large.
 
 ASICS Women's Performance Running Capri Tight, in black, soft, strtchy fabric, reflective details, secure pocket for storing essentials



 	
The ASICS Women's Performance Running Capri Tight is a high-quality, versatile tight perfect for running, yoga, and other athletic activities. The tight is made with soft, stretchy fabric that wicks away sweat to keep you cool and dry. It also features reflective details for added visibility at night. The tight has a secure pocket for storing small items such as keys or a credit card. The ASICS Women's Performance Running Capri Tight is available in black and white and comes in sizes small to large.
Turn on screen reader support


 

ASICS Women's Performance Running Capri Tight, in black, soft, strtchy fabric, reflective details, secure pocket for storing essentials
 
 
 	

# Copycat Flask API — Flow Summary

---

## Parameter Guide

### Train Parameters

#### Embedding Dimensionality
The number of dimensions used when converting ad text into a numerical vector. Higher = more detail captured, but slower and uses more memory.

| Your data size | Recommended |
|---|---|
| < 50 ads | 256 |
| 50–500 ads | 512 |
| 500+ ads | **768** (default, matches model's native output) |

> Always use **768** when using `text-embedding-005` — it's the model's native dimension. Only go lower if you hit memory issues.

---

#### Max Initial Ads
Hard cap on how many ads are fed into the clustering step. If your CSV has more rows than this, it randomly samples down first.

| Your data size | Recommended |
|---|---|
| < 500 ads | Set to your actual row count |
| 500–2000 ads | **2000** (default — keep it) |
| > 2000 ads | 2000 is fine; going higher slows clustering significantly |

> If you have 7 rows, set this to 7. If you have 300 rows, set it to 300. The default 2000 is harmless on small sets — it just won't subsample.

---

#### Max Exemplar Ads
The final number of representative ads kept after clustering. These are the ads Gemini actually sees when generating new copy. More exemplars = richer style reference, but slower and more expensive per generation call.

| Your data size | Recommended |
|---|---|
| < 20 ads | Set equal to your row count (keep all) |
| 20–100 ads | 20–50 |
| 100–500 ads | 50–100 |
| 500+ ads | **200** (default) |

> Rule of thumb: exemplars should be ≤ your total row count. Setting 200 when you only have 7 rows just keeps all 7.

---

#### Exemplar Selection Method

| Method | When to use |
|---|---|
| **`affinity_propagation`** | 20+ ads. Clusters by similarity and picks the most central representative from each cluster. Best quality. |
| **`random`** | < 20 ads. Skips clustering and keeps a random sample. Use this for small datasets — affinity propagation collapses everything to 1 exemplar on tiny sets. |

> **Critical:** if you have fewer than 20 ads, always use `random`. With 2–7 rows, `affinity_propagation` will pick just 1 exemplar, making generation useless.

---

#### On Invalid Ad
What to do when a row in your training CSV has malformed or missing data.

| Value | Behaviour |
|---|---|
| **`drop`** (default) | Silently removes bad rows and continues. Safe default. |
| `skip` | Keeps bad rows in but excludes them from exemplar selection. |
| `raise` | Crashes with an error. Use only for debugging your data. |

---

### Generation Parameters

#### In-context Examples
How many exemplar ads are retrieved and shown to Gemini as style references for each new ad. More = stronger style guidance, but longer prompt = slower + more expensive.

| Exemplar count | Recommended |
|---|---|
| < 10 exemplars | 3–5 |
| 10–50 exemplars | **5** (default) |
| 50+ exemplars | 5–10 |

---

#### Temperature
Controls how creative/random Gemini's output is. Lower = more predictable and consistent. Higher = more varied and creative.

| Goal | Value |
|---|---|
| Consistent, on-brand copy | 0.5–0.7 |
| Balanced (default) | **0.95** |
| Maximum variety | 1.0 |

---

#### Use Style Guide
Whether to include the generated style guide in the Gemini prompt. Almost always leave this **on** — it significantly improves brand consistency. Only turn it off if you haven't generated a style guide yet.

---

#### Allow Memorised Headlines / Descriptions
If **off** (default), any result where Gemini copied a headline or description verbatim from training data is marked as **failed**. This is a quality guard — you want novel copy, not regurgitated training data.

Only turn this **on** if you intentionally want to reuse existing copy (e.g. brand slogans that must appear exactly).

---

#### Versions per Ad Group
How many alternative ad variants to generate per keyword group. Useful for A/B testing.

| Use case | Value |
|---|---|
| Single best ad | **1** (default) |
| A/B testing | 2–3 |
| Broad exploration | 4–5 |

---

### Quick Reference — Small Dataset (< 20 ads)

```
Embedding Dimensionality : 768
Max Initial Ads          : [your row count]
Max Exemplar Ads         : [your row count]
Exemplar Selection       : random          ← most important change
On Invalid Ad            : drop
In-context Examples      : 3
Allow Memorised          : off
```

### Quick Reference — Large Dataset (200+ ads)

```
Embedding Dimensionality : 768
Max Initial Ads          : 2000
Max Exemplar Ads         : 200
Exemplar Selection       : affinity_propagation
On Invalid Ad            : drop
In-context Examples      : 5
Allow Memorised          : off
```

---

## What This Is

A Flask REST API wrapping the [Copycat](https://github.com/google-marketing-solutions/copycat) library (Google open-source). It generates Google Ads copy in an advertiser's established style, using Vertex AI (Gemini) + vector similarity search over existing exemplar ads.

---

## Original Flows (Before This API)

The library shipped with two original interfaces. Both are still present in this repo under `py/copycat/`.

### 1. Jupyter Notebook (`copycat_demo.ipynb`)

Intended for developers and data scientists who want to integrate the library directly into Python code or explore what it does step by step.

```
Jupyter / Colab notebook
        │
        ▼
1. Install:  pip install gtech-copycat[google_sheets]
        │
        ▼
2. Auth:     google.auth.default() or colab auth.authenticate_user()
             vertexai.init(project=..., location=...)
        │
        ▼
3. Load data from Google Sheets
             sheets.GoogleSheet.load(sheet_url)
             → sheet["Training Ads"]        (existing ads)
             → sheet["New Keywords"]        (keywords to generate for)
             → sheet["Extra Instructions"]  (optional per-ad-group instructions)
        │
        ▼
4. Prepare training data
             utils.collapse_headlines_and_descriptions(training_ads_data)
             → collapses "Headline 1", "Headline 2"... into a headlines list
        │
        ▼
5. Create Copycat instance  (slow — embeds all ads, runs clustering)
             Copycat.create_from_pandas(training_data, ad_format, ...)
        │
        ▼
6. Generate style guide  (optional)
             copycat_instance.generate_style_guide(company_name=...)
             → displays Markdown style guide inline in the notebook
        │
        ▼
7. Prepare generation data
             utils.construct_generation_data(
                 new_keywords_data, additional_instructions_data,
                 n_versions=2, ...)
        │
        ▼
8. Generate ads  (cell by cell or in batch)
             copycat_instance.generate_new_ad_copy_for_dataframe(data, ...)
        │
        ▼
9. Results displayed in notebook cells + written back to Google Sheet
```

**Limitations:** Requires Jupyter/Colab environment, manual cell-by-cell execution, results tied to a Google Sheet, not automatable without additional scripting.

---

### 2. Mesop Web UI (`py/copycat/ui/`)

A browser-based UI built with [Mesop](https://google.github.io/mesop/) (Google's Python UI framework). Aimed at non-technical users who want a point-and-click interface.

```
Terminal: mesop py/copycat/ui/main.py
        │
        ▼
Browser opens at localhost
        │
        ▼
Starting dialog:
   → Create new Google Sheet  (optionally with demo data pre-loaded)
   → OR load existing Copycat Google Sheet
        │
        ▼
Navigation sidebar — 5 pages in order:

  [1] Setup
      Configure: GCP project ID, location, company name,
      ad format, language, model names, embedding settings

  [2] Prepare Data
      View / manage the data in the connected Google Sheet
      (Training Ads tab, New Keywords tab, Extra Instructions tab)

  [3] New Copycat Instance
      Click "Build" → runs Copycat.create_from_pandas()
      Shows progress, saves model in UI session memory
      (no disk persistence — lost if server restarts)

  [4] Style Guide
      Click "Generate" → runs generate_style_guide()
      Editable text box to review / manually refine the guide
      Saved back to the Google Sheet

  [5] Generate New Ads
      Click "Generate" → iterates over batches
      Results written to the "Generated Ads" tab in the Google Sheet
      Can preview a single ad inline before batch-running

        │
        ▼
Output: Google Sheet "Generated Ads" tab
        (headlines, descriptions, evaluation scores per ad group)
```

**Limitations:** Requires the Mesop server to stay running (no persistence), all state lives in the browser session, output is locked to Google Sheets, no programmatic access.

---

### Why the Flask API

| | Notebook | Mesop UI | Flask API (this) |
|---|---|---|---|
| Interface | Manual cells | Browser clicks | HTTP requests |
| Automation | Requires scripting | Not possible | Native |
| Persistence | Google Sheet | Session memory | `copycat_model.json` |
| Output format | Google Sheet | Google Sheet | JSON |
| Restart-safe | Partial | No | Yes (loads from disk) |
| Integrates with other systems | Manual | No | Yes |

---

## High-Level Flow

```
Your existing ads (CSV / JSON)
        │
        ▼
  POST /train          ← one-time, slow (~5-10 min for large datasets)
        │ builds vector similarity index over exemplar ads
        │ embeds ads using text-embedding-005
        │ saves model to copycat_model.json
        ▼
  POST /style-guide    ← optional but recommended
        │ Gemini 2.5 Pro reads exemplar ads
        │ writes a brand writing style guide
        │ stored inside the model (persisted to JSON)
        ▼
  POST /generate       ← fast, call repeatedly
        │ Gemini 2.5 Flash generates new ad copy
        │ retrieves similar existing ads as in-context examples
        │ optionally uses the style guide
        ▼
  JSON response: headlines[], descriptions[], evaluation scores
```

---

## Endpoints

### `GET /health`
Liveness check. Returns whether a trained model is in memory and how many exemplar ads it contains.

```json
{ "status": "ok", "model_loaded": true, "n_exemplars": 42 }
```

---

### `POST /train`
Builds the vectorstore from your existing ads. **Must be called before anything else.**

**What happens internally:**
1. Accepts your ad data (list of rows, or Google Ads CSV export format)
2. Collapses `Headline 1`, `Headline 2`, ... columns into a single `headlines` list
3. Filters out rows with no headlines
4. Generates embeddings for all ads using `text-embedding-005` (768 dimensions, batch size 50)
5. Runs Affinity Propagation clustering to select the most representative **exemplar ads** (up to 200 from up to 2000 initial ads)
6. Saves the model to `copycat_model.json`

**Input — two accepted formats:**

Option A — Pre-shaped:
```json
{
  "training_data": [
    {
      "keywords": "sofa, couch, sectional",
      "headlines": ["Shop Sofas Today", "Comfortable Couches", "..."],
      "descriptions": ["Find the perfect sofa.", "..."]
    }
  ]
}
```

Option B — Google Ads CSV export format (raw, with `Headline N` / `Description N` columns):
```json
{
  "training_data": [
    {
      "Campaign ID": "1",
      "Ad Group": "Sofas",
      "Keywords": "sofa, couch",
      "Headline 1": "Shop Sofas Today",
      "Headline 2": "Comfortable Couches",
      "Description 1": "Find the perfect sofa.",
      "Description 2": "Shop now and save."
    }
  ]
}
```

**Key parameters:**

| Parameter | Default | Notes |
|---|---|---|
| `embedding_model_dimensionality` | 768 | Match model's native dimensions |
| `max_initial_ads` | 2000 | Cap on ads fed to clustering |
| `max_exemplar_ads` | 200 | Cap on exemplars kept |
| `exemplar_selection_method` | `affinity_propagation` | Or `random` |
| `on_invalid_ad` | `drop` | Or `skip`, `raise` |

**Response:**
```json
{ "status": "ok", "n_training_rows": 150, "n_exemplars": 42 }
```

---

### `POST /style-guide`
Uses Gemini 2.5 Pro to read the exemplar ads and write a brand style guide. This guide is stored in the model and automatically used during generation.

**Optional but strongly recommended** — improves consistency, especially when the training set has varied tone.

```json
{
  "company_name": "Furniture Land",
  "additional_style_instructions": "Focus on comfort and value. Avoid mentioning competitor brands."
}
```

**Response:**
```json
{ "style_guide": "## Brand Voice\nWarm, approachable, value-focused..." }
```

---

### `POST /generate`
Generates new ad copy. Can be called repeatedly after a single train.

**What happens internally:**
1. Retrieves the `num_in_context_examples` (default: 5) most similar exemplar ads to the input keywords via vector search
2. Builds a Gemini prompt with those examples + optional style guide
3. Gemini 2.5 Flash generates headlines and descriptions
4. Returns evaluation scores (style similarity, keyword similarity, memorisation check)

**Simple format** — flat list of keyword strings:
```json
{
  "keywords": ["sofa, couch, sleeper sofa", "mattress, king mattress"],
  "company_name": "Furniture Land",
  "language": "English"
}
```

**Structured format** — matches Google Ads campaign structure, groups keywords by Campaign ID + Ad Group:
```json
{
  "new_keywords": [
    {"Campaign ID": "3", "Ad Group": "Sofas", "Keyword": "sofa"},
    {"Campaign ID": "3", "Ad Group": "Sofas", "Keyword": "couch"},
    {"Campaign ID": "4", "Ad Group": "Beds",  "Keyword": "mattress"}
  ],
  "company_name": "Furniture Land",
  "n_versions": 2
}
```
In structured mode, keywords sharing the same Campaign ID + Ad Group are merged into one ad. `n_versions` generates multiple variants per ad group.

**Key parameters:**

| Parameter | Default | Notes |
|---|---|---|
| `num_in_context_examples` | 5 | Exemplar ads retrieved per keyword group |
| `use_style_guide` | `true` | Uses stored style guide if one exists |
| `allow_memorised_headlines` | `false` | Prevents copying training headlines verbatim |
| `temperature` | 0.95 | Gemini sampling temperature |
| `batch_size` | 15 | Ads per Gemini API call (structured mode) |

**Response:**
```json
{
  "results": [
    {
      "keywords": "sofa, couch, sleeper sofa",
      "success": true,
      "headlines": ["Shop New Sofas Today", "Comfortable Couches", "..."],
      "descriptions": ["Discover stylish sofas...", "..."],
      "error_message": "",
      "warning_message": "",
      "evaluation": {
        "style_similarity": 0.968,
        "keyword_similarity": 0.872,
        "headlines_are_memorised": false,
        "descriptions_are_memorised": false
      }
    }
  ]
}
```

**Evaluation scores:**
- `style_similarity` — how closely the generated copy matches your brand's writing style (0–1, higher is better)
- `keyword_similarity` — how relevant the generated copy is to the input keywords (0–1, higher is better)
- `*_are_memorised` — flags if the output is copying training data verbatim (should be `false`)

---

## Models Used

| Task | Model |
|---|---|
| Embeddings (training) | `text-embedding-005` |
| Style guide generation | `gemini-2.5-pro` |
| Ad copy generation | `gemini-2.5-flash` |

---

## Setup

```bash
# 1. Install dependencies
pip install flask google-auth
pip install -e ./py

# 2. Authenticate with GCP
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID

# 3. Set project env var
set GOOGLE_CLOUD_PROJECT=your-project-id   # Windows
export GOOGLE_CLOUD_PROJECT=your-project-id  # Linux/Mac

# 4. Run the API
python app.py
```

The model is saved to `copycat_model.json` after training and loaded automatically on every restart — you only need to train once (or when your ad data changes).

---

## File Layout

```
copycat/
├── app.py                  ← Flask API (this service)
├── requirements_api.txt    ← API-specific dependencies
├── copycat_model.json      ← Saved model (created after /train)
└── py/
    ├── requirements.txt    ← Core library dependencies
    └── copycat/
        ├── copycat.py      ← Copycat dataclass, create_from_pandas, generate_new_ad_copy
        ├── ad_copy_generator.py  ← AdCopyVectorstore, ModelName, EmbeddingModelName
        ├── data/
        │   └── utils.py    ← collapse_headlines_and_descriptions, construct_generation_data
        └── ui/
            ├── event_handlers.py  ← Original Mesop UI logic (reference for defaults)
            └── states.py          ← Default parameter values
```
