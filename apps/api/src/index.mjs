import http from 'node:http';
import { URL } from 'node:url';
import { randomUUID } from 'node:crypto';

const PROFILE_SERVICE_URL = process.env.PROFILE_SERVICE_URL || 'http://127.0.0.1:8000';
const BID_DECISION_SERVICE_URL = process.env.BID_DECISION_SERVICE_URL || 'http://127.0.0.1:8001';
const UPSTREAM_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS || 15000);
const PROFILE_AGENT_TIMEOUT_MS = Number(process.env.PROFILE_AGENT_TIMEOUT_MS || 120000);
const BID_AGENT_TIMEOUT_MS = Number(process.env.BID_AGENT_TIMEOUT_MS || 600000);

const safeSegment = value => {
  try { return encodeURIComponent(decodeURIComponent(value)); }
  catch { return encodeURIComponent(value); }
};

const profile = (method, pattern, target, timeoutMs = UPSTREAM_TIMEOUT_MS) => ({method, pattern, target, service:'profile', envelope:'passthrough', timeoutMs});
const bid = (method, pattern, target, timeoutMs = UPSTREAM_TIMEOUT_MS) => ({method, pattern, target, service:'bid', envelope:'adapt', timeoutMs});

const mappings = [
  profile('GET', /^\/api\/health$/, () => '/internal/health'),
  profile('GET', /^\/api\/ai\/capability-provider\/status$/, () => '/internal/ai/capability-provider/status'),
  profile('GET', /^\/api\/dashboard$/, () => '/internal/dashboard'),
  profile('GET', /^\/api\/companies$/, () => '/internal/companies'),
  profile('GET', /^\/api\/companies\/([^/]+)\/profile$/, m => `/internal/companies/${safeSegment(m[1])}/profile`),
  profile('GET', /^\/api\/companies\/([^/]+)\/profile\/history$/, m => `/internal/companies/${safeSegment(m[1])}/profile/history`),
  profile('GET', /^\/api\/companies\/([^/]+)\/profile\/diff$/, m => `/internal/companies/${safeSegment(m[1])}/profile/diff`),
  profile('GET', /^\/api\/companies\/([^/]+)\/gaps$/, m => `/internal/companies/${safeSegment(m[1])}/gaps`),
  profile('GET', /^\/api\/companies\/([^/]+)\/review-tasks$/, m => `/internal/companies/${safeSegment(m[1])}/review-tasks`),
  profile('GET', /^\/api\/companies\/([^/]+)\/agent-runs\/latest$/, m => `/internal/companies/${safeSegment(m[1])}/agent-runs/latest`),
  profile('POST', /^\/api\/agent\/runs$/, () => '/internal/agent/runs', PROFILE_AGENT_TIMEOUT_MS),
  profile('GET', /^\/api\/agent\/runs\/([^/]+)$/, m => `/internal/agent/runs/${safeSegment(m[1])}`),
  profile('POST', /^\/api\/agent\/runs\/([^/]+)\/responses$/, m => `/internal/agent/runs/${safeSegment(m[1])}/responses`),
  profile('POST', /^\/api\/agent\/runs\/([^/]+)\/decision-selection$/, m => `/internal/agent/runs/${safeSegment(m[1])}/decision-selection`),
  profile('POST', /^\/api\/review-tasks\/([^/]+)\/approve$/, m => `/internal/review-tasks/${safeSegment(m[1])}/approve`),
  profile('POST', /^\/api\/review-tasks\/([^/]+)\/reject$/, m => `/internal/review-tasks/${safeSegment(m[1])}/reject`),
  profile('POST', /^\/api\/review-tasks\/([^/]+)\/needs-more-information$/, m => `/internal/review-tasks/${safeSegment(m[1])}/needs-more-information`),
  profile('POST', /^\/api\/review-tasks\/([^/]+)\/supplements$/, m => `/internal/review-tasks/${safeSegment(m[1])}/supplements`),
  profile('POST', /^\/api\/agent\/runs\/([^/]+)\/decision-reconfirmation$/, m => `/internal/agent/runs/${safeSegment(m[1])}/decision-reconfirmation`),
  profile('POST', /^\/api\/integration\/fact-candidates$/, () => '/internal/integration/fact-candidates'),
  profile('GET', /^\/api\/integration\/fact-candidates$/, () => '/internal/integration/fact-candidates'),
  profile('POST', /^\/api\/integration\/task-requirements$/, () => '/internal/integration/task-requirements'),
  profile('POST', /^\/api\/companies\/([^/]+)\/decision-preferences$/, m => `/internal/companies/${safeSegment(m[1])}/decision-preferences`),
  profile('POST', /^\/api\/companies\/([^/]+)\/profile-inputs\/performance$/, m => `/internal/companies/${safeSegment(m[1])}/profile-inputs/performance`),
  profile('POST', /^\/api\/companies\/([^/]+)\/profile-inputs\/performance-supplement$/, m => `/internal/companies/${safeSegment(m[1])}/profile-inputs/performance-supplement`),
  profile('POST', /^\/api\/companies\/([^/]+)\/profile-inputs\/personnel$/, m => `/internal/companies/${safeSegment(m[1])}/profile-inputs/personnel`),
  profile('POST', /^\/api\/companies\/([^/]+)\/profile-inputs\/general$/, m => `/internal/companies/${safeSegment(m[1])}/profile-inputs/general`),
  profile('GET', /^\/api\/integration\/companies\/([^/]+)\/bid-profile-snapshot$/, m => `/internal/integration/companies/${safeSegment(m[1])}/bid-profile-snapshot`),
  profile('POST', /^\/api\/integration\/companies\/([^/]+)\/bid-profile-snapshot$/, m => `/internal/integration/companies/${safeSegment(m[1])}/bid-profile-snapshot`),
  profile('GET', /^\/api\/integration\/companies\/([^/]+)\/bid-evaluation-snapshot$/, m => `/internal/integration/companies/${safeSegment(m[1])}/bid-evaluation-snapshot`),
  profile('POST', /^\/api\/integration\/companies\/([^/]+)\/profile-updates$/, m => `/internal/integration/companies/${safeSegment(m[1])}/profile-updates`),
  profile('GET', /^\/api\/enterprise-data\/providers$/, () => '/internal/enterprise-data/providers'),
  profile('GET', /^\/api\/evaluation\/graph$/, () => '/internal/evaluation/graph'),
  profile('GET', /^\/api\/enterprise-data\/normalizers$/, () => '/internal/enterprise-data/normalizers'),
  profile('GET', /^\/api\/evaluation\/model$/, () => '/internal/evaluation/model'),
  profile('GET', /^\/api\/evaluation\/model\/indicators$/, () => '/internal/evaluation/model/indicators'),
  profile('GET', /^\/api\/evaluation\/model\/notes$/, () => '/internal/evaluation/model/notes'),
  profile('GET', /^\/api\/evaluation\/model\/api-catalog$/, () => '/internal/evaluation/model/api-catalog'),
  profile('POST', /^\/api\/evaluation\/runs$/, () => '/internal/evaluation/runs'),
  profile('GET', /^\/api\/evaluation\/runs\/([^/]+)$/, m => `/internal/evaluation/runs/${safeSegment(m[1])}`),
  profile('POST', /^\/api\/evaluation\/runs\/([^/]+)\/resume$/, m => `/internal/evaluation/runs/${safeSegment(m[1])}/resume`),
  profile('POST', /^\/api\/evaluation\/runs\/([^/]+)\/answers$/, m => `/internal/evaluation/runs/${safeSegment(m[1])}/answers`),
  profile('GET', /^\/api\/companies\/([^/]+)\/evaluation\/latest$/, m => `/internal/companies/${safeSegment(m[1])}/evaluation/latest`),
  profile('GET', /^\/api\/companies\/([^/]+)\/evaluation\/history$/, m => `/internal/companies/${safeSegment(m[1])}/evaluation/history`),
  profile('GET', /^\/api\/companies\/([^/]+)\/evaluation-card\/latest$/, m => `/internal/companies/${safeSegment(m[1])}/evaluation-card/latest`),
  profile('GET', /^\/api\/companies\/([^/]+)\/evaluation-data-gaps$/, m => `/internal/companies/${safeSegment(m[1])}/evaluation-data-gaps`),
  profile('GET', /^\/api\/companies\/([^/]+)\/evaluation-api-plan$/, m => `/internal/companies/${safeSegment(m[1])}/evaluation-api-plan`),

  bid('GET', /^\/api\/bid-agent\/health$/, () => '/health'),
  bid('GET', /^\/api\/bid-agent\/status$/, () => '/api/agent/status'),
  bid('GET', /^\/api\/bid-agent\/projects$/, () => '/api/projects'),
  bid('POST', /^\/api\/competition\/analyze$/, () => '/api/competition/analyze', BID_AGENT_TIMEOUT_MS),
  bid('GET', /^\/api\/competition\/([^/]+)\/([^/]+)\/detail$/, m => `/api/competition/${safeSegment(m[1])}/${safeSegment(m[2])}/detail`, BID_AGENT_TIMEOUT_MS),
  bid('GET', /^\/api\/competition\/([^/]+)\/([^/]+)$/, m => `/api/competition/${safeSegment(m[1])}/${safeSegment(m[2])}`),
  bid('POST', /^\/api\/bid-decisions$/, () => '/api/bid-decisions', BID_AGENT_TIMEOUT_MS),
  bid('GET', /^\/api\/bid-decisions\/([^/]+)$/, m => `/api/bid-decisions/${safeSegment(m[1])}`),
  bid('POST', /^\/api\/bid-decisions\/([^/]+)\/confirmations$/, m => `/api/bid-decisions/${safeSegment(m[1])}/confirmations`, BID_AGENT_TIMEOUT_MS),
  bid('POST', /^\/api\/bid-decisions\/([^/]+)\/resume$/, m => `/api/bid-decisions/${safeSegment(m[1])}/resume`, BID_AGENT_TIMEOUT_MS),
];

