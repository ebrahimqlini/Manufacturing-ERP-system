from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_uses_arabic_rtl_shell(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="ar" dir="rtl"')
