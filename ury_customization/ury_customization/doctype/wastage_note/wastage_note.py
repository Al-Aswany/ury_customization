# Copyright (c) 2026, Mahmoud and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, nowtime, flt
import json


class WastageNote(Document):
	def validate(self):
		self.validate_items()
		self.set_status()

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Please add at least one item to the wastage note"))

		for item in self.items:
			if flt(item.qty) <= 0:
				frappe.throw(_("Quantity must be greater than 0 for item {0}").format(item.item_code))

			if item.batch_no:
				self.validate_batch(item)

			if item.serial_no:
				self.validate_serial_no(item)

	def validate_batch(self, item):
		if not frappe.db.exists("Batch", item.batch_no):
			frappe.throw(_("Batch {0} does not exist").format(item.batch_no))

		batch = frappe.get_doc("Batch", item.batch_no)
		if batch.item != item.item_code:
			frappe.throw(_("Batch {0} does not belong to item {1}").format(item.batch_no, item.item_code))

	def validate_serial_no(self, item):
		serial_nos = [s.strip() for s in item.serial_no.split('\n') if s.strip()]
		for serial_no in serial_nos:
			if not frappe.db.exists("Serial No", serial_no):
				frappe.throw(_("Serial No {0} does not exist").format(serial_no))

			serial_doc = frappe.get_doc("Serial No", serial_no)
			if serial_doc.item_code != item.item_code:
				frappe.throw(_("Serial No {0} does not belong to item {1}").format(serial_no, item.item_code))

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 1:
			if self.stock_entry:
				self.status = "Completed"
			else:
				self.status = "Pending"
		elif self.docstatus == 2:
			self.status = "Cancelled"

	def on_submit(self):
		self.create_stock_entry()
		self.set_status()

	def on_cancel(self):
		if self.stock_entry:
			stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
			if stock_entry.docstatus == 1:
				stock_entry.cancel()
		self.set_status()

	def create_stock_entry(self):
		if self.waste_warehouse:
			stock_entry_type = "Material Transfer"
		else:
			stock_entry_type = "Material Issue"

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.stock_entry_type = stock_entry_type
		stock_entry.company = self.company
		stock_entry.posting_date = self.posting_date
		stock_entry.posting_time = self.posting_time or nowtime()
		stock_entry.purpose = stock_entry_type
		stock_entry.remarks = _("Stock Entry for Wastage Note {0}").format(self.name)

		for item in self.items:
			se_item = stock_entry.append("items", {})
			se_item.item_code = item.item_code
			se_item.qty = item.qty
			se_item.uom = item.uom or frappe.db.get_value("Item", item.item_code, "stock_uom")
			se_item.s_warehouse = self.source_warehouse

			if self.waste_warehouse:
				se_item.t_warehouse = self.waste_warehouse
			else:
				se_item.expense_account = self.expense_account

			se_item.cost_center = self.cost_center

			if item.batch_no:
				se_item.batch_no = item.batch_no
			if item.serial_no:
				se_item.serial_no = item.serial_no

		if not self.waste_warehouse:
			stock_entry.expense_account = self.expense_account

		stock_entry.insert()
		stock_entry.submit()

		self.db_set("stock_entry", stock_entry.name)
		self.db_set("status", "Completed")

		return stock_entry.name


