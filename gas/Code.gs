// ── Configuration ─────────────────────────────────────────────────────────────
// Replace this with the ID from your Google Sheet's URL:
// https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
const SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID_HERE';
const SHEET_NAME = 'Visits';

const HEADERS = [
  'record_id', 'person_name', 'prayer_level', 'evangelisers', 'status',
  'date_of_evangelism', 'date_of_accepting_christ', 'notes', 'phone_numbers',
  'location_area', 'latitude', 'longitude', 'follow_up_status', 'team_name',
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
  const headers = data[0];
  const ridIdx = headers.indexOf('record_id');

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][ridIdx]) !== String(body.record_id)) continue;

    Object.keys(body).forEach(key => {
      if (key === 'action' || key === 'record_id') return;
      const colIdx = headers.indexOf(key);
      if (colIdx >= 0) sheet.getRange(i + 1, colIdx + 1).setValue(body[key] ?? '');
    });

    const updated = sheet.getRange(i + 1, 1, 1, headers.length).getValues()[0];
    const obj = {};
    headers.forEach((h, j) => obj[h] = updated[j]);
    return respond(obj);
  }

  return respond({ error: 'Record not found' });
}

// ── List visits ────────────────────────────────────────────────────────────────
function handleList(e) {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  if (data.length < 2) return respond([]);

  const headers = data[0];
  let rows = data.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => obj[h] = row[i]);
    return obj;
  });

  const p = e.parameter || {};
  if (p.team)            rows = rows.filter(r => r.outing_day === p.team);
  if (p.evangeliser)     rows = rows.filter(r => String(r.evangelisers).toLowerCase().includes(p.evangeliser.toLowerCase()));
  if (p.status)          rows = rows.filter(r => r.status === p.status);
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

  const headers = data[0];
  const latIdx = headers.indexOf('latitude');
  const lngIdx = headers.indexOf('longitude');
  const dayIdx = headers.indexOf('outing_day');
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

  const headers  = data[0];
  const rows     = data.slice(1);
  const statusIdx = headers.indexOf('status');
  const evsIdx    = headers.indexOf('evangelisers');
  const teamIdx   = headers.indexOf('team_name');

  const evangelisers = new Set();
  const teams = new Set();
  let saved = 0, unsaved = 0, discipled = 0;

  rows.forEach(row => {
    const status = row[statusIdx];
    if (status === 'Saved')            saved++;
    else if (status === 'Unsaved')     unsaved++;
    else if (status === 'Being discipled') discipled++;

    String(row[evsIdx] || '').split(',').forEach(n => {
      const t = n.trim();
      if (t) evangelisers.add(t);
    });

    if (row[teamIdx]) teams.add(row[teamIdx]);
  });

  return respond({
    total_visits: rows.length,
    total_saved: saved,
    total_unsaved: unsaved,
    total_being_discipled: discipled,
    total_evangelisers: evangelisers.size,
    total_teams: teams.size,
  });
}

// ── JSON response ──────────────────────────────────────────────────────────────
function respond(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