const headers = traceId => ({
  'content-type':'application/json; charset=utf-8',
  'access-control-allow-origin':'*',
  'access-control-allow-headers':'content-type,x-trace-id,idempotency-key',
  'access-control-allow-methods':'GET,POST,OPTIONS',
  'x-trace-id':traceId,
});

const sendJson = (res, status, payload, traceId) => {
  res.writeHead(status, headers(traceId));
  res.end(status === 204 ? '' : JSON.stringify(payload));
};

const failure = (code, message, details, traceId) => ({
  success:false,
  data:null,
  error_code:code,
  message,
  details,
  trace_id:traceId,
  error:{code,message,details},
});

const normalizeDirectResponse = (status, payload, traceId) => {
  if (status >= 200 && status < 300) {
    return {success:true, data:payload, error:null, trace_id:traceId};
  }
  const sourceError = payload && typeof payload === 'object' ? payload : {};
  const code = String(sourceError.error_code || sourceError?.error?.code || 'bid_service_error');
  const message = String(sourceError.message || sourceError?.error?.message || '投标决策服务请求失败。');
  const details = sourceError.details && typeof sourceError.details === 'object' ? sourceError.details : {};
  return failure(code, message, details, String(sourceError.trace_id || traceId));
};

const fetchJsonWithTimeout = async (url, timeoutMs = UPSTREAM_TIMEOUT_MS) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {signal:controller.signal, headers:{'content-type':'application/json'}});
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; }
    catch { throw new Error(`服务返回无效 JSON（HTTP ${response.status}）`); }
    if (!response.ok) throw new Error(String(payload?.message || payload?.error?.message || `HTTP ${response.status}`));
    return payload;
  } finally { clearTimeout(timer); }
};

