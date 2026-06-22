import unittest

from shilun.api.routes import analyze as analyze_route


class AnalyzeRouteTests(unittest.TestCase):
    def test_analyze_route_uses_mongo_first_service(self) -> None:
        original_service = analyze_route.analysis_service

        class DummyAnalysisService:
            def __init__(self) -> None:
                self.requests = []

            def analyze(self, request):
                self.requests.append(request)
                return {"ticker": request.ticker, "date": request.analysis_date, "data_source": "mongo"}

        service = DummyAnalysisService()
        analyze_route.analysis_service = service
        try:
            result = analyze_route.analyze("000001.SZ", "2026-03-30")
        finally:
            analyze_route.analysis_service = original_service

        self.assertEqual("mongo", result["data_source"])
        self.assertEqual("000001.SZ", service.requests[0].ticker)
        self.assertFalse(service.requests[0].allow_tushare_fallback)

    def test_analyze_route_can_explicitly_pass_tushare_fallback(self) -> None:
        original_service = analyze_route.analysis_service

        class DummyAnalysisService:
            def analyze(self, request):
                return {"allow_tushare_fallback": request.allow_tushare_fallback}

        analyze_route.analysis_service = DummyAnalysisService()
        try:
            result = analyze_route.analyze("000001.SZ", "2026-03-30", allow_tushare_fallback=True)
        finally:
            analyze_route.analysis_service = original_service

        self.assertTrue(result["allow_tushare_fallback"])


if __name__ == "__main__":
    unittest.main()
