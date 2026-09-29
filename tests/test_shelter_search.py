import io
import json
import unittest
from unittest.mock import patch

import app


class ShelterSearchTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_nearby_api_returns_five_valid_shelters_in_distance_order(self):
        located = [
            {
                'id': index,
                'name': f'避難所{index}',
                'address': f'住所{index}',
                'latitude': 40 + index * 0.01,
                'longitude': 140
            }
            for index in range(1, 8)
        ]
        located.extend([
            {'id': 8, 'name': '範囲外', 'latitude': 91, 'longitude': 140},
            {'id': 9, 'name': '文字座標', 'latitude': '40', 'longitude': 140},
            {'id': 10, 'name': '真偽値座標', 'latitude': 40, 'longitude': True}
        ])
        with patch.object(app, 'shelters', located):
            response = self.client.get('/api/shelters/nearby?latitude=40&longitude=140')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['unlocated_count'], 3)
        results = response.json['shelters']
        self.assertEqual(len(results), 5)
        self.assertEqual([item['name'] for item in results], [f'避難所{i}' for i in range(1, 6)])
        self.assertEqual(results[0]['address'], '住所1')

    def test_reverse_geocode_validates_coordinates_and_returns_town(self):
        invalid = self.client.post('/api/shelters/reverse-geocode', json={
            'latitude': 91, 'longitude': 140
        })
        self.assertEqual(invalid.status_code, 400)

        response_body = json.dumps({'results': {'lv01Nm': '青森市中央'}}).encode()
        with patch.object(
            app.urllib.request, 'urlopen', return_value=io.BytesIO(response_body)
        ) as urlopen:
            response = self.client.post('/api/shelters/reverse-geocode', json={
                'latitude': 40.8296, 'longitude': 140.7348
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['town'], '青森市中央')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 8)

    def test_reverse_geocode_service_failure_is_reported(self):
        with patch.object(app.urllib.request, 'urlopen', side_effect=TimeoutError):
            response = self.client.post('/api/shelters/reverse-geocode', json={
                'latitude': 40.8296, 'longitude': 140.7348
            })
        self.assertEqual(response.status_code, 502)

    def test_route_api_returns_valid_walking_geometry_and_metrics(self):
        route_data = {
            'code': 'Ok',
            'routes': [{
                'distance': 850,
                'duration': 720,
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[140.7348, 40.8296], [140.73, 40.82]]
                },
                'legs': [{'steps': [{
                    'maneuver': {'type': 'depart', 'modifier': ''},
                    'name': '駅前通り',
                    'distance': 100
                }]}]
            }]
        }
        with patch.object(
            app.urllib.request, 'urlopen',
            return_value=io.BytesIO(json.dumps(route_data).encode())
        ) as urlopen:
            response = self.client.get(
                '/api/shelters/route?origin_lat=40.8296&origin_lon=140.7348'
                '&destination_lat=40.82&destination_lon=140.73'
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['distance_m'], 850)
        self.assertEqual(response.json['duration_s'], 720)
        self.assertEqual(response.json['geometry']['type'], 'LineString')
        self.assertEqual(response.json['steps'][0]['name'], '駅前通り')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 15)

    def test_route_api_rejects_invalid_coordinates_and_provider_geometry(self):
        invalid = self.client.get(
            '/api/shelters/route?origin_lat=91&origin_lon=140'
            '&destination_lat=40&destination_lon=140'
        )
        self.assertEqual(invalid.status_code, 400)

        malformed = {'code': 'Ok', 'routes': [{'geometry': [], 'legs': []}]}
        with patch.object(
            app.urllib.request, 'urlopen',
            return_value=io.BytesIO(json.dumps(malformed).encode())
        ):
            response = self.client.get(
                '/api/shelters/route?origin_lat=40&origin_lon=140'
                '&destination_lat=40.1&destination_lon=140.1'
            )
        self.assertEqual(response.status_code, 502)

    def test_route_api_reports_provider_failure_and_missing_route(self):
        route_url = (
            '/api/shelters/route?origin_lat=40&origin_lon=140'
            '&destination_lat=40.1&destination_lon=140.1'
        )
        with patch.object(app.urllib.request, 'urlopen', side_effect=TimeoutError):
            response = self.client.get(route_url)
        self.assertEqual(response.status_code, 502)

        no_route = json.dumps({'code': 'NoRoute', 'routes': []}).encode()
        with patch.object(
            app.urllib.request, 'urlopen', return_value=io.BytesIO(no_route)
        ):
            response = self.client.get(route_url)
        self.assertEqual(response.status_code, 404)

    def test_search_and_home_pages_expose_location_and_route_states(self):
        search = self.client.get('/shelter_search')
        home = self.client.get('/')
        self.assertEqual(search.status_code, 200)
        self.assertEqual(home.status_code, 200)
        self.assertIn('青森駅をテスト地点にする'.encode(), search.data)
        self.assertIn('青森駅をテスト地点にする'.encode(), home.data)
        self.assertIn('aria-live="polite"'.encode(), home.data)
        self.assertIn('安全を保証するものではありません'.encode(), home.data)


if __name__ == '__main__':
    unittest.main()