const systemReadiness = async () => {
  const checks = [];
  const execute = async (name, url, advice, validate = () => true) => {
    try {
      const payload = await fetchJsonWithTimeout(url);
      if (!validate(payload)) throw new Error('服务状态内容不符合要求');
      checks.push({name, status:'PASS', message:'检查通过'});
    } catch (error) {
      checks.push({name, status:'FAIL', message:String(error?.message || error), advice});
    }
  };
  await execute('企业画像服务', `${PROFILE_SERVICE_URL}/internal/health`, '检查企业画像 Python 服务、企业原始 JSON 和本地端口 8000。');
  await execute('投标决策服务', `${BID_DECISION_SERVICE_URL}/health`, '检查投标决策 Python 服务和本地端口 8001。');
  await execute(
    '真实数据与智谱配置',
    `${BID_DECISION_SERVICE_URL}/api/agent/status`,
    '检查 SSH 隧道、远程数据库、CAPABILITY_AI_API_KEY 和 CAPABILITY_AI_MODEL。',
    payload => payload?.data_source_mode === 'REAL_LOCAL_ENTERPRISE_REMOTE_PROJECT' && payload?.llm_provider === 'zhipu',
  );
  return {ready:checks.every(item => item.status === 'PASS'), checked_at:new Date().toISOString(), checks};
};