@frappe.whitelist()
def mark_items_waste(payload):
	"""
	Mark items as waste — creates a Wastage Note with Stock Entry.
	Optionally cancels or modifies the linked POS Invoice.
	"""
	if isinstance(payload, str):
		payload = json.loads(payload)

	required_fields = ["company", "source_warehouse", "expense_account", "cost_center"]
	for field in required_fields:
		if not payload.get(field):
			frappe.throw(_("{0} is required").format(field.replace("_", " ").title()))

	items = payload.get("items", [])
	pos_invoice = payload.get("pos_invoice")

	if pos_invoice and not items:
		items = get_invoice_items_for_wastage(pos_invoice)

	if not items:
		frappe.throw(_("No items provided for wastage"))

	wastage_note = frappe.new_doc("Wastage Note")
	wastage_note.company = payload.get("company")
	wastage_note.pos_invoice = pos_invoice
	wastage_note.posting_date = payload.get("posting_date") or nowdate()
	wastage_note.posting_time = payload.get("posting_time") or nowtime()
	wastage_note.source_warehouse = payload.get("source_warehouse")
	wastage_note.waste_warehouse = payload.get("waste_warehouse")
	wastage_note.expense_account = payload.get("expense_account")
	wastage_note.cost_center = payload.get("cost_center")
	wastage_note.remarks = payload.get("remarks")

	for item in items:
		validate_item_for_wastage(item)
		wastage_note.append("items", {
			"item_code": item.get("item_code"),
			"item_name": item.get("item_name") or frappe.db.get_value("Item", item.get("item_code"), "item_name"),
			"qty": flt(item.get("qty")),
			"uom": item.get("uom") or frappe.db.get_value("Item", item.get("item_code"), "stock_uom"),
			"batch_no": item.get("batch_no"),
			"serial_no": item.get("serial_no"),
			"reason": item.get("reason"),
		})

	wastage_note.insert()

	auto_submit = payload.get("auto_submit")
	if auto_submit is None:
		auto_submit = frappe.has_permission("Wastage Note", "submit")

	if auto_submit:
		wastage_note.submit()

	wastage_mode = payload.get("wastage_mode", "full")

	if pos_invoice:
		if wastage_mode == "partial":
			result = partial_invoice_wastage(pos_invoice, items, payload.get("remarks") or "Partial wastage")
			return {
				"wastage_note": wastage_note.name,
				"stock_entry": wastage_note.stock_entry,
				"status": wastage_note.status,
				"invoice_action": result.get("action"),
			}
		else:
			inv = frappe.get_doc("POS Invoice", pos_invoice)
			if inv.docstatus != 0:
				frappe.throw(_("Full wastage cancellation is only allowed on draft invoices"))
			if inv.status not in ("Draft", "Unbilled"):
				frappe.throw(_("Full wastage is only allowed on Draft or Unbilled invoices"))
			cancel_order_for_wastage(pos_invoice, payload.get("remarks") or "Marked as wastage")

	return {
		"wastage_note": wastage_note.name,
		"stock_entry": wastage_note.stock_entry,
		"status": wastage_note.status,
	}


def cancel_order_for_wastage(invoice_id, reason):
	pos_invoice = frappe.get_doc("POS Invoice", invoice_id)

	if pos_invoice.restaurant_table:
		frappe.db.set_value(
			"URY Table",
			pos_invoice.restaurant_table,
			{"occupied": 0, "latest_invoice_time": None},
		)

	try:
		from ury.ury.doctype.ury_order.ury_order import cancel_kot
		cancel_kot(invoice_id)
	except Exception:
		frappe.log_error(
			title=f"KOT cancellation failed for wastage: {invoice_id}",
			message=frappe.get_traceback(),
		)

	frappe.db.sql("""
		UPDATE `tabPOS Invoice Item`
		SET docstatus = 2
		WHERE parent = %s
	""", (invoice_id,))

	frappe.db.set_value("POS Invoice", invoice_id, "docstatus", 2)
	frappe.db.set_value("POS Invoice", invoice_id, "status", "Cancelled")
	frappe.db.set_value("POS Invoice", invoice_id, "cancel_reason", f"Wastage: {reason}")


