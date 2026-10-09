"""Explicit condition operand types and legacy compatibility."""
import unittest
from app.rules import compare, resolve_operand


class ConditionOperandTypeTests(unittest.TestCase):
    def test_explicit_string_is_not_coerced(self):
        self.assertEqual(resolve_operand({"right_type": "typed", "right": "00123"}, {}), "00123")
        self.assertEqual(resolve_operand({"right_type": "typed", "right": "false"}, {}), "false")
        self.assertEqual(resolve_operand({"right_type": "typed", "right": "123"}, {}), "123")

    def test_explicit_nonstring_types_are_preserved(self):
        for value in (True, False, None, 123, 1.5, ["a", "b"], {"key": 1}):
            with self.subTest(value=value):
                self.assertEqual(resolve_operand({"right_type": "typed", "right": value}, {}), value)

    def test_legacy_auto_conversion_still_supported(self):
        self.assertEqual(resolve_operand({"right": "123"}, {}), 123)
        self.assertIs(resolve_operand({"right": "false"}, {}), False)
        self.assertEqual(resolve_operand({"right": "jordan.grey"}, {}), "jordan.grey")

    def test_recursive_contains_with_typed_string(self):
        data = {"ESC-R1": ["julie"], "ESC-R2": ["jordan.grey"]}
        target = resolve_operand({"right_type": "typed", "right": "jordan.grey"}, {})
        self.assertTrue(compare("contains_recursive", data, target))
        self.assertFalse(compare("not_contains_recursive", data, target))


if __name__ == "__main__":
    unittest.main()
