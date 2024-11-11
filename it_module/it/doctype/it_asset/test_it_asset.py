# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe
from frappe.utils import (
	add_days,
	add_months,
	cstr,
	flt,
	get_first_day,
	get_last_day,
	getdate,
	nowdate,
)

from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from it_module.it.doctype.it_asset.it_asset import (
	make_sales_invoice,
	split_it_asset,
	update_maintenance_status,
)
from it_module.it.doctype.it_asset.depreciation import (
	is_last_day_of_the_month,
	post_depreciation_entries,
	restore_it_asset,
	scrap_it_asset,
)
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
	make_purchase_invoice as make_invoice,
)
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt


class ITAssetSetup(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		set_depreciation_settings_in_company()
		create_it_asset_data()
		enable_cwip_accounting("Computers")
		make_purchase_receipt(item_code="Macbook Pro", qty=1, rate=100000.0, location="Test Location")
		frappe.db.sql("delete from `tabTax Rule`")

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()


class TestITAsset(ITAssetSetup):
	def test_it_asset_category_is_fetched(self):
		"""Tests if the Item's ITAsset Category value is assigned to the ITAsset, if the field is empty."""

		it_asset = create_it_asset(item_code="Macbook Pro", do_not_save=1)
		it_asset.it_asset_category = None
		it_asset.save()

		self.assertEqual(it_asset.it_asset_category, "Computers")

	def test_gross_purchase_amount_is_mandatory(self):
		it_asset = create_it_asset(item_code="Macbook Pro", do_not_save=1)
		it_asset.gross_purchase_amount = 0

		self.assertRaises(frappe.MandatoryError, it_asset.save)

	def test_pr_or_pi_mandatory_if_not_existing_it_asset(self):
		"""Tests if either PI or PR is present if CWIP is enabled and is_existing_it_asset=0."""

		it_asset = create_it_asset(item_code="Macbook Pro", do_not_save=1)
		it_asset.is_existing_it_asset = 0

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_available_for_use_date_is_after_purchase_date(self):
		it_asset = create_it_asset(item_code="Macbook Pro", calculate_depreciation=1, do_not_save=1)
		it_asset.is_existing_it_asset = 0
		it_asset.purchase_date = getdate("2021-10-10")
		it_asset.available_for_use_date = getdate("2021-10-1")

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_item_exists(self):
		it_asset = create_it_asset(item_code="MacBook", do_not_save=1)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_validate_item(self):
		it_asset = create_it_asset(item_code="MacBook Pro", do_not_save=1)
		item = frappe.get_doc("Item", "MacBook Pro")

		item.disabled = 1
		item.save()
		self.assertRaises(frappe.ValidationError, it_asset.save)
		item.disabled = 0

		item.is_fixed_it_asset = 0
		self.assertRaises(frappe.ValidationError, it_asset.save)
		item.is_fixed_it_asset = 1

		item.is_stock_item = 1
		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_purchase_it_asset(self):
		pr = make_purchase_receipt(item_code="Macbook Pro", qty=1, rate=100000.0, location="Test Location")

		it_asset_name = frappe.db.get_value("ITAsset", {"purchase_receipt": pr.name}, "name")
		it_asset = frappe.get_doc("ITAsset", it_asset_name)
		it_asset.calculate_depreciation = 1

		month_end_date = get_last_day(nowdate())
		purchase_date = nowdate() if nowdate() != month_end_date else add_days(nowdate(), -15)

		it_asset.available_for_use_date = purchase_date
		it_asset.purchase_date = purchase_date
		it_asset.append(
			"finance_books",
			{
				"expected_value_after_useful_life": 10000,
				"depreciation_method": "Straight Line",
				"total_number_of_depreciations": 3,
				"frequency_of_depreciation": 10,
				"depreciation_start_date": month_end_date,
			},
		)
		it_asset.submit()

		pi = make_invoice(pr.name)
		pi.supplier = "_Test Supplier"
		pi.insert()
		pi.submit()
		it_asset.load_from_db()
		self.assertEqual(it_asset.supplier, "_Test Supplier")
		self.assertEqual(it_asset.purchase_date, getdate(purchase_date))
		# ITAsset won't have reference to PI when purchased through PR
		self.assertEqual(it_asset.purchase_receipt, pr.name)

		expected_gle = (
			("ITAsset Received But Not Billed - _TC", 100000.0, 0.0),
			("Creditors - _TC", 0.0, 100000.0),
		)

		gle = get_gl_entries("Purchase Invoice", pi.name)
		self.assertSequenceEqual(gle, expected_gle)

		pi.cancel()
		it_asset.cancel()
		it_asset.load_from_db()
		pr.load_from_db()
		pr.cancel()
		self.assertEqual(it_asset.docstatus, 2)

	def test_purchase_of_grouped_it_asset(self):
		create_fixed_it_asset_item("Rack", is_grouped_it_asset=1)
		pr = make_purchase_receipt(item_code="Rack", qty=3, rate=100000.0, location="Test Location")

		it_asset_name = frappe.db.get_value("ITAsset", {"purchase_receipt": pr.name}, "name")
		it_asset = frappe.get_doc("ITAsset", it_asset_name)
		self.assertEqual(it_asset.it_asset_quantity, 3)
		it_asset.calculate_depreciation = 1

		month_end_date = get_last_day(nowdate())
		purchase_date = nowdate() if nowdate() != month_end_date else add_days(nowdate(), -15)

		it_asset.available_for_use_date = purchase_date
		it_asset.purchase_date = purchase_date
		it_asset.append(
			"finance_books",
			{
				"expected_value_after_useful_life": 10000,
				"depreciation_method": "Straight Line",
				"total_number_of_depreciations": 3,
				"frequency_of_depreciation": 10,
				"depreciation_start_date": month_end_date,
			},
		)
		it_asset.submit()

	def test_is_fixed_it_asset_set(self):
		it_asset = create_it_asset(is_existing_it_asset=1)
		doc = frappe.new_doc("Purchase Invoice")
		doc.company = "_Test Company"
		doc.supplier = "_Test Supplier"
		doc.append("items", {"item_code": "Macbook Pro", "qty": 1, "it_asset": it_asset.name})

		doc.set_missing_values()
		self.assertEqual(doc.items[0].is_fixed_it_asset, 1)

	def test_scrap_it_asset(self):
		date = nowdate()
		purchase_date = add_months(get_first_day(date), -2)

		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date=purchase_date,
			purchase_date=purchase_date,
			expected_value_after_useful_life=10000,
			total_number_of_depreciations=10,
			frequency_of_depreciation=1,
			submit=1,
		)

		post_depreciation_entries(date=add_months(purchase_date, 2))
		it_asset.load_from_db()

		accumulated_depr_amount = flt(
			it_asset.gross_purchase_amount - it_asset.finance_books[0].value_after_depreciation,
			it_asset.precision("gross_purchase_amount"),
		)
		self.assertEqual(accumulated_depr_amount, 18000.0)

		scrap_it_asset(it_asset.name)
		it_asset.load_from_db()

		accumulated_depr_amount = flt(
			it_asset.gross_purchase_amount - it_asset.finance_books[0].value_after_depreciation,
			it_asset.precision("gross_purchase_amount"),
		)
		pro_rata_amount, _, _ = it_asset.get_pro_rata_amt(
			it_asset.finance_books[0],
			9000,
			get_last_day(add_months(purchase_date, 1)),
			date,
			original_schedule_date=get_last_day(nowdate()),
		)
		pro_rata_amount = flt(pro_rata_amount, it_asset.precision("gross_purchase_amount"))
		self.assertEqual(
			accumulated_depr_amount,
			flt(18000.0 + pro_rata_amount, it_asset.precision("gross_purchase_amount")),
		)

		self.assertEqual(it_asset.status, "Scrapped")
		self.assertTrue(it_asset.journal_entry_for_scrap)

		expected_gle = (
			(
				"_Test Accumulated Depreciations - _TC",
				flt(18000.0 + pro_rata_amount, it_asset.precision("gross_purchase_amount")),
				0.0,
			),
			("_Test Fixed ITAsset - _TC", 0.0, 100000.0),
			(
				"_Test Gain/Loss on ITAsset Disposal - _TC",
				flt(82000.0 - pro_rata_amount, it_asset.precision("gross_purchase_amount")),
				0.0,
			),
		)

		gle = get_gl_entries("Journal Entry", it_asset.journal_entry_for_scrap)
		self.assertSequenceEqual(gle, expected_gle)

		restore_it_asset(it_asset.name)

		it_asset.load_from_db()
		self.assertFalse(it_asset.journal_entry_for_scrap)
		self.assertEqual(it_asset.status, "Partially Depreciated")

		accumulated_depr_amount = flt(
			it_asset.gross_purchase_amount - it_asset.finance_books[0].value_after_depreciation,
			it_asset.precision("gross_purchase_amount"),
		)
		this_month_depr_amount = 9000.0 if is_last_day_of_the_month(date) else 0

		self.assertEqual(accumulated_depr_amount, 18000.0 + this_month_depr_amount)

	def test_gle_made_by_it_asset_sale(self):
		date = nowdate()
		purchase_date = add_months(get_first_day(date), -2)

		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date=purchase_date,
			purchase_date=purchase_date,
			expected_value_after_useful_life=10000,
			total_number_of_depreciations=10,
			frequency_of_depreciation=1,
			submit=1,
		)

		post_depreciation_entries(date=add_months(purchase_date, 2))

		si = make_sales_invoice(it_asset=it_asset.name, item_code="Macbook Pro", company="_Test Company")
		si.customer = "_Test Customer"
		si.due_date = nowdate()
		si.get("items")[0].rate = 25000
		si.insert()
		si.submit()

		self.assertEqual(frappe.db.get_value("ITAsset", it_asset.name, "status"), "Sold")

		pro_rata_amount, _, _ = it_asset.get_pro_rata_amt(
			it_asset.finance_books[0],
			9000,
			get_last_day(add_months(purchase_date, 1)),
			date,
			original_schedule_date=get_last_day(nowdate()),
		)
		pro_rata_amount = flt(pro_rata_amount, it_asset.precision("gross_purchase_amount"))

		expected_gle = (
			(
				"_Test Accumulated Depreciations - _TC",
				flt(18000.0 + pro_rata_amount, it_asset.precision("gross_purchase_amount")),
				0.0,
			),
			("_Test Fixed ITAsset - _TC", 0.0, 100000.0),
			(
				"_Test Gain/Loss on ITAsset Disposal - _TC",
				flt(57000.0 - pro_rata_amount, it_asset.precision("gross_purchase_amount")),
				0.0,
			),
			("Debtors - _TC", 25000.0, 0.0),
		)

		gle = get_gl_entries("Sales Invoice", si.name)
		self.assertSequenceEqual(gle, expected_gle)

		si.cancel()
		self.assertEqual(frappe.db.get_value("ITAsset", it_asset.name, "status"), "Partially Depreciated")

	def test_gle_made_by_it_asset_sale_for_existing_it_asset(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2020-04-01",
			purchase_date="2020-04-01",
			expected_value_after_useful_life=0,
			total_number_of_depreciations=5,
			number_of_depreciations_booked=2,
			frequency_of_depreciation=12,
			depreciation_start_date="2023-03-31",
			opening_accumulated_depreciation=24000,
			gross_purchase_amount=60000,
			submit=1,
		)

		expected_depr_values = [
			["2023-03-31", 12000, 36000],
			["2024-03-31", 12000, 48000],
			["2025-03-31", 12000, 60000],
		]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_depr_values[i][0]), schedule.schedule_date)
			self.assertEqual(expected_depr_values[i][1], schedule.depreciation_amount)
			self.assertEqual(expected_depr_values[i][2], schedule.accumulated_depreciation_amount)

		post_depreciation_entries(date="2023-03-31")

		si = create_sales_invoice(
			item_code="Macbook Pro", it_asset=it_asset.name, qty=1, rate=40000, posting_date=getdate("2023-05-23")
		)
		it_asset.load_from_db()

		self.assertEqual(frappe.db.get_value("ITAsset", it_asset.name, "status"), "Sold")

		expected_values = [["2023-03-31", 12000, 36000], ["2023-05-23", 1737.7, 37737.7]]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_values[i][0]), schedule.schedule_date)
			self.assertEqual(expected_values[i][1], schedule.depreciation_amount)
			self.assertEqual(expected_values[i][2], schedule.accumulated_depreciation_amount)
			self.assertTrue(schedule.journal_entry)

		expected_gle = (
			(
				"_Test Accumulated Depreciations - _TC",
				37737.7,
				0.0,
			),
			(
				"_Test Fixed ITAsset - _TC",
				0.0,
				60000.0,
			),
			(
				"_Test Gain/Loss on ITAsset Disposal - _TC",
				0.0,
				17737.7,
			),
			("Debtors - _TC", 40000.0, 0.0),
		)

		gle = get_gl_entries("Sales Invoice", si.name)
		self.assertSequenceEqual(gle, expected_gle)

	def test_it_asset_with_maintenance_required_status_after_sale(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2020-06-06",
			purchase_date="2020-01-01",
			expected_value_after_useful_life=10000,
			total_number_of_depreciations=3,
			frequency_of_depreciation=10,
			maintenance_required=1,
			depreciation_start_date="2020-12-31",
			submit=1,
		)

		post_depreciation_entries(date="2021-01-01")

		si = make_sales_invoice(it_asset=it_asset.name, item_code="Macbook Pro", company="_Test Company")
		si.customer = "_Test Customer"
		si.due_date = nowdate()
		si.get("items")[0].rate = 25000
		si.insert()
		si.submit()

		self.assertEqual(frappe.db.get_value("ITAsset", it_asset.name, "status"), "Sold")

		update_maintenance_status()

		self.assertEqual(frappe.db.get_value("ITAsset", it_asset.name, "status"), "Sold")

	def test_it_asset_splitting(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			it_asset_quantity=10,
			available_for_use_date="2020-01-01",
			purchase_date="2020-01-01",
			expected_value_after_useful_life=0,
			total_number_of_depreciations=6,
			number_of_depreciations_booked=1,
			frequency_of_depreciation=10,
			depreciation_start_date="2021-01-01",
			opening_accumulated_depreciation=20000,
			gross_purchase_amount=120000,
			submit=1,
		)

		post_depreciation_entries(date="2021-01-01")

		self.assertEqual(it_asset.it_asset_quantity, 10)
		self.assertEqual(it_asset.gross_purchase_amount, 120000)
		self.assertEqual(it_asset.opening_accumulated_depreciation, 20000)

		new_it_asset = split_it_asset(it_asset.name, 2)
		it_asset.load_from_db()

		self.assertEqual(new_it_asset.it_asset_quantity, 2)
		self.assertEqual(new_it_asset.gross_purchase_amount, 24000)
		self.assertEqual(new_it_asset.opening_accumulated_depreciation, 4000)
		self.assertEqual(new_it_asset.split_from, it_asset.name)
		self.assertEqual(new_it_asset.schedules[0].depreciation_amount, 4000)
		self.assertEqual(new_it_asset.schedules[1].depreciation_amount, 4000)

		self.assertEqual(it_asset.it_asset_quantity, 8)
		self.assertEqual(it_asset.gross_purchase_amount, 96000)
		self.assertEqual(it_asset.opening_accumulated_depreciation, 16000)
		self.assertEqual(it_asset.schedules[0].depreciation_amount, 16000)
		self.assertEqual(it_asset.schedules[1].depreciation_amount, 16000)

		journal_entry = it_asset.schedules[0].journal_entry

		jv = frappe.get_doc("Journal Entry", journal_entry)
		self.assertEqual(jv.accounts[0].credit_in_account_currency, 16000)
		self.assertEqual(jv.accounts[1].debit_in_account_currency, 16000)
		self.assertEqual(jv.accounts[2].credit_in_account_currency, 4000)
		self.assertEqual(jv.accounts[3].debit_in_account_currency, 4000)

		self.assertEqual(jv.accounts[0].reference_name, it_asset.name)
		self.assertEqual(jv.accounts[1].reference_name, it_asset.name)
		self.assertEqual(jv.accounts[2].reference_name, new_it_asset.name)
		self.assertEqual(jv.accounts[3].reference_name, new_it_asset.name)

	def test_expense_head(self):
		pr = make_purchase_receipt(item_code="Macbook Pro", qty=2, rate=200000.0, location="Test Location")
		doc = make_invoice(pr.name)

		self.assertEqual("ITAsset Received But Not Billed - _TC", doc.items[0].expense_account)

	# Capital Work In Progress
	def test_cwip_accounting(self):
		pr = make_purchase_receipt(
			item_code="Macbook Pro", qty=1, rate=5000, do_not_submit=True, location="Test Location"
		)

		pr.set(
			"taxes",
			[
				{
					"category": "Total",
					"add_deduct_tax": "Add",
					"charge_type": "On Net Total",
					"account_head": "_Test Account Service Tax - _TC",
					"description": "_Test Account Service Tax",
					"cost_center": "Main - _TC",
					"rate": 5.0,
				},
				{
					"category": "Valuation and Total",
					"add_deduct_tax": "Add",
					"charge_type": "On Net Total",
					"account_head": "_Test Account Shipping Charges - _TC",
					"description": "_Test Account Shipping Charges",
					"cost_center": "Main - _TC",
					"rate": 5.0,
				},
			],
		)

		pr.submit()

		expected_gle = (
			("_Test Account Shipping Charges - _TC", 0.0, 250.0),
			("ITAsset Received But Not Billed - _TC", 0.0, 5000.0),
			("CWIP Account - _TC", 5250.0, 0.0),
		)

		pr_gle = get_gl_entries("Purchase Receipt", pr.name)
		self.assertSequenceEqual(pr_gle, expected_gle)

		pi = make_invoice(pr.name)
		pi.submit()

		expected_gle = (
			("_Test Account Service Tax - _TC", 250.0, 0.0),
			("_Test Account Shipping Charges - _TC", 250.0, 0.0),
			("ITAsset Received But Not Billed - _TC", 5000.0, 0.0),
			("Creditors - _TC", 0.0, 5500.0),
		)

		pi_gle = get_gl_entries("Purchase Invoice", pi.name)
		self.assertSequenceEqual(pi_gle, expected_gle)

		it_asset = frappe.db.get_value("ITAsset", {"purchase_receipt": pr.name, "docstatus": 0}, "name")

		it_asset_doc = frappe.get_doc("ITAsset", it_asset)

		month_end_date = get_last_day(nowdate())
		it_asset_doc.available_for_use_date = (
			nowdate() if nowdate() != month_end_date else add_days(nowdate(), -15)
		)
		self.assertEqual(it_asset_doc.gross_purchase_amount, 5250.0)

		it_asset_doc.append(
			"finance_books",
			{
				"expected_value_after_useful_life": 200,
				"depreciation_method": "Straight Line",
				"total_number_of_depreciations": 3,
				"frequency_of_depreciation": 10,
				"depreciation_start_date": month_end_date,
			},
		)
		it_asset_doc.submit()

		expected_gle = (("_Test Fixed ITAsset - _TC", 5250.0, 0.0), ("CWIP Account - _TC", 0.0, 5250.0))

		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertSequenceEqual(gle, expected_gle)

	def test_it_asset_cwip_toggling_cases(self):
		cwip = frappe.db.get_value("ITAsset Category", "Computers", "enable_cwip_accounting")
		name = frappe.db.get_value(
			"ITAsset Category Account", filters={"parent": "Computers"}, fieldname=["name"]
		)
		cwip_acc = "CWIP Account - _TC"

		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", 0)
		frappe.db.set_value("ITAsset Category Account", name, "capital_work_in_progress_account", "")
		frappe.db.get_value("Company", "_Test Company", "capital_work_in_progress_account", "")

		# case 0 -- PI with cwip disable, ITAsset with cwip disabled, No cwip account set
		pi = make_purchase_invoice(
			item_code="Macbook Pro", qty=1, rate=200000.0, location="Test Location", update_stock=1
		)
		it_asset = frappe.db.get_value("ITAsset", {"purchase_invoice": pi.name, "docstatus": 0}, "name")
		it_asset_doc = frappe.get_doc("ITAsset", it_asset)
		it_asset_doc.available_for_use_date = nowdate()
		it_asset_doc.calculate_depreciation = 0
		it_asset_doc.submit()
		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertFalse(gle)

		# case 1 -- PR with cwip disabled, ITAsset with cwip enabled
		pr = make_purchase_receipt(item_code="Macbook Pro", qty=1, rate=200000.0, location="Test Location")
		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", 1)
		frappe.db.set_value("ITAsset Category Account", name, "capital_work_in_progress_account", cwip_acc)
		it_asset = frappe.db.get_value("ITAsset", {"purchase_receipt": pr.name, "docstatus": 0}, "name")
		it_asset_doc = frappe.get_doc("ITAsset", it_asset)
		it_asset_doc.available_for_use_date = nowdate()
		it_asset_doc.calculate_depreciation = 0
		it_asset_doc.submit()
		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertFalse(gle)

		# case 2 -- PR with cwip enabled, ITAsset with cwip disabled
		pr = make_purchase_receipt(item_code="Macbook Pro", qty=1, rate=200000.0, location="Test Location")
		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", 0)
		it_asset = frappe.db.get_value("ITAsset", {"purchase_receipt": pr.name, "docstatus": 0}, "name")
		it_asset_doc = frappe.get_doc("ITAsset", it_asset)
		it_asset_doc.available_for_use_date = nowdate()
		it_asset_doc.calculate_depreciation = 0
		it_asset_doc.submit()
		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertTrue(gle)

		# case 3 -- PI with cwip disabled, ITAsset with cwip enabled
		pi = make_purchase_invoice(
			item_code="Macbook Pro", qty=1, rate=200000.0, location="Test Location", update_stock=1
		)
		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", 1)
		it_asset = frappe.db.get_value("ITAsset", {"purchase_invoice": pi.name, "docstatus": 0}, "name")
		it_asset_doc = frappe.get_doc("ITAsset", it_asset)
		it_asset_doc.available_for_use_date = nowdate()
		it_asset_doc.calculate_depreciation = 0
		it_asset_doc.submit()
		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertFalse(gle)

		# case 4 -- PI with cwip enabled, ITAsset with cwip disabled
		pi = make_purchase_invoice(
			item_code="Macbook Pro", qty=1, rate=200000.0, location="Test Location", update_stock=1
		)
		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", 0)
		it_asset = frappe.db.get_value("ITAsset", {"purchase_invoice": pi.name, "docstatus": 0}, "name")
		it_asset_doc = frappe.get_doc("ITAsset", it_asset)
		it_asset_doc.available_for_use_date = nowdate()
		it_asset_doc.calculate_depreciation = 0
		it_asset_doc.submit()
		gle = get_gl_entries("ITAsset", it_asset_doc.name)
		self.assertTrue(gle)

		frappe.db.set_value("ITAsset Category", "Computers", "enable_cwip_accounting", cwip)
		frappe.db.set_value("ITAsset Category Account", name, "capital_work_in_progress_account", cwip_acc)
		frappe.db.get_value("Company", "_Test Company", "capital_work_in_progress_account", cwip_acc)