def partial_invoice_wastage(invoice_id, wasted_items, reason):
	pos_invoice = frappe.get_doc("POS Invoice", invoice_id)

	if pos_invoice.docstatus != 0:
		frappe.throw(_("Partial wastage is only allowed on draft invoices"))
	if pos_invoice.status not in ("Draft", "Unbilled"):
		frappe.throw(_("Partial wastage is only allowed on Draft or Unbilled invoices"))

	items_to_remove = []

	for wasted in wasted_items:
		row_name = wasted.get("row_name")
		waste_qty = flt(wasted.get("qty"))
		matched = False

		for inv_item in pos_invoice.items:
			if row_name and inv_item.name == row_name:
				matched = True
			elif not row_name and inv_item.item_code == wasted.get("item_code"):
				matched = True

			if matched:
				new_qty = flt(inv_item.qty) - waste_qty
				if new_qty < 0:
					frappe.throw(
						_("Cannot waste {0} qty of {1}. Only {2} available on invoice.").format(
							waste_qty, inv_item.item_code, inv_item.qty
						)
					)
				elif new_qty == 0:
					items_to_remove.append(inv_item)
				else:
					inv_item.qty = new_qty
					inv_item.amount = flt(new_qty * inv_item.rate)
				break

		if not matched:
			frappe.throw(_("Item {0} not found in invoice {1}").format(wasted.get("item_code"), invoice_id))

	for item in items_to_remove:
		pos_invoice.remove(item)

	if not pos_invoice.items:
		cancel_order_for_wastage(invoice_id, reason)
		return {"action": "cancelled"}

	pos_invoice.calculate_taxes_and_totals()
	pos_invoice.save(ignore_permissions=True)

	partial_cancel_kot(invoice_id, wasted_items)

	return {"action": "modified"}


def partial_cancel_kot(invoice_id, wasted_items):
	try:
		from ury.ury.api.ury_kot_generate import process_items_for_cancel_kot

		pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
		pos_profile = frappe.get_doc("POS Profile", pos_invoice.pos_profile)
		kot_naming_series = pos_profile.custom_kot_naming_series
		cancel_kot_naming_series = "CNCL-" + kot_naming_series

		cancel_items = [
			{"item_code": w.get("item_code"), "qty": flt(w.get("qty")), "item_name": w.get("item_name")}
			for w in wasted_items
		]

		invoice_items = [
			{"item_code": item.get("item", item.get("item_code")), "qty": item.qty, "item_name": item.item_name}
			for item in pos_invoice.items
		]

		process_items_for_cancel_kot(
			invoice_id,
			pos_invoice.customer,
			pos_invoice.restaurant_table or None,
			cancel_items,
			"Partial wastage",
			pos_invoice.pos_profile,
			cancel_kot_naming_series,
			"Partially cancelled",
			invoice_items,
		)
	except Exception:
		frappe.log_error(
			title=f"Partial KOT cancellation failed for wastage: {invoice_id}",
			message=frappe.get_traceback(),
		)


@frappe.whitelist()
def process_offline_job(job):
	if isinstance(job, str):
		job = json.loads(job)

	action = job.get("action")
	payload = job.get("payload", {})
	job_id = job.get("job_id")
	timestamp = job.get("timestamp")

	if action != "mark_waste":
		frappe.throw(_("Unknown job action: {0}").format(action))

	existing = frappe.db.exists("Wastage Note", {"remarks": ["like", f"%Job ID: {job_id}%"]})
	if existing:
		wastage_note = frappe.get_doc("Wastage Note", existing)
		return {
			"status": "already_processed",
			"wastage_note": wastage_note.name,
			"stock_entry": wastage_note.stock_entry,
		}

	if not payload.get("remarks"):
		payload["remarks"] = ""
	payload["remarks"] += f"\nJob ID: {job_id}"
	if timestamp:
		payload["remarks"] += f"\nOffline Timestamp: {timestamp}"

	try:
		result = mark_items_waste(payload)
		result["status"] = "success"
		return result
	except Exception:
		frappe.log_error(title=f"Offline Wastage Job Failed: {job_id}", message=frappe.get_traceback())
		return {"status": "failed", "error": frappe.get_traceback(), "job_id": job_id}


