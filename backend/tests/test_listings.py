import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.ingest import import_items
from app.main import create_app


class ListingPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.client = TestClient(create_app(self.engine))
        self.client.__enter__()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.client.__exit__, None, None, None)
        base = dict(url='https://example.com/flat', title='Byt', transactionType='rent',
                    propertyType='flat', city='Praha', currency='CZK', scrapedAt='2026-09-26T10:00:00Z')
        import_items(self.engine, [
            {**base, 'id': '1', 'price': 12000, 'area': 25, 'disposition': '1+kk'},
            {**base, 'id': '2', 'price': 20000, 'area': 50, 'disposition': '2+kk'},
            {**base, 'id': '3', 'price': 25000, 'area': 75, 'disposition': '3+1'},
            {**base, 'id': '4'},
        ])

    def test_filters_apply_before_pagination(self) -> None:
        response = self.client.get('/listings?min_price=15000&max_price=25000&min_area=40&max_area=80&disposition=2%2Bkk&disposition=3%2B1&limit=1&offset=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['external_id'] for row in response.json()], ['3'])

    def test_missing_area_is_excluded_only_when_filtering(self) -> None:
        self.assertEqual(len(self.client.get('/listings').json()), 4)
        self.assertEqual(len(self.client.get('/listings?max_area=100').json()), 3)

    def test_invalid_ranges_are_rejected(self) -> None:
        for query in ['min_area=60&max_area=20', 'min_price=100&max_price=50', 'min_area=-1', 'max_area=nan']:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/listings?' + query).status_code, 422)

    def test_frontend_is_served_with_api(self) -> None:
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('listing-template', response.text)
        self.assertEqual(self.client.get('/static/app.js').status_code, 200)
        self.assertEqual(self.client.get('/static/styles.css').status_code, 200)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_detail_page_and_missing_listing(self) -> None:
        response = self.client.get('/listing/bezrealitky/1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('Spolubydlící', response.text)
        self.assertEqual(self.client.get('/listing/bezrealitky/missing').status_code, 404)
        listing = self.client.get('/listings/bezrealitky/1').json()
        self.assertEqual(listing['price'], 12000)
        self.assertEqual(listing['area'], 25)


if __name__ == '__main__':
    unittest.main()
