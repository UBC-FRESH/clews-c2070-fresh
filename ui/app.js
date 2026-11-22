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
  // Zone names and province mapping mirror data/derived/national/clews/osecan/zone_crosswalk.csv.
  // Only B1/B2 have authoritative point anchors; the other 11 are explicit gaps.
  var ZONE_META = {
    A1: { name: 'Calgary–Edmonton corridor', provinces: 'Alberta (AB)' },
    A2: { name: 'North (oil sands)', provinces: 'Alberta (AB)' },
    B1: { name: 'South Coast / Lower Mainland', provinces: 'British Columbia (BC)' },
    B2: { name: 'Interior & North', provinces: 'British Columbia (BC)' },
    L1: { name: 'Newfoundland & Labrador', provinces: 'Newfoundland and Labrador (NL)' },
    M1: { name: 'Manitoba', provinces: 'Manitoba (MB)' },
    N1: { name: 'North (territories)', provinces: 'Yukon (YT); Northwest Territories (NT); Nunavut (NU)' },
    O1: { name: 'Southern Ontario', provinces: 'Ontario (ON)' },
    O2: { name: 'Northern Ontario', provinces: 'Ontario (ON)' },
    Q1: { name: 'Southern Quebec', provinces: 'Quebec (QC)' },
    Q2: { name: 'Northern Quebec (hydro)', provinces: 'Quebec (QC)' },
    S1: { name: 'Saskatchewan', provinces: 'Saskatchewan (SK)' },
    T1: { name: 'Atlantic', provinces: 'New Brunswick (NB); Nova Scotia (NS); Prince Edward Island (PE)' }
  };
  var ZONE_SOURCE = 'zone_crosswalk.csv · OSeCAN model_structure.py @ 40d5b43';
  var CANADA_BOUNDS = [[-141, 41], [-52, 84]];
  var CANADA_FIT_OPTIONS = { padding: 34 };
  var MOBILE_RAIL_QUERY = '(max-width: 720px)';
  var TEMPORAL_DEFAULT = 2050;
  var PROVINCE_ALIAS = { QC: 'CA-QB' };
  var EXPECTED_SCENARIO_COUNT = 53;
  var EXPECTED_FAMILY_COUNT = 18;
  var EXPECTED_ELIGIBLE_COUNT = 1;
  var FLEET_SCENARIO_ID = 'fleet_capacity__screening_119m_fleet';
  var FLEET_LADDER_FAMILY = 'fleet_screening_ladder';
  var ALLOCATION_FAMILY = 'allocation_screening';
  var FLEET_BANNER_TEXT = 'pre-fleet model — not comparable';
  var HARVEST_CAVEAT = 'Harvest is exogenous and free in this model; higher harvest lowers cost and emissions because no harvest cost/THLB/AAC constraint is modeled. This is a sensitivity ladder, not a policy frontier.';
  var FEASIBILITY_CAVEAT = '200 Mm³ is not admissible under the documented AAC/NFD screen; 150 Mm³ is conditional (national bound only; the provincial allocation LP reallocates it, but the 1.0 m³/ha/yr operable-proxy screen fails by 16.296 Mm³ and the 1.2 screen is still short by 4.492 Mm³; the national operable envelope alone clears at 1.2); harvest is exogenous. The default energy Pareto contains only the canonical 119 Mm³ fleet case. Allocation-LP variants are comparison/context only.';
  var ALLOCATION_CAVEAT = 'The operable-proxy intensity screen uses SCANFI merchantable area intersected with a share-based 2016 tenure proxy, not a THLB. It has no slope, road access, age/disturbance, growth, yield, or economic content. 150 Mm3 is conditional: the 1.0 m3/ha/yr per-province screen is infeasible by 16.296 Mm3, and the 1.2 screen is still short by 4.492 Mm3 because BC, ON, YT, and NT documented caps bind below 1.2 x operable area. The national operable envelope alone would clear 150 Mm3 at 1.2. 119 Mm3 passes both. The LP allocation is a comparison/screening result, not a legal AAC, enforced provincial constraint, or policy frontier.';
  var MAX_NORMAL_HARVEST_M3 = 150000000;
  var ROLE_LABELS = {
    public: 'public · read-only',
    analyst: 'analyst',
    model_builder: 'model builder',
    energy_modeller: 'energy modeller · read-only'
  };

  var FAMILY_SHORT = {
    fleet_capacity: 'Fleet capacity',
    fleet_screening_ladder: 'Harvest ladder',
    allocation_screening: 'Allocation LP',
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
    fleet_capacity: '#b23b1c',
    fleet_screening_ladder: '#d98a2b',
    allocation_screening: '#7b4b75',
    emission_factor_fix: '#b9791f',
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
  var ECONOMICS_SYSTEMS = ['ground', 'cable', 'heli'];
  var NATIONAL_SCHEMA = 'economics/national/1';
  var NATIONAL_MANIFEST_SCHEMA = 'economics/national-manifest/1';
  var NATIONAL_TIERS = [
    { id: 'reported_average', label: 'Reported average', color: '#2c5746' },
    { id: 'proxy_model', label: 'Proxy model', color: '#d98a2b' },
    { id: 'donor_proxy', label: 'Donor proxy', color: '#a94f31' },
    { id: 'assumption', label: 'Assumption', color: '#9d8b6c' }
  ];
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

  var PC_AXES = [
    { id: 'objective', label: 'Objective', unit: 'M$', get: function (sc) { return finiteOrNull((sc.metrics || {}).objective); }, format: function (v) { return fmt(v, 2) + ' M$'; }, tick: function (v) { return fmtAxis(v); } },
    { id: 'emissions_total', label: 'Emissions', unit: 'Mt CO₂ cumulative', get: function (sc) { return finiteOrNull((sc.metrics || {}).emissions_total); }, format: function (v) { return fmt(v, 3) + ' Mt CO₂'; }, tick: function (v) { return fmt(v, 2); } },
    { id: 'national_bcw_2050', label: 'National BCW', unit: 'PJ fuel · 2050', get: function (sc) { return finiteOrNull((sc.metrics || {}).national_bcw_2050); }, format: function (v) { return fmt(v, 2) + ' PJ fuel'; }, tick: function (v) { return fmt(v, 0); } },
    { id: 'pwrbcw_2050', label: 'PWRBCW', unit: 'PJ electricity · 2050', get: function (sc) { return finiteOrNull((sc.metrics || {}).pwrbcw_2050); }, format: function (v) { return fmt(v, 2) + ' PJ elec'; }, tick: function (v) { return fmt(v, 0); } },
    { id: 'bio_share_2050', label: 'Bio share', unit: '% electricity · 2050', get: function (sc) { return finiteOrNull((sc.metrics || {}).bio_share_2050); }, format: function (v) { return fmtPct(v, 2); }, tick: function (v) { return fmtPct(v, 1); } },
    { id: 'harvest', label: 'Harvest', unit: 'Mm³/yr', raw: function (sc) { var h = (sc.dimensions || {}).harvest; return h ? finiteOrNull(h.m3_per_year) : null; }, get: function (sc) { var h = (sc.dimensions || {}).harvest; return h ? finiteOrNull(h.m3_per_year) : null; }, format: function (v) { return fmt(v / 1e6, 2) + ' Mm³/yr'; }, tick: function (v) { return fmt(v / 1e6, 1); } },
    { id: 'carbon_price_usd_per_t', label: 'Carbon price', unit: '$/t CO₂', raw: function (sc) { return finiteOrNull((sc.dimensions || {}).carbon_price_usd_per_t); }, get: function (sc) { return finiteOrNull((sc.dimensions || {}).carbon_price_usd_per_t); }, format: function (v) { return '$' + fmt(v, 2) + '/t'; }, tick: function (v) { return fmt(v, 0); } },
    { id: 'recovery_fraction', label: 'Recovery', unit: 'fraction', raw: function (sc) { return finiteOrNull((sc.dimensions || {}).recovery_fraction); }, get: function (sc) { return finiteOrNull((sc.dimensions || {}).recovery_fraction); }, format: function (v) { return fmtPct(v, 1) + ' (' + fmt(v, 2) + ')'; }, tick: function (v) { return fmtPct(v, 0); } },
    { id: 'pwrbio_cap', label: 'PWRBIO cap', unit: 'PJ · 0 = none/zero', raw: function (sc) { var v = (sc.dimensions || {}).pwrbio_cap; return v === null || v === undefined ? null : finiteOrNull(v); }, get: function (sc) { return pwrbioCapValue((sc.dimensions || {}).pwrbio_cap); }, format: function (v) { return fmt(v, 2) + ' PJ'; }, tick: function (v) { return fmt(v, 0); } },
    { id: 'nuclear_cap_gw', label: 'Nuclear cap', unit: 'GW · 0 = none/zero', raw: function (sc) { return finiteOrNull((sc.dimensions || {}).nuclear_cap_gw); }, get: function (sc) { return nuclearCapValue((sc.dimensions || {}).nuclear_cap_gw); }, format: function (v) { return fmt(v, 2) + ' GW'; }, tick: function (v) { return fmt(v, 0); } },
    { id: 'wind_cap', label: 'Wind cap/int', unit: '% · max of cap or integration', raw: function (sc) { var w = (sc.dimensions || {}).wind; if (!w || (w.cap === 'none' && w.integration_limit == null)) return null; return windCapValue(w); }, get: function (sc) { return windCapValue((sc.dimensions || {}).wind); }, format: function (v) { return fmt(v, 1) + '%'; }, tick: function (v) { return fmt(v, 0) + '%'; } }
  ];
  var PC_AXIS_BY_ID = {};
  PC_AXES.forEach(function (axis) { PC_AXIS_BY_ID[axis.id] = axis; });

  var PARETO_METRICS = [
    { id: 'objective', label: 'Objective', unit: 'M$', get: PC_AXIS_BY_ID.objective.get, format: PC_AXIS_BY_ID.objective.format, higher: false },
    { id: 'emissions_total', label: 'Cumulative emissions', unit: 'Mt CO₂', get: PC_AXIS_BY_ID.emissions_total.get, format: PC_AXIS_BY_ID.emissions_total.format, higher: false },
    { id: 'national_bcw_2050', label: 'National BCW 2050', unit: 'PJ fuel', get: PC_AXIS_BY_ID.national_bcw_2050.get, format: PC_AXIS_BY_ID.national_bcw_2050.format, higher: true },
    { id: 'pwrbcw_2050', label: 'PWRBCW 2050', unit: 'PJ electricity', get: PC_AXIS_BY_ID.pwrbcw_2050.get, format: PC_AXIS_BY_ID.pwrbcw_2050.format, higher: true },
    { id: 'bio_share_2050', label: 'Bio share 2050', unit: 'share of electricity', get: PC_AXIS_BY_ID.bio_share_2050.get, format: PC_AXIS_BY_ID.bio_share_2050.format, higher: true },
    { id: 'emissions_2050', label: 'Emissions 2050', unit: 'Mt CO₂', get: function (sc) { return finiteOrNull((sc.metrics || {}).emissions_2050); }, format: function (v) { return fmt(v, 3) + ' Mt CO₂'; }, higher: false },
    { id: 'pwrbio_2050', label: 'PWRBIO 2050', unit: 'PJ electricity', get: function (sc) { return finiteOrNull((sc.metrics || {}).pwrbio_2050); }, format: function (v) { return fmt(v, 2) + ' PJ elec'; }, higher: true }
  ];
  var PARETO_BY_ID = {};
  PARETO_METRICS.forEach(function (metric) { PARETO_BY_ID[metric.id] = metric; });
  var PARETO_DEFAULTS = { x: 'objective', y: 'emissions_total', z: 'national_bcw_2050' };

  var COMPARE_METRICS = [
    { key: 'objective', label: 'Objective', unit: 'M$ (million USD)', digits: 2 },
    { key: 'emissions_total', label: 'Cumulative emissions', unit: 'Mt CO₂ · 2021—2070', digits: 3 },
    { key: 'emissions_2050', label: 'Emissions 2050', unit: 'Mt CO₂', digits: 3 },
    { key: 'national_bcw_2050', label: 'National BCW 2050', unit: 'PJ fuel', digits: 2 },
    { key: 'pwrbcw_2050', label: 'PWRBCW 2050', unit: 'PJ electricity', digits: 2 },
    { key: 'bio_share_2050', label: 'Bio share 2050', unit: 'share of electricity', digits: 3, percent: true },
    { key: 'harvest_m3_per_year', label: 'Harvest volume', unit: 'Mm³/yr', digits: 2, scale: 1e6 },
    { key: 'pwrbio_2050', label: 'PWRBIO 2050', unit: 'PJ electricity', digits: 2, optional: true }
  ];

  var state = {
    core: null,
    economics: null,
    economicsChart: null,
    national: null,
    nationalManifest: null,
    nationalChart: null,
    nationalScenario: 'donor_flat',
    outputs: new Map(),
    outputPromises: new Map(),
    warnings: [],
    selectedId: null,
    familyFilter: 'all',
    search: '',
    tab: 'overview',
    eligible: [],
    eligibleIds: new Set(),
    compare: [],
    compareIds: new Set(),
    dispatch: {
      energy: 'electricity',
      zone: 'ALL',
      from: YEAR_FIRST,
      to: YEAR_LAST,
      token: 0,
      chart: null,
      processed: null
    },
    zones: {
      energy: 'electricity',
      metric: 'total',
      year: null,
      token: 0,
      processed: null
    },
    tradeoffChart: null,
    paretoChart: null,
    compareChart: null,
    harvestChart: null,
    temporal: {
      year: TEMPORAL_DEFAULT,
      playing: false,
      timer: null
    },
    explore: {
      brushes: {},
      pareto: { x: PARETO_DEFAULTS.x, y: PARETO_DEFAULTS.y, z: PARETO_DEFAULTS.z },
      compare: { a: null, b: null },
      overlay: false,
      energy: 'electricity',
      overlayToken: 0,
      lastFront: null,
      formError: '',
      resizeTimer: null
    },
    mapState: {
      initialized: false,
      loading: false,
      error: false,
      map: null,
      data: null,
      markers: [],
      pendingSelection: null
    },
    spatial: {
      selection: null,
      mappedZone: null
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
      logRenderTimer: null,
      sessionRuns: [],
      runHistory: [],
      cancelBusy: false,
      retryBusy: false,
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
  function finiteOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  function pwrbioCapValue(value) {
    if (value === null || value === undefined) return 0;
    if (typeof value === 'object') return finiteOrNull(value.total_pj);
    return finiteOrNull(value);
  }
  function nuclearCapValue(value) {
    return value === null || value === undefined ? 0 : finiteOrNull(value);
  }
  function windCapValue(value) {
    if (!value || typeof value !== 'object') return 0;
    var values = [];
    [value.cap, value.integration_limit].forEach(function (raw) {
      if (!raw || raw === 'none') return;
      var match = /^([\d.]+)\s*(?:pct|%)/i.exec(String(raw));
      if (match) values.push(Number(match[1]));
    });
    return values.length ? Math.max.apply(null, values) : 0;
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
    requireTrue(s.scenarios.length === EXPECTED_SCENARIO_COUNT, 'expected ' + EXPECTED_SCENARIO_COUNT + ' scenarios, found ' + s.scenarios.length);
    requireTrue(m.scenario_count === s.scenarios.length, 'manifest scenario_count does not match scenarios.json');
    requireTrue(m.output_count === s.scenarios.length, 'manifest output_count does not match scenario count');
    requireTrue(Array.isArray(s.families) && s.families.length === EXPECTED_FAMILY_COUNT, 'expected ' + EXPECTED_FAMILY_COUNT + ' scenario families');

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
      requireTrue(typeof sc.flags.superseded_for_fleet === 'boolean', 'missing fleet supersession flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.fleet_corrected === 'boolean', 'missing fleet-corrected flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.canonical === 'boolean', 'missing canonical flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.policy_grade === 'boolean', 'missing policy-grade flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.harvest_exogenous === 'boolean', 'missing harvest-exogenous flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.tradeoff_eligible === 'boolean', 'missing trade-off eligibility flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.pareto_eligible === 'boolean', 'missing Pareto eligibility flag on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.allocation_screening === 'boolean', 'missing allocation-screening flag on ' + sc.scenario_id);
      requireTrue(['pass', 'conditional', 'fail'].indexOf(sc.feasibility_status) !== -1, 'missing feasibility status on ' + sc.scenario_id);
      requireTrue(Array.isArray(sc.feasibility_notes) && sc.feasibility_notes.length > 0, 'missing feasibility notes on ' + sc.scenario_id);
      requireTrue(typeof sc.pareto_eligible === 'boolean', 'missing top-level Pareto flag on ' + sc.scenario_id);
      requireTrue(sc.flags.feasibility_status === sc.feasibility_status, 'feasibility flag mismatch on ' + sc.scenario_id);
      requireTrue(sc.flags.pareto_eligible === sc.pareto_eligible, 'Pareto flag mismatch on ' + sc.scenario_id);
      requireTrue(typeof sc.flags.post_fix_emissions === 'boolean', 'missing post-fix flag on ' + sc.scenario_id);
      requireTrue(Array.isArray(sc.sources) && sc.sources.length > 0, 'missing sources on ' + sc.scenario_id);
    });
    Object.keys(familyCounts).forEach(function (family) {
      var actual = s.scenarios.filter(function (sc) { return sc.family === family; }).length;
      requireTrue(actual === familyCounts[family], 'family count mismatch for ' + family);
    });

    requireTrue(s.tradeoff && Array.isArray(s.tradeoff.scenario_ids), 'trade-off ledger is missing');
    requireTrue(s.tradeoff.scenario_ids.length === EXPECTED_ELIGIBLE_COUNT, 'expected ' + EXPECTED_ELIGIBLE_COUNT + ' Pareto-eligible scenario(s), found ' + s.tradeoff.scenario_ids.length);
    requireTrue(Array.isArray(s.tradeoff.sensitivity_scenario_ids) && s.tradeoff.sensitivity_scenario_ids.length === 3, 'harvest sensitivity ledger is missing');
    requireTrue(Array.isArray(s.tradeoff.allocation_scenario_ids) && s.tradeoff.allocation_scenario_ids.length === 2, 'allocation screening ledger is missing');
    requireTrue(s.tradeoff.allocation_caveat === ALLOCATION_CAVEAT, 'allocation/THLB caveat is missing or changed');
    requireTrue(s.allocation_screening && Array.isArray(s.allocation_screening.pairs) && s.allocation_screening.pairs.length === 2, 'allocation comparison block is missing');
    s.allocation_screening.pairs.forEach(function (pair) {
      requireTrue(pair.harvest_m3 > 0, 'allocation comparison pair has no harvest volume');
      requireTrue(seen.has(pair.observed_scenario_id), 'allocation comparison references missing observed scenario ' + pair.observed_scenario_id);
      requireTrue(seen.has(pair.reallocated_scenario_id), 'allocation comparison references missing reallocated scenario ' + pair.reallocated_scenario_id);
      requireTrue(pair.intensity && pair.intensity['1.0'] && pair.intensity['1.2'], 'allocation comparison intensity screen is incomplete');
      requireTrue(Array.isArray(pair.province_rows) && pair.province_rows.length === 12, 'allocation comparison province rows are incomplete');
    });
    requireTrue(s.tradeoff.canonical_scenario_id === FLEET_SCENARIO_ID, 'canonical scenario is not the fleet-corrected run');
    requireTrue(s.tradeoff.harvest_exogenous_caveat === HARVEST_CAVEAT, 'harvest-exogenous caveat is missing or changed');
    requireTrue(s.tradeoff.feasibility_caveat && s.tradeoff.feasibility_caveat.indexOf('200 Mm3') !== -1, 'feasibility caveat is missing');
    var canonicalCount = s.scenarios.filter(function (sc) { return sc.flags.canonical === true; }).length;
    requireTrue(canonicalCount === 1, 'expected exactly one canonical scenario, found ' + canonicalCount);
    s.tradeoff.scenario_ids.forEach(function (id) {
      requireTrue(seen.has(id), 'trade-off ledger references missing scenario ' + id);
    });

    requireTrue(links && Array.isArray(links.families), 'osecan_links.json families are missing');
    requireTrue(links.families.length === EXPECTED_FAMILY_COUNT, 'expected ' + EXPECTED_FAMILY_COUNT + ' linked source families');
    links.families.forEach(function (f) {
      requireTrue(Array.isArray(f.files) && f.files.length >= 3 && f.files.length % 3 === 0, 'linked family ' + f.family + ' does not have 3 files per scenario');
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
    requireTrue(core.operable && Array.isArray(core.operable.by_province), 'operable proxy summary is missing');
    requireTrue(core.operable.national && Number.isFinite(Number(core.operable.national.operable_proxy_ha)), 'operable proxy national area is missing');
    requireTrue(core.operable.missing_provinces.length === 2 && core.operable.missing_provinces.indexOf('PE') >= 0 && core.operable.missing_provinces.indexOf('NU') >= 0, 'operable proxy missing provinces must be PE and NU');
    requireTrue(core.operable.by_province.length === 13, 'operable proxy province table is incomplete');
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
    requireTrue(core.zoneNodes && core.zoneNodes.zone_count === 13 && core.zoneNodes.anchor_count === 2 && core.zoneNodes.gap_count === 11, 'zone-node contract mismatch');
    requireTrue(Array.isArray(core.zoneNodes.zones) && core.zoneNodes.zones.length === 13, 'zone-node rows are incomplete');
    validateEconomics(core.economics);
    validateNational(core.national, core.nationalManifest);
    state.economics = core.economics;
    state.national = core.national;
    state.nationalManifest = core.nationalManifest;
    state.core = core;
    core.scenarioById = new Map();
    core.scenarios.scenarios.forEach(function (sc) { core.scenarioById.set(sc.scenario_id, sc); });

    var eligible = [];
    var eligibleIds = new Set();
    core.scenarios.tradeoff.metrics.forEach(function (metric) {
      var sc = core.scenarioById.get(metric.scenario_id);
      if (!sc) return;
      if (sc.flags.publishable && sc.flags.post_fix_emissions && sc.flags.fleet_corrected &&
          sc.flags.tradeoff_eligible &&
          !sc.flags.superseded_for_emissions && !sc.flags.superseded_for_fleet) {
        eligible.push(sc);
        eligibleIds.add(sc.scenario_id);
      }
    });
    state.eligible = eligible;
    state.eligibleIds = eligibleIds;

    var compare = [];
    var compareIds = new Set();
    (core.scenarios.tradeoff.scenario_ids || []).concat(core.scenarios.tradeoff.sensitivity_scenario_ids || []).concat(core.scenarios.tradeoff.allocation_scenario_ids || []).forEach(function (id) {
      var sc = core.scenarioById.get(id);
      if (!sc) {
        state.warnings.push('Explore ledger references missing scenario ' + id + '.');
        return;
      }
      if (!compareIds.has(id)) {
        compare.push(sc);
        compareIds.add(id);
      }
    });
    state.compare = compare;
    state.compareIds = compareIds;

    if (eligible.length !== EXPECTED_ELIGIBLE_COUNT) {
      state.warnings.push('Pareto guard: flag-derived eligible set is ' + eligible.length + ', expected ' + EXPECTED_ELIGIBLE_COUNT + '.');
    }
    core.scenarios.tradeoff.scenario_ids.forEach(function (id) {
      var sc = core.scenarioById.get(id);
      if (!sc || !eligibleIds.has(id)) {
        state.warnings.push('Pareto guard excluded ' + id + ' because its flags are not canonical and Pareto eligible.');
      }
    });

    var defaultId = FLEET_SCENARIO_ID;
    var hash = parseHash();
    applyExploreHash(hash.params);
    var initialYear = parseHashYear(hash.params);
    if (initialYear !== null) state.temporal.year = initialYear;
    if (hash.params.sel) state.mapState.pendingSelection = parseSelectionKey(hash.params.sel);
    var initialTab = hash.tab || 'overview';
    var initialId = hash.id && core.scenarioById.has(hash.id) ? hash.id : defaultId;
    if (!core.scenarioById.has(initialId)) initialId = core.scenarios.scenarios[0].scenario_id;

    renderHeader(core);
    renderFamilies();
    renderZoneGapChips();
    selectScenario(initialId, { silent: true });
    bindRailToggle();
    bindTabs();
    bindDispatchControls();
    bindZonesControls();
    bindTemporalControls();
    bindHashRouting();
    bindExploreControls();
    bindOsecanControls();
    bindRunControls();
    bindAllocationControls();
    bindNationalControls();
    renderOsecanStatic();
    renderInputsStatic();
    renderEconomics();
    renderNational();
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
      core.scenarios.scenarios.length + ' scenarios · ' + state.eligible.length + ' Pareto eligible (canonical 119 Mm³ only) · ' +
      (core.scenarios.tradeoff.sensitivity_scenario_ids || []).length + ' harvest sensitivity/compare excluded (150 conditional, 200 fail) · ' +
      (core.scenarios.tradeoff.allocation_scenario_ids || []).length + ' allocation-LP comparison rows (not Pareto) · canonical ' +
      FLEET_SCENARIO_ID + ' · 2026-11-20 fleet+allocation runs';
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

  function feasibilityClass(status) {
    return status === 'pass' ? 'badge--feas-pass' : (status === 'conditional' ? 'badge--feas-conditional' : 'badge--feas-fail');
  }

  function feasibilityLabel(status) {
    return status === 'pass' ? 'AAC/NFD pass' : (status === 'conditional' ? 'AAC conditional' : 'AAC/NFD fail');
  }

  function feasibilityBadge(sc) {
    if (!sc || !sc.feasibility_status) return '';
    return '<span class="badge ' + feasibilityClass(sc.feasibility_status) + '">' + esc(feasibilityLabel(sc.feasibility_status)) + '</span>';
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
      html += '<li class="rail-group-label" role="presentation"><span>Session runs</span><span class="mono">live · session only</span></li>';
      sessionRows.forEach(function (sc) {
        var selected = sc.scenario_id === state.selectedId;
        var setLabel = sc.constraint_set_id === 'policy_test_v1' ? 'policy draft' : 'screening only';
        html += '<li><button type="button" class="scenario-item scenario-item--live' + (selected ? ' is-selected' : '') +
          '" data-scenario-id="' + esc(sc.scenario_id) + '" aria-pressed="' + (selected ? 'true' : 'false') + '">' +
          '<span class="s-label">' + esc(sc.label) + '</span>' +
          '<span class="s-id">' + esc(sc.runId) + '</span>' +
          '<span class="s-badges"><span class="badge badge--family">Session live</span>' +
          '<span class="badge badge--diag">' + esc(setLabel) + '</span>' + feasibilityBadge(sc) +
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
      var fleetBadge = sc.flags.fleet_corrected
        ? '<span class="badge badge--fleet">fleet-corrected</span>'
        : '<span class="badge badge--prefleet">' + esc(FLEET_BANNER_TEXT) + '</span>';
      var roleBadge = sc.flags.canonical
        ? '<span class="badge badge--fleet">canonical/base</span>'
        : (sc.family === FLEET_LADDER_FAMILY
          ? '<span class="badge badge--diag">sensitivity</span><span class="badge badge--ink">exogenous harvest</span>'
          : (sc.family === ALLOCATION_FAMILY
            ? '<span class="badge badge--allocation">allocation LP</span><span class="badge badge--ink">observed → reallocated</span>'
            : ''));
      html += '<li><button type="button" class="scenario-item' + (selected ? ' is-selected' : '') +
        '" data-scenario-id="' + esc(sc.scenario_id) + '" aria-pressed="' + (selected ? 'true' : 'false') + '">' +
        '<span class="s-label">' + esc(sc.label) + '</span>' +
        '<span class="s-id">' + esc(sc.scenario_id) + '</span>' +
        '<span class="s-badges"><span class="badge badge--family">' + esc(shortFamily(sc.family)) + '</span>' +
        fleetBadge + roleBadge + feasibilityBadge(sc) + statusBadge + diagnosticBadge + solverBadge + '</span></button></li>';
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
    if (state.tab === 'explore') renderExplore({ refreshOverlay: false });
    if (state.tab === 'zones') renderZones();
    updateHash();
  }

  function updateTabMeta() {
    var sc = state.core && state.core.scenarioById.get(state.selectedId);
    if (!sc) {
      $('tab-scenario-meta').innerHTML = '';
      return;
    }
    if (sc.liveRun) {
      $('tab-scenario-meta').innerHTML = '<strong>' + esc(sc.label) + '</strong> · live session run · ' +
        esc(sc.constraint_set_id === 'policy_test_v1' ? 'policy draft · policy_grade=false' : 'screening only') +
        ' · ' + esc(feasibilityLabel(sc.feasibility_status));
      return;
    }
    $('tab-scenario-meta').innerHTML = '<strong>' + esc(sc.label) + '</strong> · ' + esc(shortFamily(sc.family)) +
      (sc.flags.fleet_corrected
        ? ' · fleet-corrected · screening only · ' + esc(feasibilityLabel(sc.feasibility_status))
        : (sc.flags.superseded_for_emissions ? ' · superseded · pre-fleet' : ' · pre-fleet model — not comparable'));
  }

  function linkedContextHtml() {
    var sel = state.spatial.selection;
    if (!sel) return '';
    var zoneText = sel.zoneId
      ? 'mapped to C2070 zone ' + sel.zoneId + ' (authoritative anchor; dispatch filter)'
      : 'no documented C2070 zone mapping — dispatch and overview stay national';
    return '<div class="linked-context" role="status">' +
      '<span class="kicker">Spatial selection</span>' +
      '<strong>' + esc(sel.title) + '</strong>' +
      '<span class="linked-zone' + (sel.zoneId ? ' is-mapped' : '') + '">' + esc(zoneText) + '</span>' +
      '<button class="button button--quiet" type="button" data-linked-tab="map">Map</button>' +
      (sel.zoneId ? '<button class="button button--quiet" type="button" data-linked-tab="dispatch">Dispatch · ' + esc(sel.zoneId) + '</button>' : '') +
      '</div>';
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
    var preFleet = sc.flags.superseded_for_fleet;
    var isLadderSensitivity = sc.family === FLEET_LADDER_FAMILY;
    var isAllocationScreening = sc.family === ALLOCATION_FAMILY;
    var feasibilityCaveat = isAllocationScreening
      ? (sc.feasibility_status === 'conditional'
        ? 'Conditional — LP reallocation is feasible under documented caps, but the 1.0 m³/ha/yr operable-proxy screen fails by 16.296 Mm³ and the 1.2 screen is still 4.492 Mm³ short; the national operable envelope alone clears at 1.2. The proxy is not a THLB.'
        : 'Pass — LP reallocation is feasible under documented caps and both 1.0 and 1.2 m³/ha/yr operable-proxy screens. The proxy is not a THLB.')
      : (sc.feasibility_status === 'fail'
        ? 'AAC/NFD fail — historical/diagnostic only; not admissible and not Pareto eligible.'
        : (sc.feasibility_status === 'conditional'
          ? 'Conditional — national 150 Mm³ bound only; the status-quo provincial mix fails NB/BC and the LP reallocation is conditional on the operable-proxy screen.'
          : 'AAC/NFD pass — national screens only; NB/NS Crown-only tenure caveats apply.'));
    var statusTitle = sc.flags.canonical
      ? 'Fleet-corrected canonical/base · Pareto eligible'
      : (isLadderSensitivity
        ? (sc.feasibility_status === 'fail' ? 'Harvest diagnostic — AAC/NFD fail' : 'Harvest sensitivity — ' + feasibilityLabel(sc.feasibility_status))
        : (isAllocationScreening
          ? 'Allocation screening — ' + feasibilityLabel(sc.feasibility_status) + ' · not Pareto'
          : (preFleet ? FLEET_BANNER_TEXT : 'Historical record')));
    var statusCopy = sc.flags.canonical
      ? 'Canonical 119 Mm³ screening base under the CER 2021 capacity fleet. screening_only=true and policy_grade=false; the only default energy-Pareto case, but not a policy or forecast.'
      : (isLadderSensitivity
        ? 'Sensitivity case on the fleet-corrected harvest ladder. Harvest is exogenous and free; no harvest cost, THLB, or AAC constraint is modeled. ' + feasibilityCaveat + ' Compare only — excluded from the default Pareto.'
        : (isAllocationScreening
          ? 'LP reallocation of the same national harvest total across provinces under documented caps, with the 2024-share L1 objective. Observed rows keep the 2024 mix; this row is the reallocated allocation. Harvest is exogenous and free in the energy objective. ' + feasibilityCaveat + ' Comparison/context only — never Pareto eligible.'
          : 'This run predates the CER 2021 capacity-fleet correction. It remains visible for audit but is excluded from the default trade-off/Pareto ledger and is not comparable with fleet-corrected results.' +
            (superseded ? ' Its emissions factors are also superseded by the emission-factor fix.' : '')));

    var fleetBanner = sc.flags.canonical
      ? '<div class="overview-callout fleet-banner fleet-banner--corrected" role="status"><strong>Fleet-corrected · canonical/base · AAC/NFD pass</strong><br>' +
        'CER 2021 capacity fleet applied. This is the 119 Mm³ canonical screening base: objective ' + fmt(m.objective, 2) +
        ' M$, cumulative emissions ' + fmt(m.emissions_total, 3) + ' Mt CO₂, 2050 emissions ' + fmt(m.emissions_2050, 3) +
        ' Mt CO₂. Screening only — not policy-grade.</div>'
      : (isLadderSensitivity
        ? '<div class="overview-callout fleet-banner fleet-banner--pre" role="status"><strong>' +
          (sc.feasibility_status === 'fail' ? 'Diagnostic only · 200 Mm³ not admissible' : 'Sensitivity · ' + esc(feasibilityLabel(sc.feasibility_status))) +
          '</strong><br>' + esc(feasibilityCaveat) + '<br>' + esc(HARVEST_CAVEAT) + ' Objective ' + fmt(m.objective, 2) +
          ' M$, cumulative emissions ' + fmt(m.emissions_total, 3) + ' Mt CO₂. Not Pareto eligible.</div>'
        : (isAllocationScreening
          ? '<div class="overview-callout fleet-banner fleet-banner--allocation" role="status"><strong>Allocation screening · observed → reallocated · ' + esc(feasibilityLabel(sc.feasibility_status)) + '</strong><br>' +
            'Same national harvest total as the observed case, reallocated across provinces under documented caps. ' + esc(ALLOCATION_CAVEAT) +
            ' Objective ' + fmt(m.objective, 2) + ' M$, cumulative emissions ' + fmt(m.emissions_total, 3) +
            ' Mt CO₂. Comparison/context only; not Pareto eligible.</div>'
          : '<div class="overview-callout fleet-banner fleet-banner--pre" role="status"><strong>' + esc(FLEET_BANNER_TEXT) + '</strong><br>' +
            'This scenario predates the CER 2021 capacity-fleet correction. It is historical audit evidence only: ' +
            'excluded from the canonical trade-off/Pareto ledger and not comparable with fleet-corrected results.</div>'));

    var feasibilityCallout = '<div class="harvest-caveat harvest-caveat--feasibility" role="note"><strong>' +
      esc('Feasibility · ' + feasibilityLabel(sc.feasibility_status)) + '</strong><p>' + esc(feasibilityCaveat) +
      ' 200 Mm³ not admissible under documented AAC/NFD; 150 Mm³ conditional; harvest exogenous; allocation-LP variants are comparison only.</p></div>';

    var html = linkedContextHtml() + fleetBanner + feasibilityCallout + '<div class="scenario-masthead">' +
      '<div>' +
        '<p class="kicker masthead-family">' + esc(sc.family_label) + '</p>' +
        '<h2>' + esc(sc.label) + '</h2>' +
        '<p class="scenario-desc">' + esc(sc.description || 'No scenario description in the committed contract.') + '</p>' +
        '<span class="masthead-id">' + esc(sc.scenario_id) + '</span>' +
        '<div class="masthead-flags">' +
          (sc.flags.fleet_corrected
            ? '<span class="badge badge--fleet">fleet-corrected</span>'
            : '<span class="badge badge--prefleet">' + esc(FLEET_BANNER_TEXT) + '</span>') +
          '<span class="badge ' + (superseded ? 'badge--superseded' : (sc.flags.post_fix_emissions ? 'badge--postfix' : 'badge--ink')) + '">' +
            (superseded ? 'superseded for emissions' : (sc.flags.post_fix_emissions ? 'post-fix emissions' : 'pre-fix emissions')) + '</span>' +
          (sc.flags.publishable ? '<span class="badge badge--postfix">publishable</span>' : '<span class="badge badge--superseded">not publishable</span>') +
          (sc.flags.canonical ? '<span class="badge badge--fleet">canonical/base</span>' : '') +
          (isLadderSensitivity ? '<span class="badge badge--diag">sensitivity</span><span class="badge badge--ink">exogenous harvest</span>' : '') +
          (isAllocationScreening ? '<span class="badge badge--allocation">allocation LP</span><span class="badge badge--ink">observed → reallocated</span>' : '') +
          feasibilityBadge(sc) +
          (sc.feasibility_status === 'fail' ? '<span class="badge badge--diag">diagnostic only</span>' : '') +
          (eligible ? '<span class="badge badge--family">Pareto eligible</span>' : '') +
        '</div>' +
      '</div>' +
      '<div class="masthead-side">' +
        '<div><p class="kicker">Status</p><p class="status-large' + ((superseded || preFleet || isLadderSensitivity || isAllocationScreening) ? ' is-warn' : '') + '">' + statusTitle + '</p></div>' +
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
    var html = linkedContextHtml() + '<div class="scenario-masthead">' +
      '<div>' +
        '<p class="kicker masthead-family">Session run · live screening runner</p>' +
        '<h2>' + esc(sc.label) + '</h2>' +
        '<p class="scenario-desc">' + esc(sc.description) + '</p>' +
        '<span class="masthead-id">' + esc(sc.contract_scenario_id) + ' · run ' + esc(sc.runId) + '</span>' +
        '<div class="masthead-flags">' +
          '<span class="badge badge--diag">' + esc(sc.constraint_set_id === 'policy_test_v1' ? 'policy_test_v1 · draft' : 'screening_only') + '</span>' +
          '<span class="badge badge--superseded">policy_grade=false</span>' +
          feasibilityBadge(sc) +
          '<span class="badge badge--ink">not publishable</span>' +
          '<span class="badge badge--family">live session</span>' +
        '</div>' +
      '</div>' +
      '<div class="masthead-side">' +
        '<div><p class="kicker">Status</p><p class="status-large">' +
          esc(sc.constraint_set_id === 'policy_test_v1' ? 'Draft policy-test run' : 'Screening-only run') + '</p></div>' +
        '<p>' + esc(sc.constraint_set_id === 'policy_test_v1'
          ? 'Generated by the FastAPI runner in this session using the Stage-1 draft policy set. Reserve margin and build caps apply; no transmission, timeslices, curtailment, or unit commitment. Not policy-grade.'
          : 'Generated by the FastAPI runner in this session. No reserve margin, transmission, curtailment, or build-rate constraints; never policy or forecast.') + '</p>' +
        '<p class="mono">solver: ' + esc(m.solver_status || '—') + '</p>' +
      '</div>' +
    '</div>' +
    '<div class="overview-callout"><strong>' +
      esc(sc.constraint_set_id === 'policy_test_v1' ? 'Draft policy test — not policy grade' : 'Screening only — not policy grade') +
      '</strong><br>This run uses constraint set <span class="mono">' + esc(sc.constraint_set_id || 'screening_v0') +
      '</span>. It is visible in the session rail, overview, dispatch, and comparison, but it is not Pareto eligible and is excluded from the canonical ledger.</div>' +
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
      harvest_m3_per_year: 'Harvest volume',
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
    if (key === 'harvest_m3_per_year') return '<span class="mono">' + fmt(value / 1e6, 2) + ' Mm³/yr (' + fmt(value, 0) + ' m³/yr)</span>';
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
    return ['overview', 'dispatch', 'tradeoff', 'explore', 'map', 'zones', 'economics', 'osecan', 'run', 'inputs'].indexOf(tab) !== -1 ? tab : 'overview';
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
    if (state.tab === 'explore') renderExplore();
    if (state.tab === 'map') { initMap(); renderBreadcrumb(); renderSelectionPanel(); }
    if (state.tab === 'zones') renderZones();
    if (state.tab === 'economics') { renderEconomics(); renderNational(); }
    if (state.tab === 'overview' && state.core) renderOverview();
    if (state.tab === 'osecan') renderOsecan();
    if (state.tab === 'run') renderRunMode();
    if (state.tab === 'inputs') renderInputsCharts();
    window.requestAnimationFrame(function () {
      if (state.tab === 'dispatch' && state.dispatch.chart) state.dispatch.chart.resize();
      if (state.tab === 'tradeoff' && state.tradeoffChart) state.tradeoffChart.resize();
      if (state.tab === 'explore') {
        if (state.paretoChart) state.paretoChart.resize();
        if (state.compareChart) state.compareChart.resize();
      }
      if (state.tab === 'inputs' && state.harvestChart) state.harvestChart.resize();
      if (state.tab === 'economics' && state.economicsChart) state.economicsChart.resize();
      if (state.tab === 'economics' && state.nationalChart) state.nationalChart.resize();
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
    document.addEventListener('click', function (event) {
      var link = event.target.closest('[data-linked-tab]');
      if (!link) return;
      var tab = link.getAttribute('data-linked-tab');
      if (tab !== 'map' && tab !== 'dispatch') return;
      activateTab(tab);
    });
  }

  function bindHashRouting() {
    window.addEventListener('hashchange', function () {
      if (!state.core) return;
      var hash = parseHash();
      var tab = validTab(hash.tab);
      if (hash.id && state.core.scenarioById.has(hash.id) && hash.id !== state.selectedId) {
        selectScenario(hash.id, { silent: true });
      }
      if (tab !== state.tab) activateTab(tab, { silent: true });
      var year = parseHashYear(hash.params);
      if (year !== null && year !== state.temporal.year) {
        setTemporalYear(year, { skipHash: true });
      }
      if (tab === 'map') {
        if (hash.params.sel) applySpatialHash(hash.params, { duration: 0 });
        else if (state.spatial.selection) clearSpatialSelection({ skipHash: true, fit: false, silent: true });
      }
    });
  }

  function parseHashQuery(query) {
    var out = {};
    query.split('&').forEach(function (pair) {
      if (!pair) return;
      var split = pair.indexOf('=');
      var key = split === -1 ? pair : pair.slice(0, split);
      var value = split === -1 ? '' : pair.slice(split + 1);
      try { out[decodeURIComponent(key)] = decodeURIComponent(value); } catch (error) { /* ignore malformed pair */ }
    });
    return out;
  }

  function parseHash() {
    var raw = window.location.hash.replace(/^#/, '');
    if (!raw) return { tab: null, id: null, params: {} };
    var query = '';
    var split = raw.indexOf('?');
    if (split !== -1) {
      query = raw.slice(split + 1);
      raw = raw.slice(0, split);
    }
    var parts = raw.split('/');
    var id = null;
    if (parts[1]) {
      try { id = decodeURIComponent(parts[1]); } catch (error) { id = parts[1]; }
    }
    return { tab: parts[0] || null, id: id, params: parseHashQuery(query) };
  }

  function applyExploreHash(params) {
    if (!params || !state.core) return;
    if (params.brush) {
      params.brush.split(';').forEach(function (chunk) {
        var pieces = chunk.split(':');
        if (pieces.length !== 3) return;
        var axis = PC_AXIS_BY_ID[pieces[0]];
        if (!axis) return;
        var min = finiteOrNull(pieces[1]);
        var max = finiteOrNull(pieces[2]);
        if (min === null || max === null || min > max) return;
        state.explore.brushes[axis.id] = { min: min, max: max };
      });
    }
    var selected = [params.px, params.py, params.pz];
    if (selected.every(function (id) { return id && PARETO_BY_ID[id]; }) && new Set(selected).size === 3) {
      state.explore.pareto = { x: selected[0], y: selected[1], z: selected[2] };
    }
    if (params.a && state.core.scenarioById.has(params.a)) state.explore.compare.a = params.a;
    if (params.b && state.core.scenarioById.has(params.b)) state.explore.compare.b = params.b;
    if (params.ov === '1') state.explore.overlay = true;
    if (params.en === 'fuel' || params.en === 'electricity') state.explore.energy = params.en;
  }

  function parseHashYear(params) {
    if (!params || !params.y) return null;
    var year = Number(params.y);
    if (!Number.isInteger(year) || year < YEAR_FIRST || year > YEAR_LAST) return null;
    return year;
  }

  function selectionHashKey() {
    var sel = state.spatial.selection;
    if (!sel) return null;
    return sel.kind + ':' + sel.id;
  }

  function parseSelectionKey(value) {
    if (!value) return null;
    var split = value.indexOf(':');
    if (split <= 0 || split === value.length - 1) return null;
    return { kind: value.slice(0, split), id: value.slice(split + 1) };
  }

  function applySpatialHash(params, options) {
    if (!params || !state.core) return;
    if (!params.sel) {
      if (state.spatial.selection) clearSpatialSelection({ skipHash: true, fit: false });
      return;
    }
    var parsed = parseSelectionKey(params.sel);
    if (!parsed) return;
    var data = state.mapState.data;
    if (!data) {
      state.mapState.pendingSelection = parsed;
      return;
    }
    restoreSelection(parsed, options || {});
  }

  function exploreHashQuery() {
    var parts = [];
    var brushed = PC_AXES.filter(function (axis) { return state.explore.brushes[axis.id]; });
    if (brushed.length) {
      parts.push('brush=' + encodeURIComponent(brushed.map(function (axis) {
        var brush = state.explore.brushes[axis.id];
        return axis.id + ':' + brush.min + ':' + brush.max;
      }).join(';')));
    }
    parts.push('px=' + encodeURIComponent(state.explore.pareto.x));
    parts.push('py=' + encodeURIComponent(state.explore.pareto.y));
    parts.push('pz=' + encodeURIComponent(state.explore.pareto.z));
    if (state.explore.compare.a) parts.push('a=' + encodeURIComponent(state.explore.compare.a));
    if (state.explore.compare.b) parts.push('b=' + encodeURIComponent(state.explore.compare.b));
    if (state.explore.overlay) parts.push('ov=1');
    if (state.explore.energy !== 'electricity') parts.push('en=' + encodeURIComponent(state.explore.energy));
    return parts.join('&');
  }

  function updateHash() {
    if (!state.core) return;
    var next = '#' + state.tab + '/' + encodeURIComponent(state.selectedId || '');
    var params = [];
    if (state.tab === 'explore') {
      var explore = exploreHashQuery();
      if (explore) params.push(explore);
    }
    if (state.tab === 'map') {
      var key = selectionHashKey();
      if (!key && state.mapState.pendingSelection) key = state.mapState.pendingSelection.kind + ':' + state.mapState.pendingSelection.id;
      if (key) params.push('sel=' + encodeURIComponent(key));
      params.push('y=' + state.temporal.year);
    } else if (state.tab === 'dispatch') {
      params.push('y=' + state.temporal.year);
    } else if (state.temporal.year !== TEMPORAL_DEFAULT) {
      params.push('y=' + state.temporal.year);
    }
    if (params.length) next += '?' + params.join('&');
    if (window.location.hash !== next) {
      try { window.history.replaceState(null, '', next); } catch (error) { /* file:// may refuse */ }
    }
  }

  /* ---------------- Shared temporal control ---------------- */

  function temporalSlider() { return $('year-global'); }

  function bindTemporalControls() {
    var slider = temporalSlider();
    if (!slider) return;
    slider.value = String(state.temporal.year);
    slider.addEventListener('input', function () {
      setTemporalYear(Number(slider.value), { source: 'slider' });
    });
    var play = $('year-play');
    if (play) play.addEventListener('click', toggleTemporalPlay);
    var reset = $('year-reset');
    if (reset) reset.addEventListener('click', function () {
      stopTemporalPlay();
      setTemporalYear(TEMPORAL_DEFAULT, { source: 'reset' });
    });
    updateTemporalUI();
    updateMapYearGauge();
  }

  function stopTemporalPlay() {
    if (state.temporal.timer) window.clearInterval(state.temporal.timer);
    state.temporal.timer = null;
    state.temporal.playing = false;
    updateTemporalUI();
  }

  function toggleTemporalPlay() {
    if (state.temporal.playing) {
      stopTemporalPlay();
      announce('Year playback paused at ' + state.temporal.year + '.');
      return;
    }
    if (state.temporal.year >= YEAR_LAST) setTemporalYear(YEAR_FIRST, { source: 'play' });
    state.temporal.playing = true;
    updateTemporalUI();
    announce('Playing years from ' + state.temporal.year + '.');
    state.temporal.timer = window.setInterval(function () {
      var next = state.temporal.year + 1;
      if (next > YEAR_LAST) {
        stopTemporalPlay();
        return;
      }
      setTemporalYear(next, { source: 'play' });
      if (next === YEAR_LAST) stopTemporalPlay();
    }, 620);
  }

  function ensureDispatchWindowIncludes(year) {
    var min = $('year-min');
    var max = $('year-max');
    if (!min || !max) return;
    var changed = false;
    if (year < state.dispatch.from) {
      state.dispatch.from = year;
      min.value = String(year);
      changed = true;
    }
    if (year > state.dispatch.to) {
      state.dispatch.to = year;
      max.value = String(year);
      changed = true;
    }
    if (!changed) return;
    $('year-min-out').textContent = String(state.dispatch.from);
    $('year-max-out').textContent = String(state.dispatch.to);
    $('year-range-label').textContent = state.dispatch.from + '—' + state.dispatch.to;
    if (state.dispatch.processed) renderDispatchChart();
  }

  function setTemporalYear(year, options) {
    var opts = options || {};
    var next = Math.max(YEAR_FIRST, Math.min(YEAR_LAST, Math.round(Number(year))));
    if (!Number.isFinite(next)) return;
    state.temporal.year = next;
    var slider = temporalSlider();
    if (slider && Number(slider.value) !== next) slider.value = String(next);
    if (opts.expandWindow !== false) ensureDispatchWindowIncludes(next);
    updateTemporalUI();
    updateMapYearGauge();
    updateDispatchYearGuide();
    renderDispatchSnapshot();
    if (!opts.skipHash) updateHash();
  }

  function updateTemporalUI() {
    var readout = $('year-global-out');
    if (readout) readout.textContent = String(state.temporal.year);
    var play = $('year-play');
    if (play) {
      play.classList.toggle('is-playing', state.temporal.playing);
      play.setAttribute('aria-pressed', state.temporal.playing ? 'true' : 'false');
      play.setAttribute('aria-label', state.temporal.playing ? 'Pause year playback' : 'Play years');
      play.textContent = state.temporal.playing ? '❚❚' : '▶';
    }
    var note = $('year-window-note');
    if (note) {
      var inside = state.temporal.year >= state.dispatch.from && state.temporal.year <= state.dispatch.to;
      note.textContent = 'window ' + state.dispatch.from + '—' + state.dispatch.to;
      note.classList.toggle('is-outside', !inside);
      if (!inside) note.textContent += ' · year outside window';
    }
  }

  function updateMapYearGauge() {
    var dot = $('map-year-gauge-dot');
    var label = $('map-year-gauge-label');
    if (!dot || !label) return;
    var fraction = (state.temporal.year - YEAR_FIRST) / (YEAR_LAST - YEAR_FIRST);
    dot.style.bottom = (fraction * 100).toFixed(2) + '%';
    label.textContent = String(state.temporal.year);
    var gauge = $('map-year-gauge');
    if (gauge) gauge.setAttribute('aria-label', 'Shared year ' + state.temporal.year);
  }

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
      if (!state.dispatch.zone) state.dispatch.zone = 'ALL';
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
      updateTemporalUI();
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
    if (state.spatial.mappedZone) {
      if (processed.zones.indexOf(state.spatial.mappedZone) === -1) state.dispatch.zone = 'ALL';
      else state.dispatch.zone = state.spatial.mappedZone;
      renderZoneOptions(processed);
    }
    if (state.tab === 'map') renderSelectionPanel();
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
      id: 'dispatch-total',
      type: 'line',
      symbol: 'none',
      z: 20,
      lineStyle: { width: 1.6, color: '#17251d', type: 'dashed' },
      itemStyle: { color: '#17251d' },
      emphasis: { focus: 'series' },
      tooltip: { show: false },
      markLine: {
        silent: true,
        symbol: 'none',
        animation: false,
        lineStyle: { color: '#a94f31', width: 1.5, type: 'solid' },
        label: {
          show: true,
          position: 'insideEndTop',
          rotate: 0,
          formatter: String(state.temporal.year),
          color: '#7e3823',
          fontFamily: 'IBM Plex Mono, monospace',
          fontSize: 10,
          backgroundColor: 'rgba(246, 223, 180, 0.92)',
          padding: [2, 4]
        },
        data: (state.temporal.year >= from && state.temporal.year <= to) ? [{ xAxis: state.temporal.year - from }] : []
      },
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
    updateTemporalUI();
    renderDispatchSnapshot();
  }

  function updateDispatchYearGuide() {
    var chart = state.dispatch.chart;
    if (!chart || !state.dispatch.processed) return;
    var year = state.temporal.year;
    var inside = year >= state.dispatch.from && year <= state.dispatch.to;
    chart.setOption({
      series: [{
        id: 'dispatch-total',
        markLine: {
          silent: true,
          symbol: 'none',
          animation: false,
          lineStyle: { color: '#a94f31', width: 1.5, type: 'solid' },
          label: {
            show: true,
            position: 'insideEndTop',
            rotate: 0,
            formatter: String(year),
            color: '#7e3823',
            fontFamily: 'IBM Plex Mono, monospace',
            fontSize: 10,
            backgroundColor: 'rgba(246, 223, 180, 0.92)',
            padding: [2, 4]
          },
          data: inside ? [{ xAxis: year - state.dispatch.from }] : []
        }
      }]
    });
  }

  function renderDispatchSnapshot() {
    var table = $('dispatch-snapshot');
    if (!table) return;
    var processed = state.dispatch.processed;
    var caption = $('dispatch-snapshot-caption');
    if (!processed) {
      table.innerHTML = '<caption>Waiting for the selected scenario output.</caption><tbody><tr><td>No dispatch rows loaded.</td></tr></tbody>';
      if (caption) caption.textContent = 'The shared year control selects the snapshot year. The year-window sliders above still control the chart extent.';
      return;
    }
    var year = state.temporal.year;
    var rows = filterDispatchRows(processed, state.dispatch.zone).filter(function (row) { return row.year === year; });
    var zoneLabel = state.dispatch.zone === 'ALL' ? processed.allLabel : 'Zone ' + state.dispatch.zone;
    var totals = {};
    TECH_FAMILIES.forEach(function (family) { totals[family.id] = 0; });
    rows.forEach(function (row) {
      if (!Object.prototype.hasOwnProperty.call(totals, row.family)) totals[row.family] = 0;
      totals[row.family] += row.value;
    });
    var grand = Object.keys(totals).reduce(function (sum, key) { return sum + totals[key]; }, 0);
    if (caption) {
      caption.textContent = zoneLabel + ' · ' + year + ' · ' + processed.selection.label + ' · ' +
        (processed.unit || '') + (state.dispatch.zone === 'ALL' && processed.hasCan
          ? ' · CAN aggregate rows only (no zone double-count).'
          : '');
    }
    if (!rows.length) {
      table.innerHTML = '<caption>' + esc(zoneLabel) + ' · ' + year + '</caption><tbody><tr><td>No ' +
        esc(processed.selection.label) + ' rows for this year in the selected window/zone.</td></tr></tbody>';
      return;
    }
    var body = TECH_FAMILIES.filter(function (family) { return Math.abs(totals[family.id] || 0) > 1e-9; }).map(function (family) {
      var value = totals[family.id] || 0;
      var share = grand > 0 ? (value / grand) * 100 : 0;
      return '<tr><td><i class="snapshot-swatch" style="background:' + family.color + '"></i> ' + esc(family.label) +
        '</td><td class="num">' + fmt(value, 2) + '</td><td class="num">' + fmt(share, 1) + '%</td></tr>';
    }).join('');
    var extras = Object.keys(totals).filter(function (key) { return !TECH_FAMILY_BY_ID[key] && Math.abs(totals[key]) > 1e-9; });
    extras.forEach(function (key) {
      var value = totals[key] || 0;
      body += '<tr><td>' + esc(key) + '</td><td class="num">' + fmt(value, 2) + '</td><td class="num">' +
        fmt(grand > 0 ? (value / grand) * 100 : 0, 1) + '%</td></tr>';
    });
    table.innerHTML = '<caption>' + esc(zoneLabel) + ' · ' + year + ' · ' + esc(processed.selection.label) +
      ' (' + esc(processed.unit || 'units') + ')</caption>' +
      '<thead><tr><th scope="col">Technology family</th><th scope="col" class="num">PJ</th><th scope="col" class="num">Share</th></tr></thead>' +
      '<tbody>' + body + '<tr class="snapshot-total"><th scope="row">Total</th><td class="num">' + fmt(grand, 2) +
      '</td><td class="num">100.0%</td></tr></tbody>';
  }

  function outputSourcePath(id) {
    var output = state.outputs.get(id);
    return output && output.source ? output.source : 'outputs/' + id + '.json';
  }

  /* ---------------- Zones: 13-zone table and year x zone heatmap ---------------- */

  function zoneNode(zoneId) {
    var nodes = state.core && state.core.zoneNodes;
    if (!nodes || !Array.isArray(nodes.zones)) return null;
    for (var i = 0; i < nodes.zones.length; i += 1) {
      if (nodes.zones[i].zone_id === zoneId) return nodes.zones[i];
    }
    return null;
  }

  function renderZoneGapChips() {
    var container = $('zone-gap-chips');
    if (!container || !state.core || !state.core.zoneNodes) return;
    container.innerHTML = state.core.zoneNodes.zones.filter(function (zone) { return zone.gap; }).map(function (zone) {
      return '<span class="zone-chip" title="' + esc(zone.gap_reason || 'Explicit gap') + '">' + esc(zone.zone_id) + '</span>';
    }).join('');
  }

  function zoneMetricOptions(energy) {
    return energy === 'fuel'
      ? [
        { id: 'total', label: 'Total fuel supply' },
        { id: 'bcw', label: 'BCW fuel supply' }
      ]
      : [
        { id: 'total', label: 'Total electricity dispatch' },
        { id: 'pwrbcw', label: 'PWRBCW biomass electricity' }
      ];
  }

  function zoneMetricLabel(energy, metric) {
    var options = zoneMetricOptions(energy);
    for (var i = 0; i < options.length; i += 1) {
      if (options[i].id === metric) return options[i].label;
    }
    return options[0].label;
  }

  function processZones(output, energy, metric) {
    var selection = categorySelection(output, energy);
    if (!selection) selection = categorySelection(output, energy === 'electricity' ? 'fuel' : 'electricity');
    if (!selection) return null;
    var electricity = categorySelection(output, 'electricity');
    var fuel = categorySelection(output, 'fuel');
    var electricityCategories = new Set(electricity ? electricity.categories : []);
    var fuelCategories = new Set(fuel ? fuel.categories : []);
    if (!electricityCategories.size && !fuelCategories.size) return null;
    var yearsSet = new Set();
    var byZone = new Map();
    for (var i = 0; i < output.row_count; i += 1) {
      var category = output.category[i];
      var isElectricity = electricityCategories.has(category);
      var isFuel = fuelCategories.has(category);
      if (!isElectricity && !isFuel) continue;
      var zone = output.zone[i];
      if (!zone) continue;
      var year = Number(output.year[i]);
      if (!Number.isFinite(year)) continue;
      var value = Number(output.value_pj[i]);
      if (!Number.isFinite(value)) continue;
      yearsSet.add(year);
      if (!byZone.has(zone)) byZone.set(zone, new Map());
      var years = byZone.get(zone);
      if (!years.has(year)) {
        years.set(year, { dispatch: null, supply: null, bcw: null, pwrbcw: null, rows: 0 });
      }
      var cell = years.get(year);
      cell.rows += 1;
      var technology = String(output.technology[i] || '');
      if (isElectricity) {
        cell.dispatch = (cell.dispatch === null ? 0 : cell.dispatch) + value;
        if (/^PWRBCW/.test(technology)) cell.pwrbcw = (cell.pwrbcw === null ? 0 : cell.pwrbcw) + value;
      }
      if (isFuel) {
        cell.supply = (cell.supply === null ? 0 : cell.supply) + value;
        if (/^SUPBCW/.test(technology)) cell.bcw = (cell.bcw === null ? 0 : cell.bcw) + value;
      }
    }
    if (!yearsSet.size) return null;
    var years = Array.from(yearsSet).sort(function (a, b) { return a - b; });
    return {
      byZone: byZone,
      years: years,
      latest: years[years.length - 1],
      selection: selection,
      energy: energy,
      metric: metric,
      unit: selection.unit
    };
  }

  function zoneCellValue(processed, zone, year) {
    var years = processed.byZone.get(zone);
    if (!years || !years.has(year)) return { present: false, value: null };
    var cell = years.get(year);
    var value = processed.metric === 'bcw' ? cell.bcw : (processed.metric === 'pwrbcw' ? cell.pwrbcw : (processed.energy === 'fuel' ? cell.supply : cell.dispatch));
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return { present: false, value: null };
    }
    return { present: true, value: Number(value) };
  }

  function zoneTableValue(processed, zone, year, field) {
    var years = processed.byZone.get(zone);
    if (!years || !years.has(year)) return null;
    var cell = years.get(year);
    var value = cell[field];
    return value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value);
  }

  function zoneStatus(zone) {
    if (zone === 'CAN') {
      return '<span class="badge badge--family">national aggregate · never summed</span>';
    }
    var node = zoneNode(zone);
    if (node && node.anchor) {
      return '<span class="badge badge--fleet" title="Authoritative point anchor only; no polygon">anchor · ' + esc(zone) + '</span>';
    }
    var reason = node && node.gap_reason ? node.gap_reason : 'No authoritative zone anchor; do not invent geometry.';
    return '<span class="badge badge--superseded" title="' + esc(reason) + '">gap · no geometry</span>';
  }

  function zoneNumber(value) {
    return value === null || value === undefined ? '<span class="zone-missing" title="No row for this zone/year; not zero">n/r</span>' : fmt(value, 2);
  }

  function populateZoneControls(processed) {
    var energy = state.zones.energy;
    var metricSelect = $('zones-metric');
    var yearSelect = $('zones-year');
    if (!metricSelect || !yearSelect) return;
    if (processed) {
      var options = zoneMetricOptions(energy);
      var validMetric = options.some(function (option) { return option.id === state.zones.metric; });
      if (!validMetric) state.zones.metric = 'total';
      metricSelect.innerHTML = options.map(function (option) {
        return '<option value="' + esc(option.id) + '">' + esc(option.label) + '</option>';
      }).join('');
      metricSelect.value = state.zones.metric;

      if (state.zones.year === null || processed.years.indexOf(Number(state.zones.year)) === -1) {
        state.zones.year = processed.latest;
      }
      yearSelect.innerHTML = processed.years.slice().reverse().map(function (year) {
        return '<option value="' + year + '">' + year + (year === processed.latest ? ' · latest' : '') + '</option>';
      }).join('');
      yearSelect.value = String(state.zones.year);
      yearSelect.disabled = false;
    } else {
      metricSelect.innerHTML = zoneMetricOptions(energy).map(function (option) {
        return '<option value="' + esc(option.id) + '">' + esc(option.label) + '</option>';
      }).join('');
      yearSelect.innerHTML = '<option>—</option>';
      yearSelect.disabled = true;
    }
  }

  function renderZonesTable(processed) {
    var table = $('zones-table');
    if (!table) return;
    var year = Number(state.zones.year);
    var metric = processed.metric;
    var sc = state.core.scenarioById.get(state.selectedId);
    var rows = C2070_ZONES.map(function (zone) {
      var meta = ZONE_META[zone];
      var dispatch = zoneTableValue(processed, zone, year, 'dispatch');
      var supply = zoneTableValue(processed, zone, year, 'supply');
      var bcw = zoneTableValue(processed, zone, year, 'bcw');
      var pwrbcw = zoneTableValue(processed, zone, year, 'pwrbcw');
      var dispatchCell = zoneNumber(dispatch);
      var supplyCell = zoneNumber(supply);
      return '<tr data-zone-row="' + esc(zone) + '">' +
        '<td><button class="zone-link" type="button" data-zone-dispatch="' + esc(zone) + '" title="Filter Dispatch to zone ' + esc(zone) + '">' + esc(zone) + '</button></td>' +
        '<td>' + esc(meta.name) + '</td>' +
        '<td class="zone-provinces">' + esc(meta.provinces) + '</td>' +
        '<td class="num">' + dispatchCell + '</td>' +
        '<td class="num">' + supplyCell + '</td>' +
        '<td class="num">' + zoneNumber(bcw) + '</td>' +
        '<td class="num">' + zoneNumber(pwrbcw) + '</td>' +
        '<td>' + zoneStatus(zone) + '</td></tr>';
    }).join('');

    var hasCan = processed.byZone.has('CAN');
    var canDispatch = zoneTableValue(processed, 'CAN', year, 'dispatch');
    var canSupply = zoneTableValue(processed, 'CAN', year, 'supply');
    var canBcw = zoneTableValue(processed, 'CAN', year, 'bcw');
    var canPwrbcw = zoneTableValue(processed, 'CAN', year, 'pwrbcw');
    var canRow = '<tr class="zone-row--national" data-zone-row="CAN">' +
      '<td><button class="zone-link" type="button" data-zone-dispatch="CAN" title="Show the national CAN aggregate in Dispatch">CAN</button></td>' +
      '<td>Canada national aggregate</td>' +
      '<td class="zone-provinces">Canada</td>' +
      '<td class="num">' + (hasCan ? zoneNumber(canDispatch) : '<span class="zone-missing" title="CAN dispatch rows are not present in this output; not zero">not reported</span>') + '</td>' +
      '<td class="num">' + (hasCan ? zoneNumber(canSupply) : '<span class="zone-missing" title="CAN fuel rows are not present; not zero">not reported</span>') + '</td>' +
      '<td class="num">' + (hasCan ? zoneNumber(canBcw) : 'n/r') + '</td>' +
      '<td class="num">' + (hasCan ? zoneNumber(canPwrbcw) : 'n/r') + '</td>' +
      '<td>' + zoneStatus('CAN') + '</td></tr>';

    table.innerHTML = '<caption>' + esc(sc ? sc.label : state.selectedId) + ' · ' + (sc && sc.liveRun ? 'live session run' : 'static/committed output') +
      ' · ' + year + ' · ' + esc(processed.selection.label) + ' · ' + esc(processed.unit) +
      '. The CAN row is separate and never added to zone rows.</caption>' +
      '<thead><tr><th scope="col">Zone</th><th scope="col">Name</th><th scope="col">Provinces / regions</th>' +
      '<th scope="col" class="num">Electricity dispatch<br><span class="zone-unit">PJ elec</span></th>' +
      '<th scope="col" class="num">Fuel supply<br><span class="zone-unit">PJ fuel</span></th>' +
      '<th scope="col" class="num">BCW supply<br><span class="zone-unit">PJ fuel</span></th>' +
      '<th scope="col" class="num">PWRBCW<br><span class="zone-unit">PJ elec</span></th>' +
      '<th scope="col">Geometry status</th></tr></thead>' +
      '<tbody class="zone-tbody">' + rows + '</tbody>' +
      '<tbody class="zone-tbody zone-tbody--national">' + canRow + '</tbody>';

    var missing = C2070_ZONES.filter(function (zone) {
      return !processed.byZone.has(zone) || !processed.byZone.get(zone).has(year);
    }).length;
    var summary = $('zones-summary');
    if (summary) {
      summary.innerHTML = '<div><span class="zone-summary-kicker">Zone rows</span><strong>13 + CAN</strong></div>' +
        '<div><span class="zone-summary-kicker">Anchors</span><strong>B1 · B2</strong></div>' +
        '<div><span class="zone-summary-kicker">Explicit gaps</span><strong>11</strong></div>' +
        '<div><span class="zone-summary-kicker">Missing this year</span><strong>' + missing + ' zone' + (missing === 1 ? '' : 's') + '</strong></div>';
    }
    var note = $('zones-source-note');
    if (note) {
      note.textContent = 'Year ' + year + ' · metric column ' + zoneMetricLabel(processed.energy, metric) +
        ' · source ' + outputSourcePath(state.selectedId) + ' · units ' + processed.unit +
        '. Electricity and fuel are never summed. n/r means no row for that zone/year, not zero. Zone names/provinces: ' + ZONE_SOURCE +
        '. Only B1/B2 have point anchors; 11 zones are explicit gaps with no polygon. ' +
        'Click a zone ID to filter Dispatch; Overview remains national because no zone-level overview ledger exists.';
    }
  }

  function zoneHeatColor(t) {
    var stops = [[247, 239, 221], [242, 180, 92], [169, 79, 49]];
    var clamped = Math.max(0, Math.min(1, t));
    var scaled = clamped * (stops.length - 1);
    var index = Math.min(stops.length - 2, Math.floor(scaled));
    var fraction = scaled - index;
    var a = stops[index];
    var b = stops[index + 1];
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * fraction) + ',' +
      Math.round(a[1] + (b[1] - a[1]) * fraction) + ',' +
      Math.round(a[2] + (b[2] - a[2]) * fraction) + ')';
  }

  function renderZonesHeatmap(processed) {
    var table = $('zones-heatmap');
    if (!table) return;
    var metric = processed.metric;
    var values = [];
    processed.byZone.forEach(function (years) {
      years.forEach(function (cell) {
        var value = metric === 'bcw' ? cell.bcw : (metric === 'pwrbcw' ? cell.pwrbcw : (processed.energy === 'fuel' ? cell.supply : cell.dispatch));
        if (value !== null && value !== undefined && Number.isFinite(Number(value))) values.push(Number(value));
      });
    });
    var min = values.length ? Math.min.apply(null, values) : 0;
    var max = values.length ? Math.max.apply(null, values) : 0;
    var span = max - min;
    var metricLabel = zoneMetricLabel(processed.energy, metric);
    var unit = processed.unit;
    var zoneRows = C2070_ZONES.concat(['CAN']);
    var header = processed.years.map(function (year) {
      return '<th scope="col" class="zone-heat-year">' + year + '</th>';
    }).join('');
    var body = zoneRows.map(function (zone) {
      var meta = zone === 'CAN' ? { name: 'National aggregate (separate)', provinces: 'Canada' } : ZONE_META[zone];
      var cells = processed.years.map(function (year) {
        var result = zoneCellValue(processed, zone, year);
        if (!result.present) {
          return '<td class="zone-heat-cell is-missing" title="' + esc(zoneHeatLabel(zone, year, metricLabel, null, unit)) +
            '" aria-label="' + esc(zoneHeatLabel(zone, year, metricLabel, null, unit)) + '">·</td>';
        }
        var t = span > 0 ? (result.value - min) / span : 0.55;
        var color = zoneHeatColor(0.12 + t * 0.88);
        var textColor = t > 0.62 ? '#fffdf7' : '#17251d';
        return '<td class="zone-heat-cell" style="background:' + color + ';color:' + textColor + '" title="' +
          esc(zoneHeatLabel(zone, year, metricLabel, result.value, unit)) + '" aria-label="' +
          esc(zoneHeatLabel(zone, year, metricLabel, result.value, unit)) + '">' + esc(fmt(result.value, result.value < 10 ? 1 : 0)) + '</td>';
      }).join('');
      return '<tr class="' + (zone === 'CAN' ? 'zone-row--national' : '') + '">' +
        '<th scope="row" class="zone-heat-zone">' + esc(zone) + '</th>' +
        '<td class="zone-heat-name">' + esc(meta.name) + '</td>' + cells + '</tr>';
    }).join('');
    table.innerHTML = '<caption>' + esc(metricLabel) + ' · ' + esc(unit) + ' · 2021—2070. CAN is a separate national row; it is never summed with zones. Empty cells (·) mean no row, not zero.</caption>' +
      '<thead><tr><th scope="col" class="zone-heat-zone">Zone</th><th scope="col" class="zone-heat-name">Name</th>' + header + '</tr></thead>' +
      '<tbody>' + body + '</tbody>';
    var legend = $('zones-heatmap-legend');
    if (legend) {
      if (!values.length) {
        legend.innerHTML = '<span class="zone-missing">No ' + esc(metricLabel) + ' rows in this output.</span>';
      } else {
        var stepCount = 5;
        var swatches = [];
        for (var i = 0; i < stepCount; i += 1) {
          var t = i / (stepCount - 1);
          swatches.push('<i style="background:' + zoneHeatColor(0.12 + t * 0.88) + '"></i>');
        }
        legend.innerHTML = '<span class="mono">' + fmt(min, 2) + '</span><span class="zone-heat-scale" aria-hidden="true">' +
          swatches.join('') + '</span><span class="mono">' + fmt(max, 2) + ' ' + esc(unit) + '</span>' +
          '<span class="zone-missing-key"><i></i>no row · not zero</span>';
      }
    }
  }

  function zoneHeatLabel(zone, year, metricLabel, value, unit) {
    var zoneName = zone === 'CAN' ? 'Canada national aggregate' : ZONE_META[zone].name;
    return zone + ' ' + zoneName + ' · ' + year + ' · ' + metricLabel + ' · ' +
      (value === null ? 'no row; not zero' : fmt(value, 2) + ' ' + unit);
  }

  async function renderZones() {
    if (!state.core || !state.selectedId) return;
    var token = Math.max(state.zones.token, 0) + 1;
    state.zones.token = token;
    var loading = $('zones-loading');
    var empty = $('zones-empty');
    var content = $('zones-content');
    if (loading) loading.hidden = false;
    if (empty) empty.hidden = true;
    if (content) content.hidden = true;
    var output;
    try {
      output = await getOutput(state.selectedId);
    } catch (error) {
      if (token !== state.zones.token) return;
      if (loading) loading.hidden = true;
      if (empty) {
        empty.hidden = false;
        empty.innerHTML = '<div><strong>Zone ledger unavailable for this scenario.</strong><br>' + esc(error.message) + '</div>';
      }
      return;
    }
    if (token !== state.zones.token) return;
    var processed;
    try {
      processed = processZones(output, state.zones.energy, state.zones.metric);
    } catch (error) {
      processed = null;
      if (empty) {
        empty.hidden = false;
        empty.innerHTML = '<div><strong>Zone guard tripped.</strong><br>' + esc(error.message) + '</div>';
      }
    }
    if (loading) loading.hidden = true;
    if (!processed) {
      if (empty) {
        empty.hidden = false;
        if (!empty.innerHTML) {
          empty.innerHTML = '<div><strong>No ' + esc(state.zones.energy) + ' ledger in this scenario.</strong><br>' +
            'Switch ledger or select another scenario. Electricity and fuel are never summed.</div>';
        }
      }
      populateZoneControls(null);
      return;
    }
    state.zones.processed = processed;
    if (content) content.hidden = false;
    populateZoneControls(processed);
    renderZonesTable(processed);
    renderZonesHeatmap(processed);
  }

  function filterDispatchFromZone(zone) {
    state.dispatch.energy = state.zones.energy;
    qsa('input[name="energy-ledger"]').forEach(function (radio) {
      radio.checked = radio.value === state.zones.energy;
    });
    if (zone === 'CAN') {
      state.dispatch.zone = 'ALL';
      state.spatial.mappedZone = null;
    } else {
      state.dispatch.zone = zone;
      state.spatial.mappedZone = zone;
    }
    announce('Dispatch filtered to ' + (zone === 'CAN' ? 'the national CAN aggregate' : 'zone ' + zone) + '.');
    activateTab('dispatch');
    renderDispatch();
  }

  function bindZonesControls() {
    qsa('input[name="zone-energy"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        if (!radio.checked) return;
        state.zones.energy = radio.value;
        state.zones.metric = 'total';
        state.zones.year = null;
        renderZones();
      });
    });
    var metric = $('zones-metric');
    if (metric) {
      metric.addEventListener('change', function () {
        state.zones.metric = metric.value;
        if (state.zones.processed) {
          renderZonesTable(state.zones.processed);
          renderZonesHeatmap(state.zones.processed);
        }
      });
    }
    var year = $('zones-year');
    if (year) {
      year.addEventListener('change', function () {
        state.zones.year = Number(year.value);
        if (state.zones.processed) renderZonesTable(state.zones.processed);
      });
    }
    var table = $('zones-table');
    if (table) {
      table.addEventListener('click', function (event) {
        var button = event.target.closest('[data-zone-dispatch]');
        if (!button) return;
        filterDispatchFromZone(button.getAttribute('data-zone-dispatch'));
      });
    }
  }

  /* ---------------- Trade-off ---------------- */

  function allocationScreenBadge(screen) {
    if (!screen) return '<span class="badge badge--ink">not screened</span>';
    if (screen.status === 'pass') {
      return '<span class="badge badge--feas-pass">feasible · 0 deficit</span>';
    }
    var envelope = screen.national_envelope_status === 'pass'
      ? ' · national envelope pass'
      : ' · national envelope short ' + fmt(-screen.national_envelope_deficit_m3 / 1e6, 3) + ' Mm³';
    return '<span class="badge badge--feas-fail" title="Per-province min(documented, I × operable)' + esc(envelope) + '">deficit ' + fmt(screen.deficit_m3 / 1e6, 3) + ' Mm³</span>';
  }

  function allocationRoleBadge(role) {
    return role === 'reallocated'
      ? '<span class="badge badge--allocation">reallocated</span>'
      : '<span class="badge badge--ink">observed 2024</span>';
  }

  function allocationRow(pair, scenario, role) {
    var isObserved = role === 'observed';
    var screen10 = isObserved ? null : pair.intensity['1.0'];
    var screen12 = isObserved ? null : pair.intensity['1.2'];
    return '<tr' + (scenario && scenario.scenario_id === state.selectedId ? ' class="is-selected"' : '') + '>' +
      '<td><button class="link-button" type="button" data-allocation-scenario="' + esc(scenario.scenario_id) + '">' +
        esc(scenario.label) + '</button><span class="unit mono">' + esc(scenario.scenario_id) + '</span></td>' +
      '<td>' + allocationRoleBadge(role) + '</td>' +
      '<td>' + fmt(pair.harvest_m3 / 1e6, 0) + ' Mm³</td>' +
      '<td>' + feasibilityBadge(scenario) + '</td>' +
      '<td>' + fmt(scenario.metrics.objective, 2) + '</td>' +
      '<td>' + fmt(scenario.metrics.emissions_total, 3) + '</td>' +
      '<td>' + allocationScreenBadge(screen10) + '</td>' +
      '<td>' + allocationScreenBadge(screen12) + '</td></tr>';
  }

  function allocationProvinceTable(pair) {
    var operableByProvince = {};
    if (state.core.operable) {
      state.core.operable.by_province.forEach(function (row) {
        operableByProvince[row.province] = row;
      });
    }
    var rows = pair.province_rows.map(function (row) {
      var cls = row.delta_m3 > 1.0 ? 'is-pos' : (row.delta_m3 < -1.0 ? 'is-neg' : '');
      var op = operableByProvince[row.province];
      var opText = !op ? '—' : (op.operable_proxy_ha == null ? 'missing' : fmt(op.operable_proxy_ha / 1e6, 2));
      return '<tr><th scope="row">' + esc(row.province) + '</th>' +
        '<td>' + fmt(row.observed_2024_m3 / 1e6, 3) + '</td>' +
        '<td>' + fmt(row.harvest_m3 / 1e6, 3) + '</td>' +
        '<td class="' + cls + '">' + (row.delta_m3 >= 0 ? '+' : '') + fmt(row.delta_m3 / 1e6, 3) + '</td>' +
        '<td>' + fmt(row.upper_bound_m3 / 1e6, 3) + '</td>' +
        '<td>' + opText + '</td>' +
        '<td>' + (row.binding ? '<span class="badge badge--diag">binding</span>' : '') + '</td></tr>';
    }).join('');
    return '<table class="data-table data-table--compact"><caption>Mm³/yr and Mha. Delta is LP reallocated − observed 2024 share. Operable is the base-case screening proxy; PE/NU are missing, not zero.</caption>' +
      '<thead><tr><th scope="col">Province</th><th scope="col">Observed</th><th scope="col">LP</th>' +
      '<th scope="col">Δ</th><th scope="col">Cap</th><th scope="col">Operable (Mha)</th><th scope="col">Status</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function allocationOperableContext(comparison) {
    var op = comparison.operable_proxy;
    if (!op) return '';
    var pair150 = comparison.pairs.filter(function (pair) { return pair.harvest_m3 === 150000000; })[0];
    if (!pair150) return '';
    var env = comparison.national_envelope || {};
    var env10 = env['1.0'] || {};
    var env12 = env['1.2'] || {};
    return '<div class="operable-context" role="note">' +
      '<div class="operable-context__head"><span class="kicker">Screening landbase</span>' +
      '<strong>Operable proxy · ' + fmt(op.national_operable_proxy_ha / 1e6, 2) + ' Mha</strong></div>' +
      '<p>' + fmt(op.operable_share_of_managed_proxy * 100, 1) + '% of the 166.28 Mha managed proxy. ' +
      'Missing: ' + esc((op.missing_provinces || []).join(', ')) + ' — documented caps retained, not zero.</p>' +
      '<dl class="operable-stats">' +
      '<div><dt>1.0 m³/ha/yr</dt><dd class="is-neg">provincial LP −' + fmt(pair150.intensity['1.0'].deficit_m3 / 1e6, 3) + ' Mm³</dd><dd class="mono small">national envelope −' + fmt(env10.deficits_m3 ? env10.deficits_m3['150m'] / 1e6 : 0, 3) + ' Mm³</dd></div>' +
      '<div><dt>1.2 m³/ha/yr</dt><dd class="is-neg">provincial LP −' + fmt(pair150.intensity['1.2'].deficit_m3 / 1e6, 3) + ' Mm³</dd><dd class="mono small">national envelope ' + (env12.deficits_m3 && env12.deficits_m3['150m'] > 0 ? '−' + fmt(env12.deficits_m3['150m'] / 1e6, 3) + ' Mm³' : 'pass') + '</dd></div>' +
      '</dl>' +
      '<p class="mono small">Screening proxy, not a legal THLB. 150 Mm³ stays conditional.</p>' +
      '</div>';
  }

  function renderAllocationComparison() {
    var block = $('allocation-block');
    var table = $('allocation-table');
    var detail = $('allocation-province');
    var operable = $('allocation-operable');
    var caveat = $('allocation-caveat');
    if (!block || !table || !detail || !caveat || !state.core) return;
    var comparison = state.core.scenarios.allocation_screening;
    if (!comparison) { block.hidden = true; return; }
    block.hidden = false;
    caveat.textContent = comparison.thlb_caveat;
    if (operable) operable.innerHTML = allocationOperableContext(comparison);
    var body = '';
    comparison.pairs.forEach(function (pair) {
      var observed = state.core.scenarioById.get(pair.observed_scenario_id);
      var reallocated = state.core.scenarioById.get(pair.reallocated_scenario_id);
      if (!observed || !reallocated) return;
      body += allocationRow(pair, observed, 'observed');
      body += allocationRow(pair, reallocated, 'reallocated');
    });
    table.innerHTML = '<thead><tr><th scope="col">Case</th><th scope="col">Allocation</th>' +
      '<th scope="col">Harvest</th><th scope="col">Feasibility</th><th scope="col">Objective (M$)</th>' +
      '<th scope="col">Cumulative emissions (Mt CO₂)</th><th scope="col">1.0 m³/ha/yr screen</th>' +
      '<th scope="col">1.2 m³/ha/yr screen</th></tr></thead><tbody>' + body + '</tbody>';
    detail.innerHTML = comparison.pairs.map(function (pair) {
      return '<details class="fold-block"><summary>' + fmt(pair.harvest_m3 / 1e6, 0) +
        ' Mm³ provincial reallocation detail</summary>' + allocationProvinceTable(pair) + '</details>';
    }).join('');
  }

  function bindAllocationControls() {
    var table = $('allocation-table');
    if (!table || table.getAttribute('data-bound') === 'true') return;
    table.setAttribute('data-bound', 'true');
    table.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-allocation-scenario]');
      if (!button) return;
      selectScenario(button.getAttribute('data-allocation-scenario'));
    });
  }

  function bindNationalControls() {
    var select = $('national-scenario');
    if (!select || select.getAttribute('data-bound') === 'true') return;
    select.setAttribute('data-bound', 'true');
    select.value = state.nationalScenario;
    select.addEventListener('change', function () {
      var scenario = select.value;
      if (!state.national || !nationalScenarioById(scenario)) return;
      state.nationalScenario = scenario;
      renderNational();
      announce('National economics scenario: ' + nationalScenarioById(scenario).label + '.');
    });
  }

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
    if (missing.length || eligible.length !== expected.length) {
      guard.hidden = false;
      guard.textContent = 'Guard: the ' + expected.length + '-ID Pareto ledger and the flag-derived eligible set disagree. ' +
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
            symbol: sc.flags.canonical ? 'circle' : 'diamond',
            symbolSize: selected ? 19 : 13,
            itemStyle: selected
              ? { color: familyColor(family), borderColor: '#d98a2b', borderWidth: 3, shadowBlur: 14, shadowColor: 'rgba(217,138,43,0.55)' }
              : { color: familyColor(family), borderColor: '#fbf6e9', borderWidth: 1 },
            meta: {
              id: sc.scenario_id,
              family: sc.family_label,
              label: sc.label,
              role: sc.flags.canonical ? 'canonical/base' : 'sensitivity · exogenous harvest',
              objective: sc.metrics.objective,
              emissions: sc.metrics.emissions_total,
              national: sc.metrics.national_bcw_2050,
              share: sc.metrics.bio_share_2050
            }
          };
        })
      };
    });

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
            '<div class="chart-tip-sub">' + esc(meta.family) + ' · ' + esc(meta.role || '') + '</div>' +
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
    var preFleet = state.core.scenarios.scenarios.filter(function (sc) { return sc.flags.superseded_for_fleet; }).length;
    var diagnostic = state.core.scenarios.scenarios.filter(function (sc) { return sc.flags.diagnostic; }).length;
    var sensitivityIds = state.core.scenarios.tradeoff.sensitivity_scenario_ids || [];
    $('tradeoff-count').textContent = eligible.length + ' Pareto eligible · canonical 119 Mm³ only · ' +
      sensitivityIds.length + ' harvest sensitivity/compare excluded (100 pass, 150 conditional, 200 fail; harvest exogenous) · ' +
      (state.core.scenarios.tradeoff.allocation_scenario_ids || []).length + ' allocation-LP comparison rows (not Pareto) · ' +
      (state.core.scenarios.scenarios.length - eligible.length) + ' historical/excluded (' + preFleet +
      ' pre-fleet, ' + superseded + ' emissions-superseded, ' + diagnostic + ' diagnostic) · expected ledger ' + expected.length;
    renderAllocationComparison();
  }

  /* ---------------- Explore: parallel coordinates, Pareto, compare ---------------- */

  function exploreRows() {
    return state.compare.concat(state.live.sessionRuns);
  }

  function exploreIsLive(sc) {
    return !!(sc && sc.liveRun);
  }

  function exploreGrade(sc) {
    if (exploreIsLive(sc)) return 'live · screening_only';
    var feasibility = sc.feasibility_status ? ' · ' + feasibilityLabel(sc.feasibility_status) : '';
    if (sc.flags.canonical) return 'fleet-corrected · canonical/base · Pareto eligible';
    if (sc.family === FLEET_LADDER_FAMILY) return 'fleet-corrected · harvest sensitivity' + feasibility;
    if (sc.family === ALLOCATION_FAMILY) return 'fleet-corrected · allocation screening · reallocated' + feasibility + ' · not Pareto';
    return 'historical · not comparable' + feasibility;
  }

  function explorePassesBrushes(sc) {
    var ids = Object.keys(state.explore.brushes);
    for (var i = 0; i < ids.length; i += 1) {
      var axis = PC_AXIS_BY_ID[ids[i]];
      if (!axis) continue;
      var value = axis.get(sc);
      if (value === null || value === undefined) return false;
      var brush = state.explore.brushes[ids[i]];
      if (value < brush.min || value > brush.max) return false;
    }
    return true;
  }

  function pcDomain(axis, rows) {
    var values = [];
    rows.forEach(function (sc) {
      var value = axis.get(sc);
      if (value !== null && value !== undefined && Number.isFinite(value)) values.push(value);
    });
    if (!values.length) return { min: 0, max: 1 };
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    if (max - min < 1e-12) {
      var pad = Math.abs(max) * 0.02 || 1;
      min -= pad;
      max += pad;
    }
    return { min: min, max: max };
  }

  function pcAxisShortUnit(axis) {
    var units = {
      objective: 'M$',
      emissions_total: 'Mt CO₂',
      national_bcw_2050: 'PJ fuel',
      pwrbcw_2050: 'PJ elec',
      bio_share_2050: '% elec',
      harvest: 'Mm³/yr',
      carbon_price_usd_per_t: '$/t',
      recovery_fraction: 'fraction',
      pwrbio_cap: 'PJ',
      nuclear_cap_gw: 'GW',
      wind_cap: '%'
    };
    return units[axis.id] || axis.unit;
  }

  function pcLineTitle(sc) {
    var bits = [sc.label, sc.scenario_id, exploreGrade(sc), sc.pareto_eligible ? 'Pareto eligible' : 'not Pareto eligible'];
    ['objective', 'emissions_total', 'national_bcw_2050', 'bio_share_2050'].forEach(function (id) {
      var axis = PC_AXIS_BY_ID[id];
      var value = axis.get(sc);
      bits.push(axis.label + ': ' + (value === null ? 'not set' : axis.format(value)));
    });
    return bits.join('\n');
  }

  function renderParallelCoordinates() {
    var chart = $('pc-chart');
    if (!chart || !state.core) return;
    var rows = exploreRows();
    var width = Math.max(960, chart.clientWidth || 0);
    var height = 518;
    var margin = { top: 106, right: 44, bottom: 34, left: 42 };
    var plotWidth = width - margin.left - margin.right;
    var plotHeight = height - margin.top - margin.bottom;
    var stepX = PC_AXES.length > 1 ? plotWidth / (PC_AXES.length - 1) : 0;
    var positions = PC_AXES.map(function (axis, index) { return margin.left + index * stepX; });
    var domains = PC_AXES.map(function (axis) { return pcDomain(axis, rows); });
    var brushIds = Object.keys(state.explore.brushes);
    var visibleCount = 0;

    function yFor(value, domain) {
      var ratio = (value - domain.min) / (domain.max - domain.min);
      return margin.top + plotHeight - Math.max(0, Math.min(1, ratio)) * plotHeight;
    }

    function valueForY(y, domain) {
      var ratio = (margin.top + plotHeight - y) / plotHeight;
      return domain.min + Math.max(0, Math.min(1, ratio)) * (domain.max - domain.min);
    }

    var svg = ['<svg viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + height + '" aria-hidden="true" focusable="false">'];

    PC_AXES.forEach(function (axis, index) {
      var x = positions[index];
      var domain = domains[index];
      var brushed = !!state.explore.brushes[axis.id];
      svg.push('<line class="pc-axis-line' + (brushed ? ' is-brushed' : '') + '" x1="' + x + '" y1="' + margin.top + '" x2="' + x + '" y2="' + (margin.top + plotHeight) + '"/>');
      for (var t = 0; t <= 2; t += 1) {
        var tickValue = domain.min + (domain.max - domain.min) * (t / 2);
        var tickY = yFor(tickValue, domain);
        var anchor = index === PC_AXES.length - 1 ? 'end' : 'start';
        var tickX = index === PC_AXES.length - 1 ? x - 7 : x + 7;
        svg.push('<line class="pc-tick-line" x1="' + (x - 5) + '" y1="' + tickY.toFixed(1) + '" x2="' + (x + 5) + '" y2="' + tickY.toFixed(1) + '"/>');
        svg.push('<text class="pc-tick" x="' + tickX + '" y="' + (tickY + 3).toFixed(1) + '" text-anchor="' + anchor + '">' + esc(axis.tick(tickValue)) + '</text>');
      }
      var labelAnchor = index === PC_AXES.length - 1 ? 'end' : 'start';
      var labelX = index === PC_AXES.length - 1 ? x + 8 : x - 8;
      svg.push('<text class="pc-axis-name" x="' + labelX + '" y="' + (margin.top - 26) + '" text-anchor="' + labelAnchor + '" transform="rotate(-38 ' + labelX + ' ' + (margin.top - 26) + ')">' + esc(axis.label + ' · ' + pcAxisShortUnit(axis)) + '</text>');
    });

    var ordered = rows.slice().sort(function (a, b) {
      var pa = explorePassesBrushes(a) ? 1 : 0;
      var pb = explorePassesBrushes(b) ? 1 : 0;
      return pa - pb;
    });

    ordered.forEach(function (sc) {
      var passes = explorePassesBrushes(sc);
      if (passes) visibleCount += 1;
      var path = '';
      var penDown = false;
      PC_AXES.forEach(function (axis, index) {
        var value = axis.get(sc);
        if (value === null || value === undefined || !Number.isFinite(value)) {
          penDown = false;
          return;
        }
        var x = positions[index];
        var y = yFor(value, domains[index]);
        path += (penDown ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
        penDown = true;
      });
      if (!path) return;
      var classes = ['pc-line'];
      if (!passes) classes.push('pc-line--filtered');
      if (exploreIsLive(sc)) classes.push('pc-line--live');
      if (!sc.pareto_eligible) classes.push('pc-line--context');
      if (sc.feasibility_status === 'fail') classes.push('pc-line--infeasible');
      if (sc.scenario_id === state.selectedId) classes.push('pc-line--selected');
      var color = exploreIsLive(sc) ? '#e8a63c' : familyColor(sc.family);
      svg.push('<path class="' + classes.join(' ') + '" d="' + path.trim() + '" stroke="' + color + '" data-scenario-id="' + esc(sc.scenario_id) + '"><title>' + esc(pcLineTitle(sc)) + '</title></path>');
    });

    brushIds.forEach(function (id) {
      var index = PC_AXES.findIndex(function (axis) { return axis.id === id; });
      if (index === -1) return;
      var domain = domains[index];
      var brush = state.explore.brushes[id];
      var top = Math.min(yFor(brush.min, domain), yFor(brush.max, domain));
      var bottom = Math.max(yFor(brush.min, domain), yFor(brush.max, domain));
      var x = positions[index];
      svg.push('<line class="pc-brush-guide" x1="' + margin.left + '" y1="' + top.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + top.toFixed(1) + '"/>');
      svg.push('<line class="pc-brush-guide" x1="' + margin.left + '" y1="' + bottom.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + bottom.toFixed(1) + '"/>');
      svg.push('<rect class="pc-brush" x="' + (x - 9) + '" y="' + top.toFixed(1) + '" width="18" height="' + Math.max(1, bottom - top).toFixed(1) + '"/>');
    });

    PC_AXES.forEach(function (axis, index) {
      svg.push('<rect class="pc-axis-hit" data-axis="' + esc(axis.id) + '" x="' + (positions[index] - 12) + '" y="' + margin.top + '" width="24" height="' + plotHeight + '"/>');
    });
    svg.push('</svg>');
    chart.innerHTML = svg.join('');

    var svgEl = chart.querySelector('svg');
    if (!svgEl) return;

    svgEl.addEventListener('click', function (event) {
      var target = event.target;
      while (target && target !== svgEl && !target.getAttribute('data-scenario-id')) target = target.parentNode;
      if (target && target !== svgEl) selectScenario(target.getAttribute('data-scenario-id'));
    });

    var drag = null;
    var tempRect = null;
    function pointerY(event) {
      var point = svgEl.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      var matrix = svgEl.getScreenCTM();
      if (!matrix) return null;
      return point.matrixTransform(matrix.inverse()).y;
    }
    svgEl.addEventListener('pointerdown', function (event) {
      var target = event.target;
      while (target && target !== svgEl && !target.getAttribute('data-axis')) target = target.parentNode;
      if (!target || target === svgEl) return;
      var axisId = target.getAttribute('data-axis');
      var index = PC_AXES.findIndex(function (axis) { return axis.id === axisId; });
      if (index === -1) return;
      var y = pointerY(event);
      if (y === null) return;
      event.preventDefault();
      drag = { axisId: axisId, index: index, startY: Math.max(margin.top, Math.min(margin.top + plotHeight, y)) };
      tempRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      tempRect.setAttribute('class', 'pc-brush-temp');
      tempRect.setAttribute('x', String(positions[index] - 10));
      tempRect.setAttribute('width', '20');
      tempRect.setAttribute('y', String(drag.startY));
      tempRect.setAttribute('height', '1');
      svgEl.appendChild(tempRect);
      try { svgEl.setPointerCapture(event.pointerId); } catch (error) { /* older pointer implementations */ }
    });
    svgEl.addEventListener('pointermove', function (event) {
      if (!drag) return;
      var y = pointerY(event);
      if (y === null) return;
      var current = Math.max(margin.top, Math.min(margin.top + plotHeight, y));
      tempRect.setAttribute('y', String(Math.min(drag.startY, current)));
      tempRect.setAttribute('height', String(Math.max(1, Math.abs(current - drag.startY))));
    });
    function finishBrush(event) {
      if (!drag) return;
      var y = pointerY(event);
      var current = y === null ? drag.startY : Math.max(margin.top, Math.min(margin.top + plotHeight, y));
      var domain = domains[drag.index];
      var axis = PC_AXES[drag.index];
      var start = drag.startY;
      if (tempRect && tempRect.parentNode) tempRect.parentNode.removeChild(tempRect);
      tempRect = null;
      var moved = Math.abs(current - start);
      drag = null;
      if (moved < 5) {
        delete state.explore.brushes[axis.id];
        announce('Cleared the ' + axis.label + ' filter.');
      } else {
        var first = valueForY(start, domain);
        var second = valueForY(current, domain);
        state.explore.brushes[axis.id] = { min: Math.min(first, second), max: Math.max(first, second) };
        announce('Filtered ' + axis.label + ' to ' + axis.format(Math.min(first, second)) + ' – ' + axis.format(Math.max(first, second)) + '.');
      }
      state.explore.formError = '';
      renderParallelCoordinates();
      updatePcFilterHint();
      updateHash();
    }
    svgEl.addEventListener('pointerup', finishBrush);
    svgEl.addEventListener('pointercancel', function () {
      if (!drag) return;
      if (tempRect && tempRect.parentNode) tempRect.parentNode.removeChild(tempRect);
      tempRect = null;
      drag = null;
    });

    renderPcAux(rows, visibleCount);
  }

  function renderPcAux(rows, visibleCount) {
    var legend = $('pc-legend');
    if (legend) {
      var families = [];
      rows.forEach(function (sc) {
        if (!exploreIsLive(sc) && families.indexOf(sc.family) === -1) families.push(sc.family);
      });
      var order = state.core.scenarios.families.map(function (f) { return f.family; });
      families.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
      var html = families.map(function (family) {
        return '<span class="pc-legend-item"><span class="pc-legend-swatch" style="color:' + familyColor(family) + '"></span>' + esc(shortFamily(family)) + '</span>';
      }).join('');
      if (rows.some(exploreIsLive)) {
        html += '<span class="pc-legend-item"><span class="pc-legend-swatch is-live" style="color:#e8a63c"></span>Live session · screening_only</span>';
      }
      html += '<span class="pc-legend-item"><span class="pc-legend-swatch" style="color:var(--paper);opacity:.5"></span>canonical 119 · Pareto eligible</span>';
      html += '<span class="pc-legend-item"><span class="pc-legend-swatch" style="color:#a94f31"></span>ladder · compare only (150 conditional / 200 fail)</span>';
      legend.innerHTML = html;
    }

    var formError = state.explore.formError;
    var brushIds = Object.keys(state.explore.brushes);
    var missingOnBrush = 0;
    if (brushIds.length) {
      rows.forEach(function (sc) {
        if (!explorePassesBrushes(sc)) {
          var hasMissing = brushIds.some(function (id) { return PC_AXIS_BY_ID[id] && PC_AXIS_BY_ID[id].get(sc) === null; });
          if (hasMissing) missingOnBrush += 1;
        }
      });
    }

    var chips = $('pc-chips');
    if (chips) {
      if (!brushIds.length) {
        chips.innerHTML = '<span class="filter-empty">No filters — all scenarios visible. Drag an axis or use the range form.</span>';
      } else {
        chips.innerHTML = PC_AXES.filter(function (axis) { return state.explore.brushes[axis.id]; }).map(function (axis) {
          var brush = state.explore.brushes[axis.id];
          return '<span class="filter-chip"><span><b>' + esc(axis.label) + '</b> ' + esc(axis.format(brush.min)) + ' – ' + esc(axis.format(brush.max)) + '</span>' +
            '<button type="button" data-axis="' + esc(axis.id) + '" aria-label="Remove ' + esc(axis.label) + ' filter">×</button></span>';
        }).join('');
      }
    }

    var status = $('pc-status');
    if (status) {
      var text = visibleCount + ' of ' + rows.length + ' visible · ' + state.eligible.length + ' Pareto eligible + ' +
        (state.compare.length - state.eligible.length) + ' context (ladder/allocation) + ' + state.live.sessionRuns.length + ' live';
      if (brushIds.length) text += ' · ' + brushIds.length + ' axis filter' + (brushIds.length === 1 ? '' : 's');
      if (missingOnBrush) text += ' · ' + missingOnBrush + ' hidden with missing filtered metrics';
      if (formError) text = 'Filter not applied: ' + formError + ' · ' + text;
      status.textContent = text;
    }
  }

  function ensureParetoAxes() {
    var pareto = state.explore.pareto;
    var ids = [pareto.x, pareto.y, pareto.z];
    var valid = ids.every(function (id) { return PARETO_BY_ID[id]; }) && new Set(ids).size === 3;
    if (!valid) state.explore.pareto = { x: PARETO_DEFAULTS.x, y: PARETO_DEFAULTS.y, z: PARETO_DEFAULTS.z };
  }

  function paretoSelectOptions(selected, used) {
    return PARETO_METRICS.map(function (metric) {
      var usedElsewhere = used.indexOf(metric.id) !== -1 && metric.id !== selected;
      return '<option value="' + esc(metric.id) + '"' + (metric.id === selected ? ' selected' : '') + '>' +
        esc(metric.label + ' · ' + metric.unit + ' · ' + (metric.higher ? 'maximize' : 'minimize') + (usedElsewhere ? ' · swaps' : '')) + '</option>';
    }).join('');
  }

  function syncParetoSelects() {
    var pareto = state.explore.pareto;
    var x = $('pareto-x');
    var y = $('pareto-y');
    var z = $('pareto-z');
    if (!x || !y || !z) return;
    x.innerHTML = paretoSelectOptions(pareto.x, [pareto.y, pareto.z]);
    y.innerHTML = paretoSelectOptions(pareto.y, [pareto.x, pareto.z]);
    z.innerHTML = paretoSelectOptions(pareto.z, [pareto.x, pareto.y]);
    x.value = pareto.x;
    y.value = pareto.y;
    z.value = pareto.z;
  }

  function renderPareto() {
    if (!state.core) return;
    ensureParetoAxes();
    syncParetoSelects();
    var chartEl = $('pareto-chart');
    var guard = $('pareto-guard');
    var countEl = $('pareto-count');
    var selectedEl = $('pareto-selected');
    if (!chartEl || !countEl || !selectedEl) return;
    var pareto = state.explore.pareto;
    var metrics = [PARETO_BY_ID[pareto.x], PARETO_BY_ID[pareto.y], PARETO_BY_ID[pareto.z]];
    var rows = exploreRows();
    var candidates = rows.filter(function (sc) { return sc.pareto_eligible === true; });
    var contextRows = rows.filter(function (sc) { return sc.pareto_eligible !== true; });
    var complete = [];
    var missing = [];
    candidates.forEach(function (sc) {
      var values = metrics.map(function (metric) { return metric.get(sc); });
      var absent = metrics.filter(function (metric, index) { return values[index] === null; });
      if (absent.length) missing.push({ sc: sc, fields: absent.map(function (metric) { return metric.label; }) });
      else complete.push({ sc: sc, raw: values, score: [] });
    });

    var normalized = metrics.map(function (metric, index) {
      var values = complete.map(function (row) { return row.raw[index]; });
      var min = values.length ? Math.min.apply(null, values) : 0;
      var max = values.length ? Math.max.apply(null, values) : 1;
      return values.map(function (value) {
        return max - min < 1e-12 ? 0.5 : (value - min) / (max - min);
      });
    });
    complete.forEach(function (row, rowIndex) {
      row.score = metrics.map(function (metric, index) {
        var norm = normalized[index][rowIndex];
        return metric.higher ? 1 - norm : norm;
      });
    });

    function dominates(a, b) {
      var strict = false;
      for (var i = 0; i < a.score.length; i += 1) {
        if (a.score[i] > b.score[i] + 1e-12) return false;
        if (a.score[i] < b.score[i] - 1e-12) strict = true;
      }
      return strict;
    }

    complete.forEach(function (row, index) {
      row.front = !complete.some(function (other, otherIndex) {
        return otherIndex !== index && dominates(other, row);
      });
    });

    var frontRows = complete.filter(function (row) { return row.front; });
    var xi = 0;
    var yi = 1;
    var selected = state.core.scenarioById.get(state.selectedId);
    var frontIds = new Set(frontRows.map(function (row) { return row.sc.scenario_id; }));

    if (guard) {
      var guardParts = [];
      if (contextRows.length) {
        guardParts.push(contextRows.length + ' scenario(s) shown as context but excluded from the front because pareto_eligible=false (harvest ladder, allocation-LP, and live runs).');
      }
      if (missing.length) {
        guardParts.push('Excluded for missing selected objectives: ' + missing.map(function (item) {
          return item.sc.label + ' (' + item.fields.join(', ') + ')';
        }).join('; ') + '. Missing metrics are never imputed.');
      }
      guard.hidden = !guardParts.length;
      guard.textContent = guardParts.join(' ');
    }

    if (!window.echarts) {
      chartEl.innerHTML = '<div class="empty-state" style="display:grid">ECharts failed to load. Check vendor/echarts.min.js.</div>';
      countEl.textContent = frontRows.length + ' on front · chart unavailable';
      return;
    }

    if (!state.paretoChart) {
      state.paretoChart = window.echarts.init(chartEl, null, { renderer: 'canvas' });
      state.paretoChart.on('click', function (params) {
        var meta = params && params.data && params.data.meta;
        if (!meta || !meta.id) return;
        selectScenario(meta.id);
      });
    }

    function point(row, onFront) {
      var sc = row.sc;
      var isSelected = sc.scenario_id === state.selectedId;
      return {
        value: [row.raw[xi], row.raw[yi]],
        name: sc.label,
        symbol: sc.flags.canonical ? 'circle' : 'diamond',
        symbolSize: isSelected ? (onFront ? 22 : 16) : (onFront ? 17 : 10),
        itemStyle: {
          color: exploreIsLive(sc) ? '#a94f31' : familyColor(sc.family),
          borderColor: isSelected ? '#17251d' : (onFront ? '#f2b45c' : '#fbf6e9'),
          borderWidth: isSelected ? 4 : (onFront ? 3 : 1),
          shadowBlur: onFront ? 12 : 0,
          shadowColor: 'rgba(217,138,43,0.5)'
        },
        meta: {
          id: sc.scenario_id,
          label: sc.label,
          family: shortFamily(sc.family),
          grade: exploreGrade(sc),
          front: onFront,
          metrics: metrics.map(function (metric, index) {
            return { label: metric.label, unit: metric.unit, text: metric.format(row.raw[index]) };
          })
        }
      };
    }

    var dominated = complete.filter(function (row) { return !row.front; }).map(function (row) { return point(row, false); });
    var front = frontRows.map(function (row) { return point(row, true); });
    var contextPoints = contextRows.map(function (sc) {
      var values = metrics.map(function (metric) { return metric.get(sc); });
      if (values.some(function (value) { return value === null; })) return null;
      return {
        value: [values[xi], values[yi]],
        name: sc.label,
        symbol: 'emptyCircle',
        symbolSize: 11,
        itemStyle: { color: 'rgba(157,139,108,0.45)', borderColor: '#9d8b6c', borderWidth: 1 },
        meta: {
          id: sc.scenario_id,
          label: sc.label,
          family: shortFamily(sc.family),
          grade: exploreGrade(sc),
          front: false,
          context: true,
          metrics: metrics.map(function (metric, index) {
            return { label: metric.label, unit: metric.unit, text: metric.format(values[index]) };
          })
        }
      };
    }).filter(Boolean);
    var unitNote = function (metric) { return metric.unit + ' · ' + (metric.higher ? 'maximize' : 'minimize'); };

    state.paretoChart.setOption({
      animationDuration: 480,
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      grid: { left: 86, right: 34, top: 52, bottom: 66, containLabel: true },
      legend: {
        bottom: 0,
        left: 'center',
        itemWidth: 11,
        itemHeight: 8,
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' }
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
          var meta = params.data && params.data.meta;
          if (!meta) return '';
          return '<div class="chart-tip"><div class="chart-tip-head"><span>' + esc(meta.id) + '</span></div>' +
            '<div class="chart-tip-title">' + esc(meta.label) + '</div>' +
            '<div class="chart-tip-sub">' + esc(meta.family) + ' · ' + esc(meta.grade) + '</div>' +
            meta.metrics.map(function (item) {
              return '<div class="chart-tip-kv"><span>' + esc(item.label + ' (' + item.unit + ')') + '</span><b>' + esc(item.text) + '</b></div>';
            }).join('') +
            '<div class="chart-tip-kv"><span>Pareto status</span><b>' + (meta.context ? 'not Pareto eligible' : (meta.front ? 'on front' : 'dominated')) + '</b></div></div>';
        }
      },
      xAxis: {
        type: 'value',
        name: metrics[xi].label + ' (' + unitNote(metrics[xi]) + ')',
        nameLocation: 'middle',
        nameGap: 38,
        scale: true,
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { lineStyle: { color: '#b8a57e' } }
      },
      yAxis: {
        type: 'value',
        name: metrics[yi].label + ' (' + unitNote(metrics[yi]) + ')',
        nameLocation: 'middle',
        nameGap: 58,
        scale: true,
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } },
        axisLine: { show: false }
      },
      series: [
        { name: 'Context · not Pareto eligible', type: 'scatter', data: contextPoints, emphasis: { scale: 1.3 } },
        { name: 'Dominated', type: 'scatter', data: dominated, emphasis: { scale: 1.5 } },
        { name: 'Pareto front', type: 'scatter', data: front, z: 3, emphasis: { scale: 1.4 } }
      ]
    }, true);
    state.paretoChart.resize();

    state.explore.lastFront = {
      metrics: metrics,
      rows: frontRows.slice().sort(function (a, b) {
        return a.raw[xi] - b.raw[xi] || a.raw[yi] - b.raw[yi];
      })
    };

    var labelText = frontRows.length + ' on front · ' + complete.length + ' Pareto-eligible evaluated · ' +
      contextRows.length + ' context excluded (ladder, allocation-LP, and live runs are not Pareto eligible) · ' +
      (missing.length ? missing.length + ' excluded for missing metrics' : 'no missing metrics') +
      ' · dominance on min–max normalized values · mechanically computed, not a policy frontier';
    countEl.textContent = labelText;

    if (selected) {
      var onFront = frontIds.has(selected.scenario_id);
      var paretoLabel = selected.pareto_eligible ? (onFront ? 'on front' : 'not on front') : 'not Pareto eligible';
      selectedEl.innerHTML = 'Selected <strong>' + esc(selected.label) + '</strong> ' +
        '<span class="badge ' + (onFront ? 'badge--front' : 'badge--plain') + '">' + esc(paretoLabel) + '</span> ' +
        '<span class="badge badge--plain">' + esc(exploreGrade(selected)) + '</span>';
    } else {
      selectedEl.textContent = 'Select a point to load its scenario.';
    }
  }

  function exportParetoFront() {
    var model = state.explore.lastFront;
    if (!model || !model.rows.length) {
      announce('No Pareto front rows to export.');
      return;
    }
    var header = ['rank', 'scenario_id', 'label', 'family', 'grade'].concat(model.metrics.map(function (metric) {
      return metric.label + ' (' + metric.unit + ')';
    }));
    var rows = model.rows.map(function (row, index) {
      return [index + 1, row.sc.scenario_id, row.sc.label, row.sc.family, exploreGrade(row.sc)].concat(row.raw.map(function (value) {
        return Number.isFinite(value) ? String(value) : '';
      }));
    });
    downloadCsvWithHeader('pareto_front_iteration0.csv', header, rows);
  }

  function compareRows() {
    return state.core.scenarios.scenarios.concat(state.live.sessionRuns);
  }

  function orderedCompareRows() {
    var order = new Map();
    state.core.scenarios.families.forEach(function (family, index) { order.set(family.family, index); });
    return compareRows().slice().sort(function (a, b) {
      if (exploreIsLive(a) !== exploreIsLive(b)) return exploreIsLive(a) ? 1 : -1;
      var familyDelta = (order.get(a.family) || 0) - (order.get(b.family) || 0);
      if (familyDelta !== 0) return familyDelta;
      return a.label.localeCompare(b.label);
    });
  }

  function ensureCompareSelection() {
    var ids = new Set(compareRows().map(function (sc) { return sc.scenario_id; }));
    var preferredA = FLEET_SCENARIO_ID;
    var preferredB = 'fleet_screening_ladder__screening_150m';
    if (!state.explore.compare.a || !ids.has(state.explore.compare.a)) {
      state.explore.compare.a = ids.has(preferredA) ? preferredA : (state.selectedId || null);
    }
    if (!state.explore.compare.b || !ids.has(state.explore.compare.b)) {
      state.explore.compare.b = state.selectedId && state.selectedId !== state.explore.compare.a
        ? state.selectedId
        : (ids.has(preferredB) ? preferredB : null);
    }
    if (!state.explore.compare.a) {
      var first = compareRows()[0];
      state.explore.compare.a = first ? first.scenario_id : null;
    }
    if (!state.explore.compare.b) {
      var second = compareRows()[1];
      state.explore.compare.b = second ? second.scenario_id : state.explore.compare.a;
    }
  }

  function populateCompareSelects() {
    var selectA = $('compare-a');
    var selectB = $('compare-b');
    if (!selectA || !selectB) return;
    var order = new Map();
    state.core.scenarios.families.forEach(function (family, index) { order.set(family.family, index); });
    var staticRows = state.core.scenarios.scenarios.slice().sort(function (a, b) {
      var familyDelta = (order.get(a.family) || 0) - (order.get(b.family) || 0);
      if (familyDelta !== 0) return familyDelta;
      return a.label.localeCompare(b.label);
    });
    function options(rows) {
      return rows.map(function (sc) {
        return '<option value="' + esc(sc.scenario_id) + '">' + esc(sc.label + ' · ' + shortFamily(sc.family)) + '</option>';
      }).join('');
    }
    var html = '<optgroup label="Committed ledger · ' + staticRows.length + ' scenarios">' + options(staticRows) + '</optgroup>';
    if (state.live.sessionRuns.length) {
      html += '<optgroup label="Session runs · screening_only">' + options(state.live.sessionRuns) + '</optgroup>';
    }
    selectA.innerHTML = html;
    selectB.innerHTML = html;
    selectA.value = state.explore.compare.a;
    selectB.value = state.explore.compare.b;
  }

  function compareMetricValue(metric, sc) {
    return finiteOrNull((sc.metrics || {})[metric.key]);
  }

  function compareDisplay(metric, value) {
    if (value === null) return '—';
    if (metric.percent) return fmtPct(value, 3);
    return fmt(value / (metric.scale || 1), metric.digits);
  }

  function compareDelta(metric, a, b) {
    var delta = b - a;
    if (metric.percent) {
      return {
        absolute: (delta >= 0 ? '+' : '') + fmt(delta * 100, 3) + ' pp',
        relative: a === 0 ? 'n/a (A = 0)' : (delta >= 0 ? '+' : '') + fmt(delta / Math.abs(a) * 100, 1) + '%',
        numeric: delta
      };
    }
    return {
      absolute: (delta >= 0 ? '+' : '') + fmt(delta / (metric.scale || 1), metric.digits),
      relative: a === 0 ? 'n/a (A = 0)' : (delta >= 0 ? '+' : '') + fmt(delta / Math.abs(a) * 100, 1) + '%',
      numeric: delta
    };
  }

  function dimensionSnapshot(sc) {
    var dims = sc.dimensions || {};
    var harvest = dims.harvest || {};
    var wind = dims.wind || {};
    var pwrbio = dims.pwrbio_cap;
    var pwrbioText = 'not set';
    if (pwrbio !== null && pwrbio !== undefined) {
      if (typeof pwrbio === 'object' && pwrbio.total_pj !== undefined) {
        pwrbioText = (pwrbio.basis || 'cap') + ' · ' + fmt(pwrbio.total_pj, 2) + ' PJ';
      } else if (typeof pwrbio === 'object' && pwrbio.fraction !== undefined) {
        pwrbioText = (pwrbio.basis || 'fraction') + ' · ' + fmtPct(pwrbio.fraction, 1);
      } else {
        pwrbioText = 'cap · ' + fmt(pwrbio, 2) + ' PJ';
      }
    }
    return [
      { label: 'Harvest', value: harvest.level ? harvest.level + ' · ' + fmt(harvest.m3_per_year / 1e6, 2) + ' Mm³/yr' : 'not set' },
      { label: 'Allocation', value: sc.allocation ? sc.allocation + ' · ' + (sc.allocation_role || 'reallocated') : 'observed 2024' },
      { label: 'Harvest volume', value: dims.harvest_m3_per_year === null || dims.harvest_m3_per_year === undefined ? 'not set' : fmt(dims.harvest_m3_per_year / 1e6, 2) + ' Mm³/yr' },
      { label: 'Carbon price', value: dims.carbon_price_usd_per_t === null || dims.carbon_price_usd_per_t === undefined ? 'not set' : '$' + fmt(dims.carbon_price_usd_per_t, 2) + '/t' },
      { label: 'Bio cost', value: dims.bio_cost_usd_per_gj === null || dims.bio_cost_usd_per_gj === undefined ? 'not set' : '$' + fmt(dims.bio_cost_usd_per_gj, 2) + '/GJ' },
      { label: 'Recovery', value: dims.recovery_fraction === null || dims.recovery_fraction === undefined ? 'not set' : fmtPct(dims.recovery_fraction, 1) },
      { label: 'Supply curve', value: dims.supply_curve || 'not set' },
      { label: 'Hydro', value: dims.hydro_mode || 'not set' },
      { label: 'PWRBIO cap', value: pwrbioText },
      { label: 'Nuclear cap', value: dims.nuclear_cap_gw === null || dims.nuclear_cap_gw === undefined ? 'not set' : fmt(dims.nuclear_cap_gw, 2) + ' GW' },
      { label: 'Wind cap/int', value: (wind.cap || 'none') + ' · ' + (wind.integration_limit == null ? 'no integration limit' : wind.integration_limit) + ' · $' + fmt(wind.cost_usd_per_gj, 2) + '/GJ' },
      { label: 'Zero tiers', value: Array.isArray(dims.zero_tiers) && dims.zero_tiers.length ? dims.zero_tiers.join(', ') : 'none' }
    ];
  }

  function compareDimensionRows(a, b) {
    var left = dimensionSnapshot(a);
    var right = dimensionSnapshot(b);
    var differing = [];
    var same = [];
    left.forEach(function (item, index) {
      var other = right[index];
      if (item.value === other.value) same.push(item.label);
      else differing.push({ label: item.label, a: item.value, b: other.value });
    });
    return { differing: differing, same: same, total: left.length };
  }

  function renderCompare() {
    if (!state.core) return;
    ensureCompareSelection();
    populateCompareSelects();
    var a = state.core.scenarioById.get(state.explore.compare.a);
    var b = state.core.scenarioById.get(state.explore.compare.b);
    var table = $('compare-table');
    var grade = $('compare-grade');
    var dims = $('compare-dims');
    var status = $('compare-status');
    if (!a || !b || !table || !grade || !dims || !status) return;

    grade.innerHTML =
      '<span class="badge">A · ' + esc(shortFamily(a.family)) + ' · ' + esc(exploreGrade(a)) + '</span>' +
      '<span class="badge">B · ' + esc(shortFamily(b.family)) + ' · ' + esc(exploreGrade(b)) + '</span>' +
      (a.feasibility_status ? '<span class="badge ' + feasibilityClass(a.feasibility_status) + '">A · ' + esc(feasibilityLabel(a.feasibility_status)) + '</span>' : '') +
      (b.feasibility_status ? '<span class="badge ' + feasibilityClass(b.feasibility_status) + '">B · ' + esc(feasibilityLabel(b.feasibility_status)) + '</span>' : '') +
      (a.harvest_exogenous || a.flags.harvest_exogenous ? '<span class="badge badge--ink">A · harvest exogenous</span>' : '') +
      (b.harvest_exogenous || b.flags.harvest_exogenous ? '<span class="badge badge--ink">B · harvest exogenous</span>' : '') +
      (a.scenario_id === b.scenario_id ? '<span class="badge badge--live">A and B are the same scenario</span>' : '') +
      (exploreIsLive(a) || exploreIsLive(b) ? '<span class="badge badge--live">screening_only · not policy grade</span>' : '');

    var rows = COMPARE_METRICS.filter(function (metric) {
      if (!metric.optional) return true;
      return compareMetricValue(metric, a) !== null || compareMetricValue(metric, b) !== null;
    });

    var body = rows.map(function (metric) {
      var av = compareMetricValue(metric, a);
      var bv = compareMetricValue(metric, b);
      var delta = av === null || bv === null ? null : compareDelta(metric, av, bv);
      var cls = delta && Math.abs(delta.numeric) > 1e-12 ? (delta.numeric > 0 ? 'is-pos' : 'is-neg') : '';
      return '<tr data-metric="' + esc(metric.key) + '">' +
        '<th scope="row">' + esc(metric.label) + '<span class="unit">' + esc(metric.unit) + '</span></th>' +
        '<td>' + esc(compareDisplay(metric, av)) + '</td>' +
        '<td>' + esc(compareDisplay(metric, bv)) + '</td>' +
        '<td class="' + cls + '">' + esc(delta ? delta.absolute : '—') + '</td>' +
        '<td class="' + cls + '">' + esc(delta ? delta.relative : '—') + '</td>' +
        '</tr>';
    }).join('');

    table.innerHTML = '<caption>Deltas are B − A. Relative delta uses |A|; it is shown as n/a when A = 0. Units are per row; — means the metric is missing and deltas are suppressed.</caption>' +
      '<thead><tr><th scope="col">Metric</th>' +
      '<th scope="col">A <span class="mono muted small">' + esc(a.scenario_id) + '</span></th>' +
      '<th scope="col">B <span class="mono muted small">' + esc(b.scenario_id) + '</span></th>' +
      '<th scope="col">Δ absolute</th><th scope="col">Δ relative</th></tr></thead>' +
      '<tbody>' + body + '</tbody>';

    var diff = compareDimensionRows(a, b);
    var diffHtml = diff.differing.map(function (item) {
      return '<span class="diff-chip"><b>' + esc(item.label) + '</b> ' + esc(item.a) + ' → ' + esc(item.b) + '</span>';
    }).join('');
    if (!diff.differing.length) diffHtml = '<span class="diff-chip diff-chip--same">All ' + diff.total + ' dimensions identical</span>';
    dims.innerHTML = '<span class="kicker">Differing dimensions · ' + diff.differing.length + ' of ' + diff.total + '</span>' + diffHtml;

    status.textContent = 'Comparing ' + rows.length + ' metrics · ' + diff.differing.length + ' differing dimensions · ' +
      (exploreIsLive(a) || exploreIsLive(b) ? 'one or both runs are live screening_only' : 'both runs are committed post-fix scenarios');
  }

  function aggregateLedger(output, energy) {
    var processed;
    try {
      processed = processDispatch(output, energy);
    } catch (error) {
      return null;
    }
    if (!processed) return null;
    var rows = filterDispatchRows(processed, 'ALL');
    var totals = new Map();
    rows.forEach(function (row) {
      totals.set(row.year, (totals.get(row.year) || 0) + row.value);
    });
    return {
      unit: processed.unit,
      values: YEARS.map(function (year) { return totals.has(year) ? totals.get(year) : null; }),
      note: processed.selection.note
    };
  }

  function loadOverlayOutput(id) {
    return getOutput(id).then(function (output) {
      return { ok: true, output: output };
    }, function (error) {
      return { ok: false, error: error && error.message ? error.message : String(error) };
    });
  }

  function renderCompareOverlay() {
    var frame = $('compare-overlay-frame');
    var note = $('compare-overlay-note');
    if (!frame || !note) return;
    if (!state.explore.overlay) {
      frame.hidden = true;
      if (state.compareChart) state.compareChart.clear();
      return;
    }
    frame.hidden = false;
    var token = ++state.explore.overlayToken;
    var aId = state.explore.compare.a;
    var bId = state.explore.compare.b;
    var a = state.core.scenarioById.get(aId);
    var b = state.core.scenarioById.get(bId);
    if (!a || !b) {
      note.textContent = 'Select two scenarios to overlay dispatch.';
      return;
    }
    note.textContent = 'Loading dispatch outputs for A and B…';
    var energy = state.explore.energy;
    Promise.all([loadOverlayOutput(aId), loadOverlayOutput(bId)]).then(function (results) {
      if (token !== state.explore.overlayToken || !state.explore.overlay) return;
      var left = results[0].ok ? aggregateLedger(results[0].output, energy) : null;
      var right = results[1].ok ? aggregateLedger(results[1].output, energy) : null;
      if (!left && !right) {
        note.textContent = 'No ' + energy + ' ledger is available for either scenario. ' + (results[0].error || results[1].error || '');
        if (state.compareChart) state.compareChart.clear();
        return;
      }
      if (!window.echarts) {
        note.textContent = 'ECharts failed to load; dispatch overlay is unavailable.';
        return;
      }
      if (!state.compareChart) state.compareChart = window.echarts.init($('compare-overlay-chart'), null, { renderer: 'canvas' });
      var unit = left ? left.unit : right.unit;
      var series = [];
      if (left) {
        series.push({
          name: 'A · ' + a.label,
          type: 'line',
          data: left.values,
          showSymbol: false,
          lineStyle: { width: 2.6, color: '#d98a2b' },
          itemStyle: { color: '#d98a2b' },
          areaStyle: { color: 'rgba(217,138,43,0.08)' }
        });
      }
      if (right) {
        series.push({
          name: 'B · ' + b.label,
          type: 'line',
          data: right.values,
          showSymbol: false,
          lineStyle: { width: 2.2, color: '#4f7d80', type: left && right ? 'dashed' : 'solid' },
          itemStyle: { color: '#4f7d80' }
        });
      }
      state.compareChart.setOption({
        animationDuration: 420,
        textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
        grid: { left: 64, right: 28, top: 46, bottom: 44, containLabel: true },
        legend: {
          top: 0,
          left: 0,
          itemWidth: 14,
          itemHeight: 8,
          textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' }
        },
        tooltip: {
          trigger: 'axis',
          confine: true,
          backgroundColor: '#0c1f19',
          borderColor: '#d98a2b',
          borderWidth: 1,
          textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
          valueFormatter: function (value) { return fmt(value, 2) + ' ' + unit; }
        },
        xAxis: {
          type: 'category',
          data: YEARS.map(String),
          axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#6c786c', interval: 9 },
          axisLine: { lineStyle: { color: '#b8a57e' } }
        },
        yAxis: {
          type: 'value',
          name: unit,
          scale: true,
          nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#435347' },
          axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c', formatter: fmtAxis },
          splitLine: { lineStyle: { color: 'rgba(184,165,126,0.35)', type: 'dashed' } }
        },
        series: series
      }, true);
      state.compareChart.resize();
      var skipped = [];
      if (!left) skipped.push('A has no ' + energy + ' ledger');
      if (!right) skipped.push('B has no ' + energy + ' ledger');
      note.textContent = 'Annual national ' + unit + ' · 2021—2070 · ' +
        (left ? left.note : '') + (right && right.note !== (left && left.note) ? ' ' + right.note : '') +
        (skipped.length ? ' · ' + skipped.join('; ') : '') +
        (a.scenario_id === b.scenario_id ? ' · A and B are the same scenario' : '');
    });
  }

  function updatePcFilterHint() {
    var select = $('pc-axis-select');
    var min = $('pc-min');
    var max = $('pc-max');
    if (!select || !min || !max) return;
    var axis = PC_AXIS_BY_ID[select.value] || PC_AXES[0];
    var domain = pcDomain(axis, exploreRows());
    var brush = state.explore.brushes[axis.id];
    min.placeholder = String(Number(domain.min.toPrecision(8)));
    max.placeholder = String(Number(domain.max.toPrecision(8)));
    min.value = brush ? String(brush.min) : '';
    max.value = brush ? String(brush.max) : '';
  }

  function bindExploreControls() {
    var form = $('pc-filter-form');
    var axisSelect = $('pc-axis-select');
    if (axisSelect && !axisSelect.options.length) {
      axisSelect.innerHTML = PC_AXES.map(function (axis) {
        return '<option value="' + esc(axis.id) + '">' + esc(axis.label + ' · ' + pcAxisShortUnit(axis)) + '</option>';
      }).join('');
    }
    if (axisSelect) {
      axisSelect.addEventListener('change', function () {
        state.explore.formError = '';
        updatePcFilterHint();
      });
    }
    if (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var axis = PC_AXIS_BY_ID[axisSelect.value];
        var min = finiteOrNull($('pc-min').value);
        var max = finiteOrNull($('pc-max').value);
        if (!axis) {
          state.explore.formError = 'choose an axis';
        } else if (min === null || max === null) {
          state.explore.formError = 'enter finite minimum and maximum values';
        } else if (min > max) {
          state.explore.formError = 'minimum must not exceed maximum';
        } else {
          state.explore.brushes[axis.id] = { min: min, max: max };
          state.explore.formError = '';
          announce('Filtered ' + axis.label + ' to ' + axis.format(min) + ' – ' + axis.format(max) + '.');
        }
        renderParallelCoordinates();
        updatePcFilterHint();
        updateHash();
      });
    }
    var chips = $('pc-chips');
    if (chips) {
      chips.addEventListener('click', function (event) {
        var button = event.target.closest('button[data-axis]');
        if (!button) return;
        delete state.explore.brushes[button.getAttribute('data-axis')];
        renderParallelCoordinates();
        updatePcFilterHint();
        updateHash();
        announce('Removed filter.');
      });
    }
    var reset = $('pc-reset');
    if (reset) {
      reset.addEventListener('click', function () {
        state.explore.brushes = {};
        state.explore.formError = '';
        renderParallelCoordinates();
        updatePcFilterHint();
        updateHash();
        announce('Reset all parallel-coordinate filters.');
      });
    }
    ['x', 'y', 'z'].forEach(function (position) {
      var select = $('pareto-' + position);
      if (!select) return;
      select.addEventListener('change', function () {
        var previous = state.explore.pareto[position];
        var chosen = select.value;
        ['x', 'y', 'z'].forEach(function (other) {
          if (other !== position && state.explore.pareto[other] === chosen) state.explore.pareto[other] = previous;
        });
        state.explore.pareto[position] = chosen;
        ensureParetoAxes();
        renderPareto();
        updateHash();
      });
    });
    var exportButton = $('pareto-export');
    if (exportButton) exportButton.addEventListener('click', exportParetoFront);
    var selectA = $('compare-a');
    var selectB = $('compare-b');
    if (selectA) {
      selectA.addEventListener('change', function () {
        state.explore.compare.a = selectA.value;
        renderCompare();
        if (state.explore.overlay) renderCompareOverlay();
        updateHash();
      });
    }
    if (selectB) {
      selectB.addEventListener('change', function () {
        state.explore.compare.b = selectB.value;
        renderCompare();
        if (state.explore.overlay) renderCompareOverlay();
        updateHash();
      });
    }
    var toggle = $('compare-overlay-toggle');
    if (toggle) {
      toggle.addEventListener('click', function () {
        state.explore.overlay = !state.explore.overlay;
        toggle.setAttribute('aria-pressed', state.explore.overlay ? 'true' : 'false');
        toggle.textContent = state.explore.overlay ? 'Hide dispatch overlay' : 'Show dispatch overlay';
        renderCompareOverlay();
        updateHash();
      });
    }
    var energy = $('compare-energy');
    if (energy) {
      energy.value = state.explore.energy;
      energy.addEventListener('change', function () {
        state.explore.energy = energy.value;
        if (state.explore.overlay) renderCompareOverlay();
        updateHash();
      });
    }
    updatePcFilterHint();
  }

  function renderExplore(options) {
    if (!state.core) return;
    var note = $('explore-mode-note');
    if (note) {
      var sensitivity = (state.core.scenarios.tradeoff.sensitivity_scenario_ids || []).length;
      var allocation = (state.core.scenarios.tradeoff.allocation_scenario_ids || []).length;
      var canonical = state.eligible.filter(function (sc) { return sc.flags.canonical; }).length;
      var noteParts = [];
      if (canonical) noteParts.push(canonical + ' canonical 119 Mm³ case (Pareto eligible)');
      if (sensitivity) noteParts.push(sensitivity + ' harvest ladder scenarios (compare only: 150 conditional, 200 fail; harvest exogenous)');
      if (allocation) noteParts.push(allocation + ' allocation-LP comparison rows (reallocated; not Pareto)');
      if (state.live.sessionRuns.length) noteParts.push(state.live.sessionRuns.length + ' live session run(s)');
      note.hidden = !noteParts.length;
      note.textContent = noteParts.length ? noteParts.join(' · ') + '. Only pareto_eligible=true rows enter the front; ladder, allocation-LP, and live rows remain context.' : '';
    }
    ensureParetoAxes();
    renderParallelCoordinates();
    renderPareto();
    renderCompare();
    var toggle = $('compare-overlay-toggle');
    if (toggle) {
      toggle.setAttribute('aria-pressed', state.explore.overlay ? 'true' : 'false');
      toggle.textContent = state.explore.overlay ? 'Hide dispatch overlay' : 'Show dispatch overlay';
    }
    var energy = $('compare-energy');
    if (energy) energy.value = state.explore.energy;
    if (state.explore.overlay && (!options || options.refreshOverlay !== false)) renderCompareOverlay();
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

  function eachGeometryCoordinate(geometry, callback) {
    if (!geometry || !geometry.coordinates) return;
    var stack = [geometry.coordinates];
    while (stack.length) {
      var item = stack.pop();
      if (!item) continue;
      if (typeof item[0] === 'number' && typeof item[1] === 'number') {
        callback(item[0], item[1]);
      } else {
        for (var i = 0; i < item.length; i += 1) stack.push(item[i]);
      }
    }
  }

  function geometryBBox(geometry) {
    var box = [Infinity, Infinity, -Infinity, -Infinity];
    eachGeometryCoordinate(geometry, function (lng, lat) {
      if (lng < box[0]) box[0] = lng;
      if (lat < box[1]) box[1] = lat;
      if (lng > box[2]) box[2] = lng;
      if (lat > box[3]) box[3] = lat;
    });
    return box;
  }

  function geometryCentroid(geometry) {
    var sumX = 0;
    var sumY = 0;
    var count = 0;
    eachGeometryCoordinate(geometry, function (lng, lat) {
      sumX += lng;
      sumY += lat;
      count += 1;
    });
    return count ? [sumX / count, sumY / count] : [0, 0];
  }

  function ringContains(lng, lat, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      var xi = ring[i][0];
      var yi = ring[i][1];
      var xj = ring[j][0];
      var yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) {
        inside = !inside;
      }
    }
    return inside;
  }

  function polygonContains(coordinates, lng, lat) {
    if (!coordinates.length || !ringContains(lng, lat, coordinates[0])) return false;
    for (var i = 1; i < coordinates.length; i += 1) {
      if (ringContains(lng, lat, coordinates[i])) return false;
    }
    return true;
  }

  function geometryContains(geometry, lng, lat) {
    if (!geometry) return false;
    if (geometry.type === 'Polygon') return polygonContains(geometry.coordinates, lng, lat);
    if (geometry.type === 'MultiPolygon') {
      return geometry.coordinates.some(function (polygon) { return polygonContains(polygon, lng, lat); });
    }
    return false;
  }

  function featureBoxIntersectsPoint(box, lng, lat) {
    return lng >= box[0] && lng <= box[2] && lat >= box[1] && lat <= box[3];
  }

  function geometrySamplePoints(geometry) {
    var points = [];
    if (geometry.type === 'Polygon' || geometry.type === 'MultiPolygon') {
      var polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
      polygons.forEach(function (polygon) {
        var ring = polygon[0];
        if (!ring || !ring.length) return;
        points.push(ring[0]);
        points.push(ring[Math.floor(ring.length / 2)]);
      });
    }
    var centroid = geometryCentroid(geometry);
    points.push(centroid);
    var box = geometryBBox(geometry);
    points.push([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]);
    return points;
  }

  function prepareSpatialData(provinces, ecoprovinces, tiles, zoneNodes) {
    var provinceFeatures = provinces.features.map(function (feature) {
      feature._bbox = geometryBBox(feature.geometry);
      return feature;
    });
    var provinceByIso = new Map();
    provinceFeatures.forEach(function (feature) { provinceByIso.set(feature.properties.iso, feature); });

    var ecoFeatures = ecoprovinces.features.map(function (feature) {
      feature._bbox = geometryBBox(feature.geometry);
      feature._centroid = geometryCentroid(feature.geometry);
      feature._tiles = [];
      feature._provinceIso = null;
      return feature;
    });
    var ecoById = new Map();
    ecoFeatures.forEach(function (feature) { ecoById.set(feature.properties.ecoprovince_id, feature); });

    var tileById = new Map();
    var tileEco = new Map();
    var tilesByProvince = new Map();
    tiles.features.forEach(function (tile) {
      tile._bbox = geometryBBox(tile.geometry);
      tile._centroid = geometryCentroid(tile.geometry);
      tile._provinceCodes = String(tile.properties.provinces || '').split(';').filter(Boolean);
      tile._provinceIso = tile._provinceCodes.length ? (PROVINCE_ALIAS[tile._provinceCodes[0]] || 'CA-' + tile._provinceCodes[0]) : null;
      tileById.set(tile.properties.tile_id, tile);
      tile._provinceCodes.forEach(function (code) {
        var iso = PROVINCE_ALIAS[code] || 'CA-' + code;
        if (!tilesByProvince.has(iso)) tilesByProvince.set(iso, []);
        tilesByProvince.get(iso).push(tile);
      });
    });

    tiles.features.forEach(function (tile) {
      var chosen = null;
      var centroid = tile._centroid;
      ecoFeatures.some(function (eco) {
        if (!featureBoxIntersectsPoint(eco._bbox, centroid[0], centroid[1])) return false;
        if (geometryContains(eco.geometry, centroid[0], centroid[1])) { chosen = eco; return true; }
        return false;
      });
      if (!chosen) {
        var samples = [];
        var ring = tile.geometry.type === 'Polygon' ? tile.geometry.coordinates[0] : tile.geometry.coordinates[0][0];
        if (ring) {
          ring.forEach(function (point) { samples.push(point); });
        }
        samples.push([(tile._bbox[0] + tile._bbox[2]) / 2, (tile._bbox[1] + tile._bbox[3]) / 2]);
        samples.some(function (point) {
          return ecoFeatures.some(function (eco) {
            if (!featureBoxIntersectsPoint(eco._bbox, point[0], point[1])) return false;
            if (geometryContains(eco.geometry, point[0], point[1])) { chosen = eco; return true; }
            return false;
          });
        });
      }
      if (!chosen) {
        var best = Infinity;
        ecoFeatures.forEach(function (eco) {
          var dx = eco._centroid[0] - centroid[0];
          var dy = eco._centroid[1] - centroid[1];
          var distance = dx * dx + dy * dy;
          if (distance < best) { best = distance; chosen = eco; }
        });
      }
      if (chosen) {
        tileEco.set(tile.properties.tile_id, chosen.properties.ecoprovince_id);
        chosen._tiles.push(tile);
      }
    });

    ecoFeatures.forEach(function (eco) {
      var centroid = eco._centroid;
      var province = null;
      if (featureBoxIntersectsPoint(eco._bbox, centroid[0], centroid[1])) {
        province = provinceFeatures.filter(function (feature) {
          return featureBoxIntersectsPoint(feature._bbox, centroid[0], centroid[1]) &&
            geometryContains(feature.geometry, centroid[0], centroid[1]);
        })[0] || null;
      }
      if (!province && eco._tiles.length) {
        var counts = {};
        eco._tiles.forEach(function (tile) {
          tile._provinceCodes.forEach(function (code) {
            var iso = PROVINCE_ALIAS[code] || 'CA-' + code;
            counts[iso] = (counts[iso] || 0) + 1;
          });
        });
        var bestIso = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0];
        province = bestIso ? provinceByIso.get(bestIso) : null;
      }
      eco._provinceIso = province ? province.properties.iso : null;
    });

    return {
      provinces: provinces,
      ecoprovinces: ecoprovinces,
      tiles: tiles,
      zoneNodes: zoneNodes,
      provinceFeatures: provinceFeatures,
      ecoFeatures: ecoFeatures,
      provinceByIso: provinceByIso,
      ecoById: ecoById,
      tileById: tileById,
      tileEco: tileEco,
      tilesByProvince: tilesByProvince
    };
  }

  function selectedTilesFor(selection) {
    var data = state.mapState.data;
    if (!data || !selection) return [];
    if (selection.kind === 'tile') {
      var tile = data.tileById.get(selection.id);
      return tile ? [tile] : [];
    }
    if (selection.kind === 'province') return data.tilesByProvince.get(selection.id) || [];
    if (selection.kind === 'ecoprovince') {
      var eco = data.ecoById.get(selection.id);
      return eco ? eco._tiles.slice() : [];
    }
    if (selection.kind === 'canada') return data.tiles.features.slice();
    return [];
  }

  function selectionStats(selection) {
    var tiles = selectedTilesFor(selection);
    var area = 0;
    var pj = 0;
    var over = 0;
    tiles.forEach(function (tile) {
      var a = Number(tile.properties.area_ha);
      var p = Number(tile.properties.recoverable_pj_base);
      if (Number.isFinite(a)) area += a;
      if (Number.isFinite(p)) pj += p;
      if (tile.properties.over_capacity) over += 1;
    });
    return { tiles: tiles, area: area, pj: pj, cells: tiles.length, over: over };
  }

  function buildSpatialSelection(kind, id) {
    var data = state.mapState.data;
    if (!data) return null;
    if (kind === 'province') {
      var province = data.provinceByIso.get(id);
      if (!province) return null;
      return {
        kind: 'province', id: id, title: province.properties.name, subtitle: id + ' · province',
        provinceIso: id, ecoId: null, tile: null, zoneId: null, feature: province, bbox: province._bbox
      };
    }
    if (kind === 'ecoprovince') {
      var eco = data.ecoById.get(id);
      if (!eco) return null;
      return {
        kind: 'ecoprovince', id: id, title: eco.properties.ecoprovince,
        subtitle: 'Ecoprovince ' + id + ' · ' + eco.properties.ecozone,
        provinceIso: eco._provinceIso, ecoId: id, tile: null, zoneId: null, feature: eco, bbox: eco._bbox
      };
    }
    if (kind === 'tile') {
      var tile = data.tileById.get(id);
      if (!tile) return null;
      var ecoId = data.tileEco.get(id) || null;
      return {
        kind: 'tile', id: id, title: 'RSYC tile ' + id, subtitle: 'Supply tile · ' + tile.properties.provinces,
        provinceIso: tile._provinceIso, ecoId: ecoId, tile: tile, zoneId: null, feature: tile, bbox: tile._bbox
      };
    }
    if (kind === 'zone') {
      var zone = data.zoneNodes.zones.filter(function (item) { return item.zone_id === id; })[0];
      if (!zone || !zone.anchor) return null;
      return {
        kind: 'zone', id: id, title: 'C2070 zone ' + id,
        subtitle: 'Authoritative point anchor · no polygon drawn',
        provinceIso: null, ecoId: null, tile: null, zoneId: id, zone: zone, bbox: null
      };
    }
    return null;
  }

  function selectionZoneMessage(selection) {
    if (selection.zoneId) {
      var status = 'zone filter pending';
      if (state.dispatch.processed) {
        status = state.dispatch.processed.zones.indexOf(selection.zoneId) !== -1
          ? 'dispatch filtered to zone ' + selection.zoneId
          : 'zone ' + selection.zoneId + ' is absent from this output — dispatch stays national';
      }
      return { mapped: true, text: 'Mapped through the authoritative anchor. ' + status + '. No zone polygon is drawn.' };
    }
    return {
      mapped: false,
      text: 'No documented mapping from this ' + selection.kind + ' to a C2070 zone. Dispatch and overview stay national; no zone geometry is invented.'
    };
  }

  function renderBreadcrumb() {
    var el = $('map-breadcrumb');
    if (!el) return;
    var sel = state.spatial.selection;
    var crumbs = [{ label: 'Canada', action: 'root', current: !sel, root: true }];
    if (sel) {
      if (sel.provinceIso && sel.kind !== 'province') {
        crumbs.push({ label: provinceDisplayName(sel.provinceIso), action: 'province:' + sel.provinceIso, current: false });
      }
      if (sel.kind === 'province') {
        crumbs.push({ label: sel.title, action: 'province:' + sel.id, current: true });
      } else if (sel.kind === 'ecoprovince') {
        crumbs.push({ label: sel.title, action: 'ecoprovince:' + sel.id, current: true });
      } else if (sel.kind === 'tile') {
        if (sel.ecoId) crumbs.push({ label: ecoDisplayName(sel.ecoId), action: 'ecoprovince:' + sel.ecoId, current: false });
        crumbs.push({ label: sel.id, action: 'tile:' + sel.id, current: true });
      } else if (sel.kind === 'zone') {
        crumbs.push({ label: 'Zone ' + sel.id, action: 'zone:' + sel.id, current: true });
      }
    }
    el.innerHTML = crumbs.map(function (crumb, index) {
      var html = index ? '<span class="crumb-sep" aria-hidden="true">/</span>' : '';
      if (crumb.current) {
        return html + '<span class="is-current" aria-current="page">' + esc(crumb.label) + '</span>';
      }
      return html + '<button type="button" data-spatial-nav="' + esc(crumb.action) + '"' +
        (crumb.root ? ' class="crumb-root"' : '') + '>' + esc(crumb.label) + '</button>';
    }).join('');
  }

  function provinceDisplayName(iso) {
    var data = state.mapState.data;
    var feature = data && data.provinceByIso.get(iso);
    return feature ? feature.properties.name : iso;
  }

  function ecoDisplayName(id) {
    var data = state.mapState.data;
    var feature = data && data.ecoById.get(id);
    return feature ? feature.properties.ecoprovince : id;
  }

  function renderSelectionPanel() {
    var el = $('map-selection');
    if (!el) return;
    var sel = state.spatial.selection;
    var data = state.mapState.data;
    if (!sel) {
      el.innerHTML = '<p class="selection-kicker">Linked spatial selection</p>' +
        '<h4>Start with a province</h4>' +
        '<p class="selection-empty">Click a province, an ecoprovince boundary, or an RSYC tile. The selection stays linked to the shared year and the URL hash. No C2070 zone is inferred without a documented anchor.</p>' +
        (data ? '<div class="selection-drill"><h5>Provinces</h5><div class="selection-chip-list">' +
          data.provinceFeatures.map(function (feature) {
            return '<button type="button" class="selection-chip" data-spatial-pick="province:' + esc(feature.properties.iso) + '">' +
              esc(feature.properties.name) + '</button>';
          }).join('') + '</div></div>' : '');
      return;
    }
    var stats = selectionStats(sel);
    var zoneMessage = selectionZoneMessage(sel);
    var areaText = sel.zoneId ? '—' : fmt(stats.area / 1e6, 2) + ' Mha';
    var pjText = sel.zoneId ? '—' : fmt(stats.pj, 1) + ' PJ';
    var cellsText = sel.zoneId ? 'anchor only' : stats.cells + ' RSYC tile' + (stats.cells === 1 ? '' : 's');
    var speciesText = sel.zoneId ? 'not applicable' : 'not joined in this bundle';
    var provinceText = sel.provinceIso ? provinceDisplayName(sel.provinceIso) : (sel.kind === 'zone' ? 'British Columbia anchor' : 'not inferred');
    var ecoText = sel.ecoId ? ecoDisplayName(sel.ecoId) + ' (' + sel.ecoId + ')' : (sel.kind === 'zone' ? 'no ecoprovince crosswalk' : '—');
    var facts = sel.kind === 'zone'
      ? '<div><dt>Anchor</dt><dd>' + esc(sel.zone.node_id) + '</dd></div>' +
        '<div><dt>Lon / lat</dt><dd>' + fmt(sel.zone.lon, 3) + ', ' + fmt(sel.zone.lat, 3) + '</dd></div>' +
        '<div><dt>Weight basis</dt><dd>' + esc(sel.zone.weight_basis) + '</dd></div>' +
        '<div><dt>Geometry</dt><dd>point only</dd></div>'
      : '<div><dt>Area</dt><dd>' + areaText + '</dd></div>' +
        '<div><dt>Recoverable base</dt><dd>' + pjText + '</dd></div>' +
        '<div><dt>Cells / tiles</dt><dd>' + cellsText + '</dd></div>' +
        '<div><dt>Species</dt><dd>' + speciesText + '</dd></div>' +
        '<div><dt>Province</dt><dd>' + esc(provinceText) + '</dd></div>' +
        '<div><dt>Ecoprovince</dt><dd>' + esc(ecoText) + '</dd></div>';

    var actions = '<button class="button button--quiet" type="button" data-selection-action="fit">Fit selection</button>' +
      (sel.zoneId ? '<button class="button button--quiet" type="button" data-selection-action="dispatch">Open dispatch · ' + esc(sel.zoneId) + '</button>' : '') +
      '<button class="button button--quiet" type="button" data-selection-action="reset">Reset</button>';

    var drill = '';
    if (data) {
      if (sel.kind === 'province') {
        var ecos = data.ecoFeatures.filter(function (eco) { return eco._provinceIso === sel.id; });
        drill = '<div class="selection-drill"><h5>Ecoprovinces in this province</h5><div class="selection-chip-list">' +
          (ecos.length ? ecos.map(function (eco) {
            return '<button type="button" class="selection-chip" data-spatial-pick="ecoprovince:' + esc(eco.properties.ecoprovince_id) + '">' +
              esc(eco.properties.ecoprovince) + ' · ' + eco._tiles.length + ' tiles</button>';
          }).join('') : '<span class="selection-empty">No ecoprovince centroid falls in this province.</span>') +
          '</div></div>';
      } else if (sel.kind === 'ecoprovince') {
        var ecoTiles = stats.tiles.slice().sort(function (a, b) {
          return Number(b.properties.recoverable_pj_base) - Number(a.properties.recoverable_pj_base);
        });
        drill = '<div class="selection-drill"><h5>RSYC tiles in this ecoprovince</h5><div class="selection-chip-list">' +
          (ecoTiles.length ? ecoTiles.map(function (tile) {
            return '<button type="button" class="selection-chip" data-spatial-pick="tile:' + esc(tile.properties.tile_id) + '">' +
              esc(tile.properties.tile_id) + ' · ' + fmt(tile.properties.recoverable_pj_base, 1) + ' PJ</button>';
          }).join('') : '<span class="selection-empty">No RSYC tile centroid or vertex falls in this ecoprovince.</span>') +
          '</div></div>';
      } else if (sel.kind === 'tile' && sel.ecoId) {
        var siblings = (data.ecoById.get(sel.ecoId)._tiles || []).filter(function (tile) { return tile.properties.tile_id !== sel.id; });
        drill = '<div class="selection-drill"><h5>Other tiles in this ecoprovince</h5><div class="selection-chip-list">' +
          (siblings.length ? siblings.slice(0, 24).map(function (tile) {
            return '<button type="button" class="selection-chip" data-spatial-pick="tile:' + esc(tile.properties.tile_id) + '">' +
              esc(tile.properties.tile_id) + ' · ' + fmt(tile.properties.recoverable_pj_base, 1) + ' PJ</button>';
          }).join('') : '<span class="selection-empty">No sibling tiles assigned.</span>') +
          '</div></div>';
      } else if (sel.kind === 'zone') {
        drill = '<div class="selection-drill"><h5>Other anchor</h5><div class="selection-chip-list">' +
          data.zoneNodes.zones.filter(function (zone) { return zone.anchor && zone.zone_id !== sel.id; }).map(function (zone) {
            return '<button type="button" class="selection-chip" data-spatial-pick="zone:' + esc(zone.zone_id) + '">Zone ' + esc(zone.zone_id) + '</button>';
          }).join('') + '</div></div>';
      }
    }

    var evidence = [];
    evidence.push('Geometry sources: spatial/provinces.geojson (13), spatial/ecoprovinces.geojson (36), spatial/rsyc_tiles.geojson (385).');
    if (!sel.zoneId) evidence.push('Recoverable base is the committed recoverable_pj_base field, not solved dispatch. Species composition is not present in this spatial bundle and is never imputed.');
    if (sel.kind === 'ecoprovince' || sel.kind === 'tile') evidence.push('Ecoprovince assignment uses tile-centroid/vertex containment; boundary-straddling tiles are approximate.');
    if (sel.kind === 'province') evidence.push('RSYC tiles can list more than one province; provincial sums are not additive.');
    if (sel.zoneId) evidence.push('B1/B2 coordinates come from spatial/zone_nodes.json. The other 11 zones carry explicit gaps; no polygon is fabricated.');
    if (stats.over) evidence.push(stats.over + ' tile(s) in this selection carry the over_capacity flag.');

    el.innerHTML = '<p class="selection-kicker">' + esc(sel.kind === 'zone' ? 'C2070 anchor' : 'Linked spatial selection') + '</p>' +
      '<h4>' + esc(sel.title) + '</h4>' +
      '<p class="selection-sub">' + esc(sel.subtitle) + '</p>' +
      '<dl class="selection-facts">' + facts + '</dl>' +
      '<div class="selection-zone ' + (zoneMessage.mapped ? 'is-mapped' : 'is-none') + '"><strong>C2070 zone mapping</strong>' +
        esc(zoneMessage.text) + '</div>' +
      '<div class="selection-actions">' + actions + '</div>' +
      drill +
      '<details class="selection-evidence"><summary>Evidence &amp; caveats</summary><ul>' +
        evidence.map(function (line) { return '<li>' + esc(line) + '</li>'; }).join('') +
      '</ul></details>';
  }

  function applySelectionSource(selection) {
    var map = state.mapState.map;
    if (!map || !map.getSource('spatial-selection')) return;
    var features = [];
    if (selection && selection.feature) {
      features.push({ type: 'Feature', properties: { kind: selection.kind }, geometry: selection.feature.geometry });
    }
    map.getSource('spatial-selection').setData({ type: 'FeatureCollection', features: features });
  }

  function updateAnchorSelectionState() {
    var sel = state.spatial.selection;
    state.mapState.markers.forEach(function (marker) {
      var el = marker.getElement();
      var id = el.getAttribute('data-zone-id');
      el.classList.toggle('is-selected', !!sel && sel.kind === 'zone' && sel.id === id);
    });
  }

  function fitSpatialSelection(selection, duration) {
    var map = state.mapState.map;
    if (!map || !selection) return;
    if (selection.kind === 'zone' && selection.zone) {
      map.easeTo({ center: [selection.zone.lon, selection.zone.lat], zoom: 5.2, duration: duration === undefined ? 650 : duration });
      return;
    }
    if (selection.bbox) {
      map.fitBounds([[selection.bbox[0], selection.bbox[1]], [selection.bbox[2], selection.bbox[3]]], {
        padding: 46,
        maxZoom: selection.kind === 'tile' ? 8 : 6,
        duration: duration === undefined ? 650 : duration
      });
    }
  }

  function applyDispatchZone(zoneId) {
    if (zoneId && state.dispatch.processed && state.dispatch.processed.zones.indexOf(zoneId) === -1) {
      state.dispatch.zone = 'ALL';
    } else {
      state.dispatch.zone = zoneId || 'ALL';
    }
    if ($('dispatch-zone')) {
      renderZoneOptions(state.dispatch.processed);
      if (state.dispatch.processed) renderDispatchChart();
    }
  }

  function selectSpatial(selection, options) {
    if (!selection) return;
    var opts = options || {};
    var previousZone = state.spatial.mappedZone;
    state.spatial.selection = selection;
    state.spatial.mappedZone = selection.zoneId || null;
    state.mapState.pendingSelection = null;
    if (selection.zoneId) applyDispatchZone(selection.zoneId);
    else if (previousZone) applyDispatchZone(null);
    applySelectionSource(selection);
    updateAnchorSelectionState();
    renderBreadcrumb();
    renderSelectionPanel();
    if (state.tab === 'overview' && state.core) renderOverview();
    if (state.tab === 'dispatch' && state.dispatch.processed) renderDispatchChart();
    if (opts.fit !== false) fitSpatialSelection(selection, opts.duration);
    if (!opts.skipHash) updateHash();
    if (!opts.silent) announce('Selected ' + selection.title + '.');
  }

  function clearSpatialSelection(options) {
    var opts = options || {};
    var previousZone = state.spatial.mappedZone;
    state.spatial.selection = null;
    state.spatial.mappedZone = null;
    state.mapState.pendingSelection = null;
    if (previousZone) applyDispatchZone(null);
    applySelectionSource(null);
    updateAnchorSelectionState();
    renderBreadcrumb();
    renderSelectionPanel();
    if (state.tab === 'overview' && state.core) renderOverview();
    if (opts.fit !== false && state.mapState.map) {
      state.mapState.map.fitBounds(CANADA_BOUNDS, { padding: CANADA_FIT_OPTIONS.padding, duration: opts.duration === undefined ? 600 : opts.duration });
    }
    if (!opts.skipHash) updateHash();
    if (!opts.silent) announce('Spatial selection reset.');
  }

  function restoreSelection(parsed, options) {
    var selection = buildSpatialSelection(parsed.kind, parsed.id);
    if (!selection) {
      state.mapState.pendingSelection = null;
      return false;
    }
    selectSpatial(selection, {
      skipHash: true,
      silent: true,
      fit: (options && options.fit) !== false,
      duration: options && options.duration
    });
    return true;
  }

  function spatialNavigation(action) {
    if (!action || action === 'root') {
      clearSpatialSelection();
      return;
    }
    var split = action.indexOf(':');
    if (split === -1) return;
    var kind = action.slice(0, split);
    var id = action.slice(split + 1);
    var selection = buildSpatialSelection(kind, id);
    if (selection) selectSpatial(selection);
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
      var spatialData = prepareSpatialData(provinces, ecoprovinces, tiles, zoneNodes);

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
          id: 'ecoprovince-hit',
          type: 'line',
          source: 'ecoprovinces',
          paint: { 'line-color': '#8aa892', 'line-width': 9, 'line-opacity': 0.01 }
        });
        map.addLayer({
          id: 'tiles-line',
          type: 'line',
          source: 'tiles',
          paint: { 'line-color': '#5b351f', 'line-width': 0.45, 'line-opacity': 0.6 }
        });
        map.addSource('spatial-selection', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
          id: 'spatial-selection-fill',
          type: 'fill',
          source: 'spatial-selection',
          paint: { 'fill-color': '#f2b45c', 'fill-opacity': 0.18 }
        });
        map.addLayer({
          id: 'spatial-selection-line',
          type: 'line',
          source: 'spatial-selection',
          paint: { 'line-color': '#f2b45c', 'line-width': 2.2, 'line-opacity': 0.95 }
        });

        map.on('click', function (event) {
          var tileFeatures = map.queryRenderedFeatures(event.point, { layers: ['tiles-fill'] });
          if (tileFeatures.length) {
            var tileId = tileFeatures[0].properties.tile_id;
            var tileSelection = buildSpatialSelection('tile', tileId);
            if (tileSelection) selectSpatial(tileSelection);
            return;
          }
          var ecoBox = [[event.point.x - 5, event.point.y - 5], [event.point.x + 5, event.point.y + 5]];
          var ecoFeatures = map.queryRenderedFeatures(ecoBox, { layers: ['ecoprovince-hit'] });
          if (ecoFeatures.length) {
            var ecoSelection = buildSpatialSelection('ecoprovince', ecoFeatures[0].properties.ecoprovince_id);
            if (ecoSelection) selectSpatial(ecoSelection);
            return;
          }
          var provinceFeatures = map.queryRenderedFeatures(event.point, { layers: ['province-fill'] });
          if (provinceFeatures.length) {
            var provinceSelection = buildSpatialSelection('province', provinceFeatures[0].properties.iso);
            if (provinceSelection) selectSpatial(provinceSelection);
            return;
          }
          if (state.spatial.selection) clearSpatialSelection({ fit: false, silent: true });
        });
        map.on('mouseenter', 'tiles-fill', function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'tiles-fill', function () { map.getCanvas().style.cursor = ''; });
        map.on('mouseenter', 'province-fill', function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'province-fill', function () { map.getCanvas().style.cursor = ''; });
        map.on('mouseenter', 'ecoprovince-line', function () { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'ecoprovince-line', function () { map.getCanvas().style.cursor = ''; });

        applyMapVisibility();
        mapState.hidden = true;
        $('map-legend').hidden = false;
        renderMapLegend(breaks, colors);
        renderMapStats(provinces, ecoprovinces, tiles, zoneNodes);
        addZoneAnchors(map, zoneNodes);
        applyMapVisibility();
        state.mapState.initialized = true;
        state.mapState.loading = false;
        state.mapState.data = spatialData;
        state.mapState.data.breaks = breaks;
        state.mapState.data.colors = colors;
        map.resize();
        if (state.mapState.pendingSelection) {
          var pending = state.mapState.pendingSelection;
          state.mapState.pendingSelection = null;
          restoreSelection(pending, { duration: 0, fit: true });
        } else {
          map.fitBounds(CANADA_BOUNDS, { padding: CANADA_FIT_OPTIONS.padding, duration: 0 });
        }
        renderBreadcrumb();
        renderSelectionPanel();
      });
      $('map-fit').onclick = function () {
        if (state.mapState.pendingSelection) state.mapState.pendingSelection = null;
        map.fitBounds(CANADA_BOUNDS, { padding: CANADA_FIT_OPTIONS.padding, duration: 700 });
      };
      $('map-reset').onclick = function () { clearSpatialSelection(); };
      $('map-breadcrumb').onclick = function (event) {
        var button = event.target.closest('button[data-spatial-nav]');
        if (!button) return;
        spatialNavigation(button.getAttribute('data-spatial-nav'));
      };
      $('map-selection').onclick = function (event) {
        var pick = event.target.closest('[data-spatial-pick]');
        if (pick) {
          spatialNavigation(pick.getAttribute('data-spatial-pick'));
          return;
        }
        var action = event.target.closest('[data-selection-action]');
        if (!action) return;
        var name = action.getAttribute('data-selection-action');
        if (name === 'reset') clearSpatialSelection();
        else if (name === 'fit' && state.spatial.selection) fitSpatialSelection(state.spatial.selection);
        else if (name === 'dispatch') activateTab('dispatch');
      };
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
      element.setAttribute('data-zone-id', zone.zone_id);
      element.addEventListener('click', function (event) {
        event.stopPropagation();
        var selection = buildSpatialSelection('zone', zone.zone_id);
        if (selection) selectSpatial(selection);
      });
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
    map.setLayoutProperty('ecoprovince-hit', 'visibility', ecoprovinces ? 'visible' : 'none');
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
      ['150', '150 Mm³ (scenario high, conditional)']
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

  function downloadCsvWithHeader(filename, header, rows) {
    var lines = [header.map(csvCell).join(',')];
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

  function downloadCsv(filename, rows) {
    downloadCsvWithHeader(filename, ['REGION', 'TECHNOLOGY', 'YEAR', 'VALUE'], rows);
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

  function constraintSets() {
    return state.live.constraints && Array.isArray(state.live.constraints.sets)
      ? state.live.constraints.sets
      : [];
  }

  function constraintSetById(id) {
    var sets = constraintSets();
    for (var i = 0; i < sets.length; i += 1) {
      if (sets[i] && sets[i].set_id === id) return sets[i];
    }
    if (id === 'screening_v0') return { set_id: 'screening_v0', status: 'screening_only', policy_grade: false, available: true, defaults: FALLBACK_DEFAULTS };
    if (id === 'policy_test_v1') return { set_id: 'policy_test_v1', status: 'draft', policy_grade: false, available: false };
    return null;
  }

  function renderConstraintSelector() {
    var select = $('run-constraint-set');
    if (!select) return;
    var sets = constraintSets();
    var live = state.live.mode === 'live' || state.live.mode === 'no-runs' || state.live.mode === 'readonly';
    var options = [];
    if (sets.length) {
      sets.forEach(function (set) {
        var available = set.available !== false;
        var suffix = set.set_id === 'policy_test_v1'
          ? (available ? ' — draft · policy_grade=false' : ' — unavailable')
          : ' — screening only';
        options.push('<option value="' + esc(set.set_id) + '"' +
          (available && (set.set_id === 'screening_v0' || live) ? '' : ' disabled') + '>' +
          esc(set.set_id + suffix) + '</option>');
      });
    } else {
      options.push('<option value="screening_v0">screening_v0 — screening only</option>');
      options.push('<option value="policy_test_v1" disabled>policy_test_v1 — unavailable in ' + esc(state.live.mode === 'static' ? 'static mode' : 'this mode') + '</option>');
    }
    select.innerHTML = options.join('');
    var current = select.value;
    if (!current || !constraintSetById(current)) {
      var preferred = (state.live.constraints && state.live.constraints.default_set_id) || 'screening_v0';
      select.value = constraintSetById(preferred) ? preferred : (sets[0] ? sets[0].set_id : 'screening_v0');
    }
    renderConstraintBadge();
  }

  function renderConstraintBadge() {
    var badge = $('run-constraint-badge');
    var flagTitle = $('run-policy-flag-title');
    var flagText = $('run-policy-flag-text');
    var select = $('run-constraint-set');
    if (!badge || !select) return;
    var set = constraintSetById(select.value);
    if (!set) {
      badge.textContent = 'constraint set unavailable';
      return;
    }
    if (select.value === 'policy_test_v1') {
      var available = set.available !== false;
      badge.textContent = available
        ? 'draft · policy_grade=false · Stage 1 reserve/build caps'
        : 'policy_test_v1 unavailable — screening only';
      if (flagTitle) flagTitle.textContent = available ? 'policy_test_v1 · draft · policy_grade=false' : 'policy_test_v1 unavailable';
      if (flagText) flagText.textContent = available
        ? 'Stage-1 draft: national peak-proxy reserve margin, capacity credits, build rates, and explicit caps. Not policy-grade; no transmission, timeslices, curtailment, or unit commitment.'
        : 'The policy constraint set could not be loaded. Select screening_v0 or restart the live server with the policy file available.';
    } else {
      badge.textContent = 'screening_only · policy_grade=false';
      if (flagTitle) flagTitle.textContent = 'screening_only · policy_grade=false';
      if (flagText) flagText.textContent = 'No reserve margin, transmission, curtailment, or build-rate constraints. Never policy or forecast.';
    }
  }

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
    renderConstraintSelector();
    syncHarvestCustom();
    updateFormDisabled();
  }

  function updateFormDisabled() {
    var mode = state.live.mode;
    var profile = roleProfile();
    var disabled = state.live.busy || mode === 'checking' || mode === 'static' || mode === 'readonly' || mode === 'degraded' || profile.forestryLocked;
    qsa('#run-form input, #run-form select').forEach(function (input) { input.disabled = disabled; });
    var activeRun = Boolean(state.live.activeRun);
    var create = $('run-create');
    var queue = $('run-queue');
    if (create) create.disabled = disabled || activeRun;
    if (queue) queue.disabled = state.live.busy || mode !== 'live' || !state.live.createdScenario || activeRun || profile.forestryLocked;
    var osecanQueue = $('osecan-queue');
    if (osecanQueue) {
      osecanQueue.disabled = state.live.busy || mode !== 'live' || activeRun;
      if (profile.forestryLocked || profile.energyReadOnly) osecanQueue.disabled = true;
    }
    var cancel = $('run-cancel');
    if (cancel) {
      cancel.hidden = !activeRun;
      cancel.disabled = !activeRun || state.live.busy || state.live.cancelBusy || profile.forestryLocked;
    }
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
      title.textContent = 'Live runner ready — screening or draft policy.';
      text.textContent = 'Create a scenario, queue it, and watch SSE status. screening_v0 is screening_only; policy_test_v1 is a draft and policy_grade=false. Neither is Pareto eligible in the canonical ledger.';
      setModeBadge('live', 'POST /api/scenarios and /api/runs are available.');
    } else if (mode === 'no-runs') {
      banner.classList.add('is-live');
      title.textContent = 'Live API connected; run queueing is disabled.';
      text.textContent = 'Read-only scenario and constraint endpoints respond, but the server needs --allow-runs before POST /api/runs will accept work. Static views still work.';
      setModeBadge('no-runs', 'The live API is up without --allow-runs.');
    } else if (mode === 'readonly') {
      banner.classList.add('is-readonly');
      var role = roleProfile();
      title.textContent = role.forestryLocked ? 'Read-only role — create and queue are disabled.' : 'Live API is read-only.';
      text.textContent = role.forestryLocked
        ? 'Deployment role: ' + role.label + '. Run creation, queueing, and forestry edits are disabled. The committed static views and OSeCAN exports remain available.'
        : 'Create and queue are disabled. The committed static views and OSeCAN exports remain available.';
      setModeBadge('readonly', 'The server is running with --read-only.');
    } else if (mode === 'degraded') {
      title.textContent = 'Live API connected, but screening defaults failed to load.';
      text.textContent = 'The canonical screening_v0 payload did not validate. Create and queue are disabled rather than running with guessed defaults.';
      setModeBadge('degraded', 'GET /api/constraints failed validation.');
    } else if (mode === 'static') {
      title.textContent = 'Static mode — no backend reachable.';
      text.textContent = 'Showing the committed iteration-0 scenarios and outputs. policy_test_v1 is unavailable in static mode; start the FastAPI server with --allow-runs to use the live constraint-set selector.';
      setModeBadge('static', 'GET /api/health failed; using precomputed data only.');
    } else {
      title.textContent = 'Checking live API…';
      text.textContent = 'Static views remain available if no backend responds.';
      setModeBadge('checking');
    }
    renderConstraintSelector();
    updateFormDisabled();

    var osecanBanner = $('osecan-run-banner');
    var osecanNote = $('osecan-live-note');
    var osecanCopy = {
      live: {
        banner: '<span class="needs-run-mark">i</span><strong>Live runner available — screening or draft policy.</strong> “Queue this scenario” creates a live scenario from the calculator levers; screening and policy_test_v1 results are policy_grade=false.',
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
    if (level > MAX_NORMAL_HARVEST_M3) {
      throw new Error('Harvest above 150,000,000 m³/yr is not admissible under documented AAC/NFD; use the CLI --allow-infeasible diagnostic path for the historical 200 Mm³ case.');
    }
    var constraintSet = $('run-constraint-set').value;
    var selectedSet = constraintSetById(constraintSet);
    if (!selectedSet || selectedSet.available === false) {
      throw new Error('Constraint set ' + constraintSet + ' is unavailable; select screening_v0.');
    }
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
      constraintSet: constraintSet,
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
    var selectedSet = form.constraintSet || 'screening_v0';
    var provenance = (state.live.constraints && state.live.constraints.provenance) || {};
    var payload = {
      scenario_id: slugify(form.label),
      label: form.label,
      description: 'Created in the Forest Ledger Run panel with constraint set ' + selectedSet + '. ' +
        (selectedSet === 'policy_test_v1' ? 'Stage-1 draft policy test; not policy-grade.' : 'Live screening only; not policy grade.'),
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
      constraints: { set_id: selectedSet },
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
    if (roleProfile().forestryLocked) {
      setFormError('This deployment role is read-only for forestry actions; creating a scenario is disabled.');
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
    if (roleProfile().forestryLocked) {
      setFormError('This deployment role is read-only for forestry actions; queueing is disabled.');
      return;
    }
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
      upsertRunJob(run);
      renderRunHistory();
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

  function parseRunTime(value) {
    var ms = Date.parse(value || '');
    return Number.isFinite(ms) ? ms : 0;
  }

  function runLabel(job) {
    if (job && job.scenario && job.scenario.label) return job.scenario.label;
    return (job && job.scenario_id) || 'unknown scenario';
  }

  function runFormFromJob(job) {
    var sc = job && job.scenario;
    if (!sc || !sc.label) return null;
    var level = Number(sc.harvest_level_m3_per_year);
    var choice = sc.harvest_path === 'custom' ? 'custom' : String(Math.round(level / 1e6));
    return {
      label: sc.label,
      harvestChoice: choice,
      level: Number.isFinite(level) ? level : null,
      path: sc.harvest_path,
      recovery: sc.recovery_fraction,
      carbon: sc.carbon_price_usd_per_tco2e_2026,
      hydro: sc.hydro_mode,
      pwrbio: sc.pwr_bio_cap_fraction,
      nuclear: sc.nuclear_cap_gw,
      windCost: sc.wind_cost_usd_per_gj,
      windCap: sc.wind_cap_fraction,
      t3: sc.t3_cap_pj,
      t4: sc.t4_cap_pj
    };
  }

  function runElapsedText(job) {
    var start = parseRunTime(job && (job.started_utc || job.created_utc));
    if (!start) return '—';
    var end = parseRunTime(job && job.finished_utc) || Date.now();
    var seconds = Math.max(0, Math.round((end - start) / 1000));
    if (seconds < 60) return seconds + ' s';
    return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  }

  function upsertRunJob(job) {
    if (!job || !job.run_id) return;
    state.live.runHistory = state.live.runHistory.filter(function (item) {
      return item.run_id !== job.run_id;
    });
    state.live.runHistory.unshift(job);
    if (state.live.runHistory.length > 80) state.live.runHistory.length = 80;
  }

  function renderRunHistory() {
    var list = $('run-history-list');
    var count = $('run-history-count');
    if (!list) return;
    var jobs = state.live.runHistory || [];
    if (count) count.textContent = jobs.length + ' run' + (jobs.length === 1 ? '' : 's');
    if (!jobs.length) {
      list.innerHTML = '<p class="run-history-empty">No live runs loaded yet.</p>';
      return;
    }
    var canMutate = state.live.mode === 'live';
    list.innerHTML = jobs.map(function (job) {
      var status = job.status || 'unknown';
      var active = status === 'queued' || status === 'running';
      var actions = '';
      if (active && canMutate) {
        actions += '<button class="button button--quiet run-row-action" type="button" data-run-action="cancel" data-run-id="' + esc(job.run_id) + '">Cancel</button>';
      } else if (canMutate && isTerminalStatus(status)) {
        actions += '<button class="button button--quiet run-row-action" type="button" data-run-action="retry" data-run-id="' + esc(job.run_id) + '">Retry</button>';
      }
      if (status === 'completed' && job.output_available) {
        actions += '<button class="button button--quiet run-row-action" type="button" data-run-action="open" data-run-id="' + esc(job.run_id) + '">Open result</button>';
      }
      return '<div class="run-history-row is-' + esc(status) + '">' +
        '<div class="run-history-main"><strong>' + esc(runLabel(job)) + '</strong>' +
          '<span class="mono">' + esc(job.run_id) + '</span></div>' +
        '<div class="run-history-meta"><span class="run-history-status">' + esc(status) + '</span>' +
          '<span class="mono">' + esc(runElapsedText(job)) + '</span></div>' +
        '<div class="run-history-actions">' + actions + '</div></div>';
    }).join('');
  }

  function updateRunStatusNote(status) {
    var note = $('run-status-note');
    if (!note) return;
    var copy = {
      queued: 'Queued — waiting for a runner slot. Elapsed time includes queue wait; no progress percentage is estimated.',
      running: 'Running — status comes from SSE, with polling fallback. Duration is unbounded; elapsed time is exact.',
      completed: 'Completed — normalized output fetched and added to the session rail once.',
      cancelled: 'Cancelled — the runner was stopped and no result was added.',
      failed: 'Failed — no result was added. Retry the immutable snapshot from the history below.',
      interrupted: 'Interrupted — the server or worker stopped before completion. Retry the immutable snapshot.',
      not_implemented: 'Not implemented — this runner cannot produce a result yet.'
    };
    note.textContent = copy[status] || 'No progress estimate is shown; elapsed time is the only clock.';
  }

  function setRunState(status, label) {
    var text = $('run-state');
    var badge = $('run-state-badge');
    if (text) text.textContent = label || status || 'idle';
    if (!badge) return;
    badge.textContent = status || 'idle';
    badge.classList.remove('is-running', 'is-completed', 'is-failed', 'is-cancelled');
    if (status === 'queued' || status === 'running') badge.classList.add('is-running');
    if (status === 'completed') badge.classList.add('is-completed');
    if (status === 'cancelled') badge.classList.add('is-cancelled');
    if (status === 'failed' || status === 'interrupted' || status === 'not_implemented') badge.classList.add('is-failed');
    updateRunStatusNote(status);
  }

  function attachActiveRun(job, options) {
    var opts = options || {};
    if (!job || !job.run_id) return;
    clearPollTimer();
    if (state.live.logRenderTimer) {
      window.clearTimeout(state.live.logRenderTimer);
      state.live.logRenderTimer = null;
    }
    state.live.activeRun = job;
    state.live.currentJob = job;
    state.live.finalizingRunId = null;
    state.live.runStartedAt = parseRunTime(job.started_utc || job.created_utc) || Date.now();
    state.live.logLines = [];
    state.live.logCount = 0;
    if (!opts.keepOutput) {
      renderRunOutputIdle();
      $('run-policy-flag').hidden = false;
    }
    if ($('run-meta-scenario')) $('run-meta-scenario').textContent = job.scenario_id || '—';
    if ($('run-meta-run')) $('run-meta-run').textContent = job.run_id;
    setRunState(job.status || 'queued', job.status || 'queued');
    upsertRunJob(job);
    renderRunHistory();
    startElapsed();
    renderRunLog();
    updateFormDisabled();
    subscribeToRun(job.run_id);
  }

  async function hydrateRuns() {
    var client = liveApi();
    if (!client) return;
    var payload = await client.listRuns();
    var jobs = Array.isArray(payload.runs) ? payload.runs.slice(0, 80) : [];
    state.live.runHistory = jobs;
    renderRunHistory();

    var completed = jobs.filter(function (job) {
      return job.status === 'completed' && job.output_available;
    }).slice().reverse();
    var fetched = {};
    for (var i = 0; i < completed.length; i += 1) {
      var job = completed[i];
      try {
        var output = await client.getRunOutput(job.run_id);
        fetched[job.run_id] = output;
        addSessionRun(job, output, runFormFromJob(job));
      } catch (error) {
        /* A completed run without readable output stays in history with Retry. */
      }
    }

    if (state.live.activeRun) return;
    var active = jobs.filter(function (item) {
      return item.status === 'queued' || item.status === 'running';
    })[0];
    if (active) {
      attachActiveRun(active, { keepOutput: true });
      setFormStatus('Reattached to ' + active.status + ' run ' + active.run_id + '.', false);
      announce('Reattached to live run ' + active.run_id + '.');
      return;
    }
    var latest = jobs[0];
    if (!latest) return;
    state.live.currentJob = latest;
    $('run-meta-scenario').textContent = latest.scenario_id || '—';
    $('run-meta-run').textContent = latest.run_id;
    $('run-meta-elapsed').textContent = runElapsedText(latest);
    setRunState(latest.status, latest.status);
    if (latest.status === 'completed' && fetched[latest.run_id]) {
      renderRunOutput(latest, fetched[latest.run_id]);
    } else if (isTerminalStatus(latest.status)) {
      renderRunOutputError(latest, latest.message || ('Run ended with status ' + latest.status + '.'));
    }
    updateFormDisabled();
  }

  async function refreshRunHistory() {
    var client = liveApi();
    if (!client) return;
    try {
      var payload = await client.listRuns();
      if (payload && Array.isArray(payload.runs)) {
        state.live.runHistory = payload.runs.slice(0, 80);
        renderRunHistory();
      }
    } catch (error) {
      /* History is advisory; SSE/polling remains authoritative for the active run. */
    }
  }

  async function cancelRunById(runId) {
    var client = liveApi();
    if (!client || !runId || state.live.cancelBusy) return;
    state.live.cancelBusy = true;
    updateFormDisabled();
    setFormStatus('Cancelling ' + runId + '…', false);
    try {
      var job = await client.cancelRun(runId);
      upsertRunJob(job);
      if (activeRunMatches(runId)) {
        if (state.live.eventSource) {
          state.live.eventSource.close();
          state.live.eventSource = null;
        }
        clearPollTimer();
        stopElapsed();
        state.live.currentJob = job;
        setRunState(job.status, job.status);
        $('run-meta-elapsed').textContent = runElapsedText(job);
        renderRunOutputError(job, 'Run cancelled by user. The process group was stopped and no result was added.');
        state.live.activeRun = null;
        state.live.finalizingRunId = null;
      }
      renderRunHistory();
      updateFormDisabled();
      setFormStatus(job.status === 'cancelled' ? 'Cancelled ' + runId + '. No result was added.' : 'Run is already ' + job.status + '.', false);
      announce('Live run ' + runId + ' ' + job.status + '.');
      refreshRunHistory();
    } catch (error) {
      setFormError('Cancel failed: ' + error.message);
      setFormStatus('Cancel failed: ' + error.message, true);
      refreshRunHistory();
    } finally {
      state.live.cancelBusy = false;
      updateFormDisabled();
    }
  }

  async function retryRunById(runId) {
    var client = liveApi();
    if (!client || !runId || state.live.retryBusy) return;
    if (state.live.activeRun) {
      setFormError('Wait for the active run to finish or cancel it before retrying.');
      return;
    }
    state.live.retryBusy = true;
    setFormError('');
    setFormStatus('Retrying ' + runId + ' from its immutable scenario snapshot…', false);
    try {
      var job = await client.retryRun(runId);
      state.live.activeForm = runFormFromJob(job) || state.live.lastForm;
      attachActiveRun(job);
      setFormStatus('Retry queued as ' + job.run_id + '; connecting to the event stream…', false);
      announce('Retry queued for ' + runLabel(job) + '.');
    } catch (error) {
      setFormError('Retry failed: ' + error.message);
      setFormStatus('Retry failed: ' + error.message, true);
      refreshRunHistory();
    } finally {
      state.live.retryBusy = false;
      updateFormDisabled();
    }
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
    if (!state.live.logRenderTimer) {
      state.live.logRenderTimer = window.setTimeout(function () {
        state.live.logRenderTimer = null;
        renderRunLog();
      }, 180);
    }
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
    return ['completed', 'failed', 'interrupted', 'not_implemented', 'cancelled'].indexOf(status) !== -1;
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
          if (state.live.activeRun) {
            state.live.activeRun.status = 'running';
            state.live.activeRun.started_utc = state.live.activeRun.started_utc || event.ts;
            upsertRunJob(state.live.activeRun);
            renderRunHistory();
          }
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
    var current = state.live.activeRun;
    setRunState((current && current.status) || 'running', (current && current.status) || 'running');
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
        if (state.live.activeRun && job.status !== state.live.activeRun.status && !isTerminalStatus(job.status)) {
          state.live.activeRun.status = job.status;
          state.live.currentJob = job;
          upsertRunJob(job);
          renderRunHistory();
          setRunState(job.status, job.status);
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
        setFormStatus('Completed ' + job.run_id + '. Added to the session rail and comparison only (not Pareto eligible).', false);
        announce('Live run ' + job.run_id + ' completed. Screening-only result added to the session rail.');
      } catch (error) {
        if (!activeRunMatches(runId)) return;
        renderRunOutputError(job, 'Run completed but normalized output is unavailable: ' + error.message);
        setFormStatus('Completed, but output unavailable: ' + error.message, true);
      }
    } else if (job.status === 'cancelled') {
      renderRunOutputError(job, job.message || 'Run cancelled by user.');
      setFormStatus('Cancelled ' + job.run_id + '. No result was added.', false);
      announce('Live run ' + job.run_id + ' cancelled.');
    } else {
      renderRunOutputError(job, job.message || ('Run ended with status ' + job.status + '.'));
      setFormStatus('Run ' + job.status + ': ' + (job.message || 'no message'), true);
      announce('Live run ' + job.run_id + ' ' + job.status + '. No result was added.');
    }
    upsertRunJob(job);
    renderRunHistory();
    refreshRunHistory();
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

  function addSessionRun(job, output, formOverride) {
    var checked = validateLiveOutput(output);
    var form = formOverride || state.live.activeForm || state.live.lastForm || {};
    var summary = output.summary || {};
    var metrics = output.metrics || {};
    var railId = 'live:' + job.run_id;
    var label = summary.scenario_label || form.label || output.scenario_id || job.scenario_id;
    var setSummary = summary.constraints || {};
    var feasibilityStatus = summary.feasibility_status || (Number(form.level) > MAX_NORMAL_HARVEST_M3 ? 'fail' : (Number(form.level) > 118977339 ? 'conditional' : 'pass'));
    var feasibilityNotes = summary.feasibility_notes || [(setSummary.set_id === 'policy_test_v1' ? 'Policy-test draft; not policy-grade. ' : '') + 'Harvest feasibility is screened at the runner boundary.'];
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
      description: 'Live session run. Constraint set ' + (setSummary.set_id || form.constraintSet || 'screening_v0') +
        (setSummary.set_id === 'policy_test_v1' ? ' (draft, policy_grade=false)' : ' (screening_only)') +
        '; harvest feasibility=' + feasibilityStatus + '.',
      feasibility_status: feasibilityStatus,
      feasibility_notes: feasibilityNotes,
      pareto_eligible: false,
      harvest_exogenous: true,
      constraint_set_id: setSummary.set_id || form.constraintSet || 'screening_v0',
      source_run: job.run_id,
      sources: [output.source || ('run ' + job.run_id + '/output/dispatch_by_zone.csv')],
      dimensions: {
        harvest: { level: form.harvestChoice === 'custom' ? 'custom' : form.harvestChoice + 'm', m3_per_year: form.level, basis: form.path },
        carbon_price_usd_per_t: form.carbon,
        bio_cost_usd_per_gj: (state.live.defaults || {}).generic_bio_cost_usd_per_gj,
        recovery_fraction: form.recovery,
        supply_curve: 'tiered',
        hydro_mode: form.hydro,
        pwrbio_cap: { fraction: form.pwrbio },
        nuclear_cap_gw: form.nuclear,
        wind: { cap: fmtPct(form.windCap, 0), integration_limit: null, cost_usd_per_gj: form.windCost },
        hydrated: Boolean(formOverride),
        zero_tiers: zeroTiers
      },
      metrics: {
        objective: finiteOrNull(metrics.objective),
        emissions_total: finiteOrNull(metrics.emissions_total),
        emissions_2050: finiteOrNull(metrics.emissions_2050),
        national_bcw_2050: finiteOrNull(metrics.national_bcw_2050),
        pwrbcw_2050: finiteOrNull(metrics.pwrbcw_2050),
        pwrbio_2050: finiteOrNull(metrics.pwrbio_2050),
        bio_share_2050: finiteOrNull(metrics.bio_share_2050),
        bio_share_basis: metrics.bio_share_basis || 'derived_from_dispatch_2050',
        solver_status: metrics.solver_status || summary.solver_status || 'completed'
      },
      flags: {
        solver: metrics.solver_status || summary.solver_status || 'completed',
        publishable: false,
        superseded_for_emissions: false,
        diagnostic: false,
        post_fix_emissions: false,
        screening_only: (setSummary.set_id || form.constraintSet) !== 'policy_test_v1',
        policy_grade: false,
        harvest_exogenous: true,
        feasibility_status: feasibilityStatus,
        pareto_eligible: false,
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
    if (state.tab === 'explore') renderExplore();
    if (state.selectedId === railId) renderOverview();
  }

  function renderRunOutput(job, output) {
    var container = $('run-output-summary');
    if (!container) return;
    var metrics = output.metrics || {};
    var summary = output.summary || {};
    var setSummary = summary.constraints || {};
    var isPolicy = setSummary.set_id === 'policy_test_v1';
    var railId = 'live:' + job.run_id;
    container.hidden = false;
    container.innerHTML = '<div class="run-output-card">' +
      '<div class="run-output-head"><h3>Completed · ' + esc(isPolicy ? 'policy draft output' : 'screening-only output') + '</h3><span class="mono">' + esc(output.source || '') + '</span></div>' +
      '<p class="run-output-message">' + esc((output.summary && output.summary.message) || 'Run completed.') + '</p>' +
      '<div class="result-grid">' +
        '<div class="result"><span>Objective</span><strong>' + fmt(metrics.objective, 2) + '</strong><small>M$</small></div>' +
        '<div class="result"><span>Cumulative emissions</span><strong>' + fmt(metrics.emissions_total, 3) + '</strong><small>Mt CO₂</small></div>' +
        '<div class="result"><span>National BCW 2050</span><strong>' + fmt(metrics.national_bcw_2050, 2) + '</strong><small>PJ fuel</small></div>' +
        '<div class="result"><span>PWRBCW 2050</span><strong>' + fmt(metrics.pwrbcw_2050, 2) + '</strong><small>PJ electricity</small></div>' +
      '</div>' +
      '<div class="run-output-flag"><strong>' + esc(isPolicy ? 'policy_test_v1 · draft · policy_grade=false' : 'screening_only · policy_grade=false') +
      '</strong><br>Added to the session rail and comparison only. This is not a publishable or policy-grade C2070 result and is not Pareto eligible.</div>' +
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
    var canRetry = job && job.run_id && isTerminalStatus(job.status);
    container.hidden = false;
    container.innerHTML = '<div class="run-output-card ' + (job.status === 'cancelled' ? 'is-cancelled' : 'is-failed') + '">' +
      '<div class="run-output-head"><h3>No result — ' + esc(job.status) + '</h3><span class="mono">' + esc(job.run_id) + '</span></div>' +
      '<p class="run-output-message">' + esc(message || 'The run did not complete.') + '</p>' +
      '<div class="run-output-flag"><strong>No fake results.</strong><br>Nothing was added to the scenario rail, trade-off, or dispatch.' +
        (job.status === 'cancelled' ? ' The process group was stopped; retry the immutable snapshot to run it again.' : ' Inspect the event log and runner summary for diagnosis.') + '</div>' +
      (canRetry ? '<div class="run-output-actions"><button class="button button--quiet" id="run-retry-failed" type="button">Retry this run</button></div>' : '') +
    '</div>';
    var retry = $('run-retry-failed');
    if (retry) {
      retry.addEventListener('click', function () { retryRunById(job.run_id); });
    }
  }

  function renderRunOutputIdle() {
    var container = $('run-output-summary');
    if (!container) return;
    container.hidden = true;
    container.innerHTML = '';
  }

  async function queueOsecanScenario() {
    if (roleProfile().energyReadOnly) {
      setFormStatus('The OSeCAN/energy linkage is read-only for this role; queueing is disabled.', true);
      return;
    }
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

  function roleProfile() {
    var health = state.live.health;
    var mode = state.live.mode;
    if (!health || mode === 'static') {
      return {
        id: null,
        label: mode === 'static' ? 'static · no role' : 'checking…',
        staticMode: true,
        known: true,
        forestryLocked: false,
        energyReadOnly: false,
        canRun: false
      };
    }
    var role = String(health.role || '').toLowerCase();
    var known = Object.prototype.hasOwnProperty.call(ROLE_LABELS, role);
    var locked = !known || health.read_only === true || role === 'public' || role === 'energy_modeller';
    var energyReadOnly = locked || role !== 'analyst';
    return {
      id: role || null,
      label: known ? ROLE_LABELS[role] : 'unrecognised role · locked',
      staticMode: false,
      known: known,
      forestryLocked: locked,
      energyReadOnly: energyReadOnly,
      canRun: Boolean(health.allow_runs) && !locked
    };
  }

  function updateRoleUI() {
    var profile = roleProfile();
    var badge = $('role-badge');
    var badgeText = $('role-badge-text');
    var notice = $('role-notice');
    if (badge && badgeText) {
      badge.hidden = false;
      badge.classList.remove('is-checking', 'is-public', 'is-analyst', 'is-model-builder', 'is-energy');
      var badgeClass = profile.staticMode ? 'is-analyst' : (profile.id === 'model_builder' ? 'is-model-builder' : (profile.id === 'analyst' ? 'is-analyst' : (profile.id === 'energy_modeller' ? 'is-energy' : 'is-public')));
      badge.classList.add(badgeClass);
      badgeText.textContent = 'Role · ' + profile.label;
      badge.title = profile.staticMode
        ? 'No live API role. Static mode behavior is unchanged.'
        : 'Deployment role from /api/health. Roles select UI/action envelopes; they do not authenticate users.';
    }
    if (notice) {
      var message = '';
      if (!profile.staticMode) {
        if (!profile.known) {
          message = 'Unrecognised deployment role. The UI fails closed: run creation, queueing, and edits are disabled.';
        } else if (profile.id === 'public') {
          message = 'Read-only role: public. Run creation, queueing, and forestry edits are disabled. Zone, dispatch, map, and OSeCAN export surfaces remain available.';
        } else if (profile.id === 'energy_modeller') {
          message = 'Read-only role: energy modeller. Forestry edits, run creation, and queueing are disabled; the OSeCAN/energy linkage is inspection-only.';
        } else if (profile.id === 'model_builder') {
          message = 'Model builder: forestry scenario actions are enabled. The OSeCAN/energy linkage remains read-only; model rebuilds and publication stay host-operator work.';
        } else if (profile.id === 'analyst' && state.live.health.read_only) {
          message = 'Analyst role on a read-only server. Run creation, queueing, and edits are disabled.';
        }
      }
      notice.hidden = !message;
      notice.textContent = message;
      notice.classList.toggle('is-warning', Boolean(message));
    }
    document.body.setAttribute('data-role', profile.staticMode ? 'static' : (profile.id || 'unknown'));
    var runTab = $('tab-run');
    if (runTab) {
      runTab.classList.toggle('is-role-locked', profile.forestryLocked);
      runTab.setAttribute('data-role-locked', profile.forestryLocked ? 'true' : 'false');
      runTab.title = profile.forestryLocked ? 'Read-only role: run controls are disabled' : '';
    }
    qsa('#osecan-harvest, #osecan-recovery, #osecan-cost').forEach(function (control) {
      control.disabled = profile.energyReadOnly && !profile.staticMode;
    });
    var osecanQueue = $('osecan-queue');
    if (osecanQueue) {
      osecanQueue.disabled = profile.staticMode
        ? state.live.mode !== 'live'
        : (profile.energyReadOnly || !profile.canRun || state.live.busy || Boolean(state.live.activeRun));
      osecanQueue.title = profile.energyReadOnly && !profile.staticMode
        ? 'Energy linkage is read-only for this role.'
        : (profile.canRun ? '' : 'Run queueing is not available for this role or server mode.');
    }
    updateFormDisabled();
  }

  async function initLiveMode() {
    var client = liveApi();
    if (!client) {
      state.live.mode = 'static';
      applyRunDefaults(FALLBACK_DEFAULTS);
      updateRoleUI();
      renderRunMode();
      return;
    }
    var probe = await client.probe();
    if (!probe.available) {
      state.live.mode = 'static';
      state.live.health = null;
      applyRunDefaults(FALLBACK_DEFAULTS);
      updateRoleUI();
      renderRunMode();
      return;
    }
    state.live.health = probe.health;
    try {
      var constraints = await client.getConstraints();
      var defaultId = constraints.default_set_id || (constraints.sets[0] && constraints.sets[0].set_id);
      var set = (constraints.sets || []).filter(function (item) { return item.set_id === defaultId; })[0] || constraints.sets[0];
      state.live.constraints = constraints;
      state.live.defaults = (set && set.defaults) || FALLBACK_DEFAULTS;
      state.live.mode = probe.health.read_only ? 'readonly' : (probe.health.allow_runs ? 'live' : 'no-runs');
      applyRunDefaults(state.live.defaults);
    } catch (error) {
      state.live.mode = 'degraded';
      state.live.defaults = FALLBACK_DEFAULTS;
      applyRunDefaults(FALLBACK_DEFAULTS);
    }
    updateRoleUI();
    renderRunMode();
    try {
      await hydrateRuns();
    } catch (error) {
      setFormStatus('Live API ready; run history could not be hydrated: ' + error.message, true);
    }
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
    var constraintSelect = $('run-constraint-set');
    if (constraintSelect) {
      constraintSelect.addEventListener('change', function () {
        renderConstraintBadge();
        invalidateCreatedScenario();
      });
    }
    $('run-queue').addEventListener('click', queueCreatedScenario);
    var cancel = $('run-cancel');
    if (cancel) {
      cancel.addEventListener('click', function () {
        if (state.live.activeRun) cancelRunById(state.live.activeRun.run_id);
      });
    }
    var history = $('run-history-list');
    if (history) {
      history.addEventListener('click', function (event) {
        var button = event.target.closest('[data-run-action]');
        if (!button) return;
        var action = button.getAttribute('data-run-action');
        var runId = button.getAttribute('data-run-id');
        if (action === 'cancel') cancelRunById(runId);
        if (action === 'retry') retryRunById(runId);
        if (action === 'open') {
          selectScenario('live:' + runId);
          activateTab('dispatch');
        }
      });
    }
    renderRunHistory();
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
      var statusBadge = family.fleet_corrected
        ? '<span class="badge badge--fleet">fleet-corrected</span>'
        : (family.superseded_for_fleet
          ? '<span class="badge badge--prefleet">' + esc(FLEET_BANNER_TEXT) + '</span>'
          : (family.superseded_for_emissions
            ? '<span class="badge badge--superseded">superseded</span>'
            : (family.post_fix_emissions ? '<span class="badge badge--postfix">post-fix</span>' : '<span class="badge badge--ink">pre-fix</span>')));
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

  /* ---------------- Economics ---------------- */

  function validateEconomics(econ) {
    requireTrue(econ && econ.schema_version === 'economics/1', 'economics schema mismatch');
    var h = econ.headline || {};
    requireTrue(h.rows === 945 && h.zone_system_groups === 21, 'economics row count mismatch');
    requireTrue(Math.abs(h.median_base_net_model_cad_m3 - 48.84) < 1e-9, 'stale economics median');
    requireTrue(Math.abs(h.min_base_net_model_cad_m3 + 23.38) < 1e-9, 'stale economics minimum');
    requireTrue(Math.abs(h.max_base_net_model_cad_m3 - 86.85) < 1e-9, 'stale economics maximum');
    requireTrue(h.negative_net_base_rows === 168, 'stale economics negative-row count');
    requireTrue(Array.isArray(econ.zones) && econ.zones.length === 7, 'economics zone count mismatch');
    econ.zones.forEach(function (zone) {
      requireTrue(Array.isArray(zone.systems) && zone.systems.length === 3, 'economics system count mismatch');
      zone.systems.forEach(function (group) {
        requireTrue(ECONOMICS_SYSTEMS.indexOf(group.system) !== -1, 'unknown economics system');
        [group.low_cad_m3, group.base_cad_m3, group.high_cad_m3].forEach(function (value) {
          requireTrue(Number.isFinite(value), 'non-finite economics value');
        });
      });
    });
    requireTrue(econ.qc_reference &&
      econ.qc_reference.net_value_status === 'separate_reference_not_net_value', 'QC reference is not separated');
    requireTrue(Math.abs(econ.qc_reference.cost_spread_after_transfers_cad_m3 - 0.81) < 1e-9, 'stale QC spread');
    requireTrue(Array.isArray(econ.evidence_rows) && econ.evidence_rows.length > 0, 'evidence tier rows are missing');
    requireTrue(econ.labels && econ.labels.evidence === 'model_estimate' &&
      econ.labels.policy_grade === false, 'economics evidence labels mismatch');
    requireTrue(econ.labels.forest_lp_input === false && econ.labels.public_osecan_input === false,
      'economics must stay outside the Forest LP and public OSeCAN');
    requireTrue(econ.downloads && econ.downloads.detail_csv && econ.downloads.model_inputs_zip,
      'economics downloads are missing');
  }

  function economicsScale(econ) {
    var values = [];
    econ.zones.forEach(function (zone) {
      zone.systems.forEach(function (group) {
        values.push(group.low_cad_m3, group.base_cad_m3, group.high_cad_m3);
      });
    });
    var low = Math.min.apply(null, values);
    var high = Math.max.apply(null, values);
    var padding = Math.max(5, (high - low) * 0.04);
    return { min: Math.floor(low - padding), max: Math.ceil(high + padding) };
  }

  function economicsPct(value, scale) {
    return ((value - scale.min) / (scale.max - scale.min)) * 100;
  }

  function economicsBadges(econ) {
    return '<span class="badge badge--econ-public">public_reference</span>' +
      '<span class="badge badge--econ-model">' + esc(econ.labels.evidence) + '</span>' +
      '<span class="badge badge--econ-policy">policy_grade=' + (econ.labels.policy_grade ? 'true' : 'false') + '</span>' +
      '<span class="badge badge--econ-readonly">reported sensitivity · read-only</span>' +
      '<span class="badge badge--econ-readonly">no Forest LP / OSeCAN input</span>';
  }

  function renderEconomicsRange(econ) {
    var h = econ.headline;
    var scale = economicsScale(econ);
    var low = economicsPct(h.min_base_net_model_cad_m3, scale);
    var high = economicsPct(h.max_base_net_model_cad_m3, scale);
    var median = economicsPct(h.median_base_net_model_cad_m3, scale);
    var zero = economicsPct(0, scale);
    var negativeWidth = Math.max(0, Math.min(high, zero) - low);
    $('economics-range').innerHTML =
      '<div class="economics-range-axis">' +
        '<span class="economics-range-zero-label" style="left:' + zero + '%">0</span>' +
      '</div>' +
      '<div class="economics-range-track" aria-hidden="true">' +
        '<span class="economics-range-band" style="left:' + low + '%;width:' + Math.max(0, high - low) + '%"></span>' +
        '<span class="economics-range-negative" style="left:' + low + '%;width:' + negativeWidth + '%"></span>' +
        '<span class="economics-range-zero" style="left:' + zero + '%"></span>' +
        '<span class="economics-range-median" style="left:' + median + '%"></span>' +
      '</div>' +
      '<div class="economics-range-labels">' +
        '<span>' + fmt(h.min_base_net_model_cad_m3, 2) + '</span>' +
        '<span>median <strong>' + fmt(h.median_base_net_model_cad_m3, 2) + '</strong></span>' +
        '<span>' + fmt(h.max_base_net_model_cad_m3, 2) + '</span>' +
      '</div>' +
      '<div class="economics-range-foot">' +
        '<span class="economics-range-key"><i class="is-negative"></i>negative part of base range</span>' +
        '<span class="economics-range-key"><i class="is-median"></i>median</span>' +
        '<span class="mono small">' + fmt(h.negative_net_base_rows, 0) + ' / ' + fmt(h.rows, 0) +
          ' rows below zero · CAD/m³</span>' +
      '</div>';
  }

  function renderEconomicsQc(econ) {
    var qc = econ.qc_reference;
    $('economics-qc').innerHTML =
      '<p class="kicker">Separate reference · QC</p>' +
      '<h3>Not net value</h3>' +
      '<div class="economics-qc-hero">' +
        '<strong>' + fmt(qc.cost_spread_after_transfers_cad_m3, 2) + '</strong>' +
        '<span>CAD/m³ after-transfer cost spread</span>' +
      '</div>' +
      '<span class="badge badge--econ-qc">separate_reference_not_net_value</span>' +
      '<dl class="economics-qc-list">' +
        '<div><dt>Purchase cost</dt><dd>' + fmt(qc.purchase_cost_cad_m3, 2) + '</dd></div>' +
        '<div><dt>BGAD before transfers</dt><dd>' + fmt(qc.bgad_before_transfers_cad_m3, 2) + '</dd></div>' +
        '<div><dt>Transfers</dt><dd>' + fmt(qc.transfers_total_cad_m3, 2) + '</dd></div>' +
        '<div><dt>BGAD after transfers</dt><dd>' + fmt(qc.bgad_after_transfers_cad_m3, 2) + '</dd></div>' +
        '<div><dt>Spread before transfers</dt><dd>' + fmt(qc.cost_spread_before_transfers_cad_m3, 2) + '</dd></div>' +
      '</dl>' +
      '<p class="method-note">' + esc(qc.notes) + '</p>' +
      '<p class="method-note mono small">' + esc(qc.geography) + ' · ' + esc(qc.species_group) +
        ' · ' + fmt(qc.price_year, 0) + ' · ' + esc(qc.evidence) + ' · policy_grade=false</p>';
  }

  function renderEconomicsTable(econ) {
    var scale = economicsScale(econ);
    var rows = '';
    econ.zones.forEach(function (zone) {
      zone.systems.forEach(function (group, index) {
        var lowPct = economicsPct(group.low_cad_m3, scale);
        var highPct = economicsPct(group.high_cad_m3, scale);
        rows += '<tr class="' + (group.negative ? 'is-negative' : '') + '">' +
          (index === 0
            ? '<td class="economics-zone-cell" rowspan="' + zone.systems.length + '"><strong>' +
              esc(zone.zone) + '</strong><span>' + esc(zone.price_region) + ' · ' + fmt(zone.n_rows, 0) +
              ' rows</span></td>'
            : '') +
          '<td><span class="badge badge--system badge--system-' + esc(group.system) + '">' +
            esc(group.system) + '</span></td>' +
          '<td class="num' + (group.low_cad_m3 < 0 ? ' is-negative-value' : '') + '">' +
            fmt(group.low_cad_m3, 2) + '</td>' +
          '<td class="num economics-base' + (group.base_cad_m3 < 0 ? ' is-negative-value' : '') + '">' +
            fmt(group.base_cad_m3, 2) + '</td>' +
          '<td class="num' + (group.high_cad_m3 < 0 ? ' is-negative-value' : '') + '">' +
            fmt(group.high_cad_m3, 2) + '</td>' +
          '<td class="num mono">' + fmt(group.base_min_cad_m3, 2) + ' to ' + fmt(group.base_max_cad_m3, 2) + '</td>' +
          '<td><span class="economics-range-mini" aria-hidden="true">' +
            '<span class="economics-range-mini-fill" style="left:' + lowPct + '%;width:' +
              Math.max(0.8, highPct - lowPct) + '%"></span>' +
            '<span class="economics-range-mini-zero" style="left:' + economicsPct(0, scale) + '%"></span>' +
          '</span></td>' +
          '<td class="num' + (group.negative_rows ? ' is-negative-value' : '') + '">' +
            fmt(group.negative_rows, 0) + ' / ' + fmt(group.n_rows, 0) + '</td>' +
          '</tr>';
      });
    });
    $('economics-table').innerHTML =
      '<caption>Modeled base net_model by zone and system · CAD/m³ · summary medians · low/base/high are model scenarios, not observations</caption>' +
      '<thead><tr><th>Zone</th><th>System</th><th class="num">Low</th><th class="num">Base</th>' +
      '<th class="num">High</th><th class="num">Base range</th><th>Position</th><th class="num">Negative</th></tr></thead>' +
      '<tbody>' + rows + '</tbody>';
  }

  function renderEconomicsEvidence(econ) {
    var badges = econ.evidence_tiers.map(function (tier) {
      return '<span class="badge badge--econ-tier">' + esc(tier) + '</span>';
    }).join('');
    var rows = econ.evidence_rows.map(function (row) {
      var revenue = row.revenue_median_cad_m3 == null
        ? '<span class="muted">not estimated</span>'
        : '<span class="mono">' + fmt(row.revenue_min_cad_m3, 2) + ' / ' +
          fmt(row.revenue_median_cad_m3, 2) + ' / ' + fmt(row.revenue_max_cad_m3, 2) + '</span>';
      return '<tr>' +
        '<td>' + esc(row.jurisdiction) + '</td>' +
        '<td><span class="badge badge--econ-tier">' + esc(row.evidence_tier) + '</span></td>' +
        '<td>' + esc(row.revenue_basis) + '<span class="economics-evidence-measure">' +
          esc(row.revenue_measure) + '</span></td>' +
        '<td class="num">' + revenue + '</td>' +
        '<td><span class="badge badge--econ-status">' + esc(row.net_value_status) + '</span>' +
          '<span class="badge badge--econ-policy">policy_grade=' + (row.policy_grade ? 'true' : 'false') + '</span></td>' +
        '<td>' + esc(row.cost_coverage) + '</td></tr>';
    }).join('');
    $('economics-evidence').innerHTML =
      '<div class="economics-tier-strip">' + badges + '</div>' +
      '<details class="fold-block economics-evidence-fold">' +
        '<summary>' + fmt(econ.evidence_rows.length, 0) + ' evidence rows · all not_estimated · policy_grade=false</summary>' +
        '<div><div class="table-wrap"><table class="data-table data-table--compact economics-evidence-table">' +
          '<caption>Separated evidence register; revenue is never combined with the QC procurement cost or a modeled margin.</caption>' +
          '<thead><tr><th>Jurisdiction</th><th>Evidence tier</th><th>Revenue basis</th><th class="num">Min / median / max (CAD/m³)</th><th>Net value</th><th>Cost coverage</th></tr></thead>' +
          '<tbody>' + rows + '</tbody></table></div></div>' +
      '</details>';
  }

  function renderEconomicsCaveat(econ) {
    var caveats = econ.caveats || [];
    var visible = caveats.slice(0, 6);
    var rest = caveats.slice(6);
    $('economics-caveat').innerHTML =
      '<strong>Published averages + proxy cost model · not observed · not policy · no BC+QC total</strong>' +
      '<p>Corrected reported sensitivity: published mill-delivered price averages plus a proxy logging/haul cost model and a published Interior silviculture allowance. Not observed transactions, not a mill margin, not policy grade, and never a combined BC+QC national total.</p>' +
      '<ul>' + visible.map(function (item) { return '<li>' + esc(item) + '</li>'; }).join('') + '</ul>' +
      (rest.length
        ? '<details><summary>Full caveat register (' + fmt(rest.length, 0) + ' more)</summary><ul>' +
          rest.map(function (item) { return '<li>' + esc(item) + '</li>'; }).join('') + '</ul></details>'
        : '') +
      '<p class="economics-nonclaims">Non-claims: ' + esc((econ.non_claims || []).join(' · ')) + '</p>';
  }

  function renderEconomicsDownloads(econ) {
    var detail = econ.downloads.detail_csv;
    var bundle = econ.downloads.model_inputs_zip;
    var manifest = econ.downloads.manifest;
    $('economics-downloads').innerHTML =
      '<a class="economics-download" href="' + esc(DATA_ROOT + detail.path) + '" download>' +
        '<span class="economics-download-label">' + esc(detail.label) + '</span>' +
        '<span class="economics-download-meta">' + fmt(detail.rows, 0) + ' rows · ' +
          fmtBytes(detail.bytes) + '</span>' +
        '<span class="economics-download-hash mono">sha256 ' + esc(String(detail.sha256).slice(0, 16)) + '…</span>' +
      '</a>' +
      '<a class="economics-download" href="' + esc(DATA_ROOT + bundle.path) + '" download>' +
        '<span class="economics-download-label">' + esc(bundle.label) + '</span>' +
        '<span class="economics-download-meta">' + fmt(bundle.files.length, 0) + ' files · ' +
          fmtBytes(bundle.bytes) + '</span>' +
        '<span class="economics-download-hash mono">sha256 ' + esc(String(bundle.sha256).slice(0, 16)) + '…</span>' +
      '</a>' +
      '<a class="economics-download" href="' + esc(DATA_ROOT + manifest.path) + '" download>' +
        '<span class="economics-download-label">' + esc(manifest.label) + '</span>' +
        '<span class="economics-download-meta">source hashes · bundle hashes · validation checks</span>' +
        '<span class="economics-download-hash mono">' + esc(manifest.media_type) + '</span>' +
      '</a>';
  }

  function renderEconomicsChart() {
    if (!state.economics) return;
    var container = $('economics-chart');
    if (!container) return;
    if (!window.echarts) {
      container.innerHTML = '<div class="empty-state" style="display:grid">ECharts failed to load. Check vendor/echarts.min.js.</div>';
      return;
    }
    var econ = state.economics;
    var zoneLabels = econ.zones.map(function (zone) { return zone.zone.replace(/ Zone$/, ''); });
    var data = [];
    var bases = [];
    econ.zones.forEach(function (zone, zoneIndex) {
      zone.systems.forEach(function (group, systemIndex) {
        data.push({ value: [systemIndex, zoneIndex, group.base_cad_m3], group: group, zone: zone.zone });
        bases.push(group.base_cad_m3);
      });
    });
    var minBase = Math.min.apply(null, bases);
    var maxBase = Math.max.apply(null, bases);
    if (!state.economicsChart) {
      state.economicsChart = window.echarts.init(container, null, { renderer: 'canvas' });
    }
    state.economicsChart.setOption({
      animationDuration: 520,
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      grid: { left: 158, right: 26, top: 18, bottom: 82, containLabel: false },
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: '#0c1f19',
        borderColor: '#d98a2b',
        borderWidth: 1,
        textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
        formatter: function (params) {
          var group = params.data.group;
          return '<strong>' + esc(params.data.zone) + ' · ' + esc(group.system) + '</strong><br>' +
            'Base median: ' + fmt(group.base_cad_m3, 2) + ' CAD/m³<br>' +
            'Low / high: ' + fmt(group.low_cad_m3, 2) + ' / ' + fmt(group.high_cad_m3, 2) + '<br>' +
            'Negative rows: ' + fmt(group.negative_rows, 0) + ' / ' + fmt(group.n_rows, 0);
        }
      },
      xAxis: {
        type: 'category',
        data: ECONOMICS_SYSTEMS,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 10, color: '#6c786c' },
        splitArea: { show: false }
      },
      yAxis: {
        type: 'category',
        data: zoneLabels,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisLabel: { fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 10, color: '#435347' },
        splitArea: { show: false }
      },
      visualMap: {
        min: minBase,
        max: maxBase,
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 8,
        text: ['high', 'low'],
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#6c786c' },
        inRange: { color: ['#7e3823', '#a94f31', '#e9dcc3', '#fbf6e9', '#93a894', '#2c5746', '#17382c'] }
      },
      series: [{
        name: 'Base median',
        type: 'heatmap',
        data: data,
        label: {
          show: true,
          fontFamily: 'IBM Plex Mono, monospace',
          fontSize: 9,
          color: '#17251d',
          formatter: function (params) { return fmt(params.value[2], 2); }
        },
        itemStyle: { borderColor: '#f3ead7', borderWidth: 2 },
        emphasis: { itemStyle: { borderColor: '#d98a2b', borderWidth: 2 } }
      }]
    }, true);
  }

  function renderEconomics() {
    if (!state.economics) return;
    var econ = state.economics;
    var h = econ.headline;
    $('economics-badges').innerHTML = economicsBadges(econ);
    var summarySource = econ.sources.filter(function (source) {
      return /bc_open_net_value_summary\.csv$/.test(source.path);
    })[0];
    var detailSource = econ.sources.filter(function (source) {
      return /bc_open_net_value\.csv$/.test(source.path) && !/summary/.test(source.path);
    })[0];
    $('economics-source-note').innerHTML = 'Display medians: <span class="mono">' +
      esc(summarySource ? summarySource.path : 'bc_open_net_value_summary.csv') + '</span> · full detail/download: <span class="mono">' +
      esc(detailSource ? detailSource.path : 'bc_open_net_value.csv') + '</span> · ' + fmt(h.rows, 0) + ' rows.';
    $('economics-generated').textContent = 'Generated ' + fmtDate(econ.generated_utc) + ' · ' + econ.zones.length +
      ' zones · ' + fmt(h.zone_system_groups, 0) + ' groups · ' + fmt(econ.downloads.model_inputs_zip.files.length, 0) +
      ' model-input files · schema ' + econ.schema_version;
    $('economics-headline').innerHTML =
      metricCard('Median modeled margin', fmt(h.median_base_net_model_cad_m3, 2), 'CAD/m³',
        fmt(h.rows, 0) + ' rows · ' + fmt(h.zone_system_groups, 0) + ' zone × system groups', true) +
      metricCard('Base range', fmt(h.min_base_net_model_cad_m3, 2) + ' to ' + fmt(h.max_base_net_model_cad_m3, 2),
        'CAD/m³', 'lowest and highest modeled base row', false) +
      metricCard('Negative rows', fmt(h.negative_net_base_rows, 0) + ' / ' + fmt(h.rows, 0),
        'rows', fmtPct(h.negative_net_base_share, 1) + ' of modeled base rows', false) +
      metricCard('Evidence tier', econ.labels.evidence, 'not observed',
        'source: ' + econ.labels.source_licensing + ' · policy_grade=false', false);
    renderEconomicsRange(econ);
    renderEconomicsQc(econ);
    renderEconomicsTable(econ);
    renderEconomicsEvidence(econ);
    renderEconomicsCaveat(econ);
    renderEconomicsDownloads(econ);
    $('economics-loading').hidden = true;
    $('economics-content').hidden = false;
    if (state.tab === 'economics') renderEconomicsChart();
  }

  /* ---------------- National economics ---------------- */

  function validateNational(national, manifest) {
    requireTrue(national && national.schema_version === NATIONAL_SCHEMA, 'national economics schema mismatch');
    requireTrue(national.model_id === 'national_economics_v2_1', 'national economics model id mismatch');
    var labels = national.labels || {};
    requireTrue(labels.evidence === 'model_estimate' && labels.observed === false &&
      labels.policy_grade === false, 'national evidence labels mismatch');
    requireTrue(labels.combined_national_net_value === false,
      'national economics must not claim a combined national net value');
    requireTrue(labels.forest_lp_input === false && labels.public_osecan_input === false,
      'national economics must stay outside the Forest LP and public OSeCAN');
    requireTrue(labels.national_diagnostic_label === 'model_estimate_not_policy',
      'national diagnostic label mismatch');

    var decision = national.decision || {};
    requireTrue(decision.base_scenario === 'donor_flat' && decision.sensitivity_scenario === 'stumpage_scaled',
      'national base/sensitivity decision mismatch');
    requireTrue(decision.stumpage_scaled_preferred === false &&
      decision.stumpage_scaled_observed === false &&
      decision.stumpage_scaled_policy_grade === false,
      'national sensitivity must not be preferred, observed or policy grade');

    requireTrue(Array.isArray(national.scenarios) && national.scenarios.length === 2,
      'national scenario list is incomplete');
    var scenarioIds = national.scenarios.map(function (scenario) { return scenario.id; });
    requireTrue(scenarioIds.indexOf('donor_flat') !== -1 && scenarioIds.indexOf('stumpage_scaled') !== -1,
      'national scenario list is missing base or sensitivity');
    requireTrue(national.scenarios.filter(function (scenario) { return scenario.is_base === true; }).length === 1,
      'national scenario list must declare exactly one base');

    var diagnostic = national.national_diagnostic || {};
    var expectedNational = { donor_flat: 33.1365, stumpage_scaled: -16.1220 };
    ['donor_flat', 'stumpage_scaled'].forEach(function (scenario) {
      var values = diagnostic[scenario];
      requireTrue(values && Number.isFinite(values.low_cad_m3) &&
        Number.isFinite(values.base_cad_m3) && Number.isFinite(values.high_cad_m3),
        'national diagnostic is incomplete for ' + scenario);
      requireTrue(values.low_cad_m3 <= values.base_cad_m3 && values.base_cad_m3 <= values.high_cad_m3,
        'national diagnostic is unordered for ' + scenario);
      requireTrue(Math.abs(values.base_cad_m3 - expectedNational[scenario]) < 1e-6,
        'stale national diagnostic for ' + scenario);
    });

    requireTrue(Array.isArray(national.jurisdictions) && national.jurisdictions.length === 13,
      'national jurisdiction count mismatch');
    var provinces = new Set();
    national.jurisdictions.forEach(function (row) {
      requireTrue(row && typeof row.province === 'string' && !provinces.has(row.province),
        'national jurisdiction is duplicated or missing');
      provinces.add(row.province);
      ['donor_flat', 'stumpage_scaled'].forEach(function (scenario) {
        var values = row[scenario];
        requireTrue(values && Number.isFinite(values.low_cad_m3) &&
          Number.isFinite(values.base_cad_m3) && Number.isFinite(values.high_cad_m3),
          'national jurisdiction ' + row.province + ' is missing ' + scenario);
        requireTrue(values.low_cad_m3 <= values.base_cad_m3 && values.base_cad_m3 <= values.high_cad_m3,
          'national jurisdiction ' + row.province + ' is unordered for ' + scenario);
        requireTrue(typeof values.price_tier === 'string' && typeof values.cost_tier === 'string',
          'national jurisdiction ' + row.province + ' is missing tier labels');
      });
      requireTrue(Number.isFinite(row.scaled_minus_donor_flat_cad_m3),
        'national jurisdiction ' + row.province + ' is missing its delta');
    });

    var tiers = national.tier_coverage || {};
    requireTrue(tiers.cell_count === 65 && tiers.province_count === 13 && tiers.component_count === 5,
      'national tier coverage cell count mismatch');
    var shares = tiers.volume_weighted_shares || {};
    requireTrue(Math.abs((shares.reported_average || 0) - 0.17924672) < 1e-6,
      'stale reported-average coverage share');
    requireTrue(Math.abs((shares.proxy_model || 0) - 0.13440737) < 1e-6,
      'stale proxy-model coverage share');
    requireTrue(Math.abs((shares.donor_proxy || 0) - 0.68634592) < 1e-6,
      'stale donor-proxy coverage share');

    requireTrue(manifest && manifest.schema_version === NATIONAL_MANIFEST_SCHEMA,
      'national manifest schema mismatch');
    requireTrue(Array.isArray(manifest.generated) && manifest.generated.length === 1 &&
      manifest.generated[0].path === 'economics/national.json' &&
      typeof manifest.generated[0].sha256 === 'string' &&
      manifest.generated[0].sha256.length === 64,
      'national manifest payload hash is missing');
    requireTrue(Array.isArray(manifest.sources) && manifest.sources.length >= 5,
      'national manifest source hashes are missing');
  }

  function nationalScenarioById(id) {
    if (!state.national) return null;
    var found = null;
    state.national.scenarios.forEach(function (scenario) {
      if (scenario.id === id) found = scenario;
    });
    return found;
  }

  function nationalSelectedScenario() {
    var selected = nationalScenarioById(state.nationalScenario);
    if (selected) return selected;
    return nationalScenarioById('donor_flat');
  }

  function nationalTierMeta(id) {
    var meta = { id: id, label: String(id).replace(/_/g, ' '), color: '#9d8b6c' };
    NATIONAL_TIERS.forEach(function (tier) {
      if (tier.id === id) meta = tier;
    });
    return meta;
  }

  function nationalTierBadge(id, suffix) {
    var meta = nationalTierMeta(id);
    return '<span class="badge badge--national-tier badge--national-' +
      esc(String(id).replace(/_/g, '-')) + '">' + esc(meta.label) +
      (suffix ? ' · ' + esc(suffix) : '') + '</span>';
  }

  function nationalSigned(value, digits) {
    var text = fmt(value, digits);
    return value > 0 ? '+' + text : text;
  }

  function nationalScale() {
    var values = [0];
    var national = state.national;
    ['donor_flat', 'stumpage_scaled'].forEach(function (scenario) {
      var diagnostic = national.national_diagnostic[scenario];
      values.push(diagnostic.low_cad_m3, diagnostic.base_cad_m3, diagnostic.high_cad_m3);
    });
    national.jurisdictions.forEach(function (row) {
      ['donor_flat', 'stumpage_scaled'].forEach(function (scenario) {
        values.push(row[scenario].low_cad_m3, row[scenario].base_cad_m3, row[scenario].high_cad_m3);
      });
    });
    var low = Math.min.apply(null, values);
    var high = Math.max.apply(null, values);
    var padding = Math.max(5, (high - low) * 0.04);
    return { min: Math.floor(low - padding), max: Math.ceil(high + padding) };
  }

  function nationalPct(value, scale) {
    return ((value - scale.min) / (scale.max - scale.min)) * 100;
  }

  function nationalRangeBar(diagnostic, scale) {
    var low = nationalPct(diagnostic.low_cad_m3, scale);
    var high = nationalPct(diagnostic.high_cad_m3, scale);
    var base = nationalPct(diagnostic.base_cad_m3, scale);
    var zero = nationalPct(0, scale);
    var negativeWidth = Math.max(0, Math.min(high, zero) - low);
    return '<div class="economics-range-track national-range-track" aria-hidden="true">' +
        '<span class="economics-range-band" style="left:' + low + '%;width:' +
          Math.max(0, high - low) + '%"></span>' +
        '<span class="economics-range-negative" style="left:' + low + '%;width:' +
          negativeWidth + '%"></span>' +
        '<span class="economics-range-zero" style="left:' + zero + '%"></span>' +
        '<span class="economics-range-median" style="left:' + base + '%"></span>' +
      '</div>' +
      '<div class="economics-range-labels">' +
        '<span>' + fmt(diagnostic.low_cad_m3, 4) + '</span>' +
        '<span>base <strong>' + nationalSigned(diagnostic.base_cad_m3, 4) + '</strong></span>' +
        '<span>' + fmt(diagnostic.high_cad_m3, 4) + '</span>' +
      '</div>';
  }

  function nationalDiagnosticCard(scenarioId) {
    var national = state.national;
    var scenario = nationalScenarioById(scenarioId);
    var diagnostic = national.national_diagnostic[scenarioId];
    var selected = state.nationalScenario === scenarioId;
    var isSensitivity = scenarioId === national.decision.sensitivity_scenario;
    return '<article class="national-diagnostic-card' + (selected ? ' is-selected' : '') +
        (isSensitivity ? ' is-sensitivity' : ' is-base') + '">' +
      '<div class="national-diagnostic-head">' +
        '<span class="kicker">' + esc(scenario.label) + '</span>' +
        '<span class="badge badge--econ-readonly">volume-weighted</span>' +
      '</div>' +
      '<div class="national-diagnostic-value' + (diagnostic.base_cad_m3 < 0 ? ' is-negative' : '') + '">' +
        nationalSigned(diagnostic.base_cad_m3, 4) + '</div>' +
      '<span class="national-diagnostic-unit">CAD/m³ central model diagnostic</span>' +
      nationalRangeBar(diagnostic, nationalScale()) +
      '<dl class="national-diagnostic-stats">' +
        '<div><dt>Low</dt><dd>' + fmt(diagnostic.low_cad_m3, 4) + '</dd></div>' +
        '<div><dt>Base</dt><dd>' + fmt(diagnostic.base_cad_m3, 4) + '</dd></div>' +
        '<div><dt>High</dt><dd>' + fmt(diagnostic.high_cad_m3, 4) + '</dd></div>' +
      '</dl>' +
      '<p class="method-note">' +
        (isSensitivity
          ? 'Method sensitivity · not preferred · not observed · not policy grade.'
          : 'Base scenario · the consistent donor-flat case.') +
      '</p>' +
    '</article>';
  }

  function renderNationalBadges() {
    $('national-badges').innerHTML =
      '<span class="badge badge--econ-public">national_diagnostic</span>' +
      '<span class="badge badge--econ-model">model_estimate</span>' +
      '<span class="badge badge--econ-policy">observed=false</span>' +
      '<span class="badge badge--econ-policy">policy_grade=false</span>' +
      '<span class="badge badge--econ-readonly">no combined national net value</span>' +
      '<span class="badge badge--econ-readonly">not Forest LP/OSeCAN input</span>' +
      '<span class="badge badge--econ-readonly">read-only</span>';
  }

  function renderNationalDiagnostics() {
    var allocation = state.national.national_diagnostic.allocation_volume_m3;
    $('national-diagnostics').innerHTML =
      nationalDiagnosticCard('donor_flat') + nationalDiagnosticCard('stumpage_scaled') +
      '<p class="national-diagnostic-basis method-note">Allocation basis: ' +
        fmt(allocation / 1e6, 1) + ' Mm³ · 13 jurisdictions · volume-weighted model diagnostic. ' +
        'Not a legal AAC and not a combined national net value.</p>';
  }

  function renderNationalTierCoverage() {
    var national = state.national;
    var coverage = national.tier_coverage;
    var shares = coverage.volume_weighted_shares || {};
    var cellCounts = coverage.cell_counts || {};
    var selected = nationalSelectedScenario();
    var stack = NATIONAL_TIERS.map(function (tier) {
      var share = shares[tier.id] || 0;
      if (share <= 0) return '';
      return '<span class="national-tier-segment" style="width:' + (share * 100) +
        '%;background:' + tier.color + '" title="' + esc(tier.label + ' ' + fmtPct(share, 1)) + '"></span>';
    }).join('');
    var rows = NATIONAL_TIERS.map(function (tier) {
      var share = shares[tier.id] || 0;
      var count = cellCounts[tier.id] || 0;
      return '<li class="national-tier-row">' +
        '<span class="national-tier-dot" style="background:' + tier.color + '"></span>' +
        '<span class="national-tier-name">' + esc(tier.label) + '</span>' +
        '<strong>' + fmtPct(share, 1) + '</strong>' +
        '<span class="mono">' + fmt(count, 0) + ' / ' + fmt(coverage.cell_count, 0) + ' cells</span>' +
      '</li>';
    }).join('');
    var priceCounts = (coverage.scenario_price_tiers || {})[selected.id] || {};
    var priceBadges = Object.keys(priceCounts).sort().map(function (tier) {
      return nationalTierBadge(tier, fmt(priceCounts[tier], 0) + ' jurisdictions');
    }).join('');
    var costCounts = (coverage.scenario_cost_tiers || {})[selected.id] || {};
    var costBadges = Object.keys(costCounts).sort().map(function (tier) {
      return nationalTierBadge(tier, fmt(costCounts[tier], 0));
    }).join('');
    $('national-tier-coverage').innerHTML =
      '<p class="kicker">Evidence tiers · volume-weighted</p>' +
      '<h3>Coverage shares</h3>' +
      '<div class="national-tier-stack" role="img" aria-label="Volume-weighted tier coverage: reported average ' +
        fmtPct(shares.reported_average || 0, 1) + ', proxy model ' +
        fmtPct(shares.proxy_model || 0, 1) + ', donor proxy ' +
        fmtPct(shares.donor_proxy || 0, 1) + '">' + stack + '</div>' +
      '<ul class="national-tier-rows">' + rows + '</ul>' +
      '<p class="method-note">Five components per jurisdiction (price, logging, haul, road, silviculture), ' +
        'equally weighted. Shares describe evidence coverage, not margin confidence.</p>' +
      '<h4>Selected scenario · price tiers</h4>' +
      '<div class="national-tier-badges">' + priceBadges + '</div>' +
      '<details class="national-cost-details"><summary>Cost tiers · ' + esc(selected.label) + '</summary>' +
        '<div class="national-tier-badges">' + costBadges + '</div></details>';
  }

  function nationalValueCell(value, extraClass) {
    return '<td class="num' + (extraClass ? ' ' + extraClass : '') +
      (value < 0 ? ' is-negative-value' : '') + '">' + fmt(value, 2) + '</td>';
  }

  function renderNationalTable() {
    var national = state.national;
    var selected = state.nationalScenario;
    var baseSelected = selected === 'donor_flat';
    var sensitivitySelected = selected === 'stumpage_scaled';
    var header =
      '<caption>13 jurisdictions · model_estimate · observed=false · policy_grade=false · no combined national net value · ' +
        'low/base/high CAD/m³ · base=donor_flat · sensitivity=stumpage_scaled</caption>' +
      '<thead>' +
        '<tr>' +
          '<th rowspan="2">Jurisdiction</th>' +
          '<th rowspan="2" class="num">Volume (Mm³)</th>' +
          '<th rowspan="2" class="num">Share</th>' +
          '<th colspan="4" class="national-group-base' + (baseSelected ? ' is-selected-scenario' : '') + '">Base · donor_flat</th>' +
          '<th colspan="4" class="national-group-sensitivity' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">Sensitivity · stumpage_scaled</th>' +
          '<th rowspan="2" class="num">Δ base (CAD/m³)</th>' +
        '</tr>' +
        '<tr>' +
          '<th class="num national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '') + '">Low</th>' +
          '<th class="num national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '') + '">Base</th>' +
          '<th class="num national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '') + '">High</th>' +
          '<th class="national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '') + '">Price tier</th>' +
          '<th class="num national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">Low</th>' +
          '<th class="num national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">Base</th>' +
          '<th class="num national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">High</th>' +
          '<th class="national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">Price tier</th>' +
        '</tr>' +
      '</thead>';
    var rows = national.jurisdictions.map(function (row) {
      return '<tr>' +
        '<td class="national-jurisdiction-cell"><strong>' + esc(row.province) + '</strong><span>' +
          esc(row.province_name) + '</span></td>' +
        '<td class="num">' + fmt(row.allocation_volume_m3 / 1e6, 2) + '</td>' +
        '<td class="num mono">' + fmtPct(row.volume_share, 1) + '</td>' +
        nationalValueCell(row.donor_flat.low_cad_m3, 'national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '')) +
        nationalValueCell(row.donor_flat.base_cad_m3, 'national-scenario-col is-base-value' + (baseSelected ? ' is-selected-scenario' : '')) +
        nationalValueCell(row.donor_flat.high_cad_m3, 'national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '')) +
        '<td class="national-scenario-col' + (baseSelected ? ' is-selected-scenario' : '') + '">' +
          nationalTierBadge(row.donor_flat.price_tier) + '</td>' +
        nationalValueCell(row.stumpage_scaled.low_cad_m3, 'national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '')) +
        nationalValueCell(row.stumpage_scaled.base_cad_m3, 'national-scenario-col is-sensitivity-value' + (sensitivitySelected ? ' is-selected-scenario' : '')) +
        nationalValueCell(row.stumpage_scaled.high_cad_m3, 'national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '')) +
        '<td class="national-scenario-col' + (sensitivitySelected ? ' is-selected-scenario' : '') + '">' +
          nationalTierBadge(row.stumpage_scaled.price_tier) + '</td>' +
        '<td class="num' + (row.scaled_minus_donor_flat_cad_m3 < 0 ? ' is-negative-value' : '') + '">' +
          nationalSigned(row.scaled_minus_donor_flat_cad_m3, 2) + '</td>' +
      '</tr>';
    }).join('');
    $('national-table').innerHTML = header + '<tbody>' + rows + '</tbody>';
  }

  function renderNationalCaveat() {
    var national = state.national;
    var caveats = national.caveats || [];
    $('national-caveat').innerHTML =
      '<strong>National diagnostic · model_estimate · observed=false · policy_grade=false · no combined national net value</strong>' +
      '<p>13 jurisdictions, volume-weighted. Not a Forest LP input. Not a public OSeCAN input. ' +
        'The sensitivity is not preferred, not observed and not policy grade. ' +
        'Low/base/high are model scenarios, not observed transactions.</p>' +
      '<ul>' + caveats.map(function (item) { return '<li>' + esc(item) + '</li>'; }).join('') + '</ul>' +
      '<p class="economics-nonclaims">Non-claims: ' + esc((national.non_claims || []).join(' · ')) + '</p>';
  }

  function renderNationalDownloads() {
    var national = state.national;
    var manifest = state.nationalManifest;
    var payload = manifest.generated[0];
    var json = national.download.json;
    var manifestRef = national.download.manifest;
    $('national-downloads').innerHTML =
      '<a class="economics-download" href="' + esc(DATA_ROOT + json.path) + '" download>' +
        '<span class="economics-download-label">' + esc(json.label) + '</span>' +
        '<span class="economics-download-meta">' + fmt(payload.bytes, 0) + ' bytes · static JSON · ' +
          fmt(national.coverage.jurisdiction_rows, 0) + ' jurisdictions</span>' +
        '<span class="economics-download-hash mono">sha256 ' + esc(String(payload.sha256).slice(0, 16)) + '…</span>' +
      '</a>' +
      '<a class="economics-download" href="' + esc(DATA_ROOT + manifestRef.path) + '" download>' +
        '<span class="economics-download-label">' + esc(manifestRef.label) + '</span>' +
        '<span class="economics-download-meta">payload hash · ' + fmt(manifest.sources.length, 0) + ' source hashes · validation</span>' +
        '<span class="economics-download-hash mono">' + esc(manifest.schema_version) + '</span>' +
      '</a>';
  }

  function renderNationalChart() {
    if (!state.national) return;
    var container = $('national-chart');
    if (!container) return;
    if (!window.echarts) {
      container.innerHTML = '<div class="empty-state" style="display:grid">ECharts failed to load. Check vendor/echarts.min.js.</div>';
      return;
    }
    var national = state.national;
    var selected = nationalSelectedScenario();
    var selectedId = selected.id;
    var baseData = [];
    var sensitivityData = [];
    national.jurisdictions.forEach(function (row) {
      baseData.push({ value: row.donor_flat.base_cad_m3, row: row, scenarioId: 'donor_flat' });
      sensitivityData.push({ value: row.stumpage_scaled.base_cad_m3, row: row, scenarioId: 'stumpage_scaled' });
    });
    if (!state.nationalChart) {
      state.nationalChart = window.echarts.init(container, null, { renderer: 'canvas' });
    }
    state.nationalChart.setOption({
      animationDuration: 520,
      textStyle: { fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', color: '#435347' },
      grid: { left: 96, right: 60, top: 44, bottom: 38, containLabel: false },
      legend: {
        top: 2,
        right: 8,
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#435347' },
        data: ['Base · donor_flat', 'Sensitivity · stumpage_scaled']
      },
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: '#0c1f19',
        borderColor: '#d98a2b',
        borderWidth: 1,
        textStyle: { color: '#f3ead7', fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12 },
        formatter: function (params) {
          var row = params.data && params.data.row;
          if (!row) return '';
          var values = params.seriesId === 'base' ? row.donor_flat : row.stumpage_scaled;
          return '<strong>' + esc(row.province_name) + '</strong><br>' +
            esc(params.seriesName) + ': ' + nationalSigned(values.base_cad_m3, 2) + ' CAD/m³<br>' +
            'Low / high: ' + fmt(values.low_cad_m3, 2) + ' / ' + fmt(values.high_cad_m3, 2) + '<br>' +
            'Price tier: ' + esc(nationalTierMeta(values.price_tier).label) + '<br>' +
            'Volume: ' + fmt(row.allocation_volume_m3 / 1e6, 2) + ' Mm³';
        }
      },
      xAxis: {
        type: 'value',
        name: 'CAD/m³',
        nameTextStyle: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#6c786c' },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#6c786c' },
        splitLine: { lineStyle: { color: 'rgba(184,165,126,0.3)', type: 'dashed' } }
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: national.jurisdictions.map(function (row) { return row.province; }),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#b8a57e' } },
        axisLabel: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, color: '#435347' }
      },
      series: [
        {
          id: 'base',
          name: 'Base · donor_flat',
          type: 'bar',
          data: baseData,
          barWidth: 7,
          barGap: '25%',
          itemStyle: { color: selectedId === 'donor_flat' ? '#2c5746' : 'rgba(44,87,70,0.25)' },
          emphasis: { itemStyle: { color: '#2c5746' } },
          label: {
            show: selectedId === 'donor_flat',
            position: 'right',
            fontFamily: 'IBM Plex Mono, monospace',
            fontSize: 8,
            color: '#17382c',
            formatter: function (params) { return fmt(params.value, 1); }
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#0c1f19', width: 1.2 },
            label: { show: false },
            data: [{ xAxis: 0 }]
          }
        },
        {
          id: 'sensitivity',
          name: 'Sensitivity · stumpage_scaled',
          type: 'bar',
          data: sensitivityData,
          barWidth: 7,
          itemStyle: { color: selectedId === 'stumpage_scaled' ? '#a94f31' : 'rgba(169,79,49,0.22)' },
          emphasis: { itemStyle: { color: '#a94f31' } },
          label: {
            show: selectedId === 'stumpage_scaled',
            position: 'right',
            fontFamily: 'IBM Plex Mono, monospace',
            fontSize: 8,
            color: '#7e3823',
            formatter: function (params) { return fmt(params.value, 1); }
          }
        }
      ]
    }, true);
    $('national-chart-note').textContent = 'Selected: ' + selected.label +
      '. Central modeled margin by jurisdiction, CAD/m³. The selected series is opaque; the other is ghosted. Negative values are not clipped.';
  }

  function renderNational() {
    if (!state.national) return;
    var national = state.national;
    var select = $('national-scenario');
    if (select && select.value !== state.nationalScenario) select.value = state.nationalScenario;
    renderNationalBadges();
    var sources = national.source_files || [];
    var summarySource = '';
    var tierSource = '';
    sources.forEach(function (source) {
      if (/national_margin_summary\.csv$/.test(source.path)) summarySource = source.path;
      if (/national_tier_coverage\.csv$/.test(source.path)) tierSource = source.path;
    });
    $('national-source-note').innerHTML = 'Display: <span class="mono">' +
      esc(summarySource || 'national_economics_v2_1/national_margin_summary.csv') +
      '</span> · comparisons: <span class="mono">scenario_comparison.csv</span> · tier shares: <span class="mono">' +
      esc(tierSource || 'national_tier_coverage.csv') + '</span>.';
    $('national-generated').textContent = 'National v' + (national.artifact_version || '2.1.0') +
      ' · 13 jurisdictions · base donor_flat · sensitivity stumpage_scaled · generated ' +
      fmtDate(national.generated_utc) + ' · schema ' + national.schema_version;
    renderNationalDiagnostics();
    renderNationalTierCoverage();
    renderNationalTable();
    renderNationalCaveat();
    renderNationalDownloads();
    if (state.tab === 'economics') renderNationalChart();
  }

  /* ---------------- Inputs ---------------- */

  function renderInputsStatic() {
    if (!state.core) return;
    var core = state.core;
    var supply = core.supply;
    var harvest = core.harvest;
    var landbase = core.landbase;

    $('inputs-summary').innerHTML =
      metricCard('Scenarios / outputs', core.scenarios.scenarios.length + ' / ' + core.manifest.output_count, 'records', core.scenarios.scenarios.length + ' scenario contracts · ' + core.manifest.output_count + ' columnar outputs', true) +
      metricCard('Harvest template', fmt(harvest.template.rows, 0), 'rows', harvest.template.provinces.length + ' provinces · 50 years', false) +
      metricCard('Supply tier sum', fmt(supply.tier_sum_pj, 2), 'PJ fuel', 'terminal upper bound · reconciles', false) +
      metricCard('Operable proxy', fmt(core.operable.national.operable_proxy_ha / 1e6, 1), 'Mha', 'screening landbase — not legal THLB', false);

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

    var operable = core.operable;
    $('landbase-status').textContent = operable.status + ' · base thresholds: h ≥ ' + fmt(operable.thresholds.cases.base.height_m, 0) +
      ' m, biomass ≥ ' + fmt(operable.thresholds.cases.base.biomass_mg_ha, 0) + ' Mg/ha, closure ≥ ' +
      fmt(operable.thresholds.cases.base.closure_pct, 0) + '% · ' + operable.by_province.length +
      ' provinces · missing ' + operable.missing_provinces.join(', ');
    $('landbase-caveats').innerHTML = operable.caveats.map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('');

    var maxOperable = Math.max.apply(null, operable.by_province.map(function (row) { return row.operable_proxy_ha || 0; }));
    $('managed-table').innerHTML = '<div class="table-wrap"><table class="data-table">' +
      '<caption>Operable-proxy screening landbase by province · base case · not a legal THLB</caption>' +
      '<thead><tr><th>Province</th><th class="num">Treed (Mha)</th><th class="num">Merchantable (Mha)</th><th class="num">Managed comparison (Mha)</th><th class="num">Operable proxy (Mha)</th><th class="num">Operable share</th><th>Relative operable</th></tr></thead><tbody>' +
      operable.by_province.map(function (row) {
        var share = row.operable_proxy_ha == null || !row.scanfi_forest_ha ? 'missing' : fmtPct(row.operable_proxy_ha / row.scanfi_forest_ha, 1);
        var width = maxOperable > 0 && row.operable_proxy_ha ? (row.operable_proxy_ha / maxOperable) * 100 : 0;
        var operableText = row.operable_proxy_ha == null ? '<span class="badge badge--ink">missing</span>' : fmt(row.operable_proxy_ha / 1e6, 2);
        return '<tr><td>' + esc(row.province) + '</td>' +
          '<td class="num">' + fmt(row.scanfi_forest_ha / 1e6, 2) + '</td>' +
          '<td class="num">' + fmt(row.merchantable_ha / 1e6, 2) + '</td>' +
          '<td class="num">' + (row.managed_proxy_ha == null ? '—' : fmt(row.managed_proxy_ha / 1e6, 2)) + '</td>' +
          '<td class="num">' + operableText + '</td>' +
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
        loadJSON('inputs/managed_landbase.json'),
        loadJSON('inputs/operable_proxy.json'),
        loadJSON('spatial/zone_nodes.json'),
        loadJSON('economics/summary.json'),
        loadJSON('economics/national.json'),
        loadJSON('economics/national.manifest.json')
      ]);
      buildCoreState({
        manifest: bundle[0],
        scenarios: bundle[1],
        links: bundle[2],
        harvest: bundle[3],
        supply: bundle[4],
        landbase: bundle[5],
        operable: bundle[6],
        zoneNodes: bundle[7],
        economics: bundle[8],
        national: bundle[9],
        nationalManifest: bundle[10]
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
    if (state.economicsChart) state.economicsChart.resize();
    if (state.nationalChart) state.nationalChart.resize();
    if (state.mapState.map) state.mapState.map.resize();
    if (state.tab === 'explore') {
      if (state.paretoChart) state.paretoChart.resize();
      if (state.compareChart) state.compareChart.resize();
      window.clearTimeout(state.explore.resizeTimer);
      state.explore.resizeTimer = window.setTimeout(function () {
        if (state.tab === 'explore') renderParallelCoordinates();
      }, 140);
    }
  });

  init();
})();