def get_invoice_items_for_wastage(pos_invoice):
	invoice = frappe.get_doc("POS Invoice", pos_invoice)
	items = []
	for item in invoice.items:
		is_stock_item = frappe.db.get_value("Item", item.item_code, "is_stock_item")
		if is_stock_item:
			items.append({
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"uom": item.uom,
				"batch_no": item.batch_no if hasattr(item, "batch_no") else None,
				"serial_no": item.serial_no if hasattr(item, "serial_no") else None,
				"reason": "Marked as waste from invoice",
			})
	return items


def validate_item_for_wastage(item):
	if not item.get("item_code"):
		frappe.throw(_("Item code is required"))

	if not frappe.db.exists("Item", item.get("item_code")):
		frappe.throw(_("Item {0} does not exist").format(item.get("item_code")))

	if flt(item.get("qty")) <= 0:
		frappe.throw(_("Quantity must be greater than 0 for item {0}").format(item.get("item_code")))

	is_stock_item = frappe.db.get_value("Item", item.get("item_code"), "is_stock_item")
	if not is_stock_item:
		frappe.throw(_("Item {0} is not a stock item and cannot be marked as waste").format(item.get("item_code")))

	if item.get("batch_no"):
		if not frappe.db.exists("Batch", item.get("batch_no")):
			frappe.throw(_("Batch {0} does not exist").format(item.get("batch_no")))
		batch = frappe.get_doc("Batch", item.get("batch_no"))
		if batch.item != item.get("item_code"):
			frappe.throw(_("Batch {0} does not belong to item {1}").format(item.get("batch_no"), item.get("item_code")))

	if item.get("serial_no"):
		serial_nos = [s.strip() for s in item.get("serial_no").split('\n') if s.strip()]
		for serial_no in serial_nos:
			if not frappe.db.exists("Serial No", serial_no):
				frappe.throw(_("Serial No {0} does not exist").format(serial_no))
			serial_doc = frappe.get_doc("Serial No", serial_no)
			if serial_doc.item_code != item.get("item_code"):
				frappe.throw(_("Serial No {0} does not belong to item {1}").format(serial_no, item.get("item_code")))


@frappe.whitelist()
def get_wastage_defaults(company=None, pos_profile=None):
	defaults = {}

	if not company and pos_profile:
		company = frappe.db.get_value("POS Profile", pos_profile, "company")

	if company:
		defaults["company"] = company
		expense_account = frappe.db.get_value(
			"Company", company, "stock_adjustment_account"
		) or frappe.db.get_value("Company", company, "default_expense_account")
		defaults["expense_account"] = expense_account
		defaults["cost_center"] = frappe.db.get_value("Company", company, "cost_center")

	if pos_profile:
		pos_doc = frappe.get_doc("POS Profile", pos_profile)
		if pos_doc.warehouse:
			defaults["source_warehouse"] = pos_doc.warehouse
		if pos_doc.cost_center:
			defaults["cost_center"] = pos_doc.cost_center

	return defaults


@frappe.whitelist()
def get_item_stock_info(item_code, warehouse):
	from erpnext.stock.utils import get_stock_balance

	result = {"item_code": item_code, "warehouse": warehouse, "qty_available": 0, "batches": []}
	result["qty_available"] = get_stock_balance(item_code, warehouse)

	has_batch_no = frappe.db.get_value("Item", item_code, "has_batch_no")
	if has_batch_no:
		batches = frappe.db.sql("""
			SELECT sle.batch_no, b.expiry_date, SUM(sle.actual_qty) as qty
			FROM `tabStock Ledger Entry` sle
			LEFT JOIN `tabBatch` b ON b.name = sle.batch_no
			WHERE sle.item_code = %s AND sle.warehouse = %s
				AND sle.batch_no IS NOT NULL AND sle.is_cancelled = 0
			GROUP BY sle.batch_no
			HAVING qty > 0
			ORDER BY b.expiry_date ASC
		""", (item_code, warehouse), as_dict=True)
		result["batches"] = batches

	return result
