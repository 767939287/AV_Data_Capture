# -*- coding: utf-8 -*-

from .wp_works import WordPressWorks


class Faleno(WordPressWorks):
    """FALENO 集团官网（faleno.jp）。同站还发布 maryGOLD 与 JimmyScandal 番号，发行商按番号前缀判定。"""

    source = 'faleno'
    base_url = 'https://faleno.jp'
    rest_prefix = '/top'
    studio = 'FALENO'
    default_publisher = 'FALENOstar'
    publisher_prefixes = {'mgold': 'maryGOLD', 'jimmy': 'JimmyScandal'}
