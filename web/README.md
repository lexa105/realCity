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

Cards open `/listing/{source}/{external_id}`. `detail.html` and `detail.mjs`
render the saved listing returned by the existing single-listing API, including
photos, availability, description, and a link to the provider. Rentals include
the Spolubydlící calculator; sale prices are never treated as monthly rent.

`roommate.mjs` holds the pure cost and occupancy helpers. The calculator uses
`price`, `charges`, `disposition`, and `area` directly. Unknown fees produce an
explicit estimate excluding fees, and unknown rent prevents a numeric result.
The current API has no deposit field, so the deposit toggle stays disabled;
no deposit is guessed. The helper and UI support an optional `deposit` if it
becomes available through the listing API later.

Occupancy uses layout ranges and adjusts their upper bound by one when floor
area is below 15 m² or above 30 m² per baseline maximum occupant. Unknown layouts
have no recommendation. Recommendations never constrain the 1–6 slider.

Run calculator tests from `backend`: `node --test tests/roommate.test.mjs`.
