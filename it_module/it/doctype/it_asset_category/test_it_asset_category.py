# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe


class TestITAssetCategory(unittest.TestCase):
	def test_mandatory_fields(self):
		it_asset_category = frappe.new_doc("Asset Category")
		it_asset_category.it_asset_category_name = "Computers"

		self.assertRaises(frappe.MandatoryError, it_asset_category.insert)

		it_asset_category.total_number_of_depreciations = 3
		it_asset_category.frequency_of_depreciation = 3
		it_asset_category.append(
			"accounts",
			{
				"company_name": "_Test Company",
				"fixed_asset_account": "_Test Fixed Asset - _TC",
				"accumulated_depreciation_account": "_Test Accumulated Depreciations - _TC",
				"depreciation_expense_account": "_Test Depreciations - _TC",
			},
		)

		try:
			it_asset_category.insert(ignore_if_duplicate=True)
		except frappe.DuplicateEntryError:
			pass

	def test_cwip_accounting(self):
		frappe.db.get_value("Company", "_Test Company", "capital_work_in_progress_account")
		frappe.db.set_value("Company", "_Test Company", "capital_work_in_progress_account", "")

		it_asset_category = frappe.new_doc("Asset Category")
		it_asset_category.it_asset_category_name = "Computers"
		it_asset_category.enable_cwip_accounting = 1

		it_asset_category.total_number_of_depreciations = 3
		it_asset_category.frequency_of_depreciation = 3
		it_asset_category.append(
			"accounts",
			{
				"company_name": "_Test Company",
				"fixed_asset_account": "_Test Fixed Asset - _TC",
				"accumulated_depreciation_account": "_Test Accumulated Depreciations - _TC",
				"depreciation_expense_account": "_Test Depreciations - _TC",
			},
		)

		self.assertRaises(frappe.ValidationError, it_asset_category.insert)
