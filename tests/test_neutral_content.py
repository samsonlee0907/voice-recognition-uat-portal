import json
import re
import unittest
from pathlib import Path

from recognition_compare import CODE_FORMAT_RULES

ROOT = Path(__file__).parents[1]


class NeutralContentTests(unittest.TestCase):
    def test_examples_do_not_use_short_carrier_style_prefixes(self):
        examples = re.findall(r" => ([A-Z0-9]+)(?:\n|$)", CODE_FORMAT_RULES)
        self.assertTrue(examples)
        self.assertIn("DEMO63", examples)
        self.assertIn("CODE007", examples)
        for example in examples:
            self.assertIsNone(re.fullmatch(r"[A-Z]{2,3}[0-9]{1,4}", example), example)
        markup = (ROOT / "static" / "recognition.html").read_text(encoding="utf-8")
        self.assertIn("<code>D E M O 六十三</code>", markup)

    def test_question_channels_are_generic(self):
        bank = json.loads((ROOT / "config" / "telecom_uat_cases.json").read_text(encoding="utf-8"))
        cases = {item["id"]: item for item in bank["cases"]}
        self.assertEqual(bank["version"], "standalone-2026-11-04.2")
        self.assertIn("官網／服務 app", bank["groups"]["renewal"]["rules"])
        self.assertEqual(cases["REN-02"]["title"], "電信服務 app")
        self.assertEqual(cases["REN-02"]["utterance"], "我在電信服務 app 裡面辦的。")
        self.assertEqual(cases["PAY-03"]["utterance"], "我去附近的便利商店繳費。")
        self.assertEqual(cases["REN-02"]["expected"]["value"], "online")
        self.assertEqual(cases["PAY-03"]["expected"]["value"], "convenience_store")

    def test_source_identifier_is_not_distributed(self):
        provenance = json.loads((ROOT / "SOURCE-PROVENANCE.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source_repository"],
                         "Internal voice portal prototype (identifier omitted for sharing)")
        self.assertEqual(provenance["prompt_revision"], "2026-11-04.2")
        self.assertTrue(provenance["source_sha256"])
