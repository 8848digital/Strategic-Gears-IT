# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import json
import unittest

import frappe

test_records = frappe.get_test_records("ITLocation")


class TestITLocation(unittest.TestCase):
	def runTest(self):
		it_locations = ["Basil Farm", "Division 1", "Field 1", "Block 1"]
		area = 0
		formatted_it_locations = []

		for it_location in it_locations:
			doc = frappe.get_doc("ITLocation", it_location)
			doc.save()
			area += doc.area
			temp = json.loads(doc.it_location)
			temp["features"][0]["properties"]["child_feature"] = True
			temp["features"][0]["properties"]["feature_of"] = it_location
			formatted_it_locations.extend(temp["features"])

		test_it_location = frappe.get_doc("ITLocation", "Test ITLocation Area")
		test_it_location.save()

		test_it_location_features = json.loads(test_it_location.get("it_location"))["features"]
		ordered_test_it_location_features = sorted(
			test_it_location_features, key=lambda x: x["properties"]["feature_of"]
		)
		ordered_formatted_it_locations = sorted(formatted_it_locations, key=lambda x: x["properties"]["feature_of"])

		self.assertEqual(ordered_formatted_it_locations, ordered_test_it_location_features)
		self.assertEqual(area, test_it_location.get("area"))
