# Copyright (c) 2024, gopal@8848digital.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class ITEmployees(Document):
	pass


@frappe.whitelist()
def create_by_scheduler():

	get_all_employees = frappe.db.get_all('Employee', {'status':'Active'}, ['name'])

	for emp in get_all_employees:
		existing_entry = frappe.db.exists('IT Employees', {'employee': emp['name']})
		if not existing_entry:
			new_doc = frappe.new_doc('IT Employees')
			new_doc.employee = emp['name']
			new_doc.save()