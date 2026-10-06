import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { existsSync } from 'node:fs';
const helper = process.env.CALCULATOR_MODULE || new URL('../../web/roommate.mjs', import.meta.url).pathname;
test('calculator provides the listing cost and occupancy helpers', async () => {
  assert.ok(existsSync(helper), 'Calculator helper is missing');
  const { calculateCosts, recommendedOccupancy, formatMoney } = await import(pathToFileURL(helper));
  const listing = { price: 24000, charges: 2000, deposit: 30000 };
  assert.deepEqual(calculateCosts(listing, 3, true, true), {
    monthlyFees: 6000, monthlyTotal: 30000, monthlyPerPerson: 10000,
    moveInTotal: 60000, moveInPerPerson: 20000, feesUnknown: false,
  });
  assert.equal(calculateCosts(listing, 3, false, false).monthlyTotal, 26000);
  assert.equal(calculateCosts(listing, 3, true, false).moveInTotal, 30000);
  assert.equal(calculateCosts({ price: 24000, charges: null }, 2, true, true).monthlyTotal, 24000);
  assert.equal(calculateCosts({ price: 24000, charges: null }, 2).feesUnknown, true);
  assert.equal(calculateCosts({ price: null }, 2), null);
  assert.equal(calculateCosts({ price: 0, charges: 0 }, 1).monthlyTotal, 0);
  assert.throws(() => calculateCosts(listing, 0), RangeError);
  assert.throws(() => calculateCosts(listing, 1.5), RangeError);
  for (const [layout, range] of [
    ['studio', [1, 2]], ['1+kk', [1, 2]], ['1+1', [1, 2]],
    ['2+kk', [2, 3]], ['2+1', [2, 3]], ['3+kk', [2, 4]],
    ['3+1', [2, 4]], ['4+kk', [3, 5]], ['4+1', [3, 5]],
  ]) assert.deepEqual(recommendedOccupancy(layout, null), range);
  assert.deepEqual(recommendedOccupancy('2+kk', 25), [2, 2]);
  assert.deepEqual(recommendedOccupancy('2+kk', 100), [2, 4]);
  assert.deepEqual(recommendedOccupancy('3+kk', 75), [2, 4]);
  assert.equal(recommendedOccupancy(null, null), null);
  assert.equal(recommendedOccupancy('unknown', 50), null);
  assert.equal(formatMoney(10000).replace(/\s/g, ' '), '10 000 Kč');
});
