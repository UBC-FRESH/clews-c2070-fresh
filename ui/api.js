/* Forest Ledger — live API client.
   Vanilla ES2019, no build step. The static app must keep working when every
   call here fails; callers decide how to degrade. */

(function () {
  'use strict';

  var BASE = '';
  var EVENT_TYPES = ['started', 'log', 'completed', 'failed', 'interrupted', 'not_implemented'];
  var PROBE_TIMEOUT_MS = 1800;

  function ApiError(message, status, detail) {
    this.name = 'ApiError';
    this.message = message;
    this.status = status || 0;
    this.detail = detail || message;
    if (Error.captureStackTrace) Error.captureStackTrace(this, ApiError);
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  async function errorFromResponse(response) {
    var detail = 'HTTP ' + response.status;
    try {
      var payload = await response.json();
      if (payload && typeof payload.detail === 'string') detail = payload.detail;
      else if (payload && payload.detail) detail = JSON.stringify(payload.detail);
    } catch (error) { /* non-JSON error body */ }
    return new ApiError(detail, response.status, detail);
  }

  async function request(path, options) {
    var response;
    try {
      response = await fetch(BASE + path, Object.assign({
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      }, options || {}));
    } catch (error) {
      throw new ApiError('network unavailable: ' + error.message, 0, error.message);
    }
    if (!response.ok) throw await errorFromResponse(response);
    if (response.status === 204) return null;
    return response.json();
  }

  async function probe() {
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, PROBE_TIMEOUT_MS);
    try {
      var response = await fetch(BASE + '/api/health', {
        cache: 'no-store',
        headers: { 'Accept': 'application/json' },
        signal: controller.signal
      });
      if (!response.ok) {
        return { available: false, reason: 'HTTP ' + response.status };
      }
      var health = await response.json();
      return { available: true, health: health };
    } catch (error) {
      return {
        available: false,
        reason: error && error.name === 'AbortError' ? 'health probe timed out' : (error && error.message ? error.message : 'health probe failed')
      };
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function getConstraints() {
    var payload = await request('/api/constraints');
    if (!payload || !Array.isArray(payload.sets) || !payload.sets.length) {
      throw new ApiError('constraints payload has no sets', 0, 'constraints payload has no sets');
    }
    return payload;
  }

  async function createScenario(payload, overwrite) {
    var query = overwrite ? '?overwrite=true' : '';
    return request('/api/scenarios' + query, {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }

  async function queueRun(scenarioId) {
    return request('/api/runs', {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_id: scenarioId })
    });
  }

  async function getRun(runId) {
    return request('/api/runs/' + encodeURIComponent(runId));
  }

  async function getRunOutput(runId) {
    return request('/api/runs/' + encodeURIComponent(runId) + '/output');
  }

  function subscribe(runId, handlers) {
    var options = handlers || {};
    var source = new EventSource(BASE + '/api/runs/' + encodeURIComponent(runId) + '/events');
    var closed = false;

    function dispatch(type, event) {
      if (closed || !event) return;
      var payload = null;
      try { payload = JSON.parse(event.data); } catch (error) { return; }
      if (options.onEvent) options.onEvent(type, payload);
    }

    EVENT_TYPES.forEach(function (type) {
      source.addEventListener(type, function (event) { dispatch(type, event); });
    });
    source.onmessage = function (event) { dispatch('message', event); };
    source.onerror = function () {
      if (closed) return;
      if (options.onError) options.onError();
    };

    return {
      close: function () {
        if (closed) return;
        closed = true;
        source.close();
      }
    };
  }

  window.ForestLedgerApi = {
    ApiError: ApiError,
    probe: probe,
    getConstraints: getConstraints,
    createScenario: createScenario,
    queueRun: queueRun,
    getRun: getRun,
    getRunOutput: getRunOutput,
    subscribe: subscribe
  };
})();
