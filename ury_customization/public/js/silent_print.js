/**
 * Silent Print for ury_customization
 * Uses webapp-hardware-bridge (ws://127.0.0.1:12212/printer)
 *
 * How it works:
 *   1. webapp-hardware-bridge must be running on the client machine.
 *   2. Click the printer icon in the navbar to activate the "Master Tab".
 *      - Green  = master tab active + bridge connected.
 *      - Yellow = master tab active, bridge connecting…
 *      - Grey   = inactive.
 *   3. Call frappe.silent_print.print(doctype, name, print_format, print_type, qty)
 *      from any form/list button to silently print.
 *   4. If the current tab is NOT the master tab, the job is published via
 *      frappe.realtime so the designated master tab handles it instead.
 */

frappe.provide("frappe.silent_print");

// ---------------------------------------------------------------------------
// WebSocketPrinter SDK  (adapted from webapp-hardware-bridge/demo/websocket-printer.js)
// ---------------------------------------------------------------------------
function WebSocketPrinter(options) {
	var defaults = {
		url: "ws://127.0.0.1:12212/printer",
		onConnect: function () {},
		onDisconnect: function () {},
		onUpdate: function () {},
	};

	var settings = Object.assign({}, defaults, options);
	var websocket;
	var connected = false;
	var reconnect_timer = null;

	var on_message = function (evt) {
		settings.onUpdate(evt.data);
	};

	var on_connect = function () {
		connected = true;
		if (reconnect_timer) {
			clearTimeout(reconnect_timer);
			reconnect_timer = null;
		}
		settings.onConnect();
	};

	var on_disconnect = function () {
		connected = false;
		settings.onDisconnect();
		// Auto-reconnect after 3 seconds
		reconnect_timer = setTimeout(connect, 3000);
	};

	var connect = function () {
		try {
			websocket = new WebSocket(settings.url);
			websocket.onopen = on_connect;
			websocket.onclose = on_disconnect;
			websocket.onmessage = on_message;
		} catch (e) {
			reconnect_timer = setTimeout(connect, 3000);
		}
	};

	/** Send one or many print jobs */
	this.submit = function (data) {
		if (!connected) {
			frappe.show_alert(
				{ message: __("Silent Print: bridge not connected"), indicator: "red" },
				4
			);
			return false;
		}
		var jobs = Array.isArray(data) ? data : [data];
		jobs.forEach(function (job) {
			websocket.send(JSON.stringify(job));
		});
		return true;
	};

	this.isConnected = function () {
		return connected;
	};

	this.disconnect = function () {
		if (reconnect_timer) {
			clearTimeout(reconnect_timer);
			reconnect_timer = null;
		}
		if (websocket) {
			websocket.onclose = null; // prevent auto-reconnect
			websocket.close();
		}
		connected = false;
	};

	connect();
}

// ---------------------------------------------------------------------------
// Master-Tab state
// ---------------------------------------------------------------------------
frappe.silent_print.is_master = false;
frappe.silent_print.printer = null;

// ---------------------------------------------------------------------------
// Navbar button
// ---------------------------------------------------------------------------
frappe.silent_print.add_navbar_button = function () {
	if ($("#silent-print-btn").length) return; // already added

	var btn = $(`
		<li class="nav-item d-none d-sm-block" id="silent-print-btn">
			<a class="nav-link px-2" href="#" id="silent-print-toggle"
			   title="${__("Silent Print – click to activate master tab")}">
				<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
				     viewBox="0 0 24 24" fill="none" stroke="currentColor"
				     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<polyline points="6 9 6 2 18 2 18 9"></polyline>
					<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16
					         a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
					<rect x="6" y="14" width="12" height="8"></rect>
				</svg>
			</a>
		</li>
	`);

	// Insert before the vertical-bar separator (between notifications and Help)
	var anchor = $(".navbar-nav .vertical-bar").first();
	if (anchor.length) {
		anchor.before(btn);
	} else {
		// Fallback: append to the navbar-nav inside collapse
		var nav = $(".navbar-collapse .navbar-nav").first();
		if (nav.length) {
			nav.prepend(btn);
		} else {
			$("header .navbar .container").append(btn);
		}
	}

	$("#silent-print-toggle").on("click", function (e) {
		e.preventDefault();
		frappe.silent_print.toggle_master();
	});
};

frappe.silent_print.update_navbar_icon = function () {
	var icon = $("#silent-print-toggle");
	if (!icon.length) return;

	if (frappe.silent_print.is_master) {
		if (frappe.silent_print.printer && frappe.silent_print.printer.isConnected()) {
			icon.css("color", "var(--green-500, #28a745)");
			icon.attr("title", __("Silent Print: Master tab active — bridge connected"));
		} else {
			icon.css("color", "var(--yellow-500, #ffc107)");
			icon.attr("title", __("Silent Print: Master tab active — connecting to bridge…"));
		}
	} else {
		icon.css("color", "");
		icon.attr("title", __("Silent Print — click to activate master tab"));
	}
};

