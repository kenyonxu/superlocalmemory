import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

it('L-14: cluster Next requests another bounded server page', async () => {
  const h = buildHarness(['clusters-list','clusters-page-info','clusters-page-detail','clusters-prev-btn','clusters-next-btn'], {ok:true,status:200,json:{}});
  const calls = [];
  h.window.fetch = async url => {
    calls.push(String(url));
    return {ok:true,json:async () => ({clusters:[{cluster_id:'c1',member_count:1}],total_clusters:100})};
  };
  evalModule(h.window, 'clusters.js');
  await h.window.loadClusters();
  assert.match(calls[0], /\/api\/dashboard\/clusters\?limit=25&offset=0/);
  h.document.getElementById('clusters-next-btn').click(); await flushPromises();
  assert.match(calls.at(-1), /offset=25/);
  assert.match(h.document.getElementById('clusters-page-info').textContent, /of 100/);
  h.dom.window.close();
});

it('L-14: trust Next requests another page while aggregates reflect all agents', async () => {
  const h = buildHarness(['trust-agents-body','trust-page-info','trust-page-detail','trust-prev-btn','trust-next-btn','trust-agent-count','trust-avg-score','trust-burst-count'], {ok:true,status:200,json:{}});
  const calls = [];
  h.window.fetch = async url => {
    calls.push(String(url));
    return {ok:true,json:async () => ({agents:[{target_id:'t1',trust_score:0.7}],total:100,avg_trust:0.6,alert_count:5})};
  };
  evalModule(h.window, 'trust-dashboard.js'); await flushPromises();
  await h.window.loadTrustDashboard();
  assert.match(calls[0], /\/api\/dashboard\/trust\?limit=25&offset=0/);
  h.document.getElementById('trust-next-btn').click(); await flushPromises();
  assert.match(calls.at(-1), /offset=25/);
  assert.equal(h.document.getElementById('trust-agent-count').textContent, '100');
  assert.equal(h.document.getElementById('trust-avg-score').textContent, '0.600');
  h.dom.window.close();
});
