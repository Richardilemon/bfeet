// ── Configuration ─────────────────────────────────────────────────────────────
const SPREADSHEET_ID = '1YGFLHPizcd3MUrS7QRmK0sdkra04RXjY7dc66IRnjxs';
const SHEET_NAME = 'Visits';

// Internal keys — must match the column ORDER in the sheet (left to right).
// The sheet header display names don't matter; only position matters.
const HEADERS = [
  'record_id', 'person_name', 'prayer_level', 'evangelisers', 'status',
  'date_of_evangelism', 'date_of_accepting_christ', 'notes', 'phone_numbers',
  'location_area', 'latitude', 'longitude', 'follow_up_status',
  'outing_day', 'outing_date', 'created_at',
];

// ── Sheet helper ───────────────────────────────────────────────────────────────
function getSheet() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

// Map a raw sheet row (array) to an object using HEADERS positions
function rowToObj(row) {
  const obj = {};
  HEADERS.forEach((h, i) => obj[h] = row[i]);
  return obj;
}

// ── Entry points ───────────────────────────────────────────────────────────────
function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.action === 'update') return handleUpdate(body);
    return handleCreate(body);
  } catch (err) {
    return respond({ error: err.toString() });
  }
}

function doGet(e) {
  try {
    const action = (e.parameter && e.parameter.action) || 'list';
    if (action === 'heatmap') return handleHeatmap(e);
    if (action === 'stats')   return handleStats(e);
    return handleList(e);
  } catch (err) {
    return respond({ error: err.toString() });
  }
}

// ── Create visit ───────────────────────────────────────────────────────────────
function handleCreate(body) {
  const sheet = getSheet();
  const record_id = Utilities.getUuid();
  const created_at = new Date().toISOString();

  const row = HEADERS.map(h => {
    if (h === 'record_id') return record_id;
    if (h === 'created_at') return created_at;
    const val = body[h];
    return (val !== undefined && val !== null) ? val : '';
  });

  sheet.appendRow(row);
  return respond({ record_id, created_at, ...body });
}

// ── Update visit ───────────────────────────────────────────────────────────────
function handleUpdate(body) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  const ridIdx = HEADERS.indexOf('record_id');

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][ridIdx]) !== String(body.record_id)) continue;

    Object.keys(body).forEach(key => {
      if (key === 'action' || key === 'record_id') return;
      const colIdx = HEADERS.indexOf(key);
      if (colIdx >= 0) sheet.getRange(i + 1, colIdx + 1).setValue(body[key] ?? '');
    });

    const updated = sheet.getRange(i + 1, 1, 1, HEADERS.length).getValues()[0];
    return respond(rowToObj(updated));
  }

  return respond({ error: 'Record not found' });
}

// ── List visits ────────────────────────────────────────────────────────────────
function handleList(e) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return respond([]);

  let rows = data.slice(1).map(rowToObj);

  const p = e.parameter || {};
  if (p.team)             rows = rows.filter(r => r.outing_day === p.team);
  if (p.evangeliser)      rows = rows.filter(r => String(r.evangelisers).toLowerCase().includes(p.evangeliser.toLowerCase()));
  if (p.status)           rows = rows.filter(r => r.status === p.status);
  if (p.follow_up_status) rows = rows.filter(r => r.follow_up_status === p.follow_up_status);

  rows.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

  const page     = parseInt(p.page || 1);
  const pageSize = Math.min(parseInt(p.page_size || 50), 200);
  rows = rows.slice((page - 1) * pageSize, page * pageSize);

  return respond(rows);
}

// ── Heatmap ────────────────────────────────────────────────────────────────────
function handleHeatmap(e) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return respond([]);

  const latIdx = HEADERS.indexOf('latitude');
  const lngIdx = HEADERS.indexOf('longitude');
  const dayIdx = HEADERS.indexOf('outing_day');
  const team   = e.parameter && e.parameter.team;

  const counts = {};
  data.slice(1).forEach(row => {
    if (team && row[dayIdx] !== team) return;
    const lat = parseFloat(row[latIdx]);
    const lng = parseFloat(row[lngIdx]);
    if (isNaN(lat) || isNaN(lng)) return;
    const key = lat.toFixed(4) + ',' + lng.toFixed(4);
    counts[key] = (counts[key] || 0) + 1;
  });

  return respond(Object.entries(counts).map(([key, intensity]) => {
    const [latitude, longitude] = key.split(',').map(Number);
    return { latitude, longitude, intensity };
  }));
}

// ── Stats ──────────────────────────────────────────────────────────────────────
function handleStats(e) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();

  if (data.length < 2) {
    return respond({ total_visits: 0, total_saved: 0, total_unsaved: 0,
                     total_being_discipled: 0, total_evangelisers: 0, total_teams: 0 });
  }

  const statusIdx = HEADERS.indexOf('status');
  const evsIdx    = HEADERS.indexOf('evangelisers');
  const dayIdx    = HEADERS.indexOf('outing_day');

  const evangelisers = new Set();
  const days = new Set();
  let saved = 0, unsaved = 0, discipled = 0;

  data.slice(1).forEach(row => {
    const status = row[statusIdx];
    if (status === 'Saved')                saved++;
    else if (status === 'Unsaved')         unsaved++;
    else if (status === 'Being discipled') discipled++;

    String(row[evsIdx] || '').split(',').forEach(n => {
      const t = n.trim();
      if (t) evangelisers.add(t);
    });

    if (row[dayIdx]) days.add(row[dayIdx]);
  });

  return respond({
    total_visits: data.length - 1,
    total_saved: saved,
    total_unsaved: unsaved,
    total_being_discipled: discipled,
    total_evangelisers: evangelisers.size,
    total_teams: days.size,
  });
}

// ── JSON response ──────────────────────────────────────────────────────────────
function respond(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
