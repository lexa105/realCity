const money = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 2 });
export function formatMoney(value, currency = 'CZK') {
  return `${money.format(value)} ${currency === 'CZK' ? 'Kč' : currency}`;
}

// Layout sets the baseline. Less than 15 m² per recommended occupant is small;
// more than 30 m² is large. Area adjusts only the upper bound, by one person.
export function recommendedOccupancy(disposition, area) {
  const ranges = {
    studio: [1, 2], '1+kk': [1, 2], '1+1': [1, 2],
    '2+kk': [2, 3], '2+1': [2, 3], '3+kk': [2, 4], '3+1': [2, 4],
    '4+kk': [3, 5], '4+1': [3, 5],
  };
  const range = ranges[disposition?.toLowerCase().replace(/\s/g, '')];
  if (!range) return null;
  const [min, max] = range;
  if (area > 0 && area < max * 15) return [min, Math.max(min, max - 1)];
  if (area > max * 30) return [min, Math.min(6, max + 1)];
  return [min, max];
}

export function calculateCosts(listing, people, feesArePerPerson = false, includeDeposit = false) {
  if (!Number.isInteger(people) || people < 1 || people > 6) {
    throw new RangeError('People must be an integer between 1 and 6');
  }
  if (listing.price == null) return null;
  const monthlyFees = (listing.charges ?? 0) * (feesArePerPerson ? people : 1);
  const monthlyTotal = listing.price + monthlyFees;
  // The current API has no deposit field. A missing value never becomes a guess.
  const moveInTotal = monthlyTotal + (includeDeposit ? (listing.deposit ?? 0) : 0);
  return {
    monthlyFees, monthlyTotal, monthlyPerPerson: monthlyTotal / people,
    moveInTotal, moveInPerPerson: moveInTotal / people,
    feesUnknown: listing.charges == null,
  };
}
