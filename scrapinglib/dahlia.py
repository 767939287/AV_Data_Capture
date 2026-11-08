# -*- coding: utf-8 -*-

from .wp_works import WordPressWorks


class Dahlia(WordPressWorks):
    """DAHLIA 官网（dahlia-av.jp）。站内只有单一厂牌，片商与发行商同名。"""

    source = 'dahlia'
    base_url = 'https://dahlia-av.jp'
    studio = 'DAHLIA'
    default_publisher = 'DAHLIA'
