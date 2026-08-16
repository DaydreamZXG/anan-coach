#!/usr/bin/env python3
from __future__ import annotations

import unittest

from reconcile_scope import reconcile_row


class ScopeReconciliationTests(unittest.TestCase):
    def test_life_silhouette_is_restored(self) -> None:
        row, decision = reconcile_row({"assetId": "gr_life", "regularSubtype": "complex_silhouette_candidate", "regularSubtypeStatus": "excluded_by_user", "exclusionEvidence": {"status": "excluded_by_user"}}, set())
        self.assertEqual(decision, "life_restored")
        self.assertEqual(row["regularSubtypeStatus"], "semantic_or_renderer_pending")
        self.assertNotIn("exclusionEvidence", row)

    def test_tonal_candidate_requires_asset_review(self) -> None:
        row, decision = reconcile_row({"assetId": "gr_tonal", "regularSubtype": "tonal_life_or_raster_candidate", "regularSubtypeStatus": "excluded_by_user", "exclusionEvidence": {"status": "excluded_by_user"}}, set())
        self.assertEqual(decision, "photo_review")
        self.assertEqual(row["regularSubtypeStatus"], "scope_review_required")
        self.assertNotIn("exclusionEvidence", row)
        self.assertEqual(row["scopeReviewEvidence"]["status"], "review_required")

    def test_explicit_asset_exclusion_wins(self) -> None:
        row, decision = reconcile_row({"assetId": "gr_explicit", "regularSubtype": "complex_silhouette_candidate"}, {"gr_explicit"})
        self.assertEqual(decision, "explicit_exclusion")
        self.assertEqual(row["regularSubtypeStatus"], "excluded_by_user")
        self.assertEqual(row["exclusionEvidence"]["policy"], "asset_sha256")


if __name__ == "__main__":
    unittest.main()
