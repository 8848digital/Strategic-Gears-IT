// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.views.calendar["IT Asset Maintenance Log"] = {
	field_map: {
		start: "due_date",
		end: "due_date",
		id: "name",
		title: "task",
		allDay: "allDay",
		progress: "progress",
	},
	filters: [
		{
			fieldtype: "Link",
			fieldname: "asset_name",
			options: "IT Asset Maintenance",
			label: __("IT Asset Maintenance"),
		},
	],
	get_events_method: "frappe.desk.calendar.get_events",
};
