# -*- coding: utf-8 -*-


from .parser import Parser


class Tmdb(Parser):
    """
    两种实现,带apikey与不带key
    apikey
    """
    source = 'tmdb'
    imagecut = 0
    apikey = None

    expr_title = '//head/meta[@property="og:title"]/@content'
    expr_release = '//div/span[@class="release"]/text()'
    expr_cover = '//head/meta[@property="og:image"]/@content'
    expr_outline = '//head/meta[@property="og:description"]/@content'

    # def search(self, number):
    #     self.detailurl = self.queryNumberUrl(number)
    #     detailpage = self.getHtml(self.detailurl)

    def queryNumberUrl(self, number):
        """
        TODO 区分 ID 与 名称
        """
        id  = number
        movieUrl = "https://www.themoviedb.org/movie/" + id + "?language=zh-CN"
        return movieUrl

    def getCover(self, htmltree):
        url = self.getTreeElement(htmltree, self.expr_cover).strip()
        # og:image 本身是 image.tmdb.org 绝对地址，只在相对路径时才补站点前缀
        if url and not url.startswith('http'):
            url = "https://www.themoviedb.org" + url
        return url

