const state = {
  records: [],
  expanded: new Set(),
  pickleCache: new Map(),
  pickleLoading: new Set(),
};

const TAG_CLASS = {
  TWO_STATES: "tag-success",
  MANUAL: "tag-success",
  PAIRED: "tag-success",
  NOT_ENOUGH_DATA: "tag-warning",
  SINGLE_STATE: "tag-warning",
  API_ERROR: "tag-danger",
  ERROR: "tag-danger",
  UNPAIRED: "tag-muted",
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function deviceLabel(record) {
  const name = record.device_name;
  const id = record.device_id;
  if (name && id) {
    return `
      <div class="device-label">
        <div class="device-name">${escapeHtml(name)}</div>
        <div class="device-id-sub mono">${escapeHtml(id)}</div>
      </div>
    `;
  }
  return `<span class="mono">${escapeHtml(id || "—")}</span>`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

const IST_OPTIONS = {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
};

const UTC_OPTIONS = {
  timeZone: "UTC",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
};

function formatTimeWithIst(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);

  const ist = date.toLocaleString("en-IN", IST_OPTIONS);
  const utc = date.toLocaleString("en-GB", UTC_OPTIONS);
  return `
    <div class="time-cell">
      <div>${escapeHtml(ist)} IST</div>
      <div class="time-sub">UTC ${escapeHtml(utc)}</div>
    </div>
  `;
}

function latestHistoryEntry(record) {
  const history = Array.isArray(record.history) ? record.history : [];
  return history.length ? history[history.length - 1] : null;
}

function badgeClass(status) {
  if (status === "learning") return "badge-learning";
  if (status === "calibrated") return "badge-calibrated";
  return "badge-unpaired";
}

function tagClass(tag) {
  return TAG_CLASS[tag] || "tag-muted";
}

function buildQueryParams() {
  const params = new URLSearchParams();
  const status = $("status-filter").value;
  const orgId = $("org-filter").value.trim();
  const deviceId = $("device-filter").value.trim();

  if (status) params.append("filter", `calibration_status:${status}`);
  if (orgId) params.append("filter", `organization_id:${orgId}`);
  // UUID filter goes to MDB; DOZ-12345 style is filtered client-side.
  if (deviceId && /^[0-9a-f-]{36}$/i.test(deviceId)) {
    params.append("filter", `device_id:${deviceId}`);
  }
  params.append("limit", "2000");
  return params;
}

function getDeviceFilterValue() {
  return $("device-filter").value.trim().toLowerCase();
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) return;
    const data = await res.json();
    const lines = [`MDB: ${data.mdb_endpoint}`];
    if (data.efs_picklefiles_dir) {
      const status = data.efs_root_exists ? "found" : "missing";
      lines.push(`EFS: ${data.efs_picklefiles_dir} (${status})`);
      if (!localStorage.getItem("efs_root_override")) {
        $("efs-root-input").placeholder = data.efs_picklefiles_dir;
      }
    }
    $("mdb-endpoint-label").textContent = lines.join("\n");
  } catch {
    $("mdb-endpoint-label").textContent = "MDB: proxy unavailable";
  }
}

function getEfsRootOverride() {
  const saved = localStorage.getItem("efs_root_override");
  const input = $("efs-root-input").value.trim();
  return input || saved || "";
}

async function enrichDeviceNames(records) {
  const deviceIds = [...new Set(records.map((r) => r.device_id).filter(Boolean))];
  if (!deviceIds.length) return records;

  const params = new URLSearchParams();
  deviceIds.forEach((id) => params.append("device_id", id));

  try {
    const res = await fetch(`/api/device-names?${params.toString()}`);
    if (!res.ok) return records;
    const names = await res.json();
    return records.map((record) => ({
      ...record,
      device_name: record.device_name || names[record.device_id] || null,
    }));
  } catch {
    return records;
  }
}

async function loadRecords() {
  $("error-banner").classList.add("hidden");
  $("records-body").innerHTML = '<tr><td colspan="9" class="empty-row">Loading records…</td></tr>';

  try {
    const res = await fetch(`/api/records?${buildQueryParams().toString()}`);
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    state.records = Array.isArray(data) ? data : [];
    state.records = await enrichDeviceNames(state.records);
    state.expanded.clear();
    state.pickleCache.clear();
    state.pickleLoading.clear();
    render();
    $("last-updated").textContent = `Updated ${new Date().toLocaleString()}`;
  } catch (err) {
    state.records = [];
    $("error-banner").textContent = `Failed to load MDB records: ${err.message}`;
    $("error-banner").classList.remove("hidden");
    $("records-body").innerHTML = '<tr><td colspan="9" class="empty-row">No data</td></tr>';
    updateStats([]);
    $("visible-count").textContent = "0 shown";
  }
}

