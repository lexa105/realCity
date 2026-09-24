# realCity
Realitní vyhledávač v Praze. Vibecoded



 ### How the Pipeline Works

    ┌─────────────────┐       ┌──────────────────────────────┐
    │  Apify Cloud    │       │        Python Pipeline       │
    │  (Actor Scrapes │ ───►  │  1. apify-client pulls items │
    │  Sreality/etc.) │       │  2. Pydantic validates data  │
    └─────────────────┘       │  3. Saves to SQLite / DB     │
                              └──────────────┬───────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │   Next.js 15 + Leaflet Map   │
                              └──────────────────────────────┘
  ──────