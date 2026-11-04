from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, override_settings


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
class ErrorPageTests(SimpleTestCase):
    def test_404_uses_branded_template(self):
        response = Client().get('/nonexistent-url-xyz/')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, '404', status_code=404)
        self.assertContains(response, 'На главную', status_code=404)

    def test_500_template_renders(self):
        html = render_to_string('500.html')

        self.assertIn('500', html)
        self.assertIn('На главную', html)