const server = http.createServer(async (req, res) => {
  const traceId = String(req.headers['x-trace-id'] || randomUUID()).slice(0,128);
  if (req.method === 'OPTIONS') return sendJson(res, 204, {}, traceId);
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'GET' && url.pathname === '/api/system/readiness') {
    const readiness = await systemReadiness();
    return sendJson(res, readiness.ready ? 200 : 503, readiness.ready
      ? {success:true,data:readiness,error:null,trace_id:traceId}
      : failure('system_not_ready','核心服务未全部就绪。',{checks:readiness.checks,checked_at:readiness.checked_at},traceId), traceId);
  }
  const route = mappings.find(item => item.method === req.method && item.pattern.test(url.pathname));
  if (!route) return sendJson(res, 404, failure('api_not_found', '接口不存在。', {}, traceId), traceId);
  const match = url.pathname.match(route.pattern);
  const targetPath = route.target(match);
  let body = '';
  for await (const chunk of req) body += chunk;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), route.timeoutMs || UPSTREAM_TIMEOUT_MS);
  const baseUrl = route.service === 'profile' ? PROFILE_SERVICE_URL : BID_DECISION_SERVICE_URL;
  try {
    const response = await fetch(`${baseUrl}${targetPath}${url.search}`, {
      method:req.method,
      headers:{
        'content-type':'application/json',
        'x-trace-id':traceId,
        ...(req.headers['idempotency-key'] ? {'idempotency-key':String(req.headers['idempotency-key']).slice(0,256)} : {}),
      },
      body:req.method === 'GET' ? undefined : (body || '{}'),
      signal:controller.signal,
    });
    const text = await response.text();
    let payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch {
      return sendJson(
        res,
        502,
        failure(`${route.service}_service_invalid_json`, `${route.service === 'profile' ? '企业画像' : '投标决策'}服务返回了无效 JSON。`, {upstream_status:response.status}, traceId),
        traceId,
      );
    }
    const upstreamTrace = String(response.headers.get('x-trace-id') || payload?.trace_id || traceId).slice(0,128);
    if (route.envelope === 'adapt') {
      return sendJson(res, response.status, normalizeDirectResponse(response.status, payload, upstreamTrace), upstreamTrace);
    }
    return sendJson(res, response.status, payload, upstreamTrace);
  } catch (error) {
    const timedOut = error?.name === 'AbortError';
    const serviceName = route.service === 'profile' ? '企业画像' : '投标决策';
    const code = timedOut ? `${route.service}_service_timeout` : `${route.service}_service_unavailable`;
    const status = timedOut ? 504 : 503;
    return sendJson(res, status, failure(code, `${serviceName}Python服务${timedOut ? '请求超时' : '不可用'}。`, {}, traceId), traceId);
  } finally {
    clearTimeout(timer);
  }
});

server.listen(Number(process.env.API_PORT || 3000), '127.0.0.1', () => {
  console.log('Unified API: http://127.0.0.1:' + Number(process.env.API_PORT || 3000));
});
