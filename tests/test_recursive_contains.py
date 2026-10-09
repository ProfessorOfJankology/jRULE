"""Regression coverage for recursive rule conditions."""
import unittest
from app.rules import compare, evaluate_condition


class RecursiveContainsTests(unittest.TestCase):
    def setUp(self):
        self.users = {
            "ESC-R1": ["ESMC\\someone.else"],
            "ESC-R2": ["ESMC\\jordan.grey"],
            "ESC-R3": [],
        }

    def test_nested_values_found_across_workstations(self):
        self.assertTrue(compare("contains_recursive", self.users, "ESMC\\jordan.grey"))
        self.assertFalse(compare("not_contains_recursive", self.users, "ESMC\\jordan.grey"))

    def test_missing_value_and_inverse(self):
        self.assertFalse(compare("contains_recursive", self.users, "ESMC\\missing"))
        self.assertTrue(compare("not_contains_recursive", self.users, "ESMC\\missing"))

    def test_deep_list_and_mapping_values(self):
        self.assertTrue(compare("contains_recursive", {"a": [{"b": [1, {"c": True}]}]}, True))
        self.assertFalse(compare("contains_recursive", {"name": "jordan.grey"}, "name"))

    def test_exact_scalar_no_substring(self):
        self.assertFalse(compare("contains_recursive", self.users, "jordan.grey"))
        self.assertTrue(compare("contains", "ESMC\\jordan.grey", "jordan.grey"))

    def test_existing_contains_remains_nonrecursive(self):
        self.assertFalse(compare("contains", self.users, "ESMC\\jordan.grey"))
        self.assertTrue(compare("contains", self.users, "ESC-R2"))

    def test_not_contains_with_empty_or_missing(self):
        self.assertFalse(compare("contains_recursive", None, "jordan.grey"))
        self.assertTrue(compare("not_contains_recursive", None, "jordan.grey"))
        self.assertTrue(compare("not_contains_recursive", {}, "jordan.grey"))


if __name__ == "__main__":
    unittest.main()