function filteredRecords() {
  const search = $("search-input").value.trim().toLowerCase();
  const tagFilter = $("tag-filter").value;
  const deviceFilter = getDeviceFilterValue();

  return state.records
    .slice()
    .sort((a, b) => {
      const aTime = a.last_run_at || a.paired_at || "";
      const bTime = b.last_run_at || b.paired_at || "";
      return bTime.localeCompare(aTime);
    })
    .filter((record) => {
      if (deviceFilter && !/^[0-9a-f-]{36}$/i.test(deviceFilter)) {
        const name = (record.device_name || "").toLowerCase();
        const id = (record.device_id || "").toLowerCase();
        if (!name.includes(deviceFilter) && !id.includes(deviceFilter)) return false;
      }
      if (tagFilter) {
        const latest = latestHistoryEntry(record);
        if (!latest || latest.status_tag !== tagFilter) return false;
      }
      if (!search) return true;
      const haystack = [
        record.device_name,
        record.device_id,
        record.user_id,
        record.organization_id,
        record.calibration_status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(search);
    });
}

function updateStats(records) {
  const counts = { total: records.length, learning: 0, calibrated: 0, unpaired: 0 };
  for (const record of records) {
    const status = record.calibration_status;
    if (status in counts) counts[status] += 1;
  }
  $("stat-total").textContent = counts.total;
  $("stat-learning").textContent = counts.learning;
  $("stat-calibrated").textContent = counts.calibrated;
  $("stat-unpaired").textContent = counts.unpaired;
}

function renderPicklePanel(record) {
  const key = rowKey(record);
  const loading = state.pickleLoading.has(key);
  const cached = state.pickleCache.get(key);

  if (!cached && !loading) {
    return `
      <div class="pickle-panel">
        <div class="pickle-panel-header">
          <h3>Pickle Features</h3>
          <button class="btn-secondary load-pickles-btn" data-key="${key}">Load Pickles</button>
        </div>
        <p class="muted">On-demand lookup of EFS pickle paths and feature counts since pairing.</p>
      </div>
    `;
  }

  if (loading) {
    return `
      <div class="pickle-panel">
        <div class="pickle-panel-header">
          <h3>Pickle Features</h3>
          <button class="btn-secondary" disabled>Loading…</button>
        </div>
        <p class="muted">Querying recordsdb and scanning EFS…</p>
      </div>
    `;
  }

  if (cached.error) {
    return `
      <div class="pickle-panel">
        <div class="pickle-panel-header">
          <h3>Pickle Features</h3>
          <button class="btn-secondary load-pickles-btn" data-key="${key}">Retry</button>
        </div>
        <p class="pickle-error">${escapeHtml(cached.error)}</p>
      </div>
    `;
  }

  const requirementClass = cached.meets_minimum ? "tag-success" : "tag-warning";
  const timeRange = cached.time_range
    ? `${formatTime(cached.time_range.start)} → ${formatTime(cached.time_range.end)}`
    : "—";
  const warning = cached.warning
    ? `<p class="pickle-error">${escapeHtml(cached.warning)}</p>`
    : "";

  const sessionRows = (cached.sessions || [])
    .map((session) => {
      const statusClass = session.exists ? "tag-success" : "tag-warning";
      const statusText = session.exists ? session.source || "found" : "missing";
      const errorText = session.error ? `<div class="pickle-error">${escapeHtml(session.error)}</div>` : "";
      return `
        <tr>
          <td class="mono">${escapeHtml(session.sleep_id || "—")}</td>
          <td>${formatTime(session.bed_time)}</td>
          <td class="${statusClass}">${statusText}</td>
          <td>${session.feature_count ?? 0}</td>
          <td class="mono pickle-path">${escapeHtml(session.pkl_path || "—")}</td>
        </tr>
        ${errorText ? `<tr class="pickle-error-row"><td colspan="5">${errorText}</td></tr>` : ""}
      `;
    })
    .join("");

  return `
    <div class="pickle-panel">
      <div class="pickle-panel-header">
        <h3>Pickle Features</h3>
        <button class="btn-secondary load-pickles-btn" data-key="${key}">Refresh</button>
      </div>
      ${warning}
      <div class="pickle-summary">
        <div><span class="muted">EFS root</span><div class="mono pickle-path">${escapeHtml(cached.efs_root || "—")}</div></div>
        <div><span class="muted">Sleeps in DB / eligible</span><div>${cached.total_sleeps_in_db ?? 0} / ${cached.eligible_sleeps}</div></div>
        <div><span class="muted">Pickles found / missing</span><div>${cached.pickles_found} / ${cached.pickles_missing}</div></div>
        <div><span class="muted">Merged unique features</span><div class="${requirementClass}">${cached.merged_unique_features} / ${cached.minimum_required}</div></div>
        <div><span class="muted">Time range</span><div>${timeRange}</div></div>
      </div>
      <div class="pickle-table-wrap">
        <table class="pickle-table">
          <thead>
            <tr>
              <th>Sleep ID</th>
              <th>Bed Time</th>
              <th>Pickle</th>
              <th>Features</th>
              <th>Path</th>
            </tr>
          </thead>
          <tbody>
            ${sessionRows || '<tr><td colspan="5" class="empty-row">No eligible sleep sessions</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderHistory(record) {
  const history = Array.isArray(record.history) ? [...record.history].reverse() : [];
  const historyBlock = history.length
    ? `
      <div class="history-panel">
        <h3>History (${history.length} events)</h3>
        <div class="timeline">${history
          .map((entry) => {
            const details = entry.details || {};
            const msg = details.msg || "";
            const thresholds =
              details.OccupancyFsrBaseline != null
                ? `Baseline ${details.OccupancyFsrBaseline}, Delta ${details.OccupancyFsrDelta}`
                : "";
            const detailText = [msg, thresholds].filter(Boolean).join(" · ");
            const transition = [entry.status_from, entry.status_to].filter(Boolean).join(" → ");

            return `
              <div class="timeline-item">
                <div class="timeline-time">${formatTime(entry.run_at)}</div>
                <div class="timeline-event">
                  <div>${entry.event_type || "event"}</div>
                  <div class="${tagClass(entry.status_tag)}">${entry.status_tag || "—"}</div>
                </div>
                <div class="timeline-detail">
                  <div>${transition || "—"}</div>
                  <div>${detailText || "—"}</div>
                </div>
              </div>
            `;
          })
          .join("")}</div>
      </div>
    `
    : '<div class="history-panel"><p class="muted">No history entries</p></div>';

  return historyBlock + renderPicklePanel(record);
}

function findRecordByKey(key) {
  return state.records.find((record) => rowKey(record) === key);
}

async function loadPickles(key) {
  const record = findRecordByKey(key);
  if (!record) return;

  state.pickleLoading.add(key);
  render();

  const params = new URLSearchParams({
    device_id: record.device_id || "",
    user_id: record.user_id || "",
    paired_at: record.paired_at || "",
  });
  const efsRoot = getEfsRootOverride();
  if (efsRoot) params.set("efs_root", efsRoot);

  try {
    const res = await fetch(`/api/pickles?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      state.pickleCache.set(key, { error: data.error || `Request failed (${res.status})` });
    } else {
      state.pickleCache.set(key, data);
    }
  } catch (err) {
    state.pickleCache.set(key, { error: err.message });
  } finally {
    state.pickleLoading.delete(key);
    render();
  }
}

