import unittest

from translation_highlights import find_highlights


class TranslationHighlightTests(unittest.TestCase):
    def test_currency_and_promotion(self):
        text = "限时优惠，到手价29.9元，买一送一，满100减20。"
        result = find_highlights(text)
        self.assertEqual([tag for _, _, tag in result], ["promotion", "promotion", "price", "promotion", "promotion"])
        self.assertEqual([text[a:b] for a, b, _ in result], ["限时", "优惠", "到手价29.9元", "买一送一", "满100减20"])

    def test_currency_forms_and_chinese_numerals(self):
        text = "RMB 1,299.50、₫50.000、三百五十万越南盾、券后99。"
        spans = find_highlights(text)
        self.assertEqual([text[a:b] for a, b, _ in spans], ["RMB 1,299.50", "₫50.000", "三百五十万越南盾", "券后99"])
        self.assertTrue(all(tag == "price" for _, _, tag in spans))

    def test_discount_percent_and_discount_price(self):
        text = "全场8.8折，优惠20%，原价100元现价79元。"
        spans = find_highlights(text)
        self.assertEqual([text[a:b] for a, b, _ in spans], ["全场8.8折", "优惠", "20%", "原价100元", "现价79元"])

    def test_measurements_are_not_prices(self):
        text = "尺寸10厘米，时长2分钟，重量3公斤，速度20km/h。"
        self.assertEqual(find_highlights(text), [])

    def test_price_has_precedence_and_spans_do_not_overlap(self):
        text = "优惠价¥99，买2送1，包邮。"
        spans = find_highlights(text)
        for previous, current in zip(spans, spans[1:]):
            self.assertLessEqual(previous[1], current[0])
        self.assertEqual([text[a:b] for a, b, _ in spans], ["优惠价¥99", "买2送1", "包邮"])

    def test_short_video_offer_phrases(self):
        text = "拍1发5，拍1发3，全场5折，第二件半价。"
        spans = find_highlights(text)
        self.assertEqual([text[a:b] for a, b, _ in spans], ["拍1发5", "拍1发3", "全场5折", "第二件半价"])
        self.assertTrue(all(tag == "promotion" for _, _, tag in spans))

    def test_ecommerce_context_and_approximate_amounts(self):
        text = "原价几千元，现价几百元；拍1发几，满199减100，前100名立减50。"
        spans = find_highlights(text)
        self.assertEqual([text[a:b] for a, b, _ in spans], ["原价几千元", "现价几百元", "拍1发几", "满199减100", "前100名立减50"])


if __name__ == "__main__":
    unittest.main()
