# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe
from frappe.utils import flt, nowdate

from erpnext.assets.doctype.asset.asset import (
	get_asset_account,
	get_asset_value_after_depreciation,
)
from erpnext.assets.doctype.asset.test_asset import (
	create_asset,
	create_asset_data,
	set_depreciation_settings_in_company,
)
from erpnext.stock.doctype.item.test_item import create_item


class TestITAssetRepair(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		set_depreciation_settings_in_company()
		create_asset_data()
		create_item("_Test Stock Item")
		frappe.db.sql("delete from `tabTax Rule`")

	def test_update_status(self):
		asset = create_asset(submit=1)
		initial_status = asset.status
		it_asset_repair = create_it_asset_repair(asset=asset)

		if it_asset_repair.repair_status == "Pending":
			asset.reload()
			self.assertEqual(asset.status, "Out of Order")

		it_asset_repair.repair_status = "Completed"
		it_asset_repair.save()
		asset_status = frappe.db.get_value("Asset", it_asset_repair.asset, "status")
		self.assertEqual(asset_status, initial_status)

	def test_stock_item_total_value(self):
		it_asset_repair = create_it_asset_repair(stock_consumption=1)

		for item in it_asset_repair.stock_items:
			total_value = flt(item.valuation_rate) * flt(item.consumed_quantity)
			self.assertEqual(item.total_value, total_value)

	def test_total_repair_cost(self):
		it_asset_repair = create_it_asset_repair(stock_consumption=1)

		total_repair_cost = it_asset_repair.repair_cost
		self.assertEqual(total_repair_cost, it_asset_repair.repair_cost)
		for item in it_asset_repair.stock_items:
			total_repair_cost += item.total_value

		self.assertEqual(total_repair_cost, it_asset_repair.total_repair_cost)

	def test_repair_status_after_submit(self):
		it_asset_repair = create_it_asset_repair(submit=1)
		self.assertNotEqual(it_asset_repair.repair_status, "Pending")

	def test_stock_items(self):
		it_asset_repair = create_it_asset_repair(stock_consumption=1)
		self.assertTrue(it_asset_repair.stock_consumption)
		self.assertTrue(it_asset_repair.stock_items)

	def test_warehouse(self):
		it_asset_repair = create_it_asset_repair(stock_consumption=1)
		self.assertTrue(it_asset_repair.stock_consumption)
		self.assertTrue(it_asset_repair.warehouse)

	def test_decrease_stock_quantity(self):
		it_asset_repair = create_it_asset_repair(stock_consumption=1, submit=1)
		stock_entry = frappe.get_last_doc("Stock Entry")

		self.assertEqual(stock_entry.stock_entry_type, "Material Issue")
		self.assertEqual(stock_entry.items[0].s_warehouse, it_asset_repair.warehouse)
		self.assertEqual(stock_entry.items[0].item_code, it_asset_repair.stock_items[0].item_code)
		self.assertEqual(stock_entry.items[0].qty, it_asset_repair.stock_items[0].consumed_quantity)

	def test_serialized_item_consumption(self):
		from erpnext.stock.doctype.serial_no.serial_no import SerialNoRequiredError
		from erpnext.stock.doctype.stock_entry.test_stock_entry import make_serialized_item

		stock_entry = make_serialized_item()
		serial_nos = stock_entry.get("items")[0].serial_no
		serial_no = serial_nos.split("\n")[0]

		# should not raise any error
		create_it_asset_repair(
			stock_consumption=1,
			item_code=stock_entry.get("items")[0].item_code,
			warehouse="_Test Warehouse - _TC",
			serial_no=serial_no,
			submit=1,
		)

		# should raise error
		it_asset_repair = create_it_asset_repair(
			stock_consumption=1,
			warehouse="_Test Warehouse - _TC",
			item_code=stock_entry.get("items")[0].item_code,
		)

		it_asset_repair.repair_status = "Completed"
		self.assertRaises(SerialNoRequiredError, it_asset_repair.submit)

	def test_increase_in_asset_value_due_to_stock_consumption(self):
		asset = create_asset(calculate_depreciation=1, submit=1)
		initial_asset_value = get_asset_value_after_depreciation(asset.name)
		it_asset_repair = create_it_asset_repair(asset=asset, stock_consumption=1, submit=1)
		asset.reload()

		increase_in_asset_value = get_asset_value_after_depreciation(asset.name) - initial_asset_value
		self.assertEqual(it_asset_repair.stock_items[0].total_value, increase_in_asset_value)

	def test_increase_in_asset_value_due_to_repair_cost_capitalisation(self):
		asset = create_asset(calculate_depreciation=1, submit=1)
		initial_asset_value = get_asset_value_after_depreciation(asset.name)
		it_asset_repair = create_it_asset_repair(asset=asset, capitalize_repair_cost=1, submit=1)
		asset.reload()

		increase_in_asset_value = get_asset_value_after_depreciation(asset.name) - initial_asset_value
		self.assertEqual(it_asset_repair.repair_cost, increase_in_asset_value)

	def test_purchase_invoice(self):
		it_asset_repair = create_it_asset_repair(capitalize_repair_cost=1, submit=1)
		self.assertTrue(it_asset_repair.purchase_invoice)

	def test_gl_entries_with_perpetual_inventory(self):
		set_depreciation_settings_in_company(company="_Test Company with perpetual inventory")

		asset_category = frappe.get_doc("Asset Category", "Computers")
		asset_category.enable_cwip_accounting = 0
		asset_category.append(
			"accounts",
			{
				"company_name": "_Test Company with perpetual inventory",
				"fixed_asset_account": "_Test Fixed Asset - TCP1",
				"accumulated_depreciation_account": "_Test Accumulated Depreciations - TCP1",
				"depreciation_expense_account": "_Test Depreciations - TCP1",
			},
		)
		asset_category.save()

		it_asset_repair = create_it_asset_repair(
			capitalize_repair_cost=1,
			stock_consumption=1,
			warehouse="Stores - TCP1",
			company="_Test Company with perpetual inventory",
			submit=1,
		)

		gl_entries = frappe.db.sql(
			"""
			select
				account,
				sum(debit) as debit,
				sum(credit) as credit
			from `tabGL Entry`
			where
				voucher_type='Asset Repair'
				and voucher_no=%s
			group by
				account
		""",
			it_asset_repair.name,
			as_dict=1,
		)

		self.assertTrue(gl_entries)

		fixed_asset_account = get_asset_account(
			"fixed_asset_account", asset=it_asset_repair.asset, company=it_asset_repair.company
		)
		pi_expense_account = (
			frappe.get_doc("Purchase Invoice", it_asset_repair.purchase_invoice).items[0].expense_account
		)
		stock_entry_expense_account = (
			frappe.get_doc("Stock Entry", it_asset_repair.stock_entry).get("items")[0].expense_account
		)

		expected_values = {
			fixed_asset_account: [it_asset_repair.total_repair_cost, 0],
			pi_expense_account: [0, it_asset_repair.repair_cost],
			stock_entry_expense_account: [0, 100],
		}

		for d in gl_entries:
			self.assertEqual(expected_values[d.account][0], d.debit)
			self.assertEqual(expected_values[d.account][1], d.credit)

	def test_gl_entries_with_periodical_inventory(self):
		frappe.db.set_value("Company", "_Test Company", "default_expense_account", "Cost of Goods Sold - _TC")
		it_asset_repair = create_it_asset_repair(
			capitalize_repair_cost=1,
			stock_consumption=1,
			submit=1,
		)

		gl_entries = frappe.db.sql(
			"""
			select
				account,
				sum(debit) as debit,
				sum(credit) as credit
			from `tabGL Entry`
			where
				voucher_type='Asset Repair'
				and voucher_no=%s
			group by
				account
		""",
			it_asset_repair.name,
			as_dict=1,
		)

		self.assertTrue(gl_entries)

		fixed_asset_account = get_asset_account(
			"fixed_asset_account", asset=it_asset_repair.asset, company=it_asset_repair.company
		)
		default_expense_account = frappe.get_cached_value(
			"Company", it_asset_repair.company, "default_expense_account"
		)

		expected_values = {fixed_asset_account: [1100, 0], default_expense_account: [0, 1100]}

		for d in gl_entries:
			self.assertEqual(expected_values[d.account][0], d.debit)
			self.assertEqual(expected_values[d.account][1], d.credit)

	def test_increase_in_asset_life(self):
		asset = create_asset(calculate_depreciation=1, submit=1)
		initial_num_of_depreciations = num_of_depreciations(asset)
		create_it_asset_repair(asset=asset, capitalize_repair_cost=1, submit=1)
		asset.reload()

		self.assertEqual((initial_num_of_depreciations + 1), num_of_depreciations(asset))
		self.assertEqual(
			asset.schedules[-1].accumulated_depreciation_amount,
			asset.finance_books[0].value_after_depreciation,
		)


def num_of_depreciations(asset):
	return asset.finance_books[0].total_number_of_depreciations


def create_it_asset_repair(**args):
	from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
	from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse

	args = frappe._dict(args)

	if args.asset:
		asset = args.asset
	else:
		asset = create_asset(is_existing_asset=1, submit=1, company=args.company)
	it_asset_repair = frappe.new_doc("Asset Repair")
	it_asset_repair.update(
		{
			"asset": asset.name,
			"asset_name": asset.asset_name,
			"failure_date": nowdate(),
			"description": "Test Description",
			"repair_cost": 0,
			"company": asset.company,
		}
	)

	if args.stock_consumption:
		it_asset_repair.stock_consumption = 1
		it_asset_repair.warehouse = args.warehouse or create_warehouse("Test Warehouse", company=asset.company)
		it_asset_repair.append(
			"stock_items",
			{
				"item_code": args.item_code or "_Test Stock Item",
				"valuation_rate": args.rate if args.get("rate") is not None else 100,
				"consumed_quantity": args.qty or 1,
				"serial_no": args.serial_no,
			},
		)

	it_asset_repair.insert(ignore_if_duplicate=True)

	if args.submit:
		it_asset_repair.repair_status = "Completed"
		it_asset_repair.cost_center = frappe.db.get_value("Company", asset.company, "cost_center")

		if args.stock_consumption:
			stock_entry = frappe.get_doc(
				{"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": asset.company}
			)
			stock_entry.append(
				"items",
				{
					"t_warehouse": it_asset_repair.warehouse,
					"item_code": it_asset_repair.stock_items[0].item_code,
					"qty": it_asset_repair.stock_items[0].consumed_quantity,
					"basic_rate": args.rate if args.get("rate") is not None else 100,
					"cost_center": it_asset_repair.cost_center,
				},
			)
			stock_entry.submit()

		if args.capitalize_repair_cost:
			it_asset_repair.capitalize_repair_cost = 1
			it_asset_repair.repair_cost = 1000
			if asset.calculate_depreciation:
				it_asset_repair.increase_in_asset_life = 12
			pi = make_purchase_invoice(
				company=asset.company,
				expense_account=frappe.db.get_value("Company", asset.company, "default_expense_account"),
				cost_center=it_asset_repair.cost_center,
				warehouse=it_asset_repair.warehouse,
			)
			it_asset_repair.purchase_invoice = pi.name

		it_asset_repair.submit()
	return it_asset_repair
