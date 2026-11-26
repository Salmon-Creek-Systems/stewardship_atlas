// Run: node --test infrastructure/cdk/functions/
// (node is at ~/.local/node/bin on the laptop.) Loads viewer_request.js the way
// the stack does, with the domain substituted, and checks the rewrite table.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SOURCE = readFileSync(new URL('./viewer_request.js', import.meta.url), 'utf8');

function load(domain) {
    const src = SOURCE.replaceAll('__ATLAS_DOMAIN__', domain);
    return new Function(src + '\nreturn handler;')();
}

function event(uri, { host = 'fireatlas.org', query = {} } = {}) {
    const querystring = {};
    for (const [k, v] of Object.entries(query)) querystring[k] = { value: v };
    return { request: { uri, querystring, headers: { host: { value: host } } } };
}

const prod = load('fireatlas.org');
const staging = load('');

const location = (r) => r.headers.location.value;

test('outlet directories get index.html', () => {
    assert.equal(prod(event('/kennedy/current/outlets/webmap/')).uri,
                 '/kennedy/current/outlets/webmap/index.html');
    assert.equal(prod(event('/kennedy/current/outlets/webmap')).uri,
                 '/kennedy/current/outlets/webmap/index.html');
    assert.equal(prod(event('/kennedy/current/layers/roads/roads.geojson')).uri,
                 '/kennedy/current/layers/roads/roads.geojson');
});

test('a version path is served as is', () => {
    assert.equal(prod(event('/kennedy/2026-11-25_10-00-00/outlets/webmap/')).uri,
                 '/kennedy/2026-11-25_10-00-00/outlets/webmap/index.html');
});

test('the box CURRENT symlink path redirects to current, keeping the query', () => {
    const r = prod(event('/kennedy/CURRENT/outlets/webmap/', { query: { s: 'abc' } }));
    assert.equal(r.statusCode, 301);
    assert.equal(location(r), '/kennedy/current/outlets/webmap/?s=abc');
});

test('a bare atlas goes to its published public console', () => {
    for (const uri of ['/kennedy', '/kennedy/']) {
        const r = prod(event(uri));
        assert.equal(r.statusCode, 302);
        assert.equal(location(r), '/kennedy/current/outlets/console/public/');
    }
});

test('reserved top-level paths are not atlases', () => {
    for (const uri of ['/local', '/usage', '/create', '/templates/']) {
        const r = prod(event(uri));
        assert.ok(!r.statusCode, `${uri} should not redirect`);
    }
});

test('the retired html console maps to the same role and page', () => {
    let r = prod(event('/scvfd/staging/outlets/html/'));
    assert.equal(location(r), '/scvfd/staging/outlets/console/public/');
    r = prod(event('/scvfd/staging/outlets/html'));
    assert.equal(location(r), '/scvfd/staging/outlets/console/public/');
    r = prod(event('/scvfd/current/outlets/html/admin/index.html', { query: { a: '1' } }));
    assert.equal(r.statusCode, 301);
    assert.equal(location(r), '/scvfd/current/outlets/console/admin/index.html?a=1');
});

test('an outlet merely starting with html is not the retired console', () => {
    assert.equal(prod(event('/scvfd/current/outlets/html_export/')).uri,
                 '/scvfd/current/outlets/html_export/index.html');
});

test('the landing page', () => {
    assert.equal(prod(event('/')).uri, '/local/index.html');
});

test('old per-atlas hosts redirect to the path form', () => {
    let r = prod(event('/staging/outlets/webmap/', { host: 'scvfd.fireatlas.org', query: { s: 'x' } }));
    assert.equal(r.statusCode, 301);
    assert.equal(location(r), 'https://fireatlas.org/scvfd/staging/outlets/webmap/?s=x');
    r = prod(event('/', { host: 'westportvfd.fireatlas.org' }));
    assert.equal(location(r), 'https://fireatlas.org/westport/');
    r = prod(event('/about', { host: 'www.fireatlas.org' }));
    assert.equal(location(r), 'https://fireatlas.org/about');
});

test('next.fireatlas.org and the apex are not atlas hosts', () => {
    assert.equal(prod(event('/', { host: 'next.fireatlas.org' })).uri, '/local/index.html');
    assert.equal(prod(event('/')).uri, '/local/index.html');
});

test('staging has no atlas domain, so no host redirects', () => {
    assert.equal(staging(event('/', { host: 'd1y2f6bmf8ds3m.cloudfront.net' })).uri,
                 '/local/index.html');
});

test('identify_as_scs sets the cookie for the whole domain on prod only', () => {
    let r = prod(event('/identify_as_scs'));
    assert.equal(r.statusCode, 200);
    assert.match(r.cookies.fa_owner.attributes, /^Domain=\.fireatlas\.org; Path=\//);
    r = staging(event('/identify_as_scs', { host: 'd1y2f6bmf8ds3m.cloudfront.net' }));
    assert.doesNotMatch(r.cookies.fa_owner.attributes, /Domain=/);
});

test('multi-valued and flag query parameters survive a redirect', () => {
    const e = event('/kennedy/CURRENT/x/');
    e.request.querystring = { a: { value: '1', multiValue: [{ value: '1' }, { value: '2' }] },
                              embed: { value: '' } };
    assert.equal(location(prod(e)), '/kennedy/current/x/?a=1&a=2&embed');
});
