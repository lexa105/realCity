# realCity web

A small HTML/CSS/JavaScript frontend served by FastAPI. No Node dependencies or
build step are needed. From the repository root:

```bash
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --reload --host 127.0.0.1
```

Open http://127.0.0.1:8000/. Import listings with `python -m app.apify` separately.
The page only queries saved records and never launches a scraper.

`index.html` contains the filter form and listing-card template. `styles.css`
contains the responsive layout. `app.js` submits filters to `/listings`, renders
cards, and handles pagination, loading, empty, and error states. Requests use the
same origin as the page, so no CORS setup is required. Refresh the browser after
editing frontend files.

Disposition filters use exact provider values. Price filters exclude charges;
area filters exclude unknown areas. An unset filter includes unknown values.
The page requests 13 records to display 12 and detect whether a next page exists.
No total count is claimed. Numeric range fields replace the wireframe's histogram
screenshots because the backend does not yet provide histogram data.
