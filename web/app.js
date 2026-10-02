const form = document.querySelector('#filters');
const grid = document.querySelector('#listing-grid');
const template = document.querySelector('#listing-template');
const status = document.querySelector('#results-status');
const previous = document.querySelector('#previous');
const next = document.querySelector('#next');
const pageNumber = document.querySelector('#page-number');
const filterError = document.querySelector('#filter-error');
const pageSize = 12;
const number = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 2 });
const date = new Intl.DateTimeFormat('cs-CZ', { timeZone: 'Europe/Prague' });
let page = 0;
let activeFilters = new URLSearchParams();
let requestController;

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function createCard(listing) {
  const card = template.content.cloneNode(true);
  const link = card.querySelector('.listing-link');
  const url = safeUrl(listing.url);
  if (url) link.href = url;
  link.setAttribute('aria-label', `${listing.title}${listing.is_deleted ? ' — smazaná nabídka' : ''} — otevřít původní nabídku v nové kartě`);
  card.querySelector('.listing-title').textContent = listing.title;
  card.querySelector('.listing-location').textContent = listing.address || listing.city || 'Lokalita neuvedena';
  card.querySelector('.listing-disposition').textContent = (listing.disposition === 'studio' ? 'Garsoniéra' : listing.disposition) || 'Dispozice neuvedena';
  card.querySelector('.listing-area').textContent = listing.area == null ? 'Plocha neuvedena' : `${number.format(listing.area)} m²`;
  card.querySelector('.reserved-badge').hidden = !listing.is_reserved || listing.is_deleted;
  card.querySelector('.deleted-notice').hidden = !listing.is_deleted;
  if (listing.is_deleted) {
    card.querySelector('.listing-card').classList.add('is-deleted');
    const deletedAt = new Date(listing.deleted_at);
    card.querySelector('.deleted-date').textContent = listing.deleted_at && !Number.isNaN(deletedAt.getTime())
      ? `Zjištěno ${date.format(deletedAt)}` : 'Nabídka již není dostupná';
  }
  const price = card.querySelector('.listing-price');
  price.textContent = listing.price == null ? 'Cena na dotaz' : `${number.format(listing.price)} Kč`;
  if (listing.price != null && listing.transaction_type === 'rent') {
    const suffix = document.createElement('small');
    suffix.textContent = ' / měsíc';
    price.append(suffix);
  }
  card.querySelector('.listing-charges').textContent = listing.transaction_type === 'rent'
    ? (listing.charges == null ? 'Poplatky neuvedeny' : `+ ${number.format(listing.charges)} Kč poplatky / měsíc`)
    : 'Prodej nemovitosti';
  const image = card.querySelector('.listing-image');
  const imageUrl = safeUrl(listing.main_image) || (listing.images || []).map(safeUrl).find(Boolean);
  if (imageUrl) {
    image.src = imageUrl;
    image.hidden = false;
    image.addEventListener('error', () => { image.hidden = true; }, { once: true });
  }
  return card;
}

async function loadListings() {
  requestController?.abort();
  const controller = new AbortController();
  requestController = controller;
  const requestedPage = page;
  previous.disabled = true;
  next.disabled = true;
  grid.setAttribute('aria-busy', 'true');
  document.querySelector('#empty-state').hidden = true;
  document.querySelector('#error-state').hidden = true;
  status.textContent = 'Načítáme nabídky…';
  grid.replaceChildren(...Array.from({ length: 3 }, () => {
    const skeleton = document.createElement('div');
    skeleton.className = 'skeleton';
    skeleton.setAttribute('aria-hidden', 'true');
    return skeleton;
  }));
  const params = new URLSearchParams(activeFilters);
  // One extra record tells us whether another page exists; the API filters first.
  params.set('limit', String(pageSize + 1));
  params.set('offset', String(requestedPage * pageSize));
  try {
    const response = await fetch(`/listings?${params}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const listings = await response.json();
    if (controller.signal.aborted) return;
    const visible = listings.slice(0, pageSize);
    grid.replaceChildren(...visible.map(createCard));
    document.querySelector('#empty-state').hidden = visible.length !== 0;
    status.textContent = visible.length
      ? `Zobrazeno ${requestedPage * pageSize + 1}–${requestedPage * pageSize + visible.length} · Cena v Kč`
      : 'Nebyly nalezeny žádné nabídky.';
    previous.disabled = requestedPage === 0;
    next.disabled = listings.length <= pageSize;
    pageNumber.textContent = `Strana ${requestedPage + 1}`;
  } catch (error) {
    if (controller.signal.aborted) return;
    grid.replaceChildren();
    status.textContent = '';
    document.querySelector('#error-state').hidden = false;
    previous.disabled = requestedPage === 0;
  } finally {
    if (!controller.signal.aborted) grid.setAttribute('aria-busy', 'false');
  }
}

function applyFilters() {
  filterError.hidden = true;
  const values = new FormData(form);
  for (const [minimum, maximum, label] of [['min_price', 'max_price', 'ceny'], ['min_area', 'max_area', 'plochy']]) {
    if (values.get(minimum) !== '' && values.get(maximum) !== '' && Number(values.get(minimum)) > Number(values.get(maximum))) {
      filterError.textContent = `Dolní hranice ${label} nesmí být vyšší než horní.`;
      filterError.hidden = false;
      return;
    }
  }
  activeFilters = new URLSearchParams();
  for (const [key, value] of values) if (value.trim()) activeFilters.append(key, value.trim());
  page = 0;
  loadListings();
}

function updatePriceLabel() {
  const rent = form.elements.transaction_type.value === 'rent';
  document.querySelector('#price-label').firstChild.textContent = rent ? 'Měsíční nájem ' : 'Kupní cena ';
  document.querySelector('#price-hint').textContent = rent ? 'Cena bez poplatků a energií.' : 'Celková cena nemovitosti.';
}
form.addEventListener('submit', (event) => { event.preventDefault(); applyFilters(); });
form.addEventListener('reset', () => { setTimeout(() => { updatePriceLabel(); applyFilters(); }, 0); });
form.elements.transaction_type.addEventListener('change', updatePriceLabel);
form.elements.include_deleted.addEventListener('change', applyFilters);
document.querySelector('#empty-reset').addEventListener('click', () => form.reset());
document.querySelector('#retry').addEventListener('click', loadListings);
previous.addEventListener('click', () => { page -= 1; loadListings(); });
next.addEventListener('click', () => { page += 1; loadListings(); });
applyFilters();
