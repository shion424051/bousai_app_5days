import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class HazardReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temporary_directory.name)
        self.reports_path = self.temp_path / 'hazard_reports.json'
        self.seen_path = self.temp_path / 'hazard_seen.json'
        self.upload_path = self.temp_path / 'uploads'
        self.patches = [
            patch.object(app, 'HAZARD_REPORTS_FILE', str(self.reports_path)),
            patch.object(app, 'HAZARD_SEEN_FILE', str(self.seen_path)),
            patch.object(app, 'HAZARD_UPLOAD_DIR', str(self.upload_path))
        ]
        for patcher in self.patches:
            patcher.start()
        self.client = app.app.test_client()
        self.original_danger_data = Path(app.DANGER_PLACES_FILE).read_bytes()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.assertEqual(Path(app.DANGER_PLACES_FILE).read_bytes(), self.original_danger_data)
        self.temporary_directory.cleanup()

    def post_report(self, **overrides):
        data = {
            'name': '通学路の冠水',
            'type': '道路冠水',
            'latitude': '40.8246',
            'longitude': '140.7400'
        }
        data.update(overrides)
        return self.client.post('/hazard_report', data=data)

    def test_anonymous_post_accepts_optional_fields_omitted_and_legacy_url(self):
        for path in ('/hazard_report', '/shelter_register'):
            self.assertEqual(self.client.get(path).status_code, 200)

        response = self.post_report()

        self.assertEqual(response.status_code, 200)
        self.assertIn('危険箇所を投稿しました'.encode(), response.data)
        stored = json.loads(self.reports_path.read_text(encoding='utf-8'))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['address'], '')
        self.assertEqual(stored[0]['description'], '')
        self.assertEqual(stored[0]['attachments'], [])

    def test_required_fields_categories_and_coordinates_are_validated(self):
        invalid_reports = [
            {'name': '', 'type': '津波', 'latitude': '40', 'longitude': '140'},
            {'name': '位置なし', 'type': '津波', 'latitude': '', 'longitude': ''},
            {'name': '範囲外', 'type': '津波', 'latitude': '91', 'longitude': '140'},
            {'name': 'カテゴリ外', 'type': '未登録', 'latitude': '40', 'longitude': '140'}
        ]
        for invalid in invalid_reports:
            response = self.post_report(**invalid)
            self.assertEqual(response.status_code, 200)
            self.assertIn('role="alert"'.encode(), response.data)
        self.assertFalse(self.reports_path.exists())

    def test_uploads_store_unique_names_and_public_metadata(self):
        response = self.client.post('/hazard_report', data={
            'name': '現場動画', 'type': '倒木・落下物',
            'latitude': '40.82', 'longitude': '140.74',
            'attachments': [
                (io.BytesIO(b'image bytes'), '现场.jpg', 'image/jpeg'),
                (io.BytesIO(b'video bytes'), 'clip.webm', 'video/webm')
            ]
        }, content_type='multipart/form-data')

        self.assertEqual(response.status_code, 200)
        report = json.loads(self.reports_path.read_text(encoding='utf-8'))[0]
        self.assertEqual([item['media_type'] for item in report['attachments']], ['image', 'video'])
        self.assertEqual([item['size'] for item in report['attachments']], [11, 11])
        stored_names = [Path(item['url']).name for item in report['attachments']]
        self.assertTrue(all(name for name in stored_names))
        self.assertEqual(len(set(stored_names)), 2)
        self.assertTrue(all((self.upload_path / name).is_file() for name in stored_names))
        public = next(item for item in self.client.get('/api/hazard_spots').json if item['name'] == '現場動画')
        self.assertEqual(public['attachments'][0]['display_name'], '现场.jpg')

    def test_unsupported_upload_and_oversized_file_are_rejected(self):
        unsupported = self.client.post('/hazard_report', data={
            'name': '添付形式エラー', 'type': 'その他', 'latitude': '40', 'longitude': '140',
            'attachments': (io.BytesIO(b'data'), 'payload.txt', 'text/plain')
        }, content_type='multipart/form-data')
        self.assertIn('対応する画像・動画形式'.encode(), unsupported.data)

        oversized = self.client.post('/hazard_report', data={
            'name': '添付サイズエラー', 'type': 'その他', 'latitude': '40', 'longitude': '140',
            'attachments': (io.BytesIO(b'x' * (20 * 1024 * 1024 + 1)), 'large.jpg', 'image/jpeg')
        }, content_type='multipart/form-data')
        self.assertIn('1ファイルは20MB以下'.encode(), oversized.data)
        self.assertFalse(self.reports_path.exists())
        self.assertFalse(self.upload_path.exists())

    def test_file_count_and_total_request_size_are_limited(self):
        too_many = self.client.post('/hazard_report', data={
            'name': '添付件数エラー', 'type': 'その他', 'latitude': '40', 'longitude': '140',
            'attachments': [
                (io.BytesIO(b'image'), f'image-{index}.jpg', 'image/jpeg')
                for index in range(6)
            ]
        }, content_type='multipart/form-data')
        self.assertIn('添付ファイルは5件まで'.encode(), too_many.data)

        with patch.dict(app.app.config, {'MAX_CONTENT_LENGTH': 1024}):
            oversized_request = self.client.post('/hazard_report', data={
                'name': '全体サイズエラー', 'type': 'その他',
                'latitude': '40', 'longitude': '140', 'description': 'x' * 2048
            })
        self.assertEqual(oversized_request.status_code, 413)
        self.assertIn('投稿全体のサイズは50MB以下'.encode(), oversized_request.data)
        self.assertFalse(self.reports_path.exists())

    def test_seen_is_idempotent_and_voter_hash_is_not_public(self):
        self.post_report()
        spot = next(item for item in self.client.get('/api/hazard_spots').json if item['name'] == '通学路の冠水')
        browser_id = 'browser-identity-for-test'
        url = f"/api/hazard_spots/{spot['id']}/seen"

        first = self.client.post(url, json={'browser_id': browser_id})
        second = self.client.post(url, json={'browser_id': browser_id})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json, {'confirmed': True, 'seen_count': 1})
        self.assertEqual(second.json['seen_count'], 1)
        self.assertEqual(second.json['confirmed'], True)
        public_json = json.dumps(self.client.get('/api/hazard_spots').json)
        self.assertNotIn(browser_id, public_json)
        self.assertNotIn(hashlib.sha256(browser_id.encode()).hexdigest(), public_json)

    def test_reverse_geocode_caches_results_and_uses_descriptive_user_agent(self):
        app.HAZARD_GEOCODE_CACHE.clear()
        response_data = json.dumps({
            'name': '市民会館', 'display_name': '青森市中央一丁目, 青森市, 青森県',
            'address': {'road': '中央一丁目'}
        }).encode()
        with patch.object(app.urllib.request, 'urlopen', return_value=io.BytesIO(response_data)) as urlopen:
            first = self.client.post('/api/hazard_reports/reverse-geocode', json={
                'latitude': 40.8246, 'longitude': 140.7400
            })
            second = self.client.post('/api/hazard_reports/reverse-geocode', json={
                'latitude': 40.8246, 'longitude': 140.7400
            })

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json['name'], '市民会館')
        self.assertEqual(first.json['address'], '青森市中央一丁目, 青森市, 青森県')
        self.assertEqual(second.json, first.json)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn('user-agent', {
            name.lower() for name in urlopen.call_args.args[0].headers
        })
        self.assertEqual(self.client.post('/api/hazard_reports/reverse-geocode', json={
            'latitude': 91, 'longitude': 140
        }).status_code, 400)


if __name__ == '__main__':
    unittest.main()