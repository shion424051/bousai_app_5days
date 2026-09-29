import unittest

import app


class AdminLoginTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_successful_login_opens_admin_shelter_registration(self):
        response = self.client.post(
            '/login', data={'password': '123'}, follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, '/shelter_add')
        self.assertIn('避難所を追加'.encode(), response.data)
        with self.client.session_transaction() as session:
            self.assertTrue(session['logged_in'])
            self.assertEqual(session['username'], 'admin')

    def test_login_page_does_not_display_password_hint(self):
        response = self.client.get('/login')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('123'.encode(), response.data)
        self.assertIn('管理者パスワード'.encode(), response.data)


if __name__ == '__main__':
    unittest.main()