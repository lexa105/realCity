import { calculateCosts, recommendedOccupancy, formatMoney } from './roommate.mjs';

const element = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 2 });
let listing;

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function updateCalculator() {
  const people = Number(element('people').value);
  element('people-count').value = String(people);
  const range = recommendedOccupancy(listing.disposition, listing.area);
  element('occupancy-recommendation').textContent = range
    ? `Doporučeno: ${range[0] === range[1] ? range[0] : range.join('–')} osob`
    : 'Doporučený počet osob nelze určit z dostupných údajů.';
  element('occupancy-info').hidden = !range || (people >= range[0] && people <= range[1]);
  const includeDeposit = element('include-deposit').checked;
  const costs = calculateCosts(listing, people, element('fees-per-person').checked, includeDeposit);
  const money = (value) => formatMoney(value, listing.currency);
  element('per-person-price').textContent = costs ? money(costs.monthlyPerPerson) : 'Nájem neuveden';
  element('per-person-label').hidden = !costs;
  element('monthly-total').textContent = costs ? money(costs.monthlyTotal) : 'Nelze vypočítat';
  element('monthly-per-person').textContent = costs ? money(costs.monthlyPerPerson) : 'Nelze vypočítat';
  document.querySelectorAll('.move-in-row').forEach((row) => { row.hidden = !includeDeposit || !costs; });
  if (costs) {
    element('move-in-total').textContent = money(costs.moveInTotal);
    element('move-in-per-person').textContent = money(costs.moveInPerPerson);
  }
  element('cost-note').textContent = !costs ? 'Pro výpočet je potřeba znát měsíční nájem.'
    : costs.feesUnknown ? 'Orientační výpočet bez poplatků, které nejsou v nabídce uvedeny.'
    : 'Orientační výpočet při rovnoměrném rozdělení nákladů. Kauce se nepřičítá k měsíční ceně.';
}

function renderDetail() {
  const money = (value) => value == null ? 'Neuvedeno' : formatMoney(value, listing.currency);
  document.title = `${listing.title} — realCity`;
  element('detail-title').textContent = listing.title;
  element('detail-location').textContent = listing.address || listing.city || 'Lokalita neuvedena';
  element('detail-disposition').textContent = listing.disposition === 'studio' ? 'Garsoniéra' : listing.disposition || 'Neuvedeno';
  element('detail-area').textContent = listing.area == null ? 'Neuvedeno' : `${number.format(listing.area)} m²`;
  const rent = listing.transaction_type === 'rent';
  element('detail-price-label').textContent = rent ? 'Měsíční nájem' : 'Kupní cena';
  element('detail-price').textContent = money(listing.price);
  element('detail-fees').textContent = money(listing.charges);
  element('detail-fees-row').hidden = !rent;
  element('detail-description').textContent = listing.description || 'Popis není k dispozici.';
  element('availability-notice').hidden = !listing.is_deleted && !listing.is_reserved;
  element('availability-notice').textContent = listing.is_deleted
    ? 'Tato nabídka již není dostupná. Zobrazené údaje pocházejí z uložené nabídky.' : 'Tato nabídka je rezervována.';
  const original = safeUrl(listing.url);
  if (original) { element('original-listing').href = original; element('original-listing').hidden = false; }
  const urls = [...new Set([listing.main_image, ...(listing.images || [])].map(safeUrl).filter(Boolean))];
  const image = element('detail-image');
  function showImage(url) {
    image.hidden = false;
    element('detail-photo-placeholder').hidden = true;
    image.src = url;
  }
  image.addEventListener('error', () => { image.hidden = true; element('detail-photo-placeholder').hidden = false; });
  if (urls.length) showImage(urls[0]);
  element('detail-gallery').replaceChildren(...urls.slice(0, 12).map((url, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-label', `Zobrazit fotografii ${index + 1}`);
    const thumbnail = document.createElement('img');
    thumbnail.src = url; thumbnail.alt = ''; thumbnail.loading = 'lazy';
    button.append(thumbnail);
    button.addEventListener('click', () => showImage(url));
    return button;
  }));
  element('calculator').hidden = !rent;
  if (rent) {
    element('base-rent').textContent = money(listing.price);
    element('base-fees').textContent = money(listing.charges);
    element('base-deposit').textContent = money(listing.deposit);
    element('fees-per-person').disabled = listing.charges == null;
    element('include-deposit').disabled = listing.deposit == null;
    element('deposit-note').hidden = listing.deposit != null;
    const range = recommendedOccupancy(listing.disposition, listing.area);
    element('people').value = String(range?.[0] || 2);
    updateCalculator();
  }
  element('detail').hidden = false;
}

async function loadDetail() {
  element('detail-error').hidden = true;
  element('detail-status').textContent = 'Načítáme nabídku…';
  try {
    const [, , source, externalId] = location.pathname.split('/');
    const response = await fetch(`/listings/${encodeURIComponent(decodeURIComponent(source))}/${encodeURIComponent(decodeURIComponent(externalId))}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    listing = await response.json();
    renderDetail();
  } catch {
    element('detail-error').hidden = false;
  } finally {
    element('detail-status').textContent = '';
  }
}
for (const id of ['people', 'fees-per-person', 'include-deposit']) element(id).addEventListener('input', updateCalculator);
element('detail-retry').addEventListener('click', loadDetail);
loadDetail();