frappe.silent_print.toggle_master = function () {
	frappe.silent_print.is_master = !frappe.silent_print.is_master;

	if (frappe.silent_print.is_master) {
		frappe.silent_print.printer = new WebSocketPrinter({
			onConnect: function () {
				frappe.silent_print.update_navbar_icon();
				frappe.show_alert(
					{ message: __("Silent Print: connected to bridge"), indicator: "green" },
					3
				);
			},
			onDisconnect: function () {
				frappe.silent_print.update_navbar_icon();
			},
		});
		frappe.show_alert(
			{ message: __("Silent Print master tab activated"), indicator: "blue" },
			3
		);
	} else {
		if (frappe.silent_print.printer) {
			frappe.silent_print.printer.disconnect();
			frappe.silent_print.printer = null;
		}
		frappe.show_alert(
			{ message: __("Silent Print master tab deactivated"), indicator: "orange" },
			3
		);
	}

	frappe.silent_print.update_navbar_icon();
};

// ---------------------------------------------------------------------------
// Realtime listener – handles jobs sent from other tabs / devices
// ---------------------------------------------------------------------------
frappe.silent_print.setup_realtime = function () {
	frappe.realtime.on("silent-print", function (data) {
		if (!frappe.silent_print.is_master) return;
		frappe.silent_print.send_to_bridge(data);
	});
};

// ---------------------------------------------------------------------------
// Send a pre-built job object directly to the bridge
// ---------------------------------------------------------------------------
frappe.silent_print.send_to_bridge = function (job) {
	if (!frappe.silent_print.printer) {
		frappe.show_alert(
			{ message: __("Silent Print: master tab not active"), indicator: "red" },
			5
		);
		return;
	}
	frappe.silent_print.printer.submit({
		type: job.type || "DEFAULT",
		url: job.url || "document.pdf",
		file_content: job.file_content || undefined,
		qty: job.qty || 1,
	});
};

// ---------------------------------------------------------------------------
// Primary API  –  frappe.silent_print.print(...)
// ---------------------------------------------------------------------------
/**
 * Print any ERPNext document silently.
 *
 * @param {string} doctype       - e.g. "POS Invoice"
 * @param {string} name          - document name
 * @param {string} print_format  - print format name (optional)
 * @param {string} print_type    - printer key configured in WHB (default "DEFAULT")
 * @param {number} qty           - number of copies (default 1)
 */
frappe.silent_print.print = function (doctype, name, print_format, print_type, qty) {
	print_type = print_type || "DEFAULT";
	qty = qty || 1;

	if (frappe.silent_print.is_master && frappe.silent_print.printer) {
		// This tab owns the printer connection – generate PDF locally and send.
		frappe.call({
			method: "ury_customization.ury_customization.api.silent_print.create_pdf",
			args: {
				doctype: doctype,
				name: name,
				print_format: print_format || "",
			},
			freeze: false,
			callback: function (r) {
				if (!r.message) return;
				frappe.silent_print.send_to_bridge({
					type: print_type,
					url: name + ".pdf",
					file_content: r.message,
					qty: qty,
				});
				frappe.show_alert(
					{ message: __("Printing {0}…", [name]), indicator: "green" },
					3
				);
			},
		});
	} else {
		// No local printer – ask the server to publish to the master tab.
		frappe.call({
			method: "ury_customization.ury_customization.api.silent_print.print_silently",
			args: {
				doctype: doctype,
				name: name,
				print_format: print_format || "",
				print_type: print_type,
				qty: qty,
			},
			callback: function (r) {
				if (r.message) {
					frappe.show_alert(
						{ message: __("Print job sent to master tab"), indicator: "green" },
						3
					);
				}
			},
		});
	}
};

// ---------------------------------------------------------------------------
// Bootstrap – poll until the navbar is rendered, then inject the button
// ---------------------------------------------------------------------------
$(document).ready(function () {
	var attempts = 0;
	var max_attempts = 40; // 40 × 250 ms = 10 s

	var try_init = function () {
		if (!frappe.session || frappe.session.user === "Guest") {
			if (++attempts < max_attempts) setTimeout(try_init, 250);
			return;
		}
		// Wait for the vertical-bar separator that exists in the Frappe v15 navbar
		if ($(".navbar-nav .vertical-bar").length) {
			frappe.silent_print.add_navbar_button();
			frappe.silent_print.setup_realtime();
		} else if (++attempts < max_attempts) {
			setTimeout(try_init, 250);
		}
	};

	setTimeout(try_init, 250);
});
