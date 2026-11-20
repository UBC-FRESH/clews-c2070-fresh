/* Forest Ledger — iteration-0 static scenario explorer.
   Vanilla ES2019, no build step. All data is read from public/data/. */

(function () {
  'use strict';

  var DATA_ROOT = 'public/data/';
  var YEAR_FIRST = 2021;
  var YEAR_LAST = 2070;
  var YEARS = [];
  for (var y = YEAR_FIRST; y <= YEAR_LAST; y += 1) YEARS.push(y);

  var C2070_ZONES = ['A1', 'A2', 'B1', 'B2', 'L1', 'M1', 'N1', 'O1', 'O2', 'Q1', 'Q2', 'S1', 'T1'];
  var CANADA_BOUNDS = [[-141, 41], [-52, 84]];
  var CANADA_FIT_OPTIONS = { padding: 34 };
  var MOBILE_RAIL_QUERY = '(max-width: 720px)';

  var FAMILY_SHORT = {
    emission_factor_fix: 'Emission factor fix',
    pwrbio_cap: 'PWRBIO cap',
    pwrbio_tight: 'PWRBIO tight',
    nuclear_audit: 'Nuclear audit',
    wind_integration_audit: 'Wind audit',
    full_constraint: 'Full constraint',
    full_windcap: 'Full + wind cap',
    harvest_sensitivity_100_200: 'Harvest 100/200',
    zonal: 'BC zonal',
    reconcile: 'BC reconcile',
    reconcile_ffcm: 'FFCM reconcile',
    national_supply_curve: 'National curve',
    national_supply_curve_demand_sensitivity: 'Demand sensitivity',
    national_150m: '150 Mm³',
    national_base_119m: '119 Mm³ base'
  };

  var FAMILY_CHART_COLORS = {
    emission_factor_fix: '#d98a2b',
    pwrbio_cap: '#4f7d80',
    pwrbio_tight: '#a94f31',
    nuclear_audit: '#7c5a68',
    wind_integration_audit: '#58725f',
    full_constraint: '#356c82',
    full_windcap: '#b8892f'
  };
  var FALLBACK_FAMILY_COLOR = '#9d8b6c';

  var TECH_FAMILIES = [
    { id: 'HYD', label: 'Hydro', color: '#3f7f8c' },
    { id: 'WND', label: 'Wind', color: '#78a896' },
    { id: 'SOL', label: 'Solar', color: '#e8a63c' },
    { id: 'BIO', label: 'Bioenergy', color: '#7f9a55' },
    { id: 'BCW', label: 'BCW biomass', color: '#b45a36' },
    { id: 'NGS', label: 'Natural gas', color: '#8a8f86' },
    { id: 'URN', label: 'Uranium', color: '#8b6d7e' },
    { id: 'COA', label: 'Coal', color: '#3b3f3b' },
    { id: 'other', label: 'Other', color: '#c2ab7c' }
  ];
  var TECH_FAMILY_BY_ID = {};
  TECH_FAMILIES.forEach(function (f) { TECH_FAMILY_BY_ID[f.id] = f; });

  var TIER_COLORS = { T1: '#d98a2b', T2: '#c17832', T3: '#9f5a32', T4: '#7e3823' };
  var SESSION_FAMILY = 'session_live';
  var LIVE_MODEL_COMMIT = '40d5b43';
  var FALLBACK_DEFAULTS = {
    harvest_levels_m3_per_year: { 100: 100000000, 119: 118977339, 150: 150000000, 200: 200000000 },
    harvest_path_by_level: { 100: 'low', 119: 'hold', 150: 'high', 200: 'custom' },
    default_harvest_level_m3_per_year: 118977339,
    default_harvest_path: 'hold',
    recovery_fractions: [0.35, 0.6, 0.75],
    default_recovery_fraction: 0.6,
    cost_basis: 'adopted_rest_of_canada_curve',
    supply_curve: 'tiered',
    carbon_price_usd_per_tco2e_2021: 95,
    carbon_price_usd_per_tco2e_2026: 115,
    pwr_bio_cap_fraction: 0.05,
    nuclear_cap_gw: 0,
    wind_cost_usd_per_gj: 3,
    wind_cap_fraction: 0.2,
    generic_bio_cost_usd_per_gj: 3,
    t3_cap_pj: 39.873714,
    t4_cap_pj: 64.265464
  };

  var state = {
    core: null,
    outputs: new Map(),
    outputPromises: new Map(),
    warnings: [],
    selectedId: null,
    familyFilter: 'all',
    search: '',
    tab: 'overview',
    eligible: [],
    eligibleIds: new Set(),
    dispatch: {
      energy: 'electricity',
      zone: 'ALL',
      from: YEAR_FIRST,
      to: YEAR_LAST,
      token: 0,
      chart: null,
      processed: null
    },
    tradeoffChart: null,
    harvestChart: null,
    mapState: {
      initialized: false,
      loading: false,
      error: false,
      map: null,
      data: null,
      markers: []
    },
    osecan: {
      harvest: 119,
      recovery: 0.6,
      cost: 'weighted',
      lastModel: null
    },
    live: {
      mode: 'checking',
      health: null,
      constraints: null,
      defaults: FALLBACK_DEFAULTS,
      createdScenario: null,
      lastForm: null,
      activeForm: null,
      activeRun: null,
      currentJob: null,
      eventSource: null,
      pollTimer: null,
      pollFailures: 0,
      elapsedTimer: null,
      runStartedAt: 0,
      finalizingRunId: null,
      logLines: [],
      logCount: 0,
      sessionRuns: [],
      busy: false
    }
  };

  function $(id) { return document.getElementById(id); }
  function qsa(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function requireTrue(condition, message) {
    if (!condition) throw new Error(message);
  }
  function num(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  function fmt(value, digits) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    var d = digits === undefined ? 1 : digits;
    return Number(value).toLocaleString('en-CA', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtCompact(value, digits) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    var n = Number(value);
    var d = digits === undefined ? 1 : digits;
    if (Math.abs(n) >= 1e9) return fmt(n / 1e9, d) + 'B';
    if (Math.abs(n) >= 1e6) return fmt(n / 1e6, d) + 'M';
    if (Math.abs(n) >= 1e3) return fmt(n / 1e3, d) + 'k';
    return fmt(n, d);
  }
  function fmtAxis(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    var abs = Math.abs(Number(value));
    if (abs === 0) return '0';
    if (abs >= 1e9) return fmtCompact(value, 3);
    if (abs >= 1e6) return fmtCompact(value, 3);
    if (abs >= 1e3) return fmtCompact(value, 1);
    if (abs >= 10) return fmtCompact(value, 0);
    if (abs >= 1) return fmtCompact(value, 1);
    return fmtCompact(value, 2);
  }
  function fmtPct(value, digits) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    var d = digits === undefined ? 2 : digits;
    return fmt(Number(value) * 100, d) + '%';
  }
  function fmtBytes(bytes) {
    if (!Number.isFinite(Number(bytes))) return '—';
    var b = Number(bytes);
    if (b >= 1024 * 1024) return fmt(b / (1024 * 1024), 2) + ' MiB';
    if (b >= 1024) return fmt(b / 1024, 1) + ' KiB';
    return fmt(b, 0) + ' B';
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  }
  function shortFamily(family) { return FAMILY_SHORT[family] || family.replace(/_/g, ' '); }
  function familyColor(family) { return FAMILY_CHART_COLORS[family] || FALLBACK_FAMILY_COLOR; }

  async function loadJSON(relativePath) {
    var response = await fetch(DATA_ROOT + relativePath, { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status + ' for ' + relativePath);
    return response.json();
  }

  function showFatal(error) {
    var banner = $('fatal-banner');
    banner.hidden = false;
    banner.textContent = 'DATA CONTRACT FAILURE — ' + (error && error.message ? error.message : String(error));
    var overview = $('overview-content');
    if (overview) {
      overview.innerHTML = '<div class="error-block"><strong>Explorer halted</strong>' +
        'The committed data contract did not pass its client-side guard. Nothing was rendered from mismatched data.<br><br>' +
        '<code>' + esc(error && error.message ? error.message : String(error)) + '</code></div>';
    }
    var footer = $('footer-contract');
    if (footer) footer.textContent = 'Data contract: FAILED CLOSED';
    announce('Data contract check failed. Explorer halted.');
  }

  function announce(message) {
    var region = $('live-region');
    if (region) region.textContent = message;
  }

  function validateCore(core) {
    var m = core.manifest;
    var s = core.scenarios;
    var links = core.links;
    var supply = core.supply;

    requireTrue(m && typeof m === 'object', 'manifest.json is missing');
    requireTrue(String(m.schema_version).indexOf('iteration0/') === 0, 'unexpected manifest schema_version ' + m.schema_version);
    requireTrue(m.validation && m.validation.ok === true, 'manifest validation is not ok');
    requireTrue(s && typeof s === 'object', 'scenarios.json is missing');
    requireTrue(String(s.schema_version).indexOf('iteration0/') === 0, 'unexpected scenarios schema_version ' + s.schema_version);
    requireTrue(Array.isArray(s.scenarios), 'scenarios.json has no scenario array');
    requireTrue(s.scenarios.length === 47, 'expected 47 scenarios, found ' + s.scenarios.length);
    requireTrue(m.scenario_count === s.scenarios.length, 'manifest scenario_count does not match scenarios.json');
    requireTrue(m.output_count === s.scenarios.length, 'manifest output_count does not match scenario count');
    requireTrue(Array.isArray(s.families) && s.families.length === 15, 'expected 15 scenario families');

    var familyCounts = {};
    s.families.forEach(function (f) {
      requireTrue(f.family && f.scenario_count > 0, 'bad family record ' + JSON.stringify(f));
      familyCounts[f.family] = f.scenario_count;
    });
    var seen = new Set();
    s.scenarios.forEach(function (sc) {
      requireTrue(sc.scenario_id && !seen.has(sc.scenario_id), 'duplicate or missing scenario_id');
      seen.add(sc.scenario_id);
      requireTrue(sc.family && Object.prototype.hasOwnProperty.call(familyCounts, sc.family), 'unknown family on ' + sc.scenario_id);
      requireTrue(sc.label, 'missing label on ' + sc.scenario_id);
      requireTrue(sc.dimensions && typeof sc.dimensions === 'object', 'missing dimensions on ' + sc.scenario_id);
      requireTrue(sc.metrics && num(sc.metrics.objective) !== null, 'missing objective on ' + sc.scenario_id);
      requireTrue(sc.flags && typeof sc.flags === 'object', 'missing flags on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.superseded_for_emissions === 'boolean', 'missing superseded flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.post_fix_emissions === 'boolean', 'missing post-fix flag on ' + sc.scenario_id);
      requireTrue(Array.isArray(sc.sources) && sc.sources.length > 0, 'missing sources on ' + sc.scenario_id);
    });
    Object.keys(familyCounts).forEach(function (family) {
      var actual = s.scenarios.filter(function (sc) { return sc.family === family; }).length;
      requireTrue(actual === familyCounts[family], 'family count mismatch for ' + family);
    });

    requireTrue(s.tradeoff && Array.isArray(s.tradeoff.scenario_ids), 'trade-off ledger is missing');
    requireTrue(s.tradeoff.scenario_ids.length === 26, 'expected 26 trade-off scenarios, found ' + s.tradeoff.scenario_ids.length);
    s.tradeoff.scenario_ids.forEach(function (id) {
      requireTrue(seen.has(id), 'trade-off ledger references missing scenario ' + id);
    });

    requireTrue(links && Array.isArray(links.families), 'osecan_links.json families are missing');
    requireTrue(links.families.length === 15, 'expected 15 linked source families');
    links.families.forEach(function (f) {
      requireTrue(Array.isArray(f.files) && f.files.length === 3, 'linked family ' + f.family + ' does not have 3 files');
      f.files.forEach(function (file) {
        requireTrue(file.path && file.sha256 && file.sha256.length === 64, 'bad file record in ' + f.family);
      });
    });
    requireTrue(Array.isArray(links.input_files) && links.input_files.length >= 10, 'input file ledger is incomplete');
    requireTrue(links.validation && links.validation.national_cap_reconciliation, 'national cap validation missing');
    requireTrue(links.validation.national_cap_reconciliation.all_tier_sum_equals_rest === true, 'tier-sum validation failed in source');

    requireTrue(core.harvest && core.harvest.template && core.harvest.reference_paths, 'harvest_basis.json is incomplete');
    requireTrue(core.harvest.template.provinces.length === 13, 'harvest template province count is not 13');
    requireTrue(core.harvest.reference_paths.years.length === 50, 'harvest reference paths are not 50 years');

    requireTrue(supply && Array.isArray(supply.tiers) && supply.tiers.length === 4, 'supply curve must have four tiers');
    var tierSum = supply.tiers.reduce(function (sum, tier) { return sum + Number(tier.incremental_cap_pj); }, 0);
    requireTrue(Math.abs(tierSum - Number(supply.tier_sum_pj)) <= 1e-6, 'supply tier sum does not reconcile');
    requireTrue(supply.reconciliation && supply.reconciliation.all_tier_sum_equals_rest === true, 'supply reconciliation flag is not true');
    requireTrue(supply.reconciliation.years.length === 50, 'supply reconciliation is not 50 years');

    requireTrue(core.landbase && Array.isArray(core.landbase.by_province), 'managed landbase summary is missing');
    requireTrue(core.landbase.caveats && core.landbase.caveats.length >= 2, 'managed landbase caveats are missing');
  }

  function validateOutput(output, id) {
    requireTrue(output && output.scenario_id === id, 'output scenario_id mismatch for ' + id);
    var n = output.row_count;
    requireTrue(Number.isInteger(n) && n > 0, 'output row_count is invalid for ' + id);
    ['technology', 'category', 'zone', 'year', 'value_pj'].forEach(function (key) {
      requireTrue(Array.isArray(output[key]) && output[key].length === n, 'output column ' + key + ' has wrong length for ' + id);
    });
    requireTrue(output.units_by_category && typeof output.units_by_category === 'object', 'output units are missing for ' + id);
    var years = new Set();
    var finite = true;
    for (var i = 0; i < n; i += 1) {
      years.add(Number(output.year[i]));
      if (!Number.isFinite(Number(output.value_pj[i]))) finite = false;
      var category = output.category[i];
      requireTrue(typeof output.units_by_category[category] === 'string', 'missing unit for category ' + category + ' in ' + id);
    }
    requireTrue(finite, 'output contains non-finite values for ' + id);
    requireTrue(years.size === 50 && years.has(YEAR_FIRST) && years.has(YEAR_LAST), 'output does not cover the 50-year horizon for ' + id);
    return output;
  }

  async function getOutput(id) {
    if (state.outputs.has(id)) return state.outputs.get(id);
    if (state.outputPromises.has(id)) return state.outputPromises.get(id);
    var promise = loadJSON('outputs/' + id + '.json').then(function (output) {
      var checked = validateOutput(output, id);
      state.outputs.set(id, checked);
      state.outputPromises.delete(id);
      return checked;
    }).catch(function (error) {
      state.outputPromises.delete(id);
      throw error;
    });
    state.outputPromises.set(id, promise);
    return promise;
  }

  function buildCoreState(core) {
    validateCore(core);
    state.core = core;
    core.scenarioById = new Map();
    core.scenarios.scenarios.forEach(function (sc) { core.scenarioById.set(sc.scenario_id, sc); });

    var eligible = [];
    var eligibleIds = new Set();
    core.scenarios.tradeoff.metrics.forEach(function (metric) {
      var sc = core.scenarioById.get(metric.scenario_id);
      if (!sc) return;
      if (sc.flags.publishable && sc.flags.post_fix_emissions && !sc.flags.superseded_for_emissions) {
        eligible.push(sc);
        eligibleIds.add(sc.scenario_id);
      }
    });
    state.eligible = eligible;
    state.eligibleIds = eligibleIds;

    if (eligible.length !== 26) {
      state.warnings.push('Trade-off guard: flag-derived eligible set is ' + eligible.length + ', expected 26.');
    }
    core.scenarios.tradeoff.scenario_ids.forEach(function (id) {
      var sc = core.scenarioById.get(id);
      if (!sc || !eligibleIds.has(id)) {
        state.warnings.push('Trade-off guard excluded ' + id + ' because its flags are not publishable post-fix.');
      }
    });

    var defaultId = 'emission_factor_fix__efix_stress_119m';
    var hash = parseHash();
    var initialTab = hash.tab || 'overview';
    var initialId = hash.id && core.scenarioById.has(hash.id) ? hash.id : defaultId;
    if (!core.scenarioById.has(initialId)) initialId = core.scenarios.scenarios[0].scenario_id;

    renderHeader(core);
    renderFamilies();
    selectScenario(initialId, { silent: true });
    bindRailToggle();
    bindTabs();
    bindDispatchControls();
    bindOsecanControls();
    bindRunControls();
    renderOsecanStatic();
    renderInputsStatic();
    activateTab(validTab(initialTab), { silent: true });
    updateHash();
  }

  function renderHeader(core) {
    updateHeaderCounts();
    $('header-eligible-count').textContent = String(state.eligible.length);
    $('header-generated').textContent = 'Contract generated ' + fmtDate(core.scenarios.generated_utc) +
      ' · ' + core.manifest.output_count + ' outputs · ' + fmtBytes(core.manifest.total_bytes) +
      (state.live.sessionRuns.length ? ' · ' + state.live.sessionRuns.length + ' live session run(s)' : '');
    $('footer-contract').textContent = 'Data contract ' + core.scenarios.schema_version + ' · ' +
      core.scenarios.scenarios.length + ' scenarios · ' + state.eligible.length + ' trade-off eligible · model commit b5df675';
  }

  function updateHeaderCounts() {
    if (!state.core) return;
    $('header-scenario-count').textContent = String(state.core.scenarios.scenarios.length + state.live.sessionRuns.length);
  }

  function renderFamilies() {
    var core = state.core;
    var container = $('family-filter');
    var families = core.scenarios.families;
    var html = '<button type="button" class="family-chip is-active" data-family="all">All <sup>' +
      core.scenarios.scenarios.length + '</sup></button>';
    families.forEach(function (f) {
      html += '<button type="button" class="family-chip" data-family="' + esc(f.family) + '" title="' + esc(f.label) + '">' +
        esc(shortFamily(f.family)) + ' <sup>' + f.scenario_count + '</sup></button>';
    });
    container.innerHTML = html;
    updateFamilyChipState();
    container.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-family]');
      if (!button) return;
      state.familyFilter = button.getAttribute('data-family');
      updateFamilyChipState();
      renderScenarioList();
    });
  }

  function updateFamilyChipState() {
    qsa('#family-filter .family-chip').forEach(function (chip) {
      var active = chip.getAttribute('data-family') === state.familyFilter;
      chip.classList.toggle('is-active', active);
      chip.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function scenarioMatches(sc) {
    if (state.familyFilter !== 'all' && sc.family !== state.familyFilter) return false;
    var query = state.search.trim().toLowerCase();
    if (!query) return true;
    var haystack = [sc.scenario_id, sc.label, sc.family, shortFamily(sc.family), sc.description, sc.source_run].join(' ').toLowerCase();
    return haystack.indexOf(query) !== -1;
  }

  function setRailCount(text) {
    $('rail-count').textContent = text;
    $('rail-toggle-count').textContent = text;
  }

  function renderScenarioList() {
    var core = state.core;
    if (!core) return;
    var list = $('scenario-list');
    var scrollTop = list.scrollTop;
    var order = new Map();
    core.scenarios.families.forEach(function (f, index) { order.set(f.family, index); });
    var rows = core.scenarios.scenarios.filter(scenarioMatches).sort(function (a, b) {
      var familyDelta = (order.get(a.family) || 0) - (order.get(b.family) || 0);
      if (familyDelta !== 0) return familyDelta;
      return a.label.localeCompare(b.label);
    });
    var sessionRows = state.live.sessionRuns.filter(scenarioMatches);

    if (!rows.length && !sessionRows.length) {
      list.innerHTML = '<li class="rail-foot">No scenario matches this filter.</li>';
      setRailCount('0 shown');
      return;
    }

    var html = '';
    if (sessionRows.length) {
      html += '<li class="rail-group-label" role="presentation"><span>Session runs</span><span class="mono">live · screening only</span></li>';
      sessionRows.forEach(function (sc) {
        var selected = sc.scenario_id === state.selectedId;
        html += '<li><button type="button" class="scenario-item scenario-item--live' + (selected ? ' is-selected' : '') +
          '" data-scenario-id="' + esc(sc.scenario_id) + '" aria-pressed="' + (selected ? 'true' : 'false') + '">' +
          '<span class="s-label">' + esc(sc.label) + '</span>' +
          '<span class="s-id">' + esc(sc.runId) + '</span>' +
          '<span class="s-badges"><span class="badge badge--family">Session live</span>' +
          '<span class="badge badge--diag">screening only</span>' +
          '<span class="badge badge--solve">' + esc(sc.flags.solver || 'completed') + '</span></span></button></li>';
      });
    }
    rows.forEach(function (sc) {
      var selected = sc.scenario_id === state.selectedId;
      var statusBadge = sc.flags.superseded_for_emissions
        ? '<span class="badge badge--superseded">superseded</span>'
        : (sc.flags.post_fix_emissions
          ? '<span class="badge badge--postfix">post-fix</span>'
          : '<span class="badge badge--ink">pre-fix</span>');
      var diagnosticBadge = sc.flags.diagnostic ? '<span class="badge badge--diag">diagnostic</span>' : '';
      var solverBadge = sc.flags.solver && sc.flags.solver !== 'optimal'
        ? '<span class="badge badge--solve">' + esc(sc.flags.solver) + '</span>'
        : '';
      html += '<li><button type="button" class="scenario-item' + (selected ? ' is-selected' : '') +
        '" data-scenario-id="' + esc(sc.scenario_id) + '" aria-pressed="' + (selected ? 'true' : 'false') + '">' +
        '<span class="s-label">' + esc(sc.label) + '</span>' +
        '<span class="s-id">' + esc(sc.scenario_id) + '</span>' +
        '<span class="s-badges"><span class="badge badge--family">' + esc(shortFamily(sc.family)) + '</span>' +
        statusBadge + diagnosticBadge + solverBadge + '</span></button></li>';
    });
    list.innerHTML = html;
    list.scrollTop = scrollTop;
    setRailCount((rows.length + sessionRows.length) + ' of ' + (core.scenarios.scenarios.length + state.live.sessionRuns.length) + ' shown');
  }

  function selectScenario(id, options) {
    var core = state.core;
    if (!core) return;
    var sc = core.scenarioById.get(id);
    if (!sc) return;
    var opts = options || {};
    state.selectedId = id;
    renderScenarioList();
    renderOverview();
    updateTabMeta();
    if (!opts.silent) announce('Selected scenario ' + sc.label + ', ' + shortFamily(sc.family));
    if (state.dispatch.chart) state.dispatch.chart.clear();
    if (state.tab === 'dispatch') renderDispatch();
    if (state.tab === 'tradeoff') renderTradeoff();
    updateHash();
  }

  function updateTabMeta() {
    var sc = state.core && state.core.scenarioById.get(state.selectedId);
    if (!sc) {
      $('tab-scenario-meta').innerHTML = '';
      return;
    }
    if (sc.liveRun) {
      $('tab-scenario-meta').innerHTML = '<strong>' + esc(sc.label) + '</strong> · live session run · screening only';
      return;
    }
    $('tab-scenario-meta').innerHTML = '<strong>' + esc(sc.label) + '</strong> · ' + esc(shortFamily(sc.family)) +
      (sc.flags.superseded_for_emissions ? ' · superseded' : ' · post-fix');
  }

  function renderOverview() {
    var core = state.core;
    var sc = core.scenarioById.get(state.selectedId);
    if (!sc) return;
    if (sc.liveRun) {
      renderLiveOverview(sc);
      return;
    }
    var m = sc.metrics;
    var dims = sc.dimensions;
    var eligible = state.eligibleIds.has(sc.scenario_id);
    var superseded = sc.flags.superseded_for_emissions;
    var statusTitle = superseded ? 'Superseded — audit only' : (sc.flags.post_fix_emissions ? 'Post-fix canonical' : 'Pre-fix record');
    var statusCopy = superseded
      ? 'This run is retained for traceability. It is excluded from the trade-off frontier and must not be read as a current emissions result.'
      : (eligible
        ? 'Eligible for the 26-scenario trade-off view under the committed publishable / post-fix / non-superseded rule.'
        : 'Not in the trade-off ledger; retain the flags when comparing across families.');

    var html = '<div class="scenario-masthead">' +
      '<div>' +
        '<p class="kicker masthead-family">' + esc(sc.family_label) + '</p>' +
        '<h2>' + esc(sc.label) + '</h2>' +
        '<p class="scenario-desc">' + esc(sc.description || 'No scenario description in the committed contract.') + '</p>' +
        '<span class="masthead-id">' + esc(sc.scenario_id) + '</span>' +
        '<div class="masthead-flags">' +
          '<span class="badge ' + (superseded ? 'badge--superseded' : (sc.flags.post_fix_emissions ? 'badge--postfix' : 'badge--ink')) + '">' +
            (superseded ? 'superseded for emissions' : (sc.flags.post_fix_emissions ? 'post-fix emissions' : 'pre-fix emissions')) + '</span>' +
          (sc.flags.publishable ? '<span class="badge badge--postfix">publishable</span>' : '<span class="badge badge--superseded">not publishable</span>') +
          (sc.flags.diagnostic ? '<span class="badge badge--diag">diagnostic</span>' : '') +
          (eligible ? '<span class="badge badge--family">trade-off eligible</span>' : '') +
        '</div>' +
      '</div>' +
      '<div class="masthead-side">' +
        '<div><p class="kicker">Status</p><p class="status-large' + (superseded ? ' is-warn' : '') + '">' + statusTitle + '</p></div>' +
        '<p>' + statusCopy + '</p>' +
        '<p class="mono">solver: ' + esc(sc.flags.solver || '—') + '</p>' +
      '</div>' +
    '</div>';

    if (superseded) {
      html += '<div class="overview-callout"><strong>Superseded record</strong><br>' +
        'Shown because flags are part of the ledger. It is excluded from trade-off comparisons; emissions and objective are not current C2070 results.</div>';
    }

    html += '<div class="metric-grid">' +
      metricCard('Objective', fmt(m.objective, 2), 'M$ (million USD)', 'solver objective · lower is cheaper', true) +
      metricCard('Cumulative emissions', fmt(m.emissions_total, 3), 'Mt CO₂', 'sum over 2021—2070', false) +
      metricCard('National BCW 2050', fmt(m.national_bcw_2050, 2), 'PJ fuel', 'national bio supply in 2050', false) +
      metricCard('PWRBCW 2050', fmt(m.pwrbcw_2050, 2), 'PJ electricity', 'BCW power generation in 2050', false) +
      metricCard('Bio share 2050', fmtPct(m.bio_share_2050, 3), 'share of electricity', m.bio_share_basis === 'derived_from_dispatch_2050' ? 'derived from dispatch' : esc(m.bio_share_basis || '—'), false) +
    '</div>';

    var dimRows = Object.keys(dims).map(function (key) {
      return '<div><dt>' + esc(dimensionLabel(key)) + '</dt><dd>' + dimensionValue(key, dims[key]) + '</dd></div>';
    }).join('');

    var flagRows = Object.keys(sc.flags).map(function (key) {
      var value = sc.flags[key];
      var cls = typeof value === 'boolean' ? (value ? ' is-true' : ' is-false') : '';
      return '<span class="flag-pill' + cls + '">' + esc(key) + ': ' + esc(value) + '</span>';
    }).join('');

    html += '<div class="overview-columns">' +
      '<div class="ledger-card"><h3>Dimensions</h3><dl class="dim-list">' + dimRows + '</dl></div>' +
      '<div class="ledger-card"><h3>Flags</h3><div class="flags-list">' + flagRows + '</div>' +
        '<h3 style="margin-top:20px">Source paths</h3><ul class="source-list">' +
          sc.sources.map(function (source) { return '<li>' + esc(source) + '</li>'; }).join('') +
        '</ul></div>' +
    '</div>';
    $('overview-content').innerHTML = html;
  }

  function renderLiveOverview(sc) {
    var m = sc.metrics;
    var dims = sc.dimensions || {};
    var dimRows = Object.keys(dims).map(function (key) {
      return '<div><dt>' + esc(dimensionLabel(key)) + '</dt><dd>' + dimensionValue(key, dims[key]) + '</dd></div>';
    }).join('');
    var flagRows = Object.keys(sc.flags).map(function (key) {
      var value = sc.flags[key];
      var cls = typeof value === 'boolean' ? (value ? ' is-true' : ' is-false') : '';
      return '<span class="flag-pill' + cls + '">' + esc(key) + ': ' + esc(value) + '</span>';
    }).join('');
    var html = '<div class="scenario-masthead">' +
      '<div>' +
        '<p class="kicker masthead-family">Session run · live screening runner</p>' +
        '<h2>' + esc(sc.label) + '</h2>' +
        '<p class="scenario-desc">' + esc(sc.description) + '</p>' +
        '<span class="masthead-id">' + esc(sc.contract_scenario_id) + ' · run ' + esc(sc.runId) + '</span>' +
        '<div class="masthead-flags">' +
          '<span class="badge badge--diag">screening_only</span>' +
          '<span class="badge badge--superseded">policy_grade=false</span>' +
          '<span class="badge badge--ink">not publishable</span>' +
          '<span class="badge badge--family">live session</span>' +
        '</div>' +
      '</div>' +
      '<div class="masthead-side">' +
        '<div><p class="kicker">Status</p><p class="status-large">Screening-only run</p></div>' +
        '<p>Generated by the FastAPI runner in this session. No reserve margin, transmission, curtailment, or build-rate constraints; never policy or forecast.</p>' +
        '<p class="mono">solver: ' + esc(m.solver_status || '—') + '</p>' +
      '</div>' +
    '</div>' +
    '<div class="overview-callout"><strong>Screening only — not policy grade</strong><br>' +
      'This run uses constraint set <span class="mono">screening_v0</span>. It is visible in the session rail, overview, dispatch, and trade-off, but it is excluded from the 26-scenario publishable ledger.</div>' +
    '<div class="metric-grid">' +
      metricCard('Objective', fmt(m.objective, 2), 'M$ (million USD)', 'solver objective · live run', true) +
      metricCard('Cumulative emissions', fmt(m.emissions_total, 3), 'Mt CO₂', 'sum over 2021—2070', false) +
      metricCard('National BCW 2050', fmt(m.national_bcw_2050, 2), 'PJ fuel', 'national bio supply in 2050', false) +
      metricCard('PWRBCW 2050', fmt(m.pwrbcw_2050, 2), 'PJ electricity', 'BCW power generation in 2050', false) +
      metricCard('Bio share 2050', fmtPct(m.bio_share_2050, 3), 'share of electricity', esc(m.bio_share_basis || 'derived from dispatch'), false) +
    '</div>' +
    '<div class="overview-columns">' +
      '<div class="ledger-card"><h3>Dimensions</h3><dl class="dim-list">' + dimRows + '</dl></div>' +
      '<div class="ledger-card"><h3>Flags</h3><div class="flags-list">' + flagRows + '</div>' +
        '<h3 style="margin-top:20px">Source paths</h3><ul class="source-list">' +
          sc.sources.map(function (source) { return '<li>' + esc(source) + '</li>'; }).join('') +
        '</ul></div>' +
    '</div>';
    $('overview-content').innerHTML = html;
  }

  function metricCard(label, value, unit, context, hero) {
    return '<div class="metric-card' + (hero ? ' metric-card--hero' : '') + '">' +
      '<span class="metric-label">' + esc(label) + '</span>' +
      '<span class="metric-value">' + esc(value) + '</span>' +
      '<span class="metric-unit">' + esc(unit) + '</span>' +
      '<span class="metric-context">' + esc(context) + '</span></div>';
  }

  function dimensionLabel(key) {
    var labels = {
      harvest: 'Harvest basis',
      carbon_price_usd_per_t: 'Carbon price',
      bio_cost_usd_per_gj: 'Bio cost',
      recovery_fraction: 'Recovery',
      supply_curve: 'Supply curve',
      hydro_mode: 'Hydro mode',
      pwrbio_cap: 'PWRBIO cap',
      nuclear_cap_gw: 'Nuclear cap',
      wind: 'Wind',
      zero_tiers: 'Zero tiers'
    };
    return labels[key] || key.replace(/_/g, ' ');
  }

  function dimensionValue(key, value) {
    if (value === null || value === undefined) return 'not set';
    if (key === 'harvest' && value && typeof value === 'object') {
      return esc(value.level) + ' <span class="mono">· ' + fmt(value.m3_per_year, 0) + ' m³/yr · ' + esc(value.basis) + '</span>';
    }
    if (key === 'carbon_price_usd_per_t') return '<span class="mono">$' + fmt(value, 2) + ' / t CO₂</span>';
    if (key === 'bio_cost_usd_per_gj') return '<span class="mono">$' + fmt(value, 2) + ' / GJ</span>';
    if (key === 'recovery_fraction') return '<span class="mono">' + fmtPct(value, 0) + ' (' + fmt(value, 2) + ')</span>';
    if (key === 'nuclear_cap_gw') return '<span class="mono">' + fmt(value, 2) + ' GW</span>';
    if (key === 'wind' && typeof value === 'object') {
      return esc('cap ' + (value.cap || 'none') + ' · integration ' + (value.integration_limit == null ? '—' : value.integration_limit) +
        ' · $' + fmt(value.cost_usd_per_gj, 2) + '/GJ');
    }
    if (key === 'pwrbio_cap' && typeof value === 'object') {
      return esc(Object.keys(value).map(function (k) { return k + ': ' + value[k]; }).join(' · '));
    }
    if (Array.isArray(value)) return value.length ? esc(value.join(', ')) : 'none';
    if (typeof value === 'object') {
      return esc(Object.keys(value).map(function (k) { return k + ': ' + value[k]; }).join(' · '));
    }
    return esc(String(value));
  }

  function validTab(tab) {
    return ['overview', 'dispatch', 'tradeoff', 'map', 'osecan', 'run', 'inputs'].indexOf(tab) !== -1 ? tab : 'overview';
  }

  function activateTab(tab, options) {
    var opts = options || {};
    state.tab = validTab(tab);
    qsa('.tab').forEach(function (button) {
      var active = button.getAttribute('data-tab') === state.tab;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
      if (active && opts.focus) button.focus();
    });
    qsa('.panel').forEach(function (panel) {
      var active = panel.id === 'panel-' + state.tab;
      panel.hidden = !active;
      panel.classList.toggle('is-active', active);
    });
    if (state.tab === 'dispatch') renderDispatch();
    if (state.tab === 'tradeoff') renderTradeoff();
    if (state.tab === 'map') initMap();
    if (state.tab === 'osecan') renderOsecan();
    if (state.tab === 'run') renderRunMode();
    if (state.tab === 'inputs') renderInputsCharts();
    window.requestAnimationFrame(function () {
      if (state.tab === 'dispatch' && state.dispatch.chart) state.dispatch.chart.resize();
      if (state.tab === 'tradeoff' && state.tradeoffChart) state.tradeoffChart.resize();
      if (state.tab === 'inputs' && state.harvestChart) state.harvestChart.resize();
      if (state.tab === 'map' && state.mapState.map) state.mapState.map.resize();
    });
    if (!opts.silent) {
      updateHash();
      announce('Opened ' + state.tab + ' view.');
    }
  }

  function bindRailToggle() {
    var rail = $('scenario-navigator');
    var toggle = $('rail-toggle');
    if (!rail || !toggle) return;
    toggle.addEventListener('click', function () {
      var open = rail.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    var media = window.matchMedia(MOBILE_RAIL_QUERY);
    var collapseOnDesktop = function () {
      if (media.matches) return;
      rail.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
    };
    if (media.addEventListener) media.addEventListener('change', collapseOnDesktop);
    else media.addListener(collapseOnDesktop);
  }

  function bindTabs() {
    qsa('.tab').forEach(function (button) {
      button.addEventListener('click', function () {
        activateTab(button.getAttribute('data-tab'));
      });
      button.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft' && event.key !== 'Home' && event.key !== 'End') return;
        event.preventDefault();
        var tabs = qsa('.tab');
        var index = tabs.indexOf(button);
        if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') index = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') index = 0;
        if (event.key === 'End') index = tabs.length - 1;
        activateTab(tabs[index].getAttribute('data-tab'), { focus: true });
      });
    });
    var search = $('scenario-search');
    search.addEventListener('input', function () {
      state.search = search.value;
      renderScenarioList();
      updateFamilyChipState();
    });
    $('scenario-list').addEventListener('click', function (event) {
      var button = event.target.closest('button[data-scenario-id]');
      if (!button) return;
      selectScenario(button.getAttribute('data-scenario-id'));
    });
  }

  function parseHash() {
    var raw = window.location.hash.replace(/^#/, '');
    if (!raw) return {};
    var parts = raw.split('/');
    var id = null;
    if (parts[1]) {
      try { id = decodeURIComponent(parts[1]); } catch (error) { id = parts[1]; }
    }
    return { tab: parts[0] || null, id: id };
  }

  function updateHash() {
    if (!state.core) return;
    var next = '#' + state.tab + '/' + encodeURIComponent(state.selectedId || '');
    if (window.location.hash !== next) {
      try { window.history.replaceState(null, '', next); } catch (error) { /* file:// may refuse */ }
    }
  }

  /* ---------------- Dispatch ---------------- */

  function technologyFamily(technology) {
    if (/^PWRHYD/.test(technology)) return 'HYD';
    if (/^PWRWND/.test(technology)) return 'WND';
    if (/^PWRSOL/.test(technology)) return 'SOL';
    if (/^PWRBIO/.test(technology)) return 'BIO';
    if (/^PWRBCW/.test(technology)) return 'BCW';
    if (/^PWRNGS/.test(technology)) return 'NGS';
    if (/^PWRURN/.test(technology)) return 'URN';
    if (/^PWRCOA|^COA/.test(technology)) return 'COA';
    if (/^SUPBCW/.test(technology)) return 'BCW';
    if (/^RNWBIO/.test(technology)) return 'BIO';
    return 'other';
  }

  function categorySelection(output, energy) {
    var categories = Array.from(new Set(output.category));
    if (energy === 'electricity') {
      var hasDispatch = categories.indexOf('dispatch') !== -1;
      var powerCategories = categories.filter(function (category) { return category.indexOf('power_') === 0; });
      if (hasDispatch) {
        return {
          categories: ['dispatch'],
          unit: output.units_by_category.dispatch,
          label: 'dispatch',
          note: powerCategories.length ? 'dispatch aggregate used; power_* detail not added (double-count guard).' : 'aggregate dispatch table.'
        };
      }
      if (powerCategories.length) {
        var units = Array.from(new Set(powerCategories.map(function (category) { return output.units_by_category[category]; })));
        requireTrue(units.length === 1, 'power_* categories mix units in ' + output.scenario_id);
        return {
          categories: powerCategories,
          unit: units[0],
          label: powerCategories.join(' + '),
          note: 'detail power_* categories (no aggregate dispatch table).'
        };
      }
      return null;
    }

    var hasSupply = categories.indexOf('supply') !== -1;
    var supplyCategories = categories.filter(function (category) {
      return category === 'bio_supply' || category.indexOf('supply_') === 0;
    });
    if (hasSupply) {
      return {
        categories: ['supply'],
        unit: output.units_by_category.supply,
        label: 'supply',
        note: supplyCategories.length ? 'aggregate supply used; supply_* detail not added (double-count guard).' : 'aggregate fuel supply table.'
      };
    }
    if (supplyCategories.length) {
      var fuelUnits = Array.from(new Set(supplyCategories.map(function (category) { return output.units_by_category[category]; })));
      requireTrue(fuelUnits.length === 1, 'fuel categories mix units in ' + output.scenario_id);
      return {
        categories: supplyCategories,
        unit: fuelUnits[0],
        label: supplyCategories.join(' + '),
        note: 'detail fuel categories (no aggregate supply table).'
      };
    }
    return null;
  }

  function processDispatch(output, energy) {
    var selection = categorySelection(output, energy);
    if (!selection) return null;
    var categorySet = new Set(selection.categories);
    var rows = [];
    var zones = new Set();
    for (var i = 0; i < output.row_count; i += 1) {
      if (!categorySet.has(output.category[i])) continue;
      var zone = output.zone[i];
      zones.add(zone);
      rows.push({
        year: Number(output.year[i]),
        zone: zone,
        family: technologyFamily(output.technology[i]),
        value: Number(output.value_pj[i])
      });
    }
    if (!rows.length) return null;
    var zoneList = Array.from(zones);
    var canonical = C2070_ZONES.filter(function (zone) { return zoneList.indexOf(zone) !== -1; });
    var extras = zoneList.filter(function (zone) { return zone !== 'CAN' && canonical.indexOf(zone) === -1; }).sort();
    var hasCan = zoneList.indexOf('CAN') !== -1;
    var allLabel = hasCan
      ? 'National (CAN aggregate)'
      : (canonical.length === 13 ? 'All 13 C2070 zones' : 'All ' + zoneList.filter(function (z) { return z !== 'CAN'; }).length + ' reporting zones');
    return {
      rows: rows,
      selection: selection,
      zones: canonical.concat(extras),
      hasCan: hasCan,
      allLabel: allLabel,
      unit: selection.unit
    };
  }

  function filterDispatchRows(processed, zone) {
    if (!processed) return [];
    if (zone === 'ALL') {
      if (processed.hasCan) return processed.rows.filter(function (row) { return row.zone === 'CAN'; });
      return processed.rows;
    }
    return processed.rows.filter(function (row) { return row.zone === zone; });
  }

  function renderZoneOptions(processed) {
    var select = $('dispatch-zone');
    if (!processed) {
      select.innerHTML = '<option value="ALL">No reporting zones</option>';
      state.dispatch.zone = 'ALL';
      return;
    }
    var options = ['<option value="ALL">' + esc(processed.allLabel) + '</option>'];
    processed.zones.forEach(function (zone) {
      options.push('<option value="' + esc(zone) + '">Zone ' + esc(zone) + '</option>');
    });
    select.innerHTML = options.join('');
    var valid = state.dispatch.zone === 'ALL' || processed.zones.indexOf(state.dispatch.zone) !== -1;
    if (!valid) state.dispatch.zone = 'ALL';
    select.value = state.dispatch.zone;
  }

  function bindDispatchControls() {
    qsa('input[name="energy-ledger"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        if (!radio.checked) return;
        state.dispatch.energy = radio.value;
        state.dispatch.zone = 'ALL';
        renderDispatch();
      });
    });
    $('dispatch-zone').addEventListener('change', function (event) {
      state.dispatch.zone = event.target.value;
      renderDispatchChart();
    });
    var min = $('year-min');
    var max = $('year-max');
    function onRange() {
      var a = Number(min.value);
      var b = Number(max.value);
      if (a > b) {
        if (document.activeElement === min) { b = a; max.value = String(b); }
        else { a = b; min.value = String(a); }
      }
      state.dispatch.from = a;
      state.dispatch.to = b;
      $('year-min-out').textContent = String(a);
      $('year-max-out').textContent = String(b);
      $('year-range-label').textContent = a + '—' + b;
      renderDispatchChart();
    }
    min.addEventListener('input', onRange);
    max.addEventListener('input', onRange);
  }

  async function renderDispatch() {
    if (!state.core || !state.selectedId) return;
    var token = ++state.dispatch.token;
    var loading = $('dispatch-loading');
    var empty = $('dispatch-empty');
    loading.hidden = false;
    empty.hidden = true;
    var output;
    try {
      output = await getOutput(state.selectedId);
    } catch (error) {
      if (token !== state.dispatch.token) return;
      loading.hidden = true;
      empty.hidden = false;
      if (state.dispatch.chart) state.dispatch.chart.clear();
      empty.innerHTML = '<div><strong>Output unavailable for this scenario.</strong><br>' + esc(error.message) + '</div>';
      $('dispatch-source-note').textContent = 'No dispatch rendered — output contract check failed.';
      return;
    }
    if (token !== state.dispatch.token) return;
    var processed;
    try {
      processed = processDispatch(output, state.dispatch.energy);
    } catch (error) {
      if (token !== state.dispatch.token) return;
      loading.hidden = true;
      empty.hidden = false;
      if (state.dispatch.chart) state.dispatch.chart.clear();
      empty.innerHTML = '<div><strong>Category guard tripped.</strong><br>' + esc(error.message) + '</div>';
      return;
    }
    loading.hidden = true;
    state.dispatch.processed = processed;
    renderZoneOptions(processed);
    if (!processed) {
      empty.hidden = false;
      empty.innerHTML = '<div><strong>No ' + state.dispatch.energy + ' ledger in this scenario.</strong><br>' +
        'This output contains only the other energy category. Switch ledger or select another scenario; categories are never summed across units.</div>';
      if (state.dispatch.chart) { state.dispatch.chart.clear(); }
      $('dispatch-source-note').textContent = 'No rows in the selected energy ledger.';
      $('dispatch-legend').innerHTML = '';
      return;
    }
    empty.hidden = true;
    renderDispatchChart();
  }

  function renderDispatchChart() {
    var processed = state.dispatch.processed;
    if (!processed) return;
    var container = $('dispatch-chart');
    if (!window.echarts) {
      $('dispatch-empty').hidden = false;
      $('dispatch-empty').innerHTML = '<div><strong>ECharts failed to load.</strong><br>Check vendor/echarts.min.js.</div>';
      return;
    }
    var rows = filterDispatchRows(processed, state.dispatch.zone);
    var from = state.dispatch.from;
    var to = state.dispatch.to;
    rows = rows.filter(function (row) { return row.year >= from && row.year <= to; });
    if (!rows.length) {
      if (state.dispatch.chart) state.dispatch.chart.clear();
      $('dispatch-empty').hidden = false;
      $('dispatch-empty').innerHTML = '<div><strong>No rows in this window.</strong><br>Widen the year range or choose another zone.</div>';
      return;
    }
    $('dispatch-empty').hidden = true;
    if (!state.dispatch.chart) state.dispatch.chart = window.echarts.init(container, null, { renderer: 'canvas' });

    var years = [];
    for (var year = from; year <= to; year += 1) years.push(year);
    var familyTotals = {};
    TECH_FAMILIES.forEach(function (family) { familyTotals[family.id] = years.map(function () { return 0; }); });
    var yearIndex = new Map();
    years.forEach(function (year, index) { yearIndex.set(year, index); });
    rows.forEach(function (row) {
      var index = yearIndex.get(row.year);
      if (index === undefined) return;
      if (!familyTotals[row.family]) familyTotals[row.family] = years.map(function () { return 0; });
      familyTotals[row.family][index] += row.value;
    });
    var totals = years.map(function (_, index) {
      return TECH_FAMILIES.reduce(function (sum, family) { return sum + (familyTotals[family.id] ? familyTotals[family.id][index] : 0); }, 0);
    });

    var activeFamilies = TECH_FAMILIES.filter(function (family) {
      return familyTotals[family.id] && familyTotals[family.id].some(function (value) { return Math.abs(value) > 1e-9; });
    });

    var series = activeFamilies.map(function (family) {
      return {
        name: family.label,
        type: 'line',
        stack: 'dispatch-total',
        symbol: 'none',
        smooth: false,
        lineStyle: { width: 0.8, color: family.color },
        itemStyle: { color: family.color },
        areaStyle: { color: family.color, opacity: 0.86 },
        emphasis: { focus: 'series', areaStyle: { opacity: 1 } },
        data: familyTotals[family.id]
      };
    });
    series.push({
      name: 'Total',
      type: 'line',
      symbol: 'none',
      z: 20,
      lineStyle: { width: 1.6, color: '#17251d', type: 'dashed' },
      itemStyle: { color: '#17251d' },
      emphasis: { focus: 'series' },
      tooltip: { show: false },
      data: totals
    });

    var unit = processed.unit;
    var sourceNote = 'Category set: ' + processed.selection.label + ' · units: ' + unit + ' · ' + processed.selection.note + ' ' +
      (state.dispatch.zone === 'ALL'
        ? (processed.hasCan ? 'National CAN aggregate rows; zone detail is not added (double-count guard).' : 'Sum of ' + processed.zones.length + ' reporting zones with no CAN aggregate present.')
        : 'Zone ' + state.dispatch.zone + ' only.') +
      ' Source: ' + outputSourcePath(state.selectedId) + '. Never summed with the other energy ledger.';
    $('dispatch-source-note').textContent = sourceNote;

    $('dispatch-legend').innerHTML = activeFamilies.map(function (family) {
      return '<span class="swatch"><i style="background:' + family.color + '"></i>' + esc(family.label) + '</span>';
    }).join('') + '<span class="swatch"><i style="background:#17251d"></i>Total</span>';

    state.dispatch.chart.setOption({
      animationDuration: 420,
      animationEasing: 'cubicOut',
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      color: activeFamilies.map(function (family) { return family.color; }),
      grid: { left: 62, right: 24, top: 58, bottom: 46, containLabel: true },
      legend: {
        type: 'scroll',
        top: 4,
        left: 2,
        right: 2,
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
        itemWidth: 12,
        itemHeight: 8,
        pageIconColor: '#a94f31',
        pageTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10 }
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#0c1f19',
        borderColor: '#d98a2b',
        borderWidth: 1,
        padding: 0,
        textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
        axisPointer: { type: 'line', lineStyle: { color: '#a94f31', type: 'dashed', width: 1 } },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var currentYear = params[0].axisValue;
          var total = totals[yearIndex.get(Number(currentYear))] || 0;
          var lines = params.filter(function (item) { return item.seriesName !== 'Total' && Math.abs(item.value) > 1e-9; })
            .sort(function (a, b) { return b.value - a.value; })
            .map(function (item) {
              var share = total > 0 ? (item.value / total) * 100 : 0;
              return '<div class="chart-tip-row"><i style="background:' + item.color + '"></i><span>' + esc(item.seriesName) +
                '</span><b>' + fmt(item.value, 2) + ' PJ</b><em>' + fmt(share, 1) + '%</em></div>';
            }).join('');
          return '<div class="chart-tip"><div class="chart-tip-head"><span>' + esc(currentYear) + '</span><b>' + fmt(total, 2) + ' PJ</b></div>' +
            '<div class="chart-tip-sub">' + esc(unit) + '</div>' + lines + '</div>';
        }
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: years,
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisTick: { show: false },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', interval: years.length > 24 ? 9 : 4 }
      },
      yAxis: {
        type: 'value',
        name: unit,
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', padding: [0, 0, 4, 0] },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { show: false }
      },
      series: series
    }, true);
  }

  function outputSourcePath(id) {
    var output = state.outputs.get(id);
    return output && output.source ? output.source : 'outputs/' + id + '.json';
  }

  /* ---------------- Trade-off ---------------- */

  function renderTradeoff() {
    if (!state.core) return;
    var chartEl = $('tradeoff-chart');
    if (!window.echarts) {
      chartEl.innerHTML = '<div class="empty-state" style="display:grid">ECharts failed to load. Check vendor/echarts.min.js.</div>';
      return;
    }
    var eligible = state.eligible.slice();
    var expected = state.core.scenarios.tradeoff.scenario_ids;
    var eligibleIds = new Set(eligible.map(function (sc) { return sc.scenario_id; }));
    var missing = expected.filter(function (id) { return !eligibleIds.has(id); });
    var guard = $('tradeoff-guard');
    if (missing.length || eligible.length !== 26) {
      guard.hidden = false;
      guard.textContent = 'Guard: the 26-ID trade-off ledger and the flag-derived eligible set disagree. ' +
        'Excluded by flags: ' + (missing.length ? missing.join(', ') : 'none') + '. Flag-derived count: ' + eligible.length + '.';
    } else {
      guard.hidden = true;
    }

    var families = [];
    eligible.forEach(function (sc) {
      if (families.indexOf(sc.family) === -1) families.push(sc.family);
    });
    families.sort(function (a, b) {
      var order = state.core.scenarios.families.map(function (f) { return f.family; });
      return order.indexOf(a) - order.indexOf(b);
    });

    var series = families.map(function (family) {
      return {
        name: shortFamily(family),
        type: 'scatter',
        symbolSize: 13,
        itemStyle: { color: familyColor(family), borderColor: '#fbf6e9', borderWidth: 1 },
        emphasis: { scale: 1.6, itemStyle: { borderColor: '#17251d', borderWidth: 2 } },
        data: eligible.filter(function (sc) { return sc.family === family; }).map(function (sc) {
          var selected = sc.scenario_id === state.selectedId;
          return {
            value: [Number(sc.metrics.objective), Number(sc.metrics.emissions_total)],
            name: sc.label,
            symbolSize: selected ? 19 : 13,
            itemStyle: selected
              ? { color: familyColor(family), borderColor: '#d98a2b', borderWidth: 3, shadowBlur: 14, shadowColor: 'rgba(217,138,43,0.55)' }
              : { color: familyColor(family), borderColor: '#fbf6e9', borderWidth: 1 },
            meta: {
              id: sc.scenario_id,
              family: sc.family_label,
              label: sc.label,
              objective: sc.metrics.objective,
              emissions: sc.metrics.emissions_total,
              national: sc.metrics.national_bcw_2050,
              share: sc.metrics.bio_share_2050
            }
          };
        })
      };
    });

    var livePoints = state.live.sessionRuns.filter(function (sc) {
      return num(sc.metrics.objective) !== null && num(sc.metrics.emissions_total) !== null;
    }).map(function (sc) {
      var selected = sc.scenario_id === state.selectedId;
      return {
        value: [Number(sc.metrics.objective), Number(sc.metrics.emissions_total)],
        name: sc.label,
        symbol: 'diamond',
        symbolSize: selected ? 19 : 14,
        itemStyle: selected
          ? { color: '#a94f31', borderColor: '#d98a2b', borderWidth: 3, shadowBlur: 14, shadowColor: 'rgba(217,138,43,0.55)' }
          : { color: '#a94f31', borderColor: '#fbf6e9', borderWidth: 1 },
        meta: {
          id: sc.scenario_id,
          family: 'Session live run — screening only',
          label: sc.label,
          objective: sc.metrics.objective,
          emissions: sc.metrics.emissions_total,
          national: sc.metrics.national_bcw_2050,
          share: sc.metrics.bio_share_2050,
          live: true
        }
      };
    });
    if (livePoints.length) {
      series.push({
        name: 'Session live runs (screening)',
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 14,
        itemStyle: { color: '#a94f31', borderColor: '#fbf6e9', borderWidth: 1 },
        emphasis: { scale: 1.6, itemStyle: { borderColor: '#17251d', borderWidth: 2 } },
        data: livePoints
      });
    }

    if (!state.tradeoffChart) {
      state.tradeoffChart = window.echarts.init(chartEl, null, { renderer: 'canvas' });
      state.tradeoffChart.on('click', function (params) {
        var meta = params && params.data && params.data.meta;
        if (!meta || !meta.id) return;
        selectScenario(meta.id);
      });
    }
    state.tradeoffChart.setOption({
      animationDuration: 500,
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      grid: { left: 72, right: 28, top: 46, bottom: 68, containLabel: true },
      legend: {
        type: 'scroll',
        bottom: 4,
        left: 'center',
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
        itemWidth: 11,
        itemHeight: 8,
        pageIconColor: '#a94f31'
      },
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: '#0c1f19',
        borderColor: '#d98a2b',
        borderWidth: 1,
        padding: 0,
        textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
        formatter: function (params) {
          var meta = params.data.meta;
          return '<div class="chart-tip"><div class="chart-tip-head"><span>' + esc(meta.id) + '</span></div>' +
            '<div class="chart-tip-title">' + esc(meta.label) + '</div>' +
            '<div class="chart-tip-sub">' + esc(meta.family) + '</div>' +
            '<div class="chart-tip-kv"><span>Objective</span><b>' + fmt(meta.objective, 2) + ' M$</b></div>' +
            '<div class="chart-tip-kv"><span>Cumulative emissions</span><b>' + fmt(meta.emissions, 3) + ' Mt CO₂</b></div>' +
            '<div class="chart-tip-kv"><span>National BCW 2050</span><b>' + fmt(meta.national, 2) + ' PJ</b></div>' +
            '<div class="chart-tip-kv"><span>Bio share 2050</span><b>' + fmtPct(meta.share, 3) + '</b></div>' +
            (meta.live ? '<div class="chart-tip-kv"><span>Grade</span><b>screening_only · not policy</b></div>' : '') + '</div>';
        }
      },
      xAxis: {
        type: 'value',
        name: 'Objective (M$)',
        nameLocation: 'middle',
        nameGap: 34,
        scale: true,
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 11, color: '#435347' },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { lineStyle: { color: '#b8a57e' } }
      },
      yAxis: {
        type: 'value',
        name: 'Cumulative emissions (Mt CO₂)',
        nameLocation: 'middle',
        nameGap: 52,
        scale: true,
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 11, color: '#435347' },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { show: false }
      },
      series: series
    }, true);

    var superseded = state.core.scenarios.scenarios.filter(function (sc) { return sc.flags.superseded_for_emissions; }).length;
    var diagnostic = state.core.scenarios.scenarios.filter(function (sc) { return sc.flags.diagnostic; }).length;
    $('tradeoff-count').textContent = eligible.length + ' eligible · ' + (state.core.scenarios.scenarios.length - eligible.length) +
      ' excluded (' + superseded + ' superseded, ' + diagnostic + ' diagnostic) · expected ledger ' + expected.length +
      (state.live.sessionRuns.length ? ' · +' + state.live.sessionRuns.length + ' live session run(s), screening only' : '');
  }

  /* ---------------- Map ---------------- */

  function quantileBreaks(values, count) {
    var sorted = values.slice().sort(function (a, b) { return a - b; });
    var breaks = [];
    for (var i = 1; i < count; i += 1) {
      var position = (sorted.length - 1) * (i / count);
      var lower = Math.floor(position);
      var upper = Math.ceil(position);
      var value = lower === upper ? sorted[lower] : sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
      if (breaks.length === 0 || value > breaks[breaks.length - 1]) breaks.push(value);
    }
    return breaks;
  }

  async function initMap() {
    if (state.mapState.initialized || state.mapState.loading || state.mapState.error) return;
    state.mapState.loading = true;
    var mapState = $('map-load-state');
    mapState.hidden = false;
    mapState.classList.remove('is-error');
    mapState.textContent = 'Loading spatial bundle (provinces, ecoprovinces, 385 tiles)…';
    if (typeof window.maplibregl === 'undefined') {
      mapState.classList.add('is-error');
      mapState.textContent = 'MapLibre GL failed to load. Check vendor/maplibre-gl.js.';
      state.mapState.error = true;
      return;
    }
    try {
      var bundle = await Promise.all([
        loadJSON('spatial/provinces.geojson'),
        loadJSON('spatial/ecoprovinces.geojson'),
        loadJSON('spatial/rsyc_tiles.geojson'),
        loadJSON('spatial/zone_nodes.json')
      ]);
      var provinces = bundle[0];
      var ecoprovinces = bundle[1];
      var tiles = bundle[2];
      var zoneNodes = bundle[3];
      requireTrue(provinces.features && provinces.features.length === 13, 'expected 13 provinces, found ' + (provinces.features || []).length);
      requireTrue(ecoprovinces.features && ecoprovinces.features.length === 36, 'expected 36 ecoprovinces, found ' + (ecoprovinces.features || []).length);
      requireTrue(tiles.features && tiles.features.length === 385, 'expected 385 RSYC tiles, found ' + (tiles.features || []).length);
      requireTrue(zoneNodes.zone_count === 13 && zoneNodes.gap_count === 11 && zoneNodes.anchor_count === 2, 'zone-node contract mismatch');

      var values = tiles.features.map(function (feature) { return Number(feature.properties.recoverable_pj_base); }).filter(Number.isFinite);
      var breaks = quantileBreaks(values, 5);
      var colors = ['#f7e7c4', '#efcd8e', '#e3a955', '#c87832', '#9c4728'];
      var fillExpression = ['step', ['coalesce', ['get', 'recoverable_pj_base'], 0], colors[0]];
      breaks.forEach(function (value, index) { fillExpression.push(value, colors[index + 1]); });

      var map = new window.maplibregl.Map({
        container: 'map',
        style: {
          version: 8,
          sources: {},
          layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#0c1f19' } }]
        },
        center: [-101, 58],
        zoom: 2.6,
        minZoom: 1.6,
        maxZoom: 9,
        attributionControl: false,
        dragRotate: false
      });
      state.mapState.map = map;
      map.addControl(new window.maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      map.addControl(new window.maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-right');

      map.on('load', function () {
        map.addSource('provinces', { type: 'geojson', data: provinces });
        map.addSource('ecoprovinces', { type: 'geojson', data: ecoprovinces });
        map.addSource('tiles', { type: 'geojson', data: tiles });

        map.addLayer({
          id: 'province-fill',
          type: 'fill',
          source: 'provinces',
          paint: { 'fill-color': '#17382c', 'fill-opacity': 0.92 }
        });
        map.addLayer({
          id: 'province-line',
          type: 'line',
          source: 'provinces',
          paint: { 'line-color': '#cfc09c', 'line-width': 1.15, 'line-opacity': 0.82 }
        });
        map.addLayer({
          id: 'tiles-fill',
          type: 'fill',
          source: 'tiles',
          paint: { 'fill-color': fillExpression, 'fill-opacity': 0.82 }
        });
        map.addLayer({
          id: 'ecoprovince-line',
          type: 'line',
          source: 'ecoprovinces',
          paint: { 'line-color': '#8aa892', 'line-width': 0.7, 'line-opacity': 0.55, 'line-dasharray': [3, 2] }
        });
        map.addLayer({
          id: 'tiles-line',
          type: 'line',
          source: 'tiles',
          paint: { 'line-color': '#5b351f', 'line-width': 0.45, 'line-opacity': 0.6 }
        });

        map.on('click', 'tiles-fill', function (event) {
          var feature = event.features && event.features[0];
          if (!feature) return;
          var props = feature.properties || {};
          var html = '<h4>RSYC tile ' + esc(props.tile_id) + '</h4>' +
            '<div class="popup-grid">' +
              '<span>Province</span><b>' + esc(props.provinces) + '</b>' +
              '<span>Area</span><b>' + fmt(props.area_ha / 1000, 1) + ' kha</b>' +
              '<span>Volume</span><b>' + fmtCompact(props.volume_m3, 1) + ' m³</b>' +
              '<span>Recoverable ODT</span><b>' + fmtCompact(props.recoverable_odt_base, 1) + '</b>' +
              '<span>Recoverable base</span><b>' + fmt(props.recoverable_pj_base, 2) + ' PJ</b>' +
              '<span>Over capacity</span><b>' + (props.over_capacity ? 'yes' : 'no') + '</b>' +
            '</div>';
          new window.maplibregl.Popup({ closeButton: true, maxWidth: '290px' })
            .setLngLat(event.lngLat).setHTML(html).addTo(map);
        });
        map.on('mouseenter', 'tiles-fill', function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'tiles-fill', function () { map.getCanvas().style.cursor = ''; });

        applyMapVisibility();
        mapState.hidden = true;
        $('map-legend').hidden = false;
        renderMapLegend(breaks, colors);
        renderMapStats(provinces, ecoprovinces, tiles, zoneNodes);
        addZoneAnchors(map, zoneNodes);
        applyMapVisibility();
        state.mapState.initialized = true;
        state.mapState.loading = false;
        state.mapState.data = { provinces: provinces, ecoprovinces: ecoprovinces, tiles: tiles, zoneNodes: zoneNodes, breaks: breaks, colors: colors };
        map.resize();
        map.fitBounds(CANADA_BOUNDS, { padding: CANADA_FIT_OPTIONS.padding, duration: 0 });
      });
      $('map-fit').onclick = function () { map.fitBounds(CANADA_BOUNDS, { padding: CANADA_FIT_OPTIONS.padding, duration: 700 }); };
    } catch (error) {
      state.mapState.loading = false;
      state.mapState.error = true;
      mapState.classList.add('is-error');
      mapState.textContent = 'Spatial bundle failed its guard: ' + error.message;
    }
  }

  function addZoneAnchors(map, zoneNodes) {
    state.mapState.markers.forEach(function (marker) { marker.remove(); });
    state.mapState.markers = [];
    var anchors = zoneNodes.zones.filter(function (zone) { return zone.anchor; });
    anchors.forEach(function (zone) {
      var element = document.createElement('div');
      element.className = 'anchor-label';
      element.textContent = zone.zone_id;
      var popup = new window.maplibregl.Popup({ closeButton: true, offset: 18 })
        .setHTML('<h4>C2070 zone ' + esc(zone.zone_id) + '</h4>' +
          '<div class="popup-grid"><span>Node</span><b>' + esc(zone.node_id) + '</b>' +
          '<span>Lon / lat</span><b>' + fmt(zone.lon, 4) + ', ' + fmt(zone.lat, 4) + '</b>' +
          '<span>Weight basis</span><b>' + esc(zone.weight_basis) + '</b></div>' +
          '<p style="margin-top:6px;color:#6c786c;font-size:11px">Authoritative anchor; no zone polygon is drawn.</p>');
      var marker = new window.maplibregl.Marker({ element: element, anchor: 'bottom' })
        .setLngLat([zone.lon, zone.lat]).setPopup(popup).addTo(map);
      state.mapState.markers.push(marker);
    });
  }

  function applyMapVisibility() {
    var map = state.mapState.map;
    if (!map || !map.getLayer('province-fill')) return;
    var provinces = $('layer-provinces').checked;
    var ecoprovinces = $('layer-ecoprovinces').checked;
    var tiles = $('layer-tiles').checked;
    var anchors = $('layer-anchors').checked;
    map.setLayoutProperty('province-fill', 'visibility', provinces ? 'visible' : 'none');
    map.setLayoutProperty('province-line', 'visibility', provinces ? 'visible' : 'none');
    map.setLayoutProperty('ecoprovince-line', 'visibility', ecoprovinces ? 'visible' : 'none');
    map.setLayoutProperty('tiles-fill', 'visibility', tiles ? 'visible' : 'none');
    map.setLayoutProperty('tiles-line', 'visibility', tiles ? 'visible' : 'none');
    state.mapState.markers.forEach(function (marker) {
      marker.getElement().style.display = anchors ? '' : 'none';
    });
  }

  function renderMapLegend(breaks, colors) {
    var labels = [];
    var previous = 0;
    breaks.forEach(function (value, index) {
      labels.push({ color: colors[index], text: fmt(previous, 1) + ' – ' + fmt(value, 1) });
      previous = value;
    });
    labels.push({ color: colors[colors.length - 1], text: fmt(previous, 1) + ' +' });
    $('map-legend-items').innerHTML = labels.map(function (item) {
      return '<div class="legend-class"><i style="background:' + item.color + '"></i><span>' + item.text + '</span></div>';
    }).join('');
  }

  function renderMapStats(provinces, ecoprovinces, tiles, zoneNodes) {
    var totalPj = tiles.features.reduce(function (sum, feature) {
      var value = Number(feature.properties.recoverable_pj_base);
      return sum + (Number.isFinite(value) ? value : 0);
    }, 0);
    $('map-stats').innerHTML =
      '<div><dt>Provinces</dt><dd>' + provinces.features.length + '</dd></div>' +
      '<div><dt>Ecoprovinces</dt><dd>' + ecoprovinces.features.length + '</dd></div>' +
      '<div><dt>RSYC tiles</dt><dd>' + tiles.features.length + '</dd></div>' +
      '<div><dt>Tile recoverable base</dt><dd>' + fmt(totalPj, 1) + ' PJ</dd></div>' +
      '<div><dt>Zone anchors</dt><dd>' + zoneNodes.anchor_count + ' / ' + zoneNodes.zone_count + '</dd></div>' +
      '<div><dt>Zone gaps</dt><dd>' + zoneNodes.gap_count + '</dd></div>';
    var gaps = zoneNodes.zones.filter(function (zone) { return zone.gap; });
    $('gap-zones').innerHTML = gaps.map(function (zone) {
      return '<span class="zone-chip" title="' + esc(zone.gap_reason) + '">' + esc(zone.zone_id) + '</span>';
    }).join('');
  }

  /* ---------------- OSeCAN ---------------- */

  function populateOsecanControls() {
    var harvest = $('osecan-harvest');
    harvest.innerHTML = [
      ['100', '100 Mm³ (scenario low)'],
      ['119', '119 Mm³ (observed 2024, base)'],
      ['150', '150 Mm³ (scenario high)'],
      ['200', '200 Mm³ (supply-curve basis)']
    ].map(function (row) { return '<option value="' + row[0] + '">' + row[1] + '</option>'; }).join('');
    harvest.value = String(state.osecan.harvest);

    var recovery = $('osecan-recovery');
    recovery.innerHTML = [
      ['0.35', '0.35 — low recovery'],
      ['0.6', '0.60 — base recovery'],
      ['0.75', '0.75 — high recovery']
    ].map(function (row) { return '<option value="' + row[0] + '">' + row[1] + '</option>'; }).join('');
    recovery.value = String(state.osecan.recovery);

    var cost = $('osecan-cost');
    cost.innerHTML = '<option value="weighted">Weighted across consumed tiers</option>' +
      state.core.supply.tiers.map(function (tier) {
        return '<option value="' + tier.tier + '">' + tier.tier + ' — $' + fmt(tier.variable_cost_usd_per_gj, 3) + '/GJ</option>';
      }).join('');
    cost.value = state.osecan.cost;
  }

  function bindOsecanControls() {
    populateOsecanControls();
    $('osecan-harvest').addEventListener('change', function (event) {
      state.osecan.harvest = Number(event.target.value);
      renderOsecan();
    });
    $('osecan-recovery').addEventListener('change', function (event) {
      state.osecan.recovery = Number(event.target.value);
      renderOsecan();
    });
    $('osecan-cost').addEventListener('change', function (event) {
      state.osecan.cost = event.target.value;
      renderOsecan();
    });
    $('export-limits').addEventListener('click', exportActivityLimits);
    $('export-costs').addEventListener('click', exportVariableCosts);
    $('osecan-queue').addEventListener('click', queueOsecanScenario);
    qsa('.layer-toggles input').forEach(function (input) {
      input.addEventListener('change', applyMapVisibility);
    });
  }

  function computeOsecan() {
    var core = state.core;
    var supply = core.supply;
    var basis = supply.supply_basis;
    var observed = Number(basis.harvest_basis.observed_national_harvest_m3);
    var referenceIndex = 2050 - YEAR_FIRST;
    var referenceNational = Number(basis.national_cap_pj_by_year.base[referenceIndex]);
    var factor = (state.osecan.harvest * 1e6 / observed) * (state.osecan.recovery / 0.6);
    var requested = referenceNational * factor;
    var tiers = supply.tiers.map(function (tier) {
      return {
        tier: tier.tier,
        technology: tier.technology,
        cap: Number(tier.incremental_cap_pj),
        cost: Number(tier.variable_cost_usd_per_gj),
        costBasis: tier.cost_basis,
        allocated: 0
      };
    });
    var tierSum = tiers.reduce(function (sum, tier) { return sum + tier.cap; }, 0);
    var remaining = Math.min(requested, tierSum);
    tiers.forEach(function (tier) {
      var take = Math.max(0, Math.min(remaining, tier.cap));
      tier.allocated = take;
      remaining -= take;
    });
    var delivered = tiers.reduce(function (sum, tier) { return sum + tier.allocated; }, 0);
    var weightedCost = delivered > 0
      ? tiers.reduce(function (sum, tier) { return sum + tier.allocated * tier.cost; }, 0) / delivered
      : 0;
    var selectedCost = state.osecan.cost === 'weighted'
      ? weightedCost
      : Number((tiers.filter(function (tier) { return tier.tier === state.osecan.cost; })[0] || tiers[0]).cost);
    var bcBase = Number(basis.bc_zonal_total_pj);
    var bc = Math.min(delivered, bcBase * factor);
    var rest = Math.max(0, delivered - bc);
    var over = requested > tierSum + 1e-6;
    var tierSumCheck = Math.abs(tiers.reduce(function (sum, tier) { return sum + tier.cap; }, 0) - Number(supply.tier_sum_pj)) <= 1e-6;
    var residual = Number(supply.reconciliation.max_abs_tier_residual_pj);
    return {
      harvest: state.osecan.harvest,
      recovery: state.osecan.recovery,
      factor: factor,
      observed: observed,
      referenceNational: referenceNational,
      requested: requested,
      delivered: delivered,
      bc: bc,
      rest: rest,
      bcBase: bcBase,
      tierSum: tierSum,
      tiers: tiers,
      weightedCost: weightedCost,
      selectedCost: selectedCost,
      over: over,
      tierSumCheck: tierSumCheck,
      residual: residual,
      costMode: state.osecan.cost
    };
  }

  function renderOsecan() {
    if (!state.core) return;
    var model = computeOsecan();
    state.osecan.lastModel = model;
    $('osecan-national').textContent = fmt(model.delivered, 2);
    $('osecan-bc').textContent = fmt(model.bc, 2);
    $('osecan-rest').textContent = fmt(model.rest, 2);
    $('osecan-cost-value').textContent = '$' + fmt(model.selectedCost, 3);

    var validation = $('osecan-validation');
    if (model.tierSumCheck && !model.over && model.residual <= 1e-6) {
      validation.className = 'validation-strip';
      validation.innerHTML = '✓ tier-sum validated · 4 tiers = ' + fmt(model.tierSum, 3) + ' PJ · max reconciliation residual ' + fmt(model.residual, 6) + ' PJ';
    } else {
      validation.className = 'validation-strip is-warn';
      validation.innerHTML = (model.over ? '! requested ' + fmt(model.requested, 2) + ' PJ exceeds tier-sum ceiling ' + fmt(model.tierSum, 2) + ' PJ — output clamped. ' : '') +
        (model.tierSumCheck ? '' : '! tier sum does not reconcile. ') +
        (model.residual > 1e-6 ? '! reconciliation residual ' + fmt(model.residual, 6) + ' PJ. ' : '');
    }

    var note = 'Reference 2050 cap: ' + fmt(model.referenceNational, 2) + ' PJ at 119 Mm³ / 0.60. ' +
      'Requested target: ' + fmt(model.requested, 2) + ' PJ' + (model.over ? ' → clamped to ' + fmt(model.delivered, 2) + ' PJ.' : '.') +
      ' BC/rest split uses the committed BC zonal total of ' + fmt(model.bcBase, 2) + ' PJ, scaled proportionally.';
    $('osecan-result-note').textContent = note;

    var stack = model.tiers.map(function (tier) {
      if (tier.allocated <= 0) return '';
      var width = model.delivered > 0 ? (tier.allocated / model.delivered) * 100 : 0;
      return '<div class="tier-seg" style="width:' + width + '%;background:' + TIER_COLORS[tier.tier] +
        '" title="' + tier.tier + ': ' + fmt(tier.allocated, 2) + ' PJ"><span>' + tier.tier + '</span></div>';
    }).join('');
    $('osecan-tier-stack').innerHTML = stack || '<div class="tier-seg" style="width:100%;background:#d6c6a5"></div>';

    var rows = model.tiers.map(function (tier) {
      return '<tr><td>' + tier.tier + '</td><td>' + esc(tier.technology) + '</td>' +
        '<td class="num">' + fmt(tier.cap, 3) + '</td>' +
        '<td class="num">' + fmt(tier.allocated, 3) + '</td>' +
        '<td class="num">$' + fmt(tier.cost, 3) + '</td>' +
        '<td>' + esc(tier.costBasis) + '</td></tr>';
    }).join('');
    $('osecan-tier-table').innerHTML = '<caption>Tier allocation at ' + model.harvest + ' Mm³ / recovery ' + fmt(model.recovery, 2) + '</caption>' +
      '<thead><tr><th>Tier</th><th>Technology</th><th class="num">Cap (PJ)</th><th class="num">Allocated (PJ)</th><th class="num">Cost ($/GJ)</th><th>Cost basis</th></tr></thead><tbody>' + rows + '</tbody>';

    var slug = 'h' + model.harvest + 'm_r' + String(Math.round(model.recovery * 100)).padStart(3, '0');
    $('export-note').innerHTML = 'REGION,TECHNOLOGY,YEAR,VALUE · national tier rows, 2021—2070 · ' +
      'activity limits in PJ fuel/yr · variable cost in USD/GJ · <span class="mono">SUPBCW_activity_limits_' + slug + '.csv</span>';
  }

  function tierCsvRows(model, valueSelector) {
    var rows = [];
    YEARS.forEach(function (year) {
      model.tiers.forEach(function (tier) {
        rows.push(['CAN', tier.technology, year, valueSelector(tier)]);
      });
    });
    return rows;
  }

  function csvCell(value) {
    var text = String(value);
    if (/[",\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function downloadCsv(filename, rows) {
    var lines = ['REGION,TECHNOLOGY,YEAR,VALUE'];
    rows.forEach(function (row) {
      lines.push(row.map(csvCell).join(','));
    });
    var blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
    announce('Downloaded ' + filename);
  }

  function exportActivityLimits() {
    var model = state.osecan.lastModel || computeOsecan();
    var slug = 'h' + model.harvest + 'm_r' + String(Math.round(model.recovery * 100)).padStart(3, '0');
    var rows = tierCsvRows(model, function (tier) { return Number(tier.allocated).toFixed(6); });
    downloadCsv('SUPBCW_activity_limits_' + slug + '.csv', rows);
  }

  function exportVariableCosts() {
    var model = state.osecan.lastModel || computeOsecan();
    var slug = 'h' + model.harvest + 'm_r' + String(Math.round(model.recovery * 100)).padStart(3, '0');
    var rows = tierCsvRows(model, function (tier) { return Number(tier.cost).toFixed(6); });
    downloadCsv('SUPBCW_variable_cost_' + slug + '.csv', rows);
  }

  /* ---------------- Live runner ---------------- */

  function liveApi() { return window.ForestLedgerApi || null; }

  function syncHarvestCustom() {
    var select = $('run-harvest');
    var customField = $('run-harvest-custom-field');
    var customInput = $('run-harvest-custom');
    if (!select || !customField || !customInput) return;
    var custom = select.value === 'custom';
    customField.hidden = !custom;
    customInput.required = custom;
    if (custom && !customInput.value) {
      customInput.value = String(Math.round(state.live.defaults.default_harvest_level_m3_per_year || 118977339));
    }
  }

  function applyRunDefaults(defaults) {
    var d = defaults || FALLBACK_DEFAULTS;
    state.live.defaults = d;
    var harvest = d.default_harvest_level_m3_per_year || 118977339;
    var levelKey = String(Math.round(harvest / 1e6));
    if ($('run-harvest')) {
      $('run-harvest').value = Object.prototype.hasOwnProperty.call(d.harvest_levels_m3_per_year || {}, levelKey) ? levelKey : '119';
    }
    if ($('run-harvest-custom')) $('run-harvest-custom').value = String(harvest);
    if ($('run-recovery')) $('run-recovery').value = String(d.default_recovery_fraction == null ? 0.6 : d.default_recovery_fraction);
    if ($('run-cost')) $('run-cost').value = d.cost_basis || 'adopted_rest_of_canada_curve';
    if ($('run-carbon')) $('run-carbon').value = String(d.carbon_price_usd_per_tco2e_2026 == null ? 115 : d.carbon_price_usd_per_tco2e_2026);
    if ($('run-hydro')) $('run-hydro').value = d.hydro_mode || 'cap_2021';
    if ($('run-pwrbio')) $('run-pwrbio').value = String(d.pwr_bio_cap_fraction == null ? 0.05 : d.pwr_bio_cap_fraction);
    if ($('run-nuclear')) $('run-nuclear').value = String(d.nuclear_cap_gw == null ? 0 : d.nuclear_cap_gw);
    if ($('run-wind-cost')) $('run-wind-cost').value = String(d.wind_cost_usd_per_gj == null ? 3 : d.wind_cost_usd_per_gj);
    if ($('run-wind-cap')) $('run-wind-cap').value = String(d.wind_cap_fraction == null ? 0.2 : d.wind_cap_fraction);
    if ($('run-t3')) $('run-t3').value = String(d.t3_cap_pj == null ? 39.873714 : d.t3_cap_pj);
    if ($('run-t4')) $('run-t4').value = String(d.t4_cap_pj == null ? 64.265464 : d.t4_cap_pj);
    syncHarvestCustom();
    updateFormDisabled();
  }

  function updateFormDisabled() {
    var mode = state.live.mode;
    var disabled = state.live.busy || mode === 'checking' || mode === 'static' || mode === 'readonly' || mode === 'degraded';
    qsa('#run-form input, #run-form select').forEach(function (input) { input.disabled = disabled; });
    var activeRun = Boolean(state.live.activeRun);
    var create = $('run-create');
    var queue = $('run-queue');
    if (create) create.disabled = disabled || activeRun;
    if (queue) queue.disabled = state.live.busy || mode !== 'live' || !state.live.createdScenario || activeRun;
    var osecanQueue = $('osecan-queue');
    if (osecanQueue) osecanQueue.disabled = state.live.busy || mode !== 'live' || activeRun;
  }

  function setModeBadge(mode, detail) {
    var badge = $('mode-badge');
    var text = $('mode-badge-text');
    if (!badge || !text) return;
    badge.classList.remove('is-checking', 'is-live', 'is-static', 'is-readonly');
    var label = 'Checking live API…';
    if (mode === 'live' || mode === 'no-runs') {
      badge.classList.add('is-live');
      label = mode === 'live' ? 'Live API · runner ready' : 'Live API · runs disabled';
    } else if (mode === 'readonly') {
      badge.classList.add('is-readonly');
      label = 'Live API · read-only';
    } else if (mode === 'static') {
      badge.classList.add('is-static');
      label = 'Static mode · no backend';
    } else if (mode === 'degraded') {
      badge.classList.add('is-static');
      label = 'Live API · defaults unavailable';
    }
    text.textContent = label;
    badge.title = detail || label;
  }

  function renderRunMode() {
    var mode = state.live.mode;
    var banner = $('run-mode-banner');
    var title = $('run-mode-title');
    var text = $('run-mode-text');
    if (!banner || !title || !text) return;
    banner.classList.remove('is-live', 'is-readonly');
    if (mode === 'live') {
      banner.classList.add('is-live');
      title.textContent = 'Live runner ready — screening only.';
      text.textContent = 'Create a scenario, queue it, and watch SSE status. Every completed run is added to the session rail with screening_only and policy_grade=false.';
      setModeBadge('live', 'POST /api/scenarios and /api/runs are available.');
    } else if (mode === 'no-runs') {
      banner.classList.add('is-live');
      title.textContent = 'Live API connected; run queueing is disabled.';
      text.textContent = 'Read-only scenario and constraint endpoints respond, but the server needs --allow-runs before POST /api/runs will accept work. Static views still work.';
      setModeBadge('no-runs', 'The live API is up without --allow-runs.');
    } else if (mode === 'readonly') {
      banner.classList.add('is-readonly');
      title.textContent = 'Live API is read-only.';
      text.textContent = 'Create and queue are disabled. The committed static views and OSeCAN exports remain available.';
      setModeBadge('readonly', 'The server is running with --read-only.');
    } else if (mode === 'degraded') {
      title.textContent = 'Live API connected, but screening defaults failed to load.';
      text.textContent = 'The canonical screening_v0 payload did not validate. Create and queue are disabled rather than running with guessed defaults.';
      setModeBadge('degraded', 'GET /api/constraints failed validation.');
    } else if (mode === 'static') {
      title.textContent = 'Static mode — no backend reachable.';
      text.textContent = 'Showing the committed iteration-0 scenarios and outputs. Start the FastAPI server with --allow-runs to create and queue live screening scenarios.';
      setModeBadge('static', 'GET /api/health failed; using precomputed data only.');
    } else {
      title.textContent = 'Checking live API…';
      text.textContent = 'Static views remain available if no backend responds.';
      setModeBadge('checking');
    }
    updateFormDisabled();

    var osecanBanner = $('osecan-run-banner');
    var osecanNote = $('osecan-live-note');
    var osecanCopy = {
      live: {
        banner: '<span class="needs-run-mark">i</span><strong>Live runner available — screening only.</strong> “Queue this scenario” creates a live scenario from the calculator levers; every result is policy_grade=false.',
        note: 'Queue this scenario is available.',
        status: 'Defaults loaded from screening_v0. No run yet.'
      },
      'no-runs': {
        banner: '<span class="needs-run-mark">!</span><strong>Live API connected — run queueing is disabled.</strong> Start the server with --allow-runs to create live screening scenarios; static exports remain illustrative only.',
        note: 'Queue this scenario needs a server started with --allow-runs.',
        status: 'Live API is up, but run queueing is disabled without --allow-runs.'
      },
      readonly: {
        banner: '<span class="needs-run-mark">!</span><strong>Live API is read-only.</strong> Create and queue are disabled; the committed static views and OSeCAN exports remain available.',
        note: 'Queue this scenario is unavailable on a read-only server.',
        status: 'Read-only server: create and queue are disabled.'
      },
      degraded: {
        banner: '<span class="needs-run-mark">!</span><strong>Screening defaults unavailable.</strong> The canonical screening_v0 payload failed validation, so live creation is disabled rather than guessing defaults.',
        note: 'Queue this scenario is unavailable until screening defaults load.',
        status: 'Could not load screening_v0 defaults; create and queue are disabled.'
      },
      static: {
        banner: '<span class="needs-run-mark">!</span><strong>Static mode — no live solver.</strong> Static exports are illustrative inputs, not solved CLEWS results.',
        note: 'Live queueing is unavailable in static mode.',
        status: 'Static mode: create and queue are disabled.'
      }
    };
    var copy = osecanCopy[mode] || {
      banner: '<span class="needs-run-mark">!</span><strong>Checking for a live API.</strong> Static exports remain available while the health probe runs.',
      note: 'Live queueing is unavailable while the API is checked.',
      status: 'Checking for a live runner.'
    };
    if (osecanBanner) osecanBanner.innerHTML = copy.banner;
    if (osecanNote) osecanNote.textContent = copy.note;
    if (!state.live.activeRun && !state.live.createdScenario) {
      setFormStatus(copy.status, false);
    }
  }

  function setFormStatus(message, isError) {
    var status = $('run-form-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('is-error', Boolean(isError));
  }

  function setFormError(message) {
    var error = $('run-form-error');
    if (!error) return;
    if (!message) {
      error.hidden = true;
      error.textContent = '';
      return;
    }
    error.hidden = false;
    error.textContent = message;
  }

  function fieldNumber(id, label) {
    var raw = $(id).value.trim();
    var value = Number(raw);
    if (raw === '' || !Number.isFinite(value)) throw new Error(label + ' must be a finite number.');
    return value;
  }

  function slugify(label) {
    var base = String(label).toLowerCase().normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 38);
    if (!base) base = 'live-scenario';
    return base + '-' + Date.now().toString(36);
  }

  function readRunForm() {
    var d = state.live.defaults || FALLBACK_DEFAULTS;
    var label = $('run-label').value.trim();
    if (!label) throw new Error('Scenario label is required.');
    if (label.length > 200) throw new Error('Scenario label must be 200 characters or fewer.');
    var choice = $('run-harvest').value;
    var level = choice === 'custom' ? fieldNumber('run-harvest-custom', 'Custom harvest') : Number(d.harvest_levels_m3_per_year[choice]);
    if (!Number.isFinite(level) || level <= 0) throw new Error('Harvest must be a positive m³/year value.');
    var path = choice === 'custom' ? 'custom' : (d.harvest_path_by_level[choice] || 'custom');
    var recovery = fieldNumber('run-recovery', 'Recovery fraction');
    if (recovery <= 0 || recovery > 1) throw new Error('Recovery fraction must be greater than 0 and at most 1.');
    var carbon = fieldNumber('run-carbon', 'Carbon price 2026');
    if (carbon < 0) throw new Error('Carbon price must be non-negative.');
    var pwrbio = fieldNumber('run-pwrbio', 'PWRBIO cap');
    var windCap = fieldNumber('run-wind-cap', 'Wind cap');
    if (pwrbio < 0 || pwrbio > 1) throw new Error('PWRBIO cap must be between 0 and 1.');
    if (windCap < 0 || windCap > 1) throw new Error('Wind cap must be between 0 and 1.');
    var numbers = {
      nuclear: fieldNumber('run-nuclear', 'Nuclear cap'),
      windCost: fieldNumber('run-wind-cost', 'Wind cost'),
      t3: fieldNumber('run-t3', 'T3 cap'),
      t4: fieldNumber('run-t4', 'T4 cap')
    };
    Object.keys(numbers).forEach(function (key) {
      if (numbers[key] < 0) throw new Error('Caps and costs must be non-negative.');
    });
    return {
      label: label,
      harvestChoice: choice,
      level: level,
      path: path,
      recovery: recovery,
      cost: $('run-cost').value,
      carbon: carbon,
      hydro: $('run-hydro').value,
      pwrbio: pwrbio,
      nuclear: numbers.nuclear,
      windCost: numbers.windCost,
      windCap: windCap,
      t3: numbers.t3,
      t4: numbers.t4
    };
  }

  function buildScenarioPayload() {
    var form = readRunForm();
    var d = state.live.defaults || FALLBACK_DEFAULTS;
    var canonical = state.live.constraints && state.live.constraints.default_set_id
      ? state.live.constraints.default_set_id
      : 'screening_v0';
    var provenance = (state.live.constraints && state.live.constraints.provenance) || {};
    var payload = {
      scenario_id: slugify(form.label),
      label: form.label,
      description: 'Created in the Forest Ledger Run panel from screening_v0 defaults. Live screening only; not policy grade.',
      schema_version: 'clews.scenario.v1',
      harvest: {
        level_m3_per_year: form.level,
        path: form.path,
        recovery_fraction: form.recovery,
        cost_basis: form.cost,
        supply_curve: 'tiered'
      },
      energy: {
        carbon_price_schedule: [
          { year: 2021, price_usd_per_tco2e: Number(d.carbon_price_usd_per_tco2e_2021 == null ? 95 : d.carbon_price_usd_per_tco2e_2021) },
          { year: 2026, price_usd_per_tco2e: form.carbon }
        ],
        hydro_mode: form.hydro,
        pwr_bio_cap_fraction: form.pwrbio,
        nuclear_cap_gw: form.nuclear,
        wind_cost_usd_per_gj: form.windCost,
        wind_cap_fraction: form.windCap,
        t3_cap_pj: form.t3,
        t4_cap_pj: form.t4,
        generic_bio_cost_usd_per_gj: Number(d.generic_bio_cost_usd_per_gj == null ? 3 : d.generic_bio_cost_usd_per_gj)
      },
      constraints: { set_id: canonical },
      provenance: {
        base_data_commit: provenance.base_data_commit || 'session-live-ui',
        model_commit: provenance.model_commit || LIVE_MODEL_COMMIT,
        created_utc: new Date().toISOString()
      }
    };
    return { payload: payload, form: form };
  }

  async function createScenarioFromForm(autoQueue) {
    if (state.live.busy) return null;
    if (state.live.activeRun) {
      setFormError('Wait for the active run to finish before creating another scenario.');
      return null;
    }
    var client = liveApi();
    if (!client) {
      setFormError('Live API client is unavailable.');
      return null;
    }
    if (state.live.mode !== 'live' && state.live.mode !== 'no-runs') {
      setFormError('Create is unavailable in ' + state.live.mode + ' mode.');
      return null;
    }
    var built;
    try {
      built = buildScenarioPayload();
      setFormError('');
    } catch (error) {
      setFormError(error.message);
      setFormStatus(error.message, true);
      return null;
    }
    state.live.busy = true;
    updateFormDisabled();
    setFormStatus('Creating scenario ' + built.payload.scenario_id + '…', false);
    var created = null;
    try {
      created = await client.createScenario(built.payload);
      state.live.createdScenario = created;
      state.live.lastForm = built.form;
      state.live.activeRun = null;
      $('run-meta-scenario').textContent = created.scenario_id;
      setRunState('created', 'created');
      setFormStatus('Created ' + created.scenario_id + '. Queue it to start the runner.', false);
      announce('Created live scenario ' + created.label + '.');
      renderRunMode();
    } catch (error) {
      setFormError(error.message);
      setFormStatus('Create failed: ' + error.message, true);
    } finally {
      state.live.busy = false;
      updateFormDisabled();
    }
    if (created && autoQueue) await queueCreatedScenario();
    return created;
  }

  async function queueCreatedScenario() {
    var client = liveApi();
    var created = state.live.createdScenario;
    if (!client || !created || state.live.activeRun) return;
    if (state.live.mode !== 'live') {
      setFormError('Queue is disabled; the server was not started with --allow-runs.');
      return;
    }
    if (state.live.pollTimer) {
      window.clearInterval(state.live.pollTimer);
      state.live.pollTimer = null;
    }
    state.live.pollFailures = 0;
    state.live.finalizingRunId = null;
    if (state.live.eventSource) {
      state.live.eventSource.close();
      state.live.eventSource = null;
    }
    state.live.busy = true;
    updateFormDisabled();
    setFormStatus('Queueing ' + created.scenario_id + '…', false);
    try {
      var run = await client.queueRun(created.scenario_id);
      state.live.activeRun = run;
      state.live.currentJob = run;
      state.live.activeForm = state.live.lastForm;
      state.live.runStartedAt = Date.now();
      state.live.logLines = [];
      state.live.logCount = 0;
      renderRunLog();
      $('run-policy-flag').hidden = false;
      $('run-output-summary').hidden = true;
      $('run-output-summary').innerHTML = '';
      $('run-meta-scenario').textContent = created.scenario_id;
      $('run-meta-run').textContent = run.run_id;
      setRunState(run.status || 'queued', run.status || 'queued');
      startElapsed();
      setFormStatus('Queued ' + run.run_id + '; connecting to the event stream…', false);
      announce('Queued live run ' + run.run_id + '.');
      subscribeToRun(run.run_id);
    } catch (error) {
      setFormError(error.message);
      setFormStatus('Queue failed: ' + error.message, true);
    } finally {
      state.live.busy = false;
      updateFormDisabled();
    }
  }

  function startElapsed() {
    stopElapsed();
    updateElapsed();
    state.live.elapsedTimer = window.setInterval(updateElapsed, 1000);
  }

  function stopElapsed() {
    if (state.live.elapsedTimer) {
      window.clearInterval(state.live.elapsedTimer);
      state.live.elapsedTimer = null;
    }
    updateElapsed();
  }

  function updateElapsed() {
    var out = $('run-meta-elapsed');
    if (!out) return;
    if (!state.live.runStartedAt) {
      out.textContent = '—';
      return;
    }
    var seconds = Math.max(0, Math.round((Date.now() - state.live.runStartedAt) / 1000));
    if (seconds < 60) out.textContent = seconds + ' s';
    else out.textContent = Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  }

  function setRunState(status, label) {
    var text = $('run-state');
    var badge = $('run-state-badge');
    if (text) text.textContent = label || status || 'idle';
    if (!badge) return;
    badge.textContent = status || 'idle';
    badge.classList.remove('is-running', 'is-completed', 'is-failed');
    if (status === 'queued' || status === 'running') badge.classList.add('is-running');
    if (status === 'completed') badge.classList.add('is-completed');
    if (status === 'failed' || status === 'interrupted' || status === 'not_implemented') badge.classList.add('is-failed');
  }

  function formatLogTime(ts) {
    var date = ts ? new Date(ts) : new Date();
    if (Number.isNaN(date.getTime())) return '--:--:--';
    return date.toTimeString().slice(0, 8);
  }

  function appendRunLog(level, event, message, ts) {
    state.live.logLines.push({
      level: level || 'info',
      event: event || 'log',
      message: message || '',
      ts: ts || new Date().toISOString()
    });
    if (state.live.logLines.length > 250) state.live.logLines.shift();
    state.live.logCount += 1;
    renderRunLog();
  }

  function renderRunLog() {
    var log = $('run-log');
    if (!log) return;
    if (!state.live.logLines.length) {
      log.textContent = state.live.activeRun ? 'Waiting for runner events…' : 'No live run yet.';
      $('run-log-count').textContent = '0 events';
      return;
    }
    log.innerHTML = state.live.logLines.map(function (line) {
      var cls = line.level === 'error' ? 'log-error' : (line.level === 'warning' ? 'log-warning' : (line.event === 'completed' ? 'log-completed' : ''));
      return '<span class="' + cls + '">' + esc(formatLogTime(line.ts)) + ' · ' + esc(line.event) + ' · ' + esc(line.message) + '</span>';
    }).join('\n');
    log.scrollTop = log.scrollHeight;
    $('run-log-count').textContent = state.live.logCount + ' event' + (state.live.logCount === 1 ? '' : 's');
  }

  function isTerminalStatus(status) {
    return ['completed', 'failed', 'interrupted', 'not_implemented'].indexOf(status) !== -1;
  }

  function activeRunMatches(runId) {
    return Boolean(state.live.activeRun && state.live.activeRun.run_id === runId);
  }

  function clearPollTimer() {
    if (!state.live.pollTimer) return;
    window.clearInterval(state.live.pollTimer);
    state.live.pollTimer = null;
  }

  function subscribeToRun(runId) {
    var client = liveApi();
    if (!client) return;
    if (state.live.eventSource) state.live.eventSource.close();
    state.live.eventSource = client.subscribe(runId, {
      onEvent: function (type, event) {
        if (!activeRunMatches(runId)) return;
        appendRunLog(event.level, event.event, event.message, event.ts);
        if (event.event === 'started') {
          setRunState('running', 'running');
          setFormStatus('Runner started. Streaming logs…', false);
          updateFormDisabled();
        }
        if (isTerminalStatus(event.event)) {
          if (state.live.eventSource) {
            state.live.eventSource.close();
            state.live.eventSource = null;
          }
          stopElapsed();
          finalizeRun(runId);
        }
      },
      onOpen: function () {
        if (!activeRunMatches(runId)) return;
        // A reconnect can race with polling; let the stream own status again.
        clearPollTimer();
        state.live.pollFailures = 0;
      },
      onError: function () {
        if (!activeRunMatches(runId)) return;
        setFormStatus('Event stream disconnected; polling run status…', true);
        startPolling(runId);
      }
    });
  }

  function startPolling(runId) {
    if (!activeRunMatches(runId)) return;
    if (state.live.pollTimer) return;
    state.live.pollFailures = 0;
    setRunState('running', 'running');
    state.live.pollTimer = window.setInterval(async function () {
      if (!activeRunMatches(runId)) {
        clearPollTimer();
        return;
      }
      try {
        var job = await liveApi().getRun(runId);
        if (!activeRunMatches(runId)) {
          clearPollTimer();
          return;
        }
        if (isTerminalStatus(job.status)) {
          clearPollTimer();
          stopElapsed();
          finalizeRun(runId);
        }
      } catch (error) {
        state.live.pollFailures += 1;
        if (state.live.pollFailures < 20) return;
        clearPollTimer();
        if (state.live.eventSource) {
          state.live.eventSource.close();
          state.live.eventSource = null;
        }
        stopElapsed();
        setFormError('Run status polling failed: ' + error.message);
        setFormStatus('Run status polling stopped after repeated failures. Refresh the run list to reconnect.', true);
        renderRunOutputError({ status: 'unavailable', run_id: runId }, 'Run status polling failed after repeated attempts: ' + error.message);
        state.live.activeRun = null;
        state.live.currentJob = null;
        state.live.finalizingRunId = null;
        updateFormDisabled();
      }
    }, 1200);
  }

  async function terminalJob(runId) {
    var job = null;
    for (var attempt = 0; attempt < 30; attempt += 1) {
      job = await liveApi().getRun(runId);
      if (isTerminalStatus(job.status)) return job;
      await new Promise(function (resolve) { window.setTimeout(resolve, 100); });
    }
    return job;
  }

  async function finalizeRun(runId) {
    var client = liveApi();
    if (!client) return;
    if (!activeRunMatches(runId)) return;
    if (state.live.finalizingRunId === runId) return;
    state.live.finalizingRunId = runId;
    clearPollTimer();
    if (state.live.eventSource) {
      state.live.eventSource.close();
      state.live.eventSource = null;
    }
    var job;
    try {
      job = await terminalJob(runId);
    } catch (error) {
      if (!activeRunMatches(runId)) return;
      stopElapsed();
      setFormError('Could not read final run status: ' + error.message);
      setFormStatus('Could not read final run status: ' + error.message, true);
      renderRunOutputError({ status: 'unknown', run_id: runId }, 'Could not read final run status: ' + error.message);
      state.live.activeRun = null;
      state.live.currentJob = null;
      state.live.finalizingRunId = null;
      updateFormDisabled();
      return;
    }
    if (!activeRunMatches(runId)) return;
    if (!job || !isTerminalStatus(job.status)) {
      state.live.finalizingRunId = null;
      setFormStatus('Final status is not available yet; resuming status polling…', true);
      startPolling(runId);
      return;
    }
    state.live.currentJob = job;
    setRunState(job.status, job.status);
    $('run-meta-run').textContent = job.run_id;
    if (job.status === 'completed') {
      setFormStatus('Run completed. Fetching normalized dispatch…', false);
      try {
        var output = await client.getRunOutput(runId);
        if (!activeRunMatches(runId)) return;
        addSessionRun(job, output);
        renderRunOutput(job, output);
        setFormStatus('Completed ' + job.run_id + '. Added to the session rail and trade-off.', false);
        announce('Live run ' + job.run_id + ' completed. Screening-only result added to the session rail.');
      } catch (error) {
        if (!activeRunMatches(runId)) return;
        renderRunOutputError(job, 'Run completed but normalized output is unavailable: ' + error.message);
        setFormStatus('Completed, but output unavailable: ' + error.message, true);
      }
    } else {
      renderRunOutputError(job, job.message || ('Run ended with status ' + job.status + '.'));
      setFormStatus('Run ' + job.status + ': ' + (job.message || 'no message'), true);
      announce('Live run ' + job.run_id + ' ' + job.status + '. No result was added.');
    }
    if (activeRunMatches(runId)) {
      state.live.activeRun = null;
      state.live.finalizingRunId = null;
      updateFormDisabled();
    }
  }

  function validateLiveOutput(output) {
    requireTrue(output && typeof output === 'object', 'live output is not an object');
    var n = output.row_count;
    requireTrue(Number.isInteger(n) && n > 0, 'live output row_count is invalid');
    ['technology', 'category', 'zone', 'year', 'value_pj'].forEach(function (key) {
      requireTrue(Array.isArray(output[key]) && output[key].length === n, 'live output column ' + key + ' has wrong length');
    });
    requireTrue(output.units_by_category && typeof output.units_by_category === 'object', 'live output units are missing');
    for (var i = 0; i < n; i += 1) {
      requireTrue(Number.isFinite(Number(output.value_pj[i])), 'live output contains a non-finite value');
      requireTrue(typeof output.units_by_category[output.category[i]] === 'string', 'live output is missing a category unit');
    }
    return output;
  }

  function addSessionRun(job, output) {
    var checked = validateLiveOutput(output);
    var form = state.live.activeForm || state.live.lastForm || {};
    var summary = output.summary || {};
    var metrics = output.metrics || {};
    var railId = 'live:' + job.run_id;
    var label = summary.scenario_label || form.label || output.scenario_id || job.scenario_id;
    var zeroTiers = [];
    if (Number(form.t3) === 0) zeroTiers.push('T3');
    if (Number(form.t4) === 0) zeroTiers.push('T4');
    var scenario = {
      scenario_id: railId,
      contract_scenario_id: output.scenario_id || job.scenario_id,
      liveRun: true,
      runId: job.run_id,
      family: SESSION_FAMILY,
      family_label: 'Session live runs',
      label: label,
      description: 'Live screening-only run from this browser session. Constraint set screening_v0; not policy grade.',
      source_run: job.run_id,
      sources: [output.source || ('run ' + job.run_id + '/output/dispatch_by_zone.csv')],
      dimensions: {
        harvest: { level: form.harvestChoice === 'custom' ? 'custom' : form.harvestChoice + 'm', m3_per_year: form.level, basis: form.path },
        carbon_price_usd_per_t: form.carbon,
        bio_cost_usd_per_gj: state.live.defaults.generic_bio_cost_usd_per_gj,
        recovery_fraction: form.recovery,
        supply_curve: 'tiered',
        hydro_mode: form.hydro,
        pwrbio_cap: { fraction: form.pwrbio },
        nuclear_cap_gw: form.nuclear,
        wind: { cap: fmtPct(form.windCap, 0), integration_limit: null, cost_usd_per_gj: form.windCost },
        zero_tiers: zeroTiers
      },
      metrics: {
        objective: num(metrics.objective),
        emissions_total: num(metrics.emissions_total),
        emissions_2050: num(metrics.emissions_2050),
        national_bcw_2050: num(metrics.national_bcw_2050),
        pwrbcw_2050: num(metrics.pwrbcw_2050),
        pwrbio_2050: num(metrics.pwrbio_2050),
        bio_share_2050: num(metrics.bio_share_2050),
        bio_share_basis: metrics.bio_share_basis || 'derived_from_dispatch_2050',
        solver_status: metrics.solver_status || summary.solver_status || 'completed'
      },
      flags: {
        solver: metrics.solver_status || summary.solver_status || 'completed',
        publishable: false,
        superseded_for_emissions: false,
        diagnostic: false,
        post_fix_emissions: false,
        screening_only: true,
        policy_grade: false,
        live: true
      }
    };
    state.outputs.set(railId, checked);
    state.core.scenarioById.set(railId, scenario);
    state.live.sessionRuns = state.live.sessionRuns.filter(function (item) { return item.runId !== job.run_id; });
    state.live.sessionRuns.unshift(scenario);
    updateHeaderCounts();
    renderHeader(state.core);
    renderScenarioList();
    if (state.tab === 'tradeoff') renderTradeoff();
    if (state.selectedId === railId) renderOverview();
  }

  function renderRunOutput(job, output) {
    var container = $('run-output-summary');
    if (!container) return;
    var metrics = output.metrics || {};
    var railId = 'live:' + job.run_id;
    container.hidden = false;
    container.innerHTML = '<div class="run-output-card">' +
      '<div class="run-output-head"><h3>Completed · screening-only output</h3><span class="mono">' + esc(output.source || '') + '</span></div>' +
      '<p class="run-output-message">' + esc((output.summary && output.summary.message) || 'Run completed.') + '</p>' +
      '<div class="result-grid">' +
        '<div class="result"><span>Objective</span><strong>' + fmt(metrics.objective, 2) + '</strong><small>M$</small></div>' +
        '<div class="result"><span>Cumulative emissions</span><strong>' + fmt(metrics.emissions_total, 3) + '</strong><small>Mt CO₂</small></div>' +
        '<div class="result"><span>National BCW 2050</span><strong>' + fmt(metrics.national_bcw_2050, 2) + '</strong><small>PJ fuel</small></div>' +
        '<div class="result"><span>PWRBCW 2050</span><strong>' + fmt(metrics.pwrbcw_2050, 2) + '</strong><small>PJ electricity</small></div>' +
      '</div>' +
      '<div class="run-output-flag"><strong>screening_only · policy_grade=false</strong><br>Added to the session rail and trade-off. This is not a publishable or policy-grade C2070 result.</div>' +
      '<div class="run-output-actions">' +
        '<button class="button" id="run-open-dispatch" type="button">Open dispatch</button>' +
        '<button class="button button--quiet" id="run-open-overview" type="button">Open overview</button>' +
      '</div>' +
    '</div>';
    $('run-open-dispatch').addEventListener('click', function () {
      selectScenario(railId);
      activateTab('dispatch');
    });
    $('run-open-overview').addEventListener('click', function () {
      selectScenario(railId);
      activateTab('overview');
    });
  }

  function renderRunOutputError(job, message) {
    var container = $('run-output-summary');
    if (!container) return;
    container.hidden = false;
    container.innerHTML = '<div class="run-output-card is-failed">' +
      '<div class="run-output-head"><h3>No result — ' + esc(job.status) + '</h3><span class="mono">' + esc(job.run_id) + '</span></div>' +
      '<p class="run-output-message">' + esc(message || 'The run did not complete.') + '</p>' +
      '<div class="run-output-flag"><strong>No fake results.</strong><br>Nothing was added to the scenario rail, trade-off, or dispatch. Inspect the event log and runner summary for diagnosis.</div>' +
    '</div>';
  }

  function renderRunOutputIdle() {
    var container = $('run-output-summary');
    if (!container) return;
    container.hidden = true;
    container.innerHTML = '';
  }

  async function queueOsecanScenario() {
    if (state.live.mode !== 'live' || state.live.activeRun) return;
    var model = state.osecan.lastModel || (state.core ? computeOsecan() : null);
    if (!model) return;
    // Start from the canonical Run-tab defaults, then apply OSeCAN levers, so
    // stale form edits cannot leak into the queued scenario.
    applyRunDefaults(state.live.defaults || FALLBACK_DEFAULTS);
    var level = Number(model.harvest);
    var select = $('run-harvest');
    if (['100', '119', '150', '200'].indexOf(String(level)) !== -1) {
      select.value = String(level);
    } else {
      select.value = 'custom';
      $('run-harvest-custom').value = String(Math.round(level * 1e6));
    }
    $('run-recovery').value = String(model.recovery);
    $('run-cost').value = 'adopted_rest_of_canada_curve';
    $('run-label').value = 'OSeCAN ' + level + ' Mm³ · recovery ' + fmt(model.recovery, 2) + ' · live screening';
    syncHarvestCustom();
    renderRunOutputIdle();
    activateTab('run');
    await createScenarioFromForm(true);
  }

  async function initLiveMode() {
    var client = liveApi();
    if (!client) {
      state.live.mode = 'static';
      applyRunDefaults(FALLBACK_DEFAULTS);
      renderRunMode();
      return;
    }
    var probe = await client.probe();
    if (!probe.available) {
      state.live.mode = 'static';
      state.live.health = null;
      applyRunDefaults(FALLBACK_DEFAULTS);
      renderRunMode();
      return;
    }
    state.live.health = probe.health;
    try {
      var constraints = await client.getConstraints();
      var set = constraints.sets[0];
      state.live.constraints = constraints;
      state.live.defaults = set.defaults || FALLBACK_DEFAULTS;
      state.live.mode = probe.health.read_only ? 'readonly' : (probe.health.allow_runs ? 'live' : 'no-runs');
      applyRunDefaults(state.live.defaults);
    } catch (error) {
      state.live.mode = 'degraded';
      state.live.defaults = FALLBACK_DEFAULTS;
      applyRunDefaults(FALLBACK_DEFAULTS);
    }
    renderRunMode();
  }

  function invalidateCreatedScenario() {
    if (!state.live.createdScenario) return;
    state.live.createdScenario = null;
    state.live.lastForm = null;
    setFormStatus('Run form changed; create the scenario again before queueing.', false);
    updateFormDisabled();
  }

  function bindRunControls() {
    var form = $('run-form');
    if (!form) return;
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      createScenarioFromForm(false);
    });
    qsa('#run-form input, #run-form select').forEach(function (input) {
      input.addEventListener('change', invalidateCreatedScenario);
    });
    $('run-harvest').addEventListener('change', syncHarvestCustom);
    $('run-queue').addEventListener('click', queueCreatedScenario);
  }

  function fileTable(files) {
    var rows = files.map(function (file) {
      return '<tr><td>' + esc(file.role) + '</td>' +
        '<td class="path-cell">' + esc(file.path) + '</td>' +
        '<td class="num">' + fmtBytes(file.bytes) + '</td>' +
        '<td class="num">' + (file.rows == null ? '—' : fmt(file.rows, 0)) + '</td>' +
        '<td class="sha-cell" title="' + esc(file.sha256) + '">' + esc(String(file.sha256).slice(0, 12)) + '…</td></tr>';
    }).join('');
    return '<div class="table-wrap"><table class="data-table"><thead><tr><th>Role</th><th>Path</th><th class="num">Bytes</th><th class="num">Rows</th><th>SHA-256</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function renderOsecanStatic() {
    if (!state.core) return;
    var links = state.core.links;
    var validation = links.validation.national_cap_reconciliation;
    var familyHtml = '<div class="validation-strip">National cap reconciliation: ' + validation.rows +
      ' rows · tier sum = rest ' + (validation.all_tier_sum_equals_rest ? '✓' : '✗') +
      ' · national = 200 Mm³ total ' + (validation.all_national_equals_200m_total ? '✓' : '✗') +
      ' · max residual ' + fmt(validation.max_abs_tier_residual_pj, 6) + ' PJ</div>';

    var familyRows = links.families.map(function (family) {
      var scenarioFamily = state.core.scenarios.families.filter(function (f) { return f.family === family.family; })[0];
      var familyScenarios = state.core.scenarios.scenarios.filter(function (sc) { return sc.family === family.family; });
      var diagnostic = familyScenarios.some(function (sc) { return sc.flags.diagnostic; });
      var filesOk = family.files.length === 3;
      var shaOk = family.files.every(function (file) { return String(file.sha256).length === 64; });
      var statusBadge = family.superseded_for_emissions
        ? '<span class="badge badge--superseded">superseded</span>'
        : (family.post_fix_emissions ? '<span class="badge badge--postfix">post-fix</span>' : '<span class="badge badge--ink">pre-fix</span>');
      return '<details class="family-row"><summary>' +
        '<span class="check-pill' + (filesOk && shaOk ? '' : ' is-warn') + '">' + (filesOk && shaOk ? 'files recorded' : 'check') + '</span>' +
        '<span class="family-summary-label">' + esc(scenarioFamily ? scenarioFamily.label : family.family) + '</span>' +
        statusBadge + (diagnostic ? '<span class="badge badge--diag">diagnostic</span>' : '') +
        '<span class="family-summary-path">' + esc(family.source_dir) + '</span></summary>' +
        '<div class="family-body">' + fileTable(family.files) + '</div></details>';
    }).join('');
    $('osecan-family-table').innerHTML = familyHtml + familyRows;
    $('osecan-input-table').innerHTML = fileTable(links.input_files);
    $('osecan-supbcw-table').innerHTML = fileTable(links.supbcw_inputs);
  }

  /* ---------------- Inputs ---------------- */

  function renderInputsStatic() {
    if (!state.core) return;
    var core = state.core;
    var supply = core.supply;
    var harvest = core.harvest;
    var landbase = core.landbase;

    $('inputs-summary').innerHTML =
      metricCard('Scenarios / outputs', core.scenarios.scenarios.length + ' / ' + core.manifest.output_count, 'records', '47 scenario contracts · 47 columnar outputs', true) +
      metricCard('Harvest template', fmt(harvest.template.rows, 0), 'rows', harvest.template.provinces.length + ' provinces · 50 years', false) +
      metricCard('Supply tier sum', fmt(supply.tier_sum_pj, 2), 'PJ fuel', 'terminal upper bound · reconciles', false) +
      metricCard('Managed proxy', fmt(landbase.by_province.reduce(function (sum, row) { return sum + row.managed_proxy_ha; }, 0) / 1e6, 1), 'Mha', 'screening only — not legal THLB', false);

    var caveats = harvest.caveats || [];
    $('harvest-caveats').innerHTML = caveats.length
      ? '<ul>' + caveats.map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('') + '</ul>'
      : '';

    var tierSum = Number(supply.tier_sum_pj);
    $('input-tier-bar').innerHTML = supply.tiers.map(function (tier) {
      var width = (Number(tier.incremental_cap_pj) / tierSum) * 100;
      return '<div class="tier-seg" style="width:' + width + '%;background:' + TIER_COLORS[tier.tier] + '"><span>' +
        tier.tier + ' · ' + fmt(tier.incremental_cap_pj, 1) + ' PJ</span></div>';
    }).join('');

    $('input-tier-table').innerHTML = '<div class="table-wrap"><table class="data-table">' +
      '<caption>Tier definitions · ' + esc(supply.reconciliation.path) + '</caption>' +
      '<thead><tr><th>Tier</th><th>Technology</th><th class="num">Lower (PJ)</th><th class="num">Upper (PJ)</th><th class="num">Increment (PJ)</th><th class="num">Cost ($/GJ)</th><th>Flag</th></tr></thead><tbody>' +
      supply.tiers.map(function (tier) {
        return '<tr><td>' + tier.tier + '</td><td>' + esc(tier.technology) + '</td>' +
          '<td class="num">' + fmt(tier.lower_pj, 3) + '</td><td class="num">' + fmt(tier.upper_pj, 3) + '</td>' +
          '<td class="num">' + fmt(tier.incremental_cap_pj, 3) + '</td><td class="num">$' + fmt(tier.variable_cost_usd_per_gj, 3) + '</td>' +
          '<td>' + esc(tier.scenario_flag) + '</td></tr>';
      }).join('') +
      '</tbody><tfoot><tr><th colspan="4">Tier sum</th><th class="num">' + fmt(tierSum, 3) + '</th><th colspan="2">terminal upper ' + fmt(supply.tier_terminal_upper_pj, 3) + ' PJ</th></tr></tfoot></table></div>';

    $('landbase-status').textContent = landbase.manifest_status + ' · ' + landbase.status + ' · ' + landbase.rows.by_province +
      ' province rows, ' + landbase.rows.by_ecoprovince + ' ecoprovince rows';
    $('landbase-caveats').innerHTML = landbase.caveats.map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('');

    var maxManaged = Math.max.apply(null, landbase.by_province.map(function (row) { return row.managed_proxy_ha; }));
    $('managed-table').innerHTML = '<div class="table-wrap"><table class="data-table">' +
      '<caption>Managed-landbase screening proxy by province · ' + esc(landbase.manifest_status) + '</caption>' +
      '<thead><tr><th>Province</th><th class="num">Forest area (Mha)</th><th class="num">Managed proxy (Mha)</th><th class="num">Share</th><th>Relative proxy</th></tr></thead><tbody>' +
      landbase.by_province.map(function (row) {
        var share = row.managed_share == null ? '—' : fmtPct(row.managed_share, 1);
        var width = maxManaged > 0 ? (row.managed_proxy_ha / maxManaged) * 100 : 0;
        return '<tr><td>' + esc(row.province) + '</td>' +
          '<td class="num">' + fmt(row.forest_area_ha / 1e6, 2) + '</td>' +
          '<td class="num">' + fmt(row.managed_proxy_ha / 1e6, 2) + '</td>' +
          '<td class="num">' + share + '</td>' +
          '<td><span class="bar-track"><span class="bar-fill" style="width:' + width + '%"></span></span></td></tr>';
      }).join('') +
      '</tbody></table></div>';

    var basis = supply.supply_basis;
    $('managed-supply-note').textContent = 'Committed physical supply basis: ' + esc(basis.path) + ' · ' +
      'BC zonal total ' + fmt(basis.bc_zonal_total_pj, 3) + ' PJ · log share ' + fmtPct(basis.log_share, 1) +
      ' · gross ' + fmt(basis.gross_odt_per_year, 0) + ' ODT/yr. Note: ' + basis.note;
  }

  function renderInputsCharts() {
    if (!state.core) return;
    renderHarvestChart();
  }

  function renderHarvestChart() {
    var container = $('harvest-chart');
    if (!window.echarts) {
      container.innerHTML = '<div class="empty-state" style="display:grid">ECharts failed to load. Check vendor/echarts.min.js.</div>';
      return;
    }
    var seriesRaw = state.core.harvest.reference_paths.series;
    var definitions = [
      { key: 'observed_2021_2024_m3', label: 'Observed 2021–2024', color: '#d98a2b', dash: 'solid', width: 2.2, dots: true },
      { key: 'base_hold_2024_m3', label: 'Base hold 2024 (119 Mm³)', color: '#17382c', dash: 'solid', width: 2, dots: false },
      { key: 'low_100m_m3', label: 'Low 100 Mm³ (scenario bound)', color: '#4f7d80', dash: 'dashed', width: 1.4, dots: false },
      { key: 'high_150m_m3', label: 'High 150 Mm³ (scenario bound)', color: '#a94f31', dash: 'dashed', width: 1.4, dots: false }
    ];
    if (!state.harvestChart) state.harvestChart = window.echarts.init(container, null, { renderer: 'canvas' });
    state.harvestChart.setOption({
      animationDuration: 450,
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      grid: { left: 64, right: 24, top: 48, bottom: 38, containLabel: true },
      legend: {
        top: 4,
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
        itemWidth: 14,
        itemHeight: 8
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: '#0c1f19',
        borderColor: '#d98a2b',
        borderWidth: 1,
        textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
        valueFormatter: function (value) { return value == null ? '—' : fmt(value, 0) + ' m³/yr'; }
      },
      xAxis: {
        type: 'category',
        data: state.core.harvest.reference_paths.years,
        boundaryGap: false,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', interval: 9 }
      },
      yAxis: {
        type: 'value',
        name: 'm³ / year',
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c' },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: function (value) { return fmt(value / 1e6, 0) + 'M'; } },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { show: false }
      },
      series: definitions.map(function (definition) {
        return {
          name: definition.label,
          type: 'line',
          data: seriesRaw[definition.key],
          symbol: definition.dots ? 'circle' : 'none',
          symbolSize: definition.dots ? 5 : 0,
          connectNulls: false,
          lineStyle: { color: definition.color, width: definition.width, type: definition.dash },
          itemStyle: { color: definition.color },
          emphasis: { focus: 'series' }
        };
      })
    }, true);
  }

  /* ---------------- Boot ---------------- */

  async function init() {
    try {
      var bundle = await Promise.all([
        loadJSON('manifest.json'),
        loadJSON('scenarios.json'),
        loadJSON('inputs/osecan_links.json'),
        loadJSON('inputs/harvest_basis.json'),
        loadJSON('inputs/supply_curve.json'),
        loadJSON('inputs/managed_landbase.json')
      ]);
      buildCoreState({
        manifest: bundle[0],
        scenarios: bundle[1],
        links: bundle[2],
        harvest: bundle[3],
        supply: bundle[4],
        landbase: bundle[5]
      });
      await initLiveMode();
      if (state.warnings.length) {
        var banner = $('fatal-banner');
        banner.hidden = false;
        banner.textContent = state.warnings.join(' ');
      }
      announce('Scenario explorer ready. ' + state.core.scenarios.scenarios.length + ' scenarios loaded.');
    } catch (error) {
      showFatal(error);
    }
  }

  window.addEventListener('resize', function () {
    if (state.dispatch.chart) state.dispatch.chart.resize();
    if (state.tradeoffChart) state.tradeoffChart.resize();
    if (state.harvestChart) state.harvestChart.resize();
    if (state.mapState.map) state.mapState.map.resize();
  });

  init();
})();
