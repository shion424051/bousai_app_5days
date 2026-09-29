import io
import json
import unittest
from unittest.mock import patch

import app


def encode_polyline6(points):
    encoded = []
    previous = [0, 0]
    for latitude, longitude in points:
        for component, value in enumerate((
            round(latitude * 1_000_000),
            round(longitude * 1_000_000)
        )):
            delta = value - previous[component]
            previous[component] = value
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                encoded.append(chr((0x20 | (value & 0x1f)) + 63))
                value >>= 5
            encoded.append(chr(value + 63))
    return ''.join(encoded)


class EvacuationRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_page_displays_both_departure_tabs_and_all_registered_shelters(self):
        response = self.client.get('/evacuation_route')
        self.assertEqual(response.status_code, 200)
        self.assertIn('避難経路表示画面'.encode(), response.data)
        self.assertIn('現在地から'.encode(), response.data)
        self.assertIn('自宅から'.encode(), response.data)
        for shelter in app.shelters:
            self.assertIn(shelter['name'].encode(), response.data)

    def test_fushimi_danger_circle_is_registered_and_drawn(self):
        response = self.client.get('/api/evacuation/danger_places')
        self.assertEqual(response.status_code, 200)
        fushimi = next(place for place in response.json if place['name'] == '伏見駅')
        self.assertEqual(fushimi['radius_m'], 1000)
        self.assertTrue(all(place['radius_m'] == 1000 for place in response.json))
        page = self.client.get('/evacuation_route').get_data(as_text=True)
        self.assertIn('dangerRadius', page)

        legacy_response = self.client.get('/api/danger_places')
        legacy_flood = next(place for place in legacy_response.json if place['name'] == '川の氾濫')
        self.assertNotIn('radius_m', legacy_flood)

    def test_route_segment_crossing_danger_circle_is_rejected(self):
        route = [(35.1696587, 136.88), (35.1696587, 136.92)]
        self.assertFalse(app.is_route_safe(route, [{'latitude': 35.1696587,
                                                   'longitude': 136.9011239,
                                                   'radius_m': 1000}]))

    def test_route_is_checked_against_dangers_not_sent_to_provider(self):
        dangers = app.load_route_dangers()
        origin, destination = (35.15, 136.87), (35.15, 136.88)
        nearby = app.get_relevant_route_dangers(origin, destination, dangers)
        self.assertEqual([place['name'] for place in nearby], ['伏見駅'])

        fushimi = next(place for place in dangers if place['name'] == '伏見駅')
        shape = encode_polyline6([
            (fushimi['latitude'], fushimi['longitude'] - 0.02),
            (fushimi['latitude'], fushimi['longitude'] + 0.02)
        ])
        response = io.BytesIO(json.dumps({
            'trip': {
                'summary': {'length': 1, 'time': 600},
                'legs': [{'shape': shape}]
            }
        }).encode())
        with patch.object(app.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(ValueError):
                app.get_valhalla_route(
                    origin, destination, [], validation_dangers=dangers
                )

    def test_route_outside_danger_circle_is_accepted(self):
        route = [(35.19, 136.88), (35.19, 136.92)]
        self.assertTrue(app.is_route_safe(route, [{'latitude': 35.1696587,
                                                   'longitude': 136.9011239,
                                                   'radius_m': 1000}]))

    def test_sakae_station_is_inside_fushimi_danger_area(self):
        distance = app.calculate_distance_meters(
            35.1696587, 136.9011239, 35.1693885, 136.90861
        )
        self.assertLess(distance, 1000)

    def test_exclusion_polygons_enclose_circles_within_provider_vertex_limit(self):
        dangers = app.load_route_dangers()
        polygons = [app.danger_exclusion_polygon(place) for place in dangers]
        self.assertTrue(all(polygon[0] == polygon[-1] for polygon in polygons))
        self.assertLessEqual(sum(len(polygon) for polygon in polygons), 100)
        fushimi = next(place for place in dangers if place['name'] == '伏見駅')
        for longitude, latitude in polygons[-1][:-1]:
            self.assertGreaterEqual(
                app.calculate_distance_meters(
                    latitude, longitude, fushimi['latitude'], fushimi['longitude']
                ),
                fushimi['radius_m'] + app.ROUTE_SAFETY_MARGIN_METERS
            )

    def test_valhalla_route_is_decoded_and_distance_order_is_preserved(self):
        shape = encode_polyline6([(35.15, 136.88), (35.15, 136.87)])
        response_data = {
            'trip': {
                'summary': {'length': 1.2, 'time': 900},
                'legs': [{'shape': shape}]
            }
        }
        response = io.BytesIO(json.dumps(response_data).encode())
        with patch.object(app, 'shelters', [
            {'id': 1, 'name': '遠い避難所', 'latitude': 35.15, 'longitude': 136.88},
            {'id': 2, 'name': '栄駅', 'latitude': 35.1693885, 'longitude': 136.90861},
            {'id': 3, 'name': '近い避難所', 'latitude': 35.15, 'longitude': 136.87},
            {'id': 4, 'name': '位置未登録'}
        ]), patch.object(app.urllib.request, 'urlopen', return_value=response) as urlopen:
            result = app.get_valhalla_route(
                (35.15, 136.88), (35.15, 136.87), app.load_route_dangers()
            )
            self.assertEqual(result['distance_m'], 1200)
            self.assertEqual(result['geometry']['type'], 'LineString')
            request_payload = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual(request_payload['costing'], 'pedestrian')
            self.assertTrue(request_payload['exclude_polygons'])

            with patch.object(app, 'get_valhalla_route', side_effect=[
                {'distance_m': 1200, 'duration_s': 900,
                 'geometry': {'type': 'LineString', 'coordinates': []}},
                 {'distance_m': 900, 'duration_s': 700,
                  'geometry': {'type': 'LineString', 'coordinates': []}},
            ]):
                response = self.client.post('/api/evacuation/routes', json={
                    'latitude': 35.15, 'longitude': 136.88
                })
        self.assertEqual(response.status_code, 200)
        results = response.json['results']
        self.assertEqual([item['name'] for item in results[:2]], ['近い避難所', '遠い避難所'])
        self.assertEqual([item['distance_m'] for item in results[:2]], [900, 1200])
        self.assertEqual(results[2]['status'], 'danger_zone')
        self.assertEqual(results[3]['status'], 'unlocated')

    def test_geocoder_not_found_and_service_failure_are_reported(self):
        with patch.object(app, 'GEOCODER_LAST_REQUEST_AT', 0), patch.object(
            app.urllib.request, 'urlopen', return_value=io.BytesIO(b'[]')
        ):
            response = self.client.post('/api/evacuation/geocode', json={'address': '存在しない場所'})
        self.assertEqual(response.status_code, 404)
        self.assertIn('見つかりません', response.json['error'])

        with patch.object(app, 'GEOCODER_LAST_REQUEST_AT', 0), patch.object(
            app.urllib.request, 'urlopen', side_effect=OSError('offline')
        ):
            response = self.client.post('/api/evacuation/geocode', json={'address': '名古屋駅'})
        self.assertEqual(response.status_code, 502)

    def test_origin_in_danger_and_no_safe_routes_are_reported(self):
        response = self.client.post('/api/evacuation/routes', json={
            'latitude': 35.1696587, 'longitude': 136.9011239
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['status'], 'origin_in_danger')

        with patch.object(app, 'shelters', [
            {'id': 1, 'name': '避難所', 'latitude': 35.15, 'longitude': 136.88}
        ]), patch.object(app, 'get_valhalla_route', side_effect=OSError('offline')):
            response = self.client.post('/api/evacuation/routes', json={
                'latitude': 35.15, 'longitude': 136.87
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results'][0]['status'], 'route_unavailable')
        self.assertEqual(response.json['provider_failures'], 1)

    def test_invalid_requests_and_malformed_provider_data_are_handled(self):
        response = self.client.post('/api/evacuation/routes', json=[])
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/evacuation/geocode', json={'address': 123})
        self.assertEqual(response.status_code, 400)

        with patch.object(app, 'shelters', [
            {'id': 1, 'name': '避難所', 'latitude': 35.15, 'longitude': 136.88}
        ]), patch.object(app.urllib.request, 'urlopen', return_value=io.BytesIO(b'[]')):
            response = self.client.post('/api/evacuation/routes', json={
                'latitude': 35.15, 'longitude': 136.87
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results'][0]['status'], 'route_unavailable')

    def test_malformed_route_shape_is_never_accepted(self):
        with self.assertRaises(ValueError):
            app.decode_polyline6('!')
        self.assertFalse(app.is_route_safe([(35.15, 136.88)], []))


if __name__ == '__main__':
    unittest.main()