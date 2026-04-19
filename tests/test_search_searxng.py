# -*- coding: utf-8 -*-
"""
Unit tests for SearXNG search provider public-instance rotation and failover.
"""

import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SearchService, SearXNGSearchProvider


class TestSearXNGSearchProvider(unittest.TestCase):
    """Tests for SearXNG search provider."""

    def setUp(self) -> None:
        SearXNGSearchProvider.reset_public_instance_cache()
        # Clear penalized-instance state if the provider implements it.
        if hasattr(SearXNGSearchProvider, "_penalized_instances"):
            SearXNGSearchProvider._penalized_instances.clear()

    def _create_provider(
        self,
        base_urls=None,
        *,
        use_public_instances: bool = False,
    ) -> SearXNGSearchProvider:
        return SearXNGSearchProvider(
            base_urls=base_urls or [],
            use_public_instances=use_public_instances,
        )

    @staticmethod
    def _response(
        *,
        status_code: int = 200,
        json_payload=None,
        text: str = "",
        headers=None,
        json_side_effect=None,
    ) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.headers = headers or {"content-type": "application/json"}
        if json_side_effect is not None:
            resp.json.side_effect = json_side_effect
        else:
            resp.json.return_value = {} if json_payload is None else json_payload
        return resp

    @staticmethod
    def _public_feed(urls):
        instances = {}
        for idx, url in enumerate(urls):
            instances[url] = {
                "network_type": "normal",
                "http": {"status_code": 200},
                "timing": {
                    "search": {
                        "success_percentage": 100.0 - idx,
                        "all": {"mean": 0.3 + idx * 0.1},
                    }
                },
            }
        return {"instances": instances}

    @patch("src.search_service._get_with_retry")
    def test_success_response_maps_fields_for_self_hosted_instance(self, mock_get):
        fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "Test Article",
                        "url": "https://example.com/article",
                        "content": "Summary snippet here",
                        "publishedDate": fresh_date,
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("AAPL stock", max_results=5, days=7)

        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "SearXNG")
        self.assertEqual(len(resp.results), 1)
        result = resp.results[0]
        self.assertEqual(result.title, "Test Article")
        self.assertEqual(result.url, "https://example.com/article")
        self.assertEqual(result.snippet, "Summary snippet here")
        expected_date = datetime.fromisoformat(fresh_date.replace("Z", "+00:00")).astimezone().date().isoformat()
        self.assertEqual(result.published_date, expected_date)
        self.assertEqual(result.source, "example.com")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_uses_description_when_content_missing(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "Title",
                        "url": "https://foo.com/page",
                        "description": "Desc text",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(resp.results[0].snippet, "Desc text")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_extracts_relative_english_date_from_content_when_fields_empty(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "News",
                        "url": "https://foo.com/page",
                        "content": "5 days ago · Important company update",
                        "publishedDate": None,
                        "pubdate": "",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        fixed_now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        original_normalize = SearchService._normalize_news_publish_date
        with patch.object(
            SearchService,
            "_normalize_news_publish_date",
            side_effect=lambda value: original_normalize(value, now=fixed_now),
        ):
            resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(resp.results[0].published_date, "2026-04-10")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_extracts_date_from_url_when_fields_empty(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "[担保]钱江生化(600796):为子公司提供担保的进展公告- CFi.CN 中财网",
                        "url": "https://www.cfi.net.cn/p20260415003312.html",
                        "content": "中财网版权所有(C) HTTPS://WWW.CFi.CN",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                    {
                        "title": "重要提示",
                        "url": "https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/2026-04-15/115475_20260415_EZPL.pdf",
                        "content": "公司控股股东保证本报告所载资料不存在虚假记载",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.published_date for r in resp.results], ["2026-04-15", "2026-04-15"])

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_extracts_relative_chinese_date_from_content_when_fields_empty(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "新闻",
                        "url": "https://foo.com/page-cn",
                        "content": "3天前 · 公司披露重要进展",
                        "publishedDate": None,
                        "pubdate": "",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        fixed_now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        original_normalize = SearchService._normalize_news_publish_date
        with patch.object(
            SearchService,
            "_normalize_news_publish_date",
            side_effect=lambda value: original_normalize(value, now=fixed_now),
        ):
            resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(resp.results[0].published_date, "2026-04-12")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_extracts_explicit_date_from_content_when_fields_empty(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "钱江生化：公司无逾期担保",
                        "url": "https://news.10jqka.com.cn/20260415/c676010066.shtml",
                        "content": "证券日报网讯 2026-04-15 钱江生化（600796）发布公告称，截至本公告披露日，公司及控股子公司对外担保总额为170383万元。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                    {
                        "title": "另一个公告样本",
                        "url": "https://finance.example.com/notice-2",
                        "content": "公司公告披露时间：2026.04.15，后续将继续推进相关事项。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.published_date for r in resp.results], ["2026-04-15", "2026-04-15"])

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_403_returns_specific_error(self, mock_get):
        mock_get.return_value = self._response(
            status_code=403,
            text="forbidden",
            headers={"content-type": "text/plain"},
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertFalse(resp.success)
        self.assertIn("settings.yml", resp.error_message or "")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_empty_results_success(self, mock_get):
        mock_get.return_value = self._response(json_payload={"results": []})

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(resp.results, [])

    @patch("src.search_service._get_with_retry")
    def test_filters_before_applying_max_results(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {"title": "Missing URL", "content": "x"},
                    {"title": "Valid", "url": "https://x.com/valid", "content": "ok"},
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=1)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].title, "Valid")

    @patch("src.search_service._get_with_retry")
    def test_skips_low_quality_placeholder_and_overview_results(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "百家号",
                        "url": "https://baijiahao.baidu.com/s?id=1",
                        "content": "We would like to show you a description here but the site won’t allow us.",
                    },
                    {
                        "title": "山东黄金 (600547)_个股概览_股票价格_实时行情_走势图_新闻...",
                        "url": "https://finscope.example.com/quote/600547",
                        "content": "FinScope提供山东黄金 (600547)的行情报价、新闻资讯、股评、财报等信息。AI让投资更简单。",
                    },
                    {
                        "title": "顺络电子 36.09 (1.81%)_股票行情_新浪财经_新浪网",
                        "url": "https://finance.sina.com.cn/realstock/company/sz002138/nc.shtml",
                        "content": "行情页",
                    },
                    {
                        "title": "shixun/stocksystem0107.sql at master · icreame/shixun · GitHub",
                        "url": "https://github.com/icreame/shixun/blob/master/stocksystem0107.sql",
                        "content": "代码仓库结果",
                    },
                    {
                        "title": "重大新闻。 #重大事件 - 抖音",
                        "url": "https://www.douyin.com/video/123",
                        "content": "短视频站点壳页",
                    },
                    {
                        "title": "正常新闻",
                        "url": "https://news.example.com/article",
                        "content": "5 days ago · Important company update",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        fixed_now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
        original_normalize = SearchService._normalize_news_publish_date
        with patch.object(
            SearchService,
            "_normalize_news_publish_date",
            side_effect=lambda value: original_normalize(value, now=fixed_now),
        ):
            resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].title, "正常新闻")
        self.assertEqual(resp.results[0].published_date, "2026-04-10")

    @patch("src.search_service._get_with_retry")
    def test_skips_social_search_noise_pages(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "Results for \"垃圾关键词\" - Twitter",
                        "url": "https://x.com/search?q=spam",
                        "content": "Results on X",
                    },
                    {
                        "title": "Ausgefüllte Kusssecka Porn [ficktreffenː tryhuk．com] Pornhyb ...",
                        "url": "https://gr.linkedin.com/jobs/spam",
                        "content": "1 day ago · spam job page",
                    },
                    {
                        "title": "\"彩票大乐透｛官网：854.tw｝.ila\" - Results on X",
                        "url": "https://x.com/search?q=%E5%BD%A9%E7%A5%A8",
                        "content": "15 hours ago · 垃圾关键词 results on x",
                    },
                    {
                        "title": "皇冠代理",
                        "url": "https://spam.example.com/crown-agent",
                        "content": "博彩代理页",
                    },
                    {
                        "title": "皇冠hga030app 会员注册",
                        "url": "https://hga030app.com/register",
                        "content": "博彩注册页",
                    },
                    {
                        "title": "正常风险新闻",
                        "url": "https://finance.example.com/risk-news",
                        "content": "2026-04-14 Company risk disclosure",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].title, "正常风险新闻")

    @patch("src.search_service._get_with_retry")
    def test_skips_generic_announcement_portal_and_legal_qa_noise(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "最新公告 - 上海证券交易所",
                        "url": "https://www.sse.com.cn/disclosure/listedinfo/announcement/",
                        "content": "最新公告栏目提供上海证券交易所相关重要信息和通知，帮助用户了解最新动态。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                    {
                        "title": "信息披露 - 巨潮资讯网",
                        "url": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
                        "content": "巨潮资讯网是中国证监会指定的上市公司信息披露网站，平台提供上市公司公告、公司资讯、公司互动、股东大会网络投票等内容功能。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                    {
                        "title": "违规减持被行政处罚案例 - ailegal.baidu.com",
                        "url": "https://ailegal.baidu.com/legalarticle/qadetail?id=9f520013e48812250627",
                        "content": "###文章摘要 1. 违规减持的法律后果。2. 违规减持行政处罚案例。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                    {
                        "title": "钱江生化：公司无逾期担保",
                        "url": "https://news.10jqka.com.cn/20260415/c676010066.shtml",
                        "content": "证券日报网讯 2026-04-15 钱江生化（600796）发布公告称，截至本公告披露日，公司及控股子公司对外担保总额为170383万元。",
                        "publishedDate": None,
                        "pubdate": "",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["钱江生化：公司无逾期担保"])

    @patch("src.search_service._get_with_retry")
    def test_skips_quote_f10_announcement_and_forum_pages_from_real_samples(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "山东黄金 (600547)_股票行情_走势图—东方财富网",
                        "url": "https://quote.eastmoney.com/sh600547.html",
                        "content": "山东黄金(600547)股票的行情走势、五档盘口、逐笔交易等实时行情数据",
                    },
                    {
                        "title": "山东黄金（600547）公告列表 _ 数据中心 _ 东方财富网",
                        "url": "https://data.eastmoney.com/notices/stock/600547.html",
                        "content": "公告列表页面",
                    },
                    {
                        "title": "山东黄金 (600547) 最新动态_F10_同花顺金融服务网",
                        "url": "https://basic.10jqka.com.cn/600547/news.html",
                        "content": "F10资料页",
                    },
                    {
                        "title": "归根结底还是因为油轮被扣的新闻导致的没有主力敢投怕意外事件_小商品城(600415)股吧_东方财富网股吧",
                        "url": "https://guba.eastmoney.com/news,600415,123456.html",
                        "content": "股票论坛社区讨论",
                    },
                    {
                        "title": "正常新闻",
                        "url": "https://finance.example.com/news/1",
                        "content": "2026-04-15 公司披露新进展",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["正常新闻"])

    @patch("src.search_service._get_with_retry")
    def test_skips_x_result_pages_with_domain_variants(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": '"欧洲杯2012赛程｛官网：abcde.hk｝.uce" - Results on X',
                        "url": "https://x.com/search?q=abcde.hk",
                        "content": "2026-04-13 博彩关键词 results on x",
                    },
                    {
                        "title": "正常风险新闻",
                        "url": "https://finance.example.com/risk/2",
                        "content": "2026-04-13 公司收到问询函",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["正常风险新闻"])

    @patch("src.search_service._get_with_retry")
    def test_skips_quote_prediction_and_insider_rumor_pages(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "山东黄金(600547)股票最新价格行情,实时走势图,股价分析预测 - 英为财情",
                        "url": "https://cn.investing.com/equities/shandong-gold-mining-co-ltd",
                        "content": "纯行情页",
                    },
                    {
                        "title": "顺络电子最新消息 002138最新消息_新闻公告_重大利好内幕_爱股网",
                        "url": "https://www.igu888.com/stock/002138",
                        "content": "重大利好内幕",
                    },
                    {
                        "title": "正常新闻",
                        "url": "https://finance.example.com/news/3",
                        "content": "2026-04-15 公司发布公告",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["正常新闻"])

    @patch("src.search_service._get_with_retry")
    def test_skips_betting_shell_and_official_site_placeholder_pages(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "沙巴体育网(中国)官方网站IOS/安卓通用版/APP下载",
                        "url": "https://xtleyang.com/app/123",
                        "content": "2026-04-18 沙巴体育最新网址、注册登录与APP下载入口。",
                    },
                    {
                        "title": "平安银行官方网站",
                        "url": "https://bank.pingan.com/",
                        "content": "平安银行官方网站，提供存款、贷款、信用卡、理财等服务。",
                    },
                    {
                        "title": "银行公司业务-平安银行 - Ping An Bank",
                        "url": "https://bank.pingan.com/gongsi/index.shtml",
                        "content": "平安银行公司业务介绍页面，涵盖账户、结算、贸易融资等服务。",
                    },
                    {
                        "title": "正常业绩新闻",
                        "url": "https://finance.example.com/earnings/000001",
                        "content": "2026-04-19 平安银行发布一季度业绩快报，营收与利润表现稳健。",
                    },
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("平安银行 业绩预告 财报 营收 净利润 同比增长", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["正常业绩新闻"])

    @patch("src.search_service._get_with_retry")
    def test_does_not_treat_bare_eight_digit_identifier_as_date(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "正常新闻",
                        "url": "https://finance.example.com/news/4",
                        "content": "文章编号 20260415，继续跟踪公司进展",
                        "publishedDate": None,
                        "pubdate": "",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertIsNone(resp.results[0].published_date)

    @patch("src.search_service._get_with_retry")
    def test_does_not_drop_non_spam_linkedin_domain_by_domain_only(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "正常新闻",
                        "url": "https://www.linkedin.com/pulse/company-risk-update-example",
                        "content": "2026-04-15 Company risk update",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["正常新闻"])

    @patch("src.search_service._get_with_retry")
    def test_does_not_drop_asianfda_title_without_other_noise_markers(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "行业动态-亚洲金融与发展协会",
                        "url": "https://asianfda.com/article/market-update",
                        "content": "2026-04-15 正常行业动态内容",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["行业动态-亚洲金融与发展协会"])

    @patch("src.search_service._get_with_retry")
    def test_does_not_drop_igu_title_without_insider_marker(self, mock_get):
        mock_get.return_value = self._response(
            json_payload={
                "results": [
                    {
                        "title": "顺络电子最新消息_新闻公告_爱股网",
                        "url": "https://www.igu888.com/stock/002138/news",
                        "content": "2026-04-15 正常资讯页面",
                    }
                ]
            }
        )

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual([r.title for r in resp.results], ["顺络电子最新消息_新闻公告_爱股网"])

    @patch("src.search_service._get_with_retry")
    def test_time_range_mapping(self, mock_get):
        mock_get.return_value = self._response(json_payload={"results": []})
        provider = self._create_provider(["https://searx.example.org"])

        cases = [
            (1, "day"),
            (7, "week"),
            (30, "month"),
            (31, "year"),
        ]
        for days, expected in cases:
            with self.subTest(days=days):
                provider.search("query", max_results=5, days=days)
                self.assertEqual(mock_get.call_args[1]["params"]["time_range"], expected)

    @patch("src.search_service._get_with_retry")
    def test_non_json_response_returns_failure(self, mock_get):
        mock_get.return_value = self._response(json_side_effect=ValueError("No JSON"))

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertFalse(resp.success)
        self.assertIn("JSON", resp.error_message or "")

    @patch("src.search_service._get_with_retry")
    def test_json_returns_non_dict_returns_failure(self, mock_get):
        mock_get.return_value = self._response(json_payload=[{"results": []}])

        provider = self._create_provider(["https://searx.example.org"])
        resp = provider.search("query", max_results=5)

        self.assertFalse(resp.success)
        self.assertIn("格式无效", resp.error_message or "")

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_failover_tries_next_instance_on_timeout(self, mock_get):
        import requests as req_module

        mock_get.side_effect = [
            req_module.exceptions.Timeout(),
            self._response(json_payload={"results": [{"title": "OK", "url": "https://ok.example", "content": "done"}]}),
        ]

        provider = self._create_provider(
            ["https://searx-a.example.org", "https://searx-b.example.org"]
        )
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("https://searx-a.example.org/search", mock_get.call_args_list[0][0][0])
        self.assertIn("https://searx-b.example.org/search", mock_get.call_args_list[1][0][0])

    @patch("src.search_service._get_with_retry")
    def test_self_hosted_rotation_advances_start_instance(self, mock_get):
        mock_get.return_value = self._response(json_payload={"results": []})
        provider = self._create_provider(
            [
                "https://searx-a.example.org",
                "https://searx-b.example.org",
                "https://searx-c.example.org",
            ]
        )

        provider.search("first", max_results=5)
        provider.search("second", max_results=5)

        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("https://searx-a.example.org/search", mock_get.call_args_list[0][0][0])
        self.assertIn("https://searx-b.example.org/search", mock_get.call_args_list[1][0][0])

    def test_public_instance_extraction_filters_and_sorts(self):
        payload = {
            "instances": {
                "https://slow.example/": {
                    "network_type": "normal",
                    "http": {"status_code": 200},
                    "timing": {"search": {"success_percentage": 95.0, "all": {"mean": 1.1}}},
                },
                "https://fast.example/": {
                    "network_type": "normal",
                    "http": {"status_code": 200},
                    "timing": {"search": {"success_percentage": 95.0, "all": {"median": 0.4}}},
                },
                "https://best.example/": {
                    "network_type": "normal",
                    "http": {"status_code": 200},
                    "timing": {"search": {"success_percentage": 100.0, "all": {"mean": 0.8}}},
                },
                "https://zero.example/": {
                    "network_type": "normal",
                    "http": {"status_code": 200},
                    "timing": {"search": {"success_percentage": 0.0, "all": {"mean": 0.1}}},
                },
                "https://tor.example/": {
                    "network_type": "tor",
                    "http": {"status_code": 200},
                    "timing": {"search": {"success_percentage": 100.0, "all": {"mean": 0.1}}},
                },
            }
        }

        urls = SearXNGSearchProvider._extract_public_instances(payload)
        self.assertEqual(
            urls,
            [
                "https://best.example",
                "https://fast.example",
                "https://slow.example",
            ],
        )

    @patch("src.search_service.requests.get")
    def test_public_mode_lazily_fetches_and_caches_instance_feed(self, mock_get):
        feed_resp = self._response(json_payload=self._public_feed(["https://public-1.example/"]))
        search_resp = self._response(json_payload={"results": []})
        mock_get.side_effect = [feed_resp, search_resp, search_resp]

        provider = self._create_provider(use_public_instances=True)
        first = provider.search("first", max_results=5)
        second = provider.search("second", max_results=5)

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_get.call_args_list[0][0][0], SearXNGSearchProvider.PUBLIC_INSTANCES_URL)
        self.assertIn("https://public-1.example/search", mock_get.call_args_list[1][0][0])
        self.assertIn("https://public-1.example/search", mock_get.call_args_list[2][0][0])

    @patch("src.search_service._get_with_retry")
    @patch("src.search_service.requests.get")
    def test_public_mode_uses_requests_without_tenacity_retry(self, mock_get, mock_retry_get):
        mock_get.side_effect = [
            self._response(json_payload=self._public_feed(["https://public-1.example/"])),
            self._response(json_payload={"results": []}),
        ]

        provider = self._create_provider(use_public_instances=True)
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        mock_retry_get.assert_not_called()

    @patch("src.search_service.requests.get")
    def test_public_mode_limits_failover_to_max_attempts_instances(self, mock_get):
        feed_urls = [
            f"https://public-{i}.example/"
            for i in range(1, SearXNGSearchProvider.PUBLIC_INSTANCES_MAX_ATTEMPTS + 2)
        ]
        max_attempts = SearXNGSearchProvider.PUBLIC_INSTANCES_MAX_ATTEMPTS
        mock_get.side_effect = [
            self._response(json_payload=self._public_feed(feed_urls)),
        ] + [
            self._response(status_code=500, text=f"bad-{i}", headers={"content-type": "text/plain"})
            for i in range(1, max_attempts + 1)
        ]

        provider = self._create_provider(use_public_instances=True)
        resp = provider.search("query", max_results=5)

        self.assertFalse(resp.success)
        self.assertEqual(mock_get.call_count, 1 + max_attempts)
        last_search_url = mock_get.call_args_list[-1][0][0]
        self.assertIn(f"https://public-{max_attempts}.example/search", last_search_url)

    @patch("src.search_service.requests.get")
    def test_public_mode_rotates_start_instance_across_requests(self, mock_get):
        feed_urls = [
            "https://public-1.example/",
            "https://public-2.example/",
            "https://public-3.example/",
        ]
        mock_get.side_effect = [
            self._response(json_payload=self._public_feed(feed_urls)),
            self._response(json_payload={"results": []}),
            self._response(json_payload={"results": []}),
        ]

        provider = self._create_provider(use_public_instances=True)
        provider.search("first", max_results=5)
        provider.search("second", max_results=5)

        self.assertEqual(mock_get.call_count, 3)
        self.assertIn("https://public-1.example/search", mock_get.call_args_list[1][0][0])
        self.assertIn("https://public-2.example/search", mock_get.call_args_list[2][0][0])

    @patch("src.search_service.requests.get")
    def test_public_mode_returns_failure_when_feed_unavailable(self, mock_get):
        import requests as req_module

        mock_get.side_effect = req_module.exceptions.ConnectionError("dns failed")

        provider = self._create_provider(use_public_instances=True)
        resp = provider.search("query", max_results=5)

        self.assertFalse(resp.success)
        self.assertIn("公共 SearXNG 实例", resp.error_message or "")
        self.assertEqual(mock_get.call_count, 1)

    @patch("src.search_service.time.time")
    @patch("src.search_service.requests.get")
    def test_public_mode_cold_start_failure_honors_backoff_then_retries(self, mock_get, mock_time):
        import requests as req_module

        current_time = [1000.0]
        mock_time.side_effect = lambda: current_time[0]  # noqa: E731
        mock_get.side_effect = [
            req_module.exceptions.ConnectionError("dns failed"),
            self._response(json_payload=self._public_feed(["https://public-1.example/"])),
            self._response(json_payload={"results": []}),
        ]

        provider = self._create_provider(use_public_instances=True)
        first = provider.search("first", max_results=5)
        current_time[0] = 1001.0
        second = provider.search("second", max_results=5)
        current_time[0] = 1000.0 + SearXNGSearchProvider.PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS + 1
        third = provider.search("third", max_results=5)

        self.assertFalse(first.success)
        self.assertFalse(second.success)
        self.assertTrue(third.success)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_get.call_args_list[0][0][0], SearXNGSearchProvider.PUBLIC_INSTANCES_URL)
        self.assertEqual(mock_get.call_args_list[1][0][0], SearXNGSearchProvider.PUBLIC_INSTANCES_URL)
        self.assertIn("https://public-1.example/search", mock_get.call_args_list[2][0][0])

    @patch("src.search_service.time.time")
    @patch("src.search_service.requests.get")
    def test_public_instance_refresh_failure_reuses_stale_cache(self, mock_get, mock_time):
        import requests as req_module

        fallback_time = (
            1000.0 + SearXNGSearchProvider.PUBLIC_INSTANCES_CACHE_TTL_SECONDS + 2
        )
        time_values = iter(
            [
                1000.0,
                1000.0 + SearXNGSearchProvider.PUBLIC_INSTANCES_CACHE_TTL_SECONDS + 1,
                fallback_time,
            ]
        )
        mock_time.side_effect = lambda: next(time_values, fallback_time)  # noqa: E731
        mock_get.side_effect = [
            self._response(json_payload=self._public_feed(["https://public-1.example/"])),
            req_module.exceptions.ConnectionError("dns failed"),
        ]

        first = SearXNGSearchProvider._get_public_instances()
        second = SearXNGSearchProvider._get_public_instances()
        third = SearXNGSearchProvider._get_public_instances()

        self.assertEqual(first, ["https://public-1.example"])
        self.assertEqual(second, ["https://public-1.example"])
        self.assertEqual(third, ["https://public-1.example"])
        self.assertEqual(mock_get.call_count, 2)

    @patch.object(SearXNGSearchProvider, "_get_public_instances")
    @patch("src.search_service._get_with_retry")
    def test_self_hosted_mode_does_not_fetch_public_instances(self, mock_get, mock_public_instances):
        mock_get.return_value = self._response(json_payload={"results": []})

        provider = self._create_provider(["https://searx.example.org"], use_public_instances=True)
        resp = provider.search("query", max_results=5)

        self.assertTrue(resp.success)
        mock_public_instances.assert_not_called()

    def test_search_service_adds_public_searxng_provider_when_enabled(self):
        service = SearchService(searxng_public_instances_enabled=True)

        self.assertTrue(service.is_available)
        self.assertTrue(any(provider.name == "SearXNG" for provider in service._providers))


if __name__ == "__main__":
    unittest.main()
