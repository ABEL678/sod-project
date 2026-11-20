from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.urls import reverse
from django.views.generic import RedirectView, TemplateView

from .content import DEFAULT_SLUG, get_article, load_articles, toc_tree


class HelpHomeView(LoginRequiredMixin, RedirectView):
    """`/help/` → статья `start`."""

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse('help:article', args=[DEFAULT_SLUG])


class HelpArticleView(LoginRequiredMixin, TemplateView):
    template_name = 'help/article.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = kwargs['slug']
        article = get_article(slug)
        if article is None:
            raise Http404('Статья справки не найдена')

        articles = load_articles()
        ordered = sorted(articles.values(), key=lambda a: a.order)
        index = next(i for i, a in enumerate(ordered) if a.slug == slug)

        context.update({
            'article': article,
            'sections': toc_tree(articles),
            'prev_article': ordered[index - 1] if index > 0 else None,
            'next_article': ordered[index + 1] if index + 1 < len(ordered) else None,
            'breadcrumbs': [
                ('Помощь', reverse('help:home')),
                (article.section, None),
                (article.title, None),
            ],
        })
        return context
