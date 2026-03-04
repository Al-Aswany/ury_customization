import base64

import frappe


@frappe.whitelist()
def create_pdf(doctype, name, print_format=None, no_letterhead=0):
	"""
	Generate a PDF for the given document and return it as a base64 string.

	Called by the master tab to produce the file_content for the bridge.
	"""
	pdf_bytes = frappe.get_print(
		doctype,
		name,
		print_format=print_format or None,
		no_letterhead=int(no_letterhead),
		as_pdf=True,
	)
	return base64.b64encode(pdf_bytes).decode("utf-8")


@frappe.whitelist()
def print_silently(doctype, name, print_format=None, print_type="DEFAULT", qty=1):
	"""
	Generate a PDF server-side and publish it via realtime so the master tab
	(which holds the WebSocket connection to the bridge) can print it.

	Use this when the calling tab is NOT the master tab, or when printing
	is triggered from a device that has no local printer connection.
	"""
	pdf_bytes = frappe.get_print(
		doctype,
		name,
		print_format=print_format or None,
		as_pdf=True,
	)
	file_content = base64.b64encode(pdf_bytes).decode("utf-8")

	frappe.publish_realtime(
		"silent-print",
		{
			"type": print_type,
			"url": f"{name}.pdf",
			"file_content": file_content,
			"qty": int(qty),
		},
		user=frappe.session.user,
	)
	return True
