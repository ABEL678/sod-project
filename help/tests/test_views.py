from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class HelpViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='helpuser', password='pw-help-12345')

    def setUp(self):
        self.client.force_login(self.user)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get('/help/', follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

        response = self.client.get('/help/start/', follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_home_redirects_to_start(self):
        response = self.client.get('/help/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('help:article', args=['start']))

    def test_start_article_renders(self):
        response = self.client.get(reverse('help:article', args=['start']))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'help/article.html')
        self.assertContains(response, 'help-toc')

    def test_unknown_slug_returns_404(self):
        response = self.client.get('/help/no-such-article/')
        self.assertEqual(response.status_code, 404)

    def test_article_has_toc_and_breadcrumbs(self):
        response = self.client.get(reverse('help:article', args=['issues']))
        self.assertContains(response, 'help-toc__link')
        self.assertContains(response, 'help-breadcrumbs')
        self.assertContains(response, 'Помощь')
        self.assertContains(response, 'Замечания')

    def test_article_has_prev_next_navigation(self):
        response = self.client.get(reverse('help:article', args=['documents']))
        self.assertContains(response, reverse('help:article', args=['start']))
        self.assertContains(response, reverse('help:article', args=['issues']))

    def test_topbar_help_link_present(self):
        response = self.client.get(reverse('help:article', args=['start']))
        self.assertContains(response, 'bi-question-circle')
        self.assertContains(response, f'href="{reverse("help:article", args=["start"])}"')
