// Loyalty Ledger page. Talks only to the backend API (window.API_BASE) with fetch.
"use strict";

const API = (window.API_BASE || "http://localhost:8000").replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
let currentAccount = null;

// ---------------------------------------------------------------- helpers

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...options });
  } catch (err) {
    return { ok: false, status: 0, data: { error: "NETWORK_ERROR", detail: `cannot reach the API at ${API}` } };
  }
  const data = await response.json().catch(() => ({ error: "BAD_RESPONSE", detail: "response was not JSON" }));
  const total = response.headers.get("X-Total-Count");     // set by GET /accounts (all pages)
  return { ok: response.ok, status: response.status, data, total: total === null ? null : Number(total) };
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const int = (n) => Number(n).toLocaleString("en-IN");
const money = (n) => "₹" + Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ts = (s) => (s ? String(s).replace("T", " ") : "");
const pct = (x) => (x === null || x === undefined ? "n/a" : (x * 100).toFixed(1) + "%");
const metric = (x) => (x === null || x === undefined ? "n/a" : Number(x).toFixed(4));

function card(label, value, tone = "") {
  return `<div class="card ${tone}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`;
}

function badge(text, tone) {
  return `<span class="badge ${tone}">${esc(text)}</span>`;
}

// columns: [{ key, label, num?, render?(row) }]; rowAttrs?(row) -> extra attributes for <tr>
function table(columns, rows, rowAttrs) {
  if (!rows.length) return `<p class="note">Nothing to show.</p>`;
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r) => {
    const cells = columns.map((c) => {
      const value = c.render ? c.render(r) : esc(r[c.key]);
      return `<td class="${c.num ? "num" : ""}">${value}</td>`;
    }).join("");
    return `<tr ${rowAttrs ? rowAttrs(r) : ""}>${cells}</tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function errorText(data) {
  if (Array.isArray(data.detail)) return data.detail.map((d) => `${d.field}: ${d.message}`).join("; ");
  return data.detail ?? "";
}

function showError(target, result) {
  $(target).innerHTML = `<p class="error">${esc(result.data.error || "ERROR")}: ${esc(errorText(result.data))}</p>`;
}

// ---------------------------------------------------------------- dashboard

const TIER_COLORS = ["var(--tier-1)", "var(--tier-2)", "var(--tier-3)", "var(--tier-4)"];
const STATUS_TONE = { PASS: "ok", EXPECTED_FAIL: "warn", FAIL: "bad" };

async function loadStats() {
  const r = await api("/stats");
  $("api-status").className = "pill " + (r.ok ? "ok" : "bad");
  $("api-status").textContent = r.ok ? "API connected" : "API unreachable";
  if (!r.ok) return showError("stats", r);
  const s = r.data;
  $("stats").innerHTML = [
    card("Customers", int(s.customers)),
    card("Orders", int(s.orders)),
    card("Ledger rows", int(s.ledger_rows)),
    card("Points outstanding", int(s.points_outstanding)),
    card("Redemptions approved / rejected", `${int(s.redemptions_approved)} / ${int(s.redemptions_rejected)}`),
  ].join("");

  const bar = s.tier_mix.map((t, i) =>
    `<span style="width:${t.share * 100}%;background:${TIER_COLORS[i % 4]}" title="${esc(t.code)} ${pct(t.share)}"></span>`).join("");
  const legend = s.tier_mix.map((t, i) =>
    `<span class="swatch" style="background:${TIER_COLORS[i % 4]}"></span><span>${esc(t.code)}</span>` +
    `<span class="num">${int(t.accounts)}</span><span class="num">${pct(t.share)}</span>`).join("");
  $("tier-mix").innerHTML = `<div class="bar" role="img" aria-label="Tier mix">${bar}</div><div class="legend">${legend}</div>`;
}

async function loadValidation() {
  const r = await api("/validation");
  if (!r.ok) return showError("validation-table", r);
  const t = r.data.totals;
  $("validation-totals").innerHTML = [
    card("Checks", t.checks),
    card("PASS", t.PASS, "ok"),
    card("EXPECTED_FAIL", t.EXPECTED_FAIL, t.EXPECTED_FAIL ? "warn" : ""),
    card("FAIL", t.FAIL, t.FAIL ? "bad" : "ok"),
  ].join("");
  const columns = [
    { key: "status", label: "Status", render: (c) => badge(c.status, STATUS_TONE[c.status]) },
    { key: "id", label: "Check", render: (c) => `<code>${esc(c.id)}</code>` },
    { key: "category", label: "Category" },
    { key: "violations", label: "Violations", num: true },
    { key: "explained", label: "Explained", num: true },
    { key: "ground_truth_count", label: "Ground truth", num: true, render: (c) => esc(c.ground_truth_count ?? "") },
    { key: "description", label: "Description" },
  ];
  const notPassing = r.data.checks.filter((c) => c.status !== "PASS");
  $("validation-table").innerHTML =
    `<h4>Checks that did not pass</h4>${table(columns, notPassing)}` +
    `<details><summary class="note">Show all ${t.checks} checks</summary>${table(columns, r.data.checks)}</details>`;
}

// ---------------------------------------------------------------- anomalies

async function loadAnomalies() {
  const r = await api("/anomalies");
  if (!r.ok) {
    showError("metrics", r);
    return showError("anomaly-counts", r);
  }
  const report = r.data;
  $("anomaly-counts").innerHTML = table([
    { key: "issue_type", label: "Issue type", render: (m) => `<code>${esc(m.issue_type)}</code>` },
    { key: "ground_truth", label: "Labelled", num: true },
    { key: "flagged", label: "Flagged", num: true },
    { key: "tp", label: "Matched", num: true },
  ], report.metrics);

  const rows = [...report.metrics, { ...report.overall, issue_type: "Overall (micro)" }];
  $("metrics").innerHTML = table([
    { key: "issue_type", label: "Issue type", render: (m) => `<code>${esc(m.issue_type)}</code>` },
    { key: "ground_truth", label: "Ground truth", num: true },
    { key: "flagged", label: "Flagged", num: true },
    { key: "tp", label: "TP", num: true },
    { key: "fp", label: "FP", num: true },
    { key: "fn", label: "FN", num: true },
    { key: "precision", label: "Precision", num: true, render: (m) => metric(m.precision) },
    { key: "recall", label: "Recall", num: true, render: (m) => metric(m.recall) },
    { key: "f1", label: "F1", num: true, render: (m) => metric(m.f1) },
  ], rows);

  // One row per (issue type, record): labelled and/or flagged.
  const records = new Map();
  for (const g of report.ground_truth) {
    records.set(`${g.issue_type}|${g.record_id}`, { ...g, labelled: true, flagged: false, reason: "" });
  }
  for (const f of report.flagged) {
    const key = `${f.issue_type}|${f.record_id}`;
    const row = records.get(key) || { issue_type: f.issue_type, record_id: f.record_id, labelled: false, description: "" };
    records.set(key, { ...row, flagged: true, reason: f.reason, loy_id: row.loy_id || f.loy_id });
  }
  const outcome = (x) => (x.labelled && x.flagged ? badge("TP", "ok") : x.flagged ? badge("FP", "warn") : badge("FN", "bad"));
  $("records").innerHTML = table([
    { key: "outcome", label: "Outcome", render: outcome },
    { key: "issue_type", label: "Issue type", render: (x) => `<code>${esc(x.issue_type)}</code>` },
    { key: "record_id", label: "Record" },
    { key: "loy_id", label: "Account" },
    { key: "description", label: "Ground truth", render: (x) => esc(x.description || "—") },
    { key: "reason", label: "Detector", render: (x) => esc(x.reason || "—") },
  ], [...records.values()], (x) => (x.loy_id ? `class="click" tabindex="0" data-account="${esc(x.loy_id)}"` : ""));
}

// ---------------------------------------------------------------- accounts

const PAGE_SIZE = 50;
const search = { query: null, rows: [], total: 0 };    // the current search and what is loaded so far

async function searchAccounts(event) {
  if (event) event.preventDefault();
  const query = new URLSearchParams();
  if ($("search").value.trim()) query.set("search", $("search").value.trim());
  if ($("tier-filter").value) query.set("tier", $("tier-filter").value);
  Object.assign(search, { query, rows: [], total: 0 });
  await loadMoreAccounts();
}

async function loadMoreAccounts() {
  const params = new URLSearchParams(search.query);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(search.rows.length));
  $("load-more").disabled = true;
  const r = await api("/accounts?" + params);
  $("load-more").disabled = false;
  if (!r.ok) return showError("search-results", r);
  search.rows = search.rows.concat(r.data);
  search.total = r.total ?? search.rows.length;
  $("search-count").textContent =
    `Showing ${int(search.rows.length)} of ${int(search.total)} account${search.total === 1 ? "" : "s"}`;
  $("load-more").hidden = search.rows.length >= search.total;
  $("search-results").innerHTML = table([
    { key: "loy_id", label: "Account" },
    { key: "name", label: "Name" },
    { key: "email", label: "Email" },
    { key: "city", label: "City" },
    { key: "tier_id", label: "Tier" },
    { key: "current_balance", label: "Balance", num: true, render: (a) => int(a.current_balance) },
  ], search.rows, (a) => `class="click" tabindex="0" data-account="${esc(a.loy_id)}"`);
}

async function openAccount(loyId) {
  currentAccount = loyId;
  $("account").hidden = false;
  $("action-result").innerHTML = "";
  await loadAccount();
  $("accounts").scrollIntoView({ behavior: "smooth" });
}

async function loadAccount() {
  const id = encodeURIComponent(currentAccount);
  const [detail, lots, ledger] = await Promise.all([
    api(`/accounts/${id}`), api(`/accounts/${id}/lots`), api(`/accounts/${id}/ledger`),
  ]);
  if (!detail.ok) return showError("account-cards", detail);
  const a = detail.data;
  $("account-title").textContent = `${a.loy_id} · ${a.name} · ${a.email}`;
  $("account-cards").innerHTML = [
    card("Tier", a.tier_id.replace("TIER-", "")),
    card("Stored balance", int(a.current_balance), a.balance_matches ? "" : "bad"),
    card("Ledger balance", int(a.ledger_balance)),
    card("Balance matches ledger", a.balance_matches ? "Yes" : "No", a.balance_matches ? "ok" : "bad"),
    card("Available", int(a.available)),
    card("Pending", int(a.pending)),
    card("Expired, awaiting batch", int(a.expired_awaiting_batch)),
    card("Qualifying spend", money(a.qualifying_spend)),
  ].join("");

  $("lots").innerHTML = lots.ok ? table([
    { key: "lot_pts_id", label: "Lot" },
    { key: "earned_at", label: "Earned", render: (l) => esc(ts(l.earned_at)) },
    { key: "available_from", label: "Available from" },
    { key: "expires_at", label: "Expires" },
    { key: "original", label: "Earned pts", num: true },
    { key: "reversed", label: "Reversed", num: true },
    { key: "redeemed", label: "Redeemed", num: true },
    { key: "expired", label: "Expired", num: true },
    { key: "remaining", label: "Remaining", num: true },
    { key: "status", label: "Status" },
  ], lots.data) : "";

  const ref = (e) => esc(e.line_id || e.rdm_id || e.exp_id || "") +
    (e.reverses_pts_id ? ` ← ${esc(e.reverses_pts_id)}` : "") + (e.expires_pts_id ? ` ← ${esc(e.expires_pts_id)}` : "") +
    (e.return_id ? ` (${esc(e.return_id)})` : "");
  if (ledger.ok) {
    $("ledger").innerHTML = table([
      { key: "txn_ts", label: "Time", render: (e) => esc(ts(e.txn_ts)) },
      { key: "pts_id", label: "Entry" },
      { key: "txn_type", label: "Type" },
      { key: "points", label: "Points", num: true },
      { key: "ref", label: "Reference", render: ref },
      { key: "earn_rate", label: "Rate", num: true, render: (e) => esc(e.earn_rate ?? "") },
    ], ledger.data);
    // Order lines that earned points, newest first, for the return form.
    const lines = ledger.data.filter((e) => e.txn_type === "ACCRUAL").reverse();
    $("return-line").innerHTML = lines.map((e) =>
      `<option value="${esc(e.line_id)}">${esc(e.line_id)} · ${esc(ts(e.txn_ts).slice(0, 10))} · ${int(e.points)} pts</option>`).join("");
  }
}

// ---------------------------------------------------------------- actions

function showResult(result, summary) {
  const tone = result.ok ? "ok" : "bad";
  const head = result.ok ? summary(result.data)
    : result.data.status === "REJECTED"
      ? `REJECTED: ${result.data.reject_reason} (cost ${int(result.data.points_cost)}, available ${int(result.data.available_before)})`
      : `${result.data.error}: ${errorText(result.data)}`;
  $("action-result").innerHTML =
    `<div class="result ${tone}"><strong>HTTP ${result.status}</strong> · ${esc(head)}<pre>${esc(JSON.stringify(result.data, null, 2))}</pre></div>`;
}

async function runAction(form, request, summary) {
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const result = await request();
    showResult(result, summary);
    await Promise.all([currentAccount ? loadAccount() : null, loadStats(), loadValidation(), loadAnomalies()]);
  } finally {
    button.disabled = false;
  }
}

const seconds = (value) => (value.length === 16 ? value + ":00" : value);   // datetime-local -> ISO with seconds

function redeem(event) {
  event.preventDefault();
  runAction(event.target, () => api("/redemptions", {
    method: "POST",
    body: JSON.stringify({ loy_id: currentAccount, reward_id: $("redeem-reward").value,
                           requested_at: seconds($("redeem-ts").value) }),
  }), (d) => `${d.status}: ${d.rdm_id}, ${int(d.points_cost)} points from ${d.lots_used.length} lot(s)`);
}

function returnLine(event) {
  event.preventDefault();
  const lineId = $("return-line").value;
  if (!lineId) return;
  const ordId = lineId.slice(0, -3);                 // ORD-2026-000001-01 -> ORD-2026-000001, line 1
  const lineNo = Number(lineId.slice(-2));
  runAction(event.target, () => api(`/orders/${encodeURIComponent(ordId)}/returns`, {
    method: "POST",
    body: JSON.stringify({ line_no: lineNo, qty: Number($("return-qty").value), return_ts: seconds($("return-ts").value) }),
  }), (d) => `${d.return_id}: refund ${money(d.refund_amount)}, ${int(d.points_reversed)} points reversed`);
}

function runExpiry(event) {
  event.preventDefault();
  runAction(event.target, () => api(`/expiry-batches/run?run_date=${$("expiry-date").value}`, { method: "POST" }),
    (d) => d.already_run ? `${d.exp_id} already ran for ${d.run_date}: nothing new expired`
                         : `${d.exp_id}: ${int(d.lots_expired)} lots, ${int(d.points_expired)} points expired`);
}

// ---------------------------------------------------------------- start

async function loadReferenceData() {
  const [tiers, rewards] = await Promise.all([api("/tiers"), api("/rewards")]);
  if (tiers.ok) {
    $("tier-filter").innerHTML += tiers.data.map((t) => `<option value="${esc(t.code)}">${esc(t.code)}</option>`).join("");
  }
  if (rewards.ok) {
    $("redeem-reward").innerHTML = rewards.data.filter((w) => w.active)
      .map((w) => `<option value="${esc(w.reward_id)}">${esc(w.name)} · ${int(w.points_cost)} pts</option>`).join("");
  }
  const dates = [];
  for (let m = 1; m <= 12; m++) dates.push(`2026-${String(m).padStart(2, "0")}-01`);
  dates.push("2027-01-01");
  $("expiry-date").innerHTML = dates.map((d) => `<option ${d === "2026-12-01" ? "selected" : ""}>${d}</option>`).join("");
}

function openFromRow(event) {
  const row = event.target.closest("tr[data-account]");
  if (row && (event.type === "click" || event.key === "Enter")) openAccount(row.dataset.account);
}

document.addEventListener("DOMContentLoaded", () => {
  $("search-form").addEventListener("submit", searchAccounts);
  $("load-more").addEventListener("click", loadMoreAccounts);
  $("redeem-form").addEventListener("submit", redeem);
  $("return-form").addEventListener("submit", returnLine);
  $("expiry-form").addEventListener("submit", runExpiry);
  for (const id of ["search-results", "records"]) {
    $(id).addEventListener("click", openFromRow);
    $(id).addEventListener("keydown", openFromRow);
  }
  loadReferenceData();
  loadStats();
  searchAccounts();
  loadValidation();
  loadAnomalies();
});