function rowKey(record) {
  return `${record.device_id || ""}:${record.user_id || ""}`;
}

function render() {
  const records = filteredRecords();
  updateStats(state.records);
  $("visible-count").textContent = `${records.length} shown`;

  if (!records.length) {
    $("records-body").innerHTML = '<tr><td colspan="9" class="empty-row">No matching records</td></tr>';
    return;
  }

  const rows = records
    .map((record) => {
      const key = rowKey(record);
      const latest = latestHistoryEntry(record);
      const expanded = state.expanded.has(key);
      const status = record.calibration_status || "unknown";

      const mainRow = `
        <tr>
          <td>
            <button class="expand-btn" data-key="${key}" aria-label="Toggle history">${expanded ? "−" : "+"}</button>
          </td>
          <td>${deviceLabel(record)}</td>
          <td class="mono">${record.user_id || "—"}</td>
          <td class="mono">${record.organization_id || "—"}</td>
          <td><span class="badge ${badgeClass(status)}">${status}</span></td>
          <td>${formatTimeWithIst(record.paired_at)}</td>
          <td>${formatTimeWithIst(record.first_eligible_at)}</td>
          <td>${formatTime(record.last_run_at)}</td>
          <td class="${tagClass(latest?.status_tag)}">${latest?.status_tag || "—"}</td>
        </tr>
      `;

      const historyRow = expanded
        ? `<tr class="history-row"><td colspan="9">${renderHistory(record)}</td></tr>`
        : "";

      return mainRow + historyRow;
    })
    .join("");

  $("records-body").innerHTML = rows;

  document.querySelectorAll(".expand-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      if (state.expanded.has(key)) state.expanded.delete(key);
      else state.expanded.add(key);
      render();
    });
  });

  document.querySelectorAll(".load-pickles-btn").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      loadPickles(btn.dataset.key);
    });
  });
}

function bindEvents() {
  $("refresh-btn").addEventListener("click", loadRecords);
  $("status-filter").addEventListener("change", loadRecords);
  $("org-filter").addEventListener("change", loadRecords);
  $("device-filter").addEventListener("input", () => {
    const value = $("device-filter").value.trim();
    if (/^[0-9a-f-]{36}$/i.test(value)) {
      loadRecords();
    } else {
      render();
    }
  });
  $("tag-filter").addEventListener("change", render);
  $("search-input").addEventListener("input", render);
  $("efs-root-input").addEventListener("change", () => {
    const value = $("efs-root-input").value.trim();
    if (value) localStorage.setItem("efs_root_override", value);
    else localStorage.removeItem("efs_root_override");
    state.pickleCache.clear();
  });
  const savedEfs = localStorage.getItem("efs_root_override");
  if (savedEfs) $("efs-root-input").value = savedEfs;
}

async function init() {
  bindEvents();
  await loadConfig();
  await loadRecords();
}

init();
