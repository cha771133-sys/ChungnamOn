from django.test import TestCase, override_settings


class HealthTests(TestCase):
    def test_health(self):
        for url in ('/api/health', '/api/health/'):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'status': 'ok'})


class CorsTests(TestCase):
    def allowed_origin(self, origin):
        response = self.client.get('/api/health/', HTTP_ORIGIN=origin)
        return response.headers.get('access-control-allow-origin')

    def test_vercel_preview_origin_is_allowed(self):
        for origin in (
            'https://chungnam-on-git-dev-cha771133-1587s-projects.vercel.app',
            'https://chungnam-on-git-docs-readme-title-cha771133-1587s-projects.vercel.app',
            'https://chungnam-on-git-feature-be-depl-3c03ed-cha771133-1587s-projects.vercel.app',
            'https://chungnam-on-abc123xyz-cha771133-1587s-projects.vercel.app',
        ):
            with self.subTest(origin=origin):
                self.assertEqual(self.allowed_origin(origin), origin)

    def test_other_origins_are_rejected(self):
        for origin in (
            'https://example.com',
            'https://other-project.vercel.app',
            'https://chungnam-on-git-dev-other-account.vercel.app',
            'https://chungnam-on-git-dev-cha771133-1587s-projects.vercel.app.example.com',
            'http://chungnam-on-git-dev-cha771133-1587s-projects.vercel.app',
        ):
            with self.subTest(origin=origin):
                self.assertIsNone(self.allowed_origin(origin))

    @override_settings(CORS_ALLOWED_ORIGINS=['https://chungnam-on.vercel.app'])
    def test_allowed_origins_still_apply(self):
        origin = 'https://chungnam-on.vercel.app'

        self.assertEqual(self.allowed_origin(origin), origin)
