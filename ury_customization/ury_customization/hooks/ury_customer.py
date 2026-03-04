import frappe


def before_insert(doc, event):
	if not doc.mobile_number:
		frappe.throw("Mobile Number is Mandatory")
