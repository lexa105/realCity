# realCity — AI Agent Context & Development Guide

> **Project Mission**: Real estate search aggregator for Prague flats & rooms ("Realitní vyhledávač v Praze"). Scrapes unstructured housing listings (e.g. social groups, portals), extracts structured data using Gemini LLMs, geocodes locations to coordinates, and presents them in a modern web UI.

---

## 1. Repository Architecture

```
realCity/
├── backend/                  # Python data ingestion, LLM parsing, API
│   ├── .venv/                # Virtual environment (Python 3.12+)
│   ├── test_scrape.py        # Prototype scraper & Gemini structured extraction
│   └── requirements.txt      # Python dependencies
├── web/                      # Frontend application (search, map, filters)
├── CLAUDE.md                 # Agent instructions & context (this file)
├── README.md                 # Project introduction
└── .gitignore                # Ignored files (venv, env, pycache, etc.)
```

---

## 2. Tech Stack & Key Libraries

### Backend
- **Runtime**: Python 3.12+ (managed in `backend/.venv`)
- **LLM & Structured Extraction**: `google-genai` (Gemini API with native Pydantic schema enforcement)
- **Data Validation**: `pydantic` v2 (`BaseModel`, `Field`, `Literal`)
- **Geocoding**: `geopy` (`Nominatim` for Praha coordinates and street/district resolution)
- **Scraping & HTTP**: `httpx`, `requests`, `beautifulsoup4`
- **Environment Config**: `python-dotenv`

### Frontend (`web/`)
- Web client (planned: Map-centric search interface with Prague district filtering, price sliders, and flat preview cards).

---

## 3. Core Data Contract: `FlatListing`

All listing extraction pipelines must conform to the unified data model defined in `backend/test_scrape.py`:

```python
class FlatListing(BaseModel):
    is_offer: bool                  # True = Offering (nabídka), False = Seeking (poptávka/hledám) or spam
    listing_type: Literal[
        "entire_flat", "room", "sublet", "unknown"
    ]
    disposition: Optional[Literal[
        "1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1", "4+kk", "room", "other"
    ]]
    rent_czk: Optional[int]         # Pure monthly rent in CZK
    utilities_czk: Optional[int]    # Monthly utilities (poplatky / energie / služby)
    deposit_czk: Optional[int]      # Security deposit (kauce / jistota)
    location_name: Optional[str]    # Area/quarter (e.g. 'Vinohrady', 'Praha 7 - Holešovice')
    geocoding_query: Optional[str]  # Landmark/street query for geocoder (e.g. 'Náměstí Míru, Praha')
    summary_cs: str                 # 1-sentence clean summary in Czech
```

### Critical Domain Rules (Prague Real Estate)
- **Offer vs. Request**: High priority on filtering out `is_offer=False` (people looking for rooms).
- **Price Separation**: In Prague, rent (`nájem`) and utilities (`poplatky/energie`) are frequently quoted separately. Extract them into their respective fields if separated, otherwise capture total in `rent_czk`.
- **Layouts (`dispozice`)**: Distinguish between `+kk` (kitchenette in living area) and `+1` (separate kitchen).
- **Language**: Code, schemas, and commits in **English**. Data summaries (`summary_cs`) and user-facing location labels in **Czech**.

---

## 4. Environment Setup & Common Commands

### Backend

```bash
# Activate virtual environment
source backend/.venv/bin/activate

# Install / update dependencies
pip install -r backend/requirements.txt

# Run the test scraper & extraction
python backend/test_scrape.py
```

### Environment Variables
Store secrets in `backend/.env` (never commit this file):
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## 5. Development Guidelines for AI Agents

1. **Schema Integrity**: Always use Pydantic v2 schemas for LLM extraction to guarantee strongly typed responses (`response_schema=FlatListing`, `response_mime_type="application/json"`).
2. **Geocoding Etiquette**: Nominatim (`geopy`) requires a custom `user_agent` header and rate limiting (maximum 1 request per second). Cache geocoding results by location query whenever possible.
3. **Idempotence & Caching**: Scraped raw texts should be deduplicated (by URL, post ID, or hash) before making expensive LLM or geocoding calls.
4. **Security & Secrets**: Never log or commit API keys (`GEMINI_API_KEY`, etc.). Ensure `.gitignore` is respected.
5. **Code Style**:
   - Strict type annotations on functions and models.
   - Prefer modern Python features (pattern matching, `pathlib`, `pydantic.Field` descriptions).
   - Keep scraper logic modular (separate fetcher, parser, LLM extractor, and geocoder).