class TestDepreciationMethods(ITAssetSetup):
	def test_schedule_for_straight_line_method(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-01-01",
			purchase_date="2030-01-01",
			expected_value_after_useful_life=10000,
			depreciation_start_date="2030-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.status, "Draft")
		expected_schedules = [
			["2030-12-31", 30000.00, 30000.00],
			["2031-12-31", 30000.00, 60000.00],
			["2032-12-31", 30000.00, 90000.00],
		]

		schedules = [
			[cstr(d.schedule_date), d.depreciation_amount, d.accumulated_depreciation_amount]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_schedule_for_straight_line_method_for_existing_it_asset(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-06-06",
			is_existing_it_asset=1,
			number_of_depreciations_booked=2,
			opening_accumulated_depreciation=47095.89,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2032-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.status, "Draft")
		expected_schedules = [["2032-12-31", 42904.11, 90000.0]]
		schedules = [
			[cstr(d.schedule_date), flt(d.depreciation_amount, 2), d.accumulated_depreciation_amount]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_schedule_for_straight_line_method_with_daily_prorata_based(
		self,
	):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2023-01-01",
			purchase_date="2023-01-01",
			gross_purchase_amount=12000,
			depreciation_start_date="2023-01-31",
			total_number_of_depreciations=12,
			frequency_of_depreciation=1,
			daily_prorata_based=1,
		)

		expected_schedules = [
			["2023-01-31", 1019.18, 1019.18],
			["2023-02-28", 920.55, 1939.73],
			["2023-03-31", 1019.18, 2958.91],
			["2023-04-30", 986.3, 3945.21],
			["2023-05-31", 1019.18, 4964.39],
			["2023-06-30", 986.3, 5950.69],
			["2023-07-31", 1019.18, 6969.87],
			["2023-08-31", 1019.18, 7989.05],
			["2023-09-30", 986.3, 8975.35],
			["2023-10-31", 1019.18, 9994.53],
			["2023-11-30", 986.3, 10980.83],
			["2023-12-31", 1019.17, 12000.0],
		]

		schedules = [
			[cstr(d.schedule_date), d.depreciation_amount, d.accumulated_depreciation_amount]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_schedule_for_double_declining_method(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-01-01",
			purchase_date="2030-01-01",
			depreciation_method="Double Declining Balance",
			expected_value_after_useful_life=10000,
			depreciation_start_date="2030-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.status, "Draft")

		expected_schedules = [
			["2030-12-31", 66667.00, 66667.00],
			["2031-12-31", 22222.11, 88889.11],
			["2032-12-31", 1110.89, 90000.0],
		]

		schedules = [
			[cstr(d.schedule_date), d.depreciation_amount, d.accumulated_depreciation_amount]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_schedule_for_double_declining_method_for_existing_it_asset(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-01-01",
			is_existing_it_asset=1,
			depreciation_method="Double Declining Balance",
			number_of_depreciations_booked=1,
			opening_accumulated_depreciation=50000,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2031-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.status, "Draft")

		expected_schedules = [["2031-12-31", 33333.50, 83333.50], ["2032-12-31", 6666.50, 90000.0]]

		schedules = [
			[cstr(d.schedule_date), d.depreciation_amount, d.accumulated_depreciation_amount]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_schedule_for_prorated_straight_line_method(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-01-30",
			purchase_date="2030-01-30",
			depreciation_method="Straight Line",
			expected_value_after_useful_life=10000,
			depreciation_start_date="2030-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		expected_schedules = [
			["2030-12-31", 27616.44, 27616.44],
			["2031-12-31", 30000.0, 57616.44],
			["2032-12-31", 30000.0, 87616.44],
			["2033-01-30", 2383.56, 90000.0],
		]

		schedules = [
			[
				cstr(d.schedule_date),
				flt(d.depreciation_amount, 2),
				flt(d.accumulated_depreciation_amount, 2),
			]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	# WDV: Written Down Value method
	def test_depreciation_entry_for_wdv_without_pro_rata(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-01-01",
			purchase_date="2030-01-01",
			depreciation_method="Written Down Value",
			expected_value_after_useful_life=12500,
			depreciation_start_date="2030-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.finance_books[0].rate_of_depreciation, 50.0)

		expected_schedules = [
			["2030-12-31", 50000.0, 50000.0],
			["2031-12-31", 25000.0, 75000.0],
			["2032-12-31", 12500.0, 87500.0],
		]

		schedules = [
			[
				cstr(d.schedule_date),
				flt(d.depreciation_amount, 2),
				flt(d.accumulated_depreciation_amount, 2),
			]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	# WDV: Written Down Value method
	def test_pro_rata_depreciation_entry_for_wdv(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2030-06-06",
			purchase_date="2030-01-01",
			depreciation_method="Written Down Value",
			expected_value_after_useful_life=12500,
			depreciation_start_date="2030-12-31",
			total_number_of_depreciations=3,
			frequency_of_depreciation=12,
		)

		self.assertEqual(it_asset.finance_books[0].rate_of_depreciation, 50.0)

		expected_schedules = [
			["2030-12-31", 28630.14, 28630.14],
			["2031-12-31", 35684.93, 64315.07],
			["2032-12-31", 17842.47, 82157.54],
			["2033-06-06", 5342.47, 87500.01],
		]

		schedules = [
			[
				cstr(d.schedule_date),
				flt(d.depreciation_amount, 2),
				flt(d.accumulated_depreciation_amount, 2),
			]
			for d in it_asset.get("schedules")
		]

		self.assertEqual(schedules, expected_schedules)

	def test_monthly_depreciation_by_wdv_method(self):
		it_asset = create_it_asset(
			calculate_depreciation=1,
			available_for_use_date="2022-02-15",
			purchase_date="2022-02-15",
			depreciation_method="Written Down Value",
			gross_purchase_amount=10000,
			expected_value_after_useful_life=5000,
			depreciation_start_date="2022-02-28",
			total_number_of_depreciations=5,
			frequency_of_depreciation=1,
		)

		expected_schedules = [
			["2022-02-28", 310.89, 310.89],
			["2022-03-31", 654.45, 965.34],
			["2022-04-30", 654.45, 1619.79],
			["2022-05-31", 654.45, 2274.24],
			["2022-06-30", 654.45, 2928.69],
			["2022-07-15", 2071.31, 5000.0],
		]

		schedules = [
			[
				cstr(d.schedule_date),
				flt(d.depreciation_amount, 2),
				flt(d.accumulated_depreciation_amount, 2),
			]
			for d in it_asset.get("schedules")
		]
		self.assertEqual(schedules, expected_schedules)


class TestDepreciationBasics(ITAssetSetup):
	def test_depreciation_without_pro_rata(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date=getdate("2019-12-31"),
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date=getdate("2020-12-31"),
			submit=1,
		)

		expected_values = [
			["2020-12-31", 30000, 30000],
			["2021-12-31", 30000, 60000],
			["2022-12-31", 30000, 90000],
		]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_values[i][0]), schedule.schedule_date)
			self.assertEqual(expected_values[i][1], schedule.depreciation_amount)
			self.assertEqual(expected_values[i][2], schedule.accumulated_depreciation_amount)

	def test_depreciation_with_pro_rata(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date=getdate("2020-01-01"),
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date=getdate("2020-07-01"),
			submit=1,
		)

		expected_values = [
			["2020-07-01", 15000, 15000],
			["2021-07-01", 30000, 45000],
			["2022-07-01", 30000, 75000],
			["2023-01-01", 15000, 90000],
		]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_values[i][0]), schedule.schedule_date)
			self.assertEqual(expected_values[i][1], schedule.depreciation_amount)
			self.assertEqual(expected_values[i][2], schedule.accumulated_depreciation_amount)

	def test_get_depreciation_amount(self):
		"""Tests if get_depreciation_amount() returns the right value."""

		from it_module.it.doctype.it_asset.it_asset import get_depreciation_amount

		it_asset = create_it_asset(item_code="Macbook Pro", available_for_use_date="2019-12-31")

		it_asset.calculate_depreciation = 1
		it_asset.append(
			"finance_books",
			{
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-12-31",
			},
		)

		depreciation_amount = get_depreciation_amount(it_asset, 100000, 100000, it_asset.finance_books[0])
		self.assertEqual(depreciation_amount, 30000)

	def test_make_depreciation_schedule(self):
		"""Tests if make_depreciation_schedule() returns the right values."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_method="Straight Line",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-12-31",
		)

		expected_values = [["2020-12-31", 30000.0], ["2021-12-31", 30000.0], ["2022-12-31", 30000.0]]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_values[i][0]), schedule.schedule_date)
			self.assertEqual(expected_values[i][1], schedule.depreciation_amount)

	def test_set_accumulated_depreciation(self):
		"""Tests if set_accumulated_depreciation() returns the right values."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_method="Straight Line",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-12-31",
		)

		expected_values = [30000.0, 60000.0, 90000.0]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(expected_values[i], schedule.accumulated_depreciation_amount)

	def test_check_is_pro_rata(self):
		"""Tests if check_is_pro_rata() returns the right value(i.e. checks if has_pro_rata is accurate)."""

		it_asset = create_it_asset(item_code="Macbook Pro", available_for_use_date="2019-12-31", do_not_save=1)

		it_asset.calculate_depreciation = 1
		it_asset.append(
			"finance_books",
			{
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-12-31",
			},
		)

		has_pro_rata = it_asset.check_is_pro_rata(it_asset.finance_books[0])
		self.assertFalse(has_pro_rata)

		it_asset.finance_books = []
		it_asset.append(
			"finance_books",
			{
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-07-01",
			},
		)

		has_pro_rata = it_asset.check_is_pro_rata(it_asset.finance_books[0])
		self.assertTrue(has_pro_rata)

	def test_expected_value_after_useful_life_greater_than_purchase_amount(self):
		"""Tests if an error is raised when expected_value_after_useful_life(110,000) > gross_purchase_amount(100,000)."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=110000,
			depreciation_start_date="2020-07-01",
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_depreciation_start_date(self):
		"""Tests if an error is raised when neither depreciation_start_date nor available_for_use_date are specified."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=110000,
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_opening_accumulated_depreciation(self):
		"""Tests if an error is raised when opening_accumulated_depreciation > (gross_purchase_amount - expected_value_after_useful_life)."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-07-01",
			opening_accumulated_depreciation=100000,
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_number_of_depreciations_booked(self):
		"""Tests if an error is raised when number_of_depreciations_booked is not specified when opening_accumulated_depreciation is."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-07-01",
			opening_accumulated_depreciation=10000,
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_number_of_depreciations(self):
		"""Tests if an error is raised when number_of_depreciations_booked >= total_number_of_depreciations."""

		# number_of_depreciations_booked > total_number_of_depreciations
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-07-01",
			opening_accumulated_depreciation=10000,
			number_of_depreciations_booked=5,
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

		# number_of_depreciations_booked = total_number_of_depreciations
		it_asset_2 = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=5,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2020-07-01",
			opening_accumulated_depreciation=10000,
			number_of_depreciations_booked=5,
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset_2.save)

	def test_depreciation_start_date_is_before_purchase_date(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2014-07-01",
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_depreciation_start_date_is_before_available_for_use_date(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			depreciation_start_date="2018-07-01",
			do_not_save=1,
		)

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_finance_books_are_present_if_calculate_depreciation_is_enabled(self):
		it_asset = create_it_asset(item_code="Macbook Pro", do_not_save=1)
		it_asset.calculate_depreciation = 1

		self.assertRaises(frappe.ValidationError, it_asset.save)

	def test_post_depreciation_entries(self):
		"""Tests if post_depreciation_entries() works as expected."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		post_depreciation_entries(date="2021-06-01")
		it_asset.load_from_db()

		self.assertTrue(it_asset.schedules[0].journal_entry)
		self.assertFalse(it_asset.schedules[1].journal_entry)
		self.assertFalse(it_asset.schedules[2].journal_entry)

	def test_depr_entry_posting_when_depr_expense_account_is_an_expense_account(self):
		"""Tests if the Depreciation Expense Account gets debited and the Accumulated Depreciation Account gets credited when the former's an Expense Account."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		post_depreciation_entries(date="2021-06-01")
		it_asset.load_from_db()

		je = frappe.get_doc("Journal Entry", it_asset.schedules[0].journal_entry)
		accounting_entries = [
			{"account": entry.account, "debit": entry.debit, "credit": entry.credit} for entry in je.accounts
		]

		for entry in accounting_entries:
			if entry["account"] == "_Test Depreciations - _TC":
				self.assertTrue(entry["debit"])
				self.assertFalse(entry["credit"])
			else:
				self.assertTrue(entry["credit"])
				self.assertFalse(entry["debit"])

	def test_depr_entry_posting_when_depr_expense_account_is_an_income_account(self):
		"""Tests if the Depreciation Expense Account gets credited and the Accumulated Depreciation Account gets debited when the former's an Income Account."""

		depr_expense_account = frappe.get_doc("Account", "_Test Depreciations - _TC")
		depr_expense_account.root_type = "Income"
		depr_expense_account.parent_account = "Income - _TC"
		depr_expense_account.save()

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		post_depreciation_entries(date="2021-06-01")
		it_asset.load_from_db()

		je = frappe.get_doc("Journal Entry", it_asset.schedules[0].journal_entry)
		accounting_entries = [
			{"account": entry.account, "debit": entry.debit, "credit": entry.credit} for entry in je.accounts
		]

		for entry in accounting_entries:
			if entry["account"] == "_Test Depreciations - _TC":
				self.assertTrue(entry["credit"])
				self.assertFalse(entry["debit"])
			else:
				self.assertTrue(entry["debit"])
				self.assertFalse(entry["credit"])

		# resetting
		depr_expense_account.root_type = "Expense"
		depr_expense_account.parent_account = "Expenses - _TC"
		depr_expense_account.save()

	def test_clear_depreciation_schedule(self):
		"""Tests if clear_depreciation_schedule() works as expected."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2019-12-31",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=12,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		post_depreciation_entries(date="2021-06-01")
		it_asset.load_from_db()

		it_asset.clear_depreciation_schedule()

		self.assertEqual(len(it_asset.schedules), 1)

	def test_clear_depreciation_schedule_for_multiple_finance_books(self):
		it_asset = create_it_asset(item_code="Macbook Pro", available_for_use_date="2019-12-31", do_not_save=1)

		it_asset.calculate_depreciation = 1
		it_asset.append(
			"finance_books",
			{
				"finance_book": "Test Finance Book 1",
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 1,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-01-31",
			},
		)
		it_asset.append(
			"finance_books",
			{
				"finance_book": "Test Finance Book 2",
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 1,
				"total_number_of_depreciations": 6,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-01-31",
			},
		)
		it_asset.append(
			"finance_books",
			{
				"finance_book": "Test Finance Book 3",
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-12-31",
			},
		)
		it_asset.submit()

		post_depreciation_entries(date="2020-04-01")
		it_asset.load_from_db()

		it_asset.clear_depreciation_schedule()

		self.assertEqual(len(it_asset.schedules), 6)

		for schedule in it_asset.schedules:
			if schedule.idx <= 3:
				self.assertEqual(schedule.finance_book_id, "1")
			else:
				self.assertEqual(schedule.finance_book_id, "2")

	def test_depreciation_schedules_are_set_up_for_multiple_finance_books(self):
		it_asset = create_it_asset(item_code="Macbook Pro", available_for_use_date="2019-12-31", do_not_save=1)

		it_asset.calculate_depreciation = 1
		it_asset.append(
			"finance_books",
			{
				"finance_book": "Test Finance Book 1",
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 3,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-12-31",
			},
		)
		it_asset.append(
			"finance_books",
			{
				"finance_book": "Test Finance Book 2",
				"depreciation_method": "Straight Line",
				"frequency_of_depreciation": 12,
				"total_number_of_depreciations": 6,
				"expected_value_after_useful_life": 10000,
				"depreciation_start_date": "2020-12-31",
			},
		)
		it_asset.save()

		self.assertEqual(len(it_asset.schedules), 9)

		for schedule in it_asset.schedules:
			if schedule.idx <= 3:
				self.assertEqual(schedule.finance_book_id, "1")
			else:
				self.assertEqual(schedule.finance_book_id, "2")

	def test_depreciation_entry_cancellation(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			purchase_date="2020-06-06",
			available_for_use_date="2020-06-06",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=10,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		post_depreciation_entries(date="2021-01-01")

		it_asset.load_from_db()

		# cancel depreciation entry
		depr_entry = it_asset.get("schedules")[0].journal_entry
		self.assertTrue(depr_entry)
		frappe.get_doc("Journal Entry", depr_entry).cancel()

		it_asset.load_from_db()
		depr_entry = it_asset.get("schedules")[0].journal_entry
		self.assertFalse(depr_entry)

	def test_it_asset_expected_value_after_useful_life(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			available_for_use_date="2020-06-06",
			purchase_date="2020-06-06",
			frequency_of_depreciation=10,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
		)

		accumulated_depreciation_after_full_schedule = max(
			d.accumulated_depreciation_amount for d in it_asset.get("schedules")
		)

		it_asset_value_after_full_schedule = flt(it_asset.gross_purchase_amount) - flt(
			accumulated_depreciation_after_full_schedule
		)

		self.assertTrue(
			it_asset.finance_books[0].expected_value_after_useful_life >= it_asset_value_after_full_schedule
		)

	def test_gle_made_by_depreciation_entries(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			purchase_date="2020-01-30",
			available_for_use_date="2020-01-30",
			depreciation_start_date="2020-12-31",
			frequency_of_depreciation=10,
			total_number_of_depreciations=3,
			expected_value_after_useful_life=10000,
			submit=1,
		)

		self.assertEqual(it_asset.status, "Submitted")

		frappe.db.set_value("Company", "_Test Company", "series_for_depreciation_entry", "DEPR-")
		post_depreciation_entries(date="2021-01-01")
		it_asset.load_from_db()

		# check depreciation entry series
		self.assertEqual(it_asset.get("schedules")[0].journal_entry[:4], "DEPR")

		expected_gle = (
			("_Test Accumulated Depreciations - _TC", 0.0, 30000.0),
			("_Test Depreciations - _TC", 30000.0, 0.0),
		)

		gle = frappe.db.sql(
			"""select account, debit, credit from `tabGL Entry`
			where against_voucher_type='ITAsset' and against_voucher = %s
			order by account""",
			it_asset.name,
		)

		self.assertSequenceEqual(gle, expected_gle)
		self.assertEqual(it_asset.get("value_after_depreciation"), 0)

	def test_expected_value_change(self):
		"""
		tests if changing `expected_value_after_useful_life`
		affects `value_after_depreciation`
		"""

		it_asset = create_it_asset(calculate_depreciation=1)
		it_asset.opening_accumulated_depreciation = 2000
		it_asset.number_of_depreciations_booked = 1

		it_asset.finance_books[0].expected_value_after_useful_life = 100
		it_asset.save()
		it_asset.reload()
		self.assertEqual(it_asset.finance_books[0].value_after_depreciation, 98000.0)

		# changing expected_value_after_useful_life shouldn't affect value_after_depreciation
		it_asset.finance_books[0].expected_value_after_useful_life = 200
		it_asset.save()
		it_asset.reload()
		self.assertEqual(it_asset.finance_books[0].value_after_depreciation, 98000.0)

	def test_it_asset_cost_center(self):
		it_asset = create_it_asset(is_existing_it_asset=1, do_not_save=1)
		it_asset.cost_center = "Main - WP"

		self.assertRaises(frappe.ValidationError, it_asset.submit)

		it_asset.cost_center = "Main - _TC"
		it_asset.submit()

	def test_depreciation_on_final_day_of_the_month(self):
		"""Tests if final day of the month is picked each time, if the depreciation start date is the last day of the month."""

		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			purchase_date="2020-01-30",
			available_for_use_date="2020-02-15",
			depreciation_start_date="2020-02-29",
			frequency_of_depreciation=1,
			total_number_of_depreciations=5,
			submit=1,
		)

		expected_dates = [
			"2020-02-29",
			"2020-03-31",
			"2020-04-30",
			"2020-05-31",
			"2020-06-30",
			"2020-07-15",
		]

		for i, schedule in enumerate(it_asset.schedules):
			self.assertEqual(getdate(expected_dates[i]), getdate(schedule.schedule_date))

	def test_manual_depreciation_for_existing_it_asset(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			is_existing_it_asset=1,
			purchase_date="2020-01-30",
			available_for_use_date="2020-01-30",
			submit=1,
		)

		self.assertEqual(it_asset.status, "Submitted")
		self.assertEqual(it_asset.get_value_after_depreciation(), 100000)

		jv = make_journal_entry(
			"_Test Depreciations - _TC", "_Test Accumulated Depreciations - _TC", 100, save=False
		)
		for d in jv.accounts:
			d.reference_type = "ITAsset"
			d.reference_name = it_asset.name
		jv.voucher_type = "Depreciation Entry"
		jv.insert()
		jv.submit()

		it_asset.reload()
		self.assertEqual(it_asset.get_value_after_depreciation(), 99900)

		jv.cancel()

		it_asset.reload()
		self.assertEqual(it_asset.get_value_after_depreciation(), 100000)

	def test_manual_depreciation_for_depreciable_it_asset(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			purchase_date="2020-01-30",
			available_for_use_date="2020-01-30",
			expected_value_after_useful_life=10000,
			total_number_of_depreciations=10,
			frequency_of_depreciation=1,
			submit=1,
		)

		self.assertEqual(it_asset.status, "Submitted")
		self.assertEqual(it_asset.get_value_after_depreciation(), 100000)

		jv = make_journal_entry(
			"_Test Depreciations - _TC", "_Test Accumulated Depreciations - _TC", 100, save=False
		)
		for d in jv.accounts:
			d.reference_type = "ITAsset"
			d.reference_name = it_asset.name
		jv.voucher_type = "Depreciation Entry"
		jv.insert()
		jv.submit()

		it_asset.reload()
		self.assertEqual(it_asset.get_value_after_depreciation(), 99900)

		jv.cancel()

		it_asset.reload()
		self.assertEqual(it_asset.get_value_after_depreciation(), 100000)

	def test_manual_depreciation_with_incorrect_jv_voucher_type(self):
		it_asset = create_it_asset(
			item_code="Macbook Pro",
			calculate_depreciation=1,
			purchase_date="2020-01-30",
			available_for_use_date="2020-01-30",
			expected_value_after_useful_life=10000,
			total_number_of_depreciations=10,
			frequency_of_depreciation=1,
			submit=1,
		)

		jv = make_journal_entry(
			"_Test Depreciations - _TC", "_Test Accumulated Depreciations - _TC", 100, save=False
		)
		for d in jv.accounts:
			d.reference_type = "ITAsset"
			d.reference_name = it_asset.name
			d.account_type = "Depreciation"
		jv.voucher_type = "Journal Entry"

		self.assertRaises(frappe.ValidationError, jv.insert)

	def test_multi_currency_it_asset_pr_creation(self):
		pr = make_purchase_receipt(
			item_code="Macbook Pro",
			qty=1,
			rate=100.0,
			location="Test Location",
			supplier="_Test Supplier USD",
			currency="USD",
		)

		pr.submit()
		self.assertTrue(get_gl_entries("Purchase Receipt", pr.name))


def get_gl_entries(doctype, docname):
	gl_entry = frappe.qb.DocType("GL Entry")
	return (
		frappe.qb.from_(gl_entry)
		.select(gl_entry.account, gl_entry.debit, gl_entry.credit)
		.where((gl_entry.voucher_type == doctype) & (gl_entry.voucher_no == docname))
		.orderby(gl_entry.account)
		.run()
	)


def create_it_asset_data():
	if not frappe.db.exists("ITAsset Category", "Computers"):
		create_it_asset_category()

	if not frappe.db.exists("Item", "Macbook Pro"):
		create_fixed_it_asset_item()

	if not frappe.db.exists("Location", "Test Location"):
		frappe.get_doc({"doctype": "Location", "location_name": "Test Location"}).insert()

	if not frappe.db.exists("Finance Book", "Test Finance Book 1"):
		frappe.get_doc({"doctype": "Finance Book", "finance_book_name": "Test Finance Book 1"}).insert()

	if not frappe.db.exists("Finance Book", "Test Finance Book 2"):
		frappe.get_doc({"doctype": "Finance Book", "finance_book_name": "Test Finance Book 2"}).insert()

	if not frappe.db.exists("Finance Book", "Test Finance Book 3"):
		frappe.get_doc({"doctype": "Finance Book", "finance_book_name": "Test Finance Book 3"}).insert()


def create_it_asset(**args):
	args = frappe._dict(args)

	create_it_asset_data()

	it_asset = frappe.get_doc(
		{
			"doctype": "ITAsset",
			"it_asset_name": args.it_asset_name or "Macbook Pro 1",
			"it_asset_category": args.it_asset_category or "Computers",
			"item_code": args.item_code or "Macbook Pro",
			"company": args.company or "_Test Company",
			"purchase_date": args.purchase_date or "2015-01-01",
			"calculate_depreciation": args.calculate_depreciation or 0,
			"opening_accumulated_depreciation": args.opening_accumulated_depreciation or 0,
			"number_of_depreciations_booked": args.number_of_depreciations_booked or 0,
			"gross_purchase_amount": args.gross_purchase_amount or 100000,
			"purchase_receipt_amount": args.purchase_receipt_amount or 100000,
			"maintenance_required": args.maintenance_required or 0,
			"warehouse": args.warehouse or "_Test Warehouse - _TC",
			"available_for_use_date": args.available_for_use_date or "2020-06-06",
			"location": args.location or "Test Location",
			"it_asset_owner": args.it_asset_owner or "Company",
			"is_existing_it_asset": args.is_existing_it_asset or 1,
			"is_composite_it_asset": args.is_composite_it_asset or 0,
			"it_asset_quantity": args.get("it_asset_quantity") or 1,
			"depr_entry_posting_status": args.depr_entry_posting_status or "",
		}
	)

	if it_asset.calculate_depreciation:
		it_asset.append(
			"finance_books",
			{
				"finance_book": args.finance_book,
				"depreciation_method": args.depreciation_method or "Straight Line",
				"frequency_of_depreciation": args.frequency_of_depreciation or 12,
				"total_number_of_depreciations": args.total_number_of_depreciations or 5,
				"expected_value_after_useful_life": args.expected_value_after_useful_life or 0,
				"depreciation_start_date": args.depreciation_start_date,
				"daily_prorata_based": args.daily_prorata_based or 0,
				"shift_based": args.shift_based or 0,
			},
		)

	if not args.do_not_save:
		try:
			it_asset.insert(ignore_if_duplicate=True)
		except frappe.DuplicateEntryError:
			pass

	if args.submit:
		it_asset.submit()

	return it_asset


def create_it_asset_category(enable_cwip=1):
	it_asset_category = frappe.new_doc("ITAsset Category")
	it_asset_category.it_asset_category_name = "Computers"
	it_asset_category.total_number_of_depreciations = 3
	it_asset_category.frequency_of_depreciation = 3
	it_asset_category.enable_cwip_accounting = enable_cwip
	it_asset_category.append(
		"accounts",
		{
			"company_name": "_Test Company",
			"fixed_it_asset_account": "_Test Fixed ITAsset - _TC",
			"accumulated_depreciation_account": "_Test Accumulated Depreciations - _TC",
			"depreciation_expense_account": "_Test Depreciations - _TC",
			"capital_work_in_progress_account": "CWIP Account - _TC",
		},
	)
	it_asset_category.append(
		"accounts",
		{
			"company_name": "_Test Company with perpetual inventory",
			"fixed_it_asset_account": "_Test Fixed ITAsset - TCP1",
			"accumulated_depreciation_account": "_Test Accumulated Depreciations - TCP1",
			"depreciation_expense_account": "_Test Depreciations - TCP1",
		},
	)

	it_asset_category.insert()


def create_fixed_it_asset_item(item_code=None, auto_create_it_assets=1, is_grouped_it_asset=0):
	meta = frappe.get_meta("ITAsset")
	naming_series = meta.get_field("naming_series").options.splitlines()[0] or "ACC-ASS-.YYYY.-"
	try:
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code or "Macbook Pro",
				"item_name": "Macbook Pro",
				"description": "Macbook Pro Retina Display",
				"it_asset_category": "Computers",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_fixed_it_asset": 1,
				"auto_create_it_assets": auto_create_it_assets,
				"is_grouped_it_asset": is_grouped_it_asset,
				"it_asset_naming_series": naming_series,
			}
		)
		item.insert(ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		pass
	return item


def set_depreciation_settings_in_company(company=None):
	if not company:
		company = "_Test Company"
	company = frappe.get_doc("Company", company)
	company.accumulated_depreciation_account = "_Test Accumulated Depreciations - " + company.abbr
	company.depreciation_expense_account = "_Test Depreciations - " + company.abbr
	company.disposal_account = "_Test Gain/Loss on ITAsset Disposal - " + company.abbr
	company.depreciation_cost_center = "Main - " + company.abbr
	company.save()

	# Enable booking it_asset depreciation entry automatically
	frappe.db.set_value("Accounts Settings", None, "book_it_asset_depreciation_entry_automatically", 1)


def enable_cwip_accounting(it_asset_category, enable=1):
	frappe.db.set_value("ITAsset Category", it_asset_category, "enable_cwip_accounting", enable)
