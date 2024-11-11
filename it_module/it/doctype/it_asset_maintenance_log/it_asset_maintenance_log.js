// Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("IT Asset Maintenance Log", {
	asset_maintenance: (frm) => {
		frm.set_query("task", function (doc) {
			return {
				query: "it_module.it.doctype.it_asset_maintenance_log.it_asset_maintenance_log.get_maintenance_tasks",
				filters: {
					asset_maintenance: doc.asset_maintenance,
				},
			};
		});
	},
});
