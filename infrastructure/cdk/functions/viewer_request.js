// Viewer-request function for the static behaviors (public outlets and the
// private staging tier). /api/* has its own behavior and never sees this.
//
// Does what the box's nginx did for URLs, in this order:
//   1. {atlas}.DOMAIN/...       -> DOMAIN/{atlas}/...   (old per-atlas hosts)
//   2. /identify_as_scs         -> sets the fa_owner cookie (usage report)
//   3. /                        -> the landing page (local/index.html)
//      /create                  -> the create page (local/create/index.html)
//      /usage                   -> /api/usage/ (the usage report, scs-admin)
//   4. /biochar_atlas_poc       -> the ABI demo's biochar page
//   5. /{atlas} or /{atlas}/    -> that atlas's published public console
//   6. /{atlas}/CURRENT/...     -> /{atlas}/current/...  (the box's symlink)
//   7. .../outlets/html/...     -> .../outlets/console/... (retired console)
//   8. a directory URL          -> its index.html (S3 behind OAC doesn't index)
//
// ATLAS_DOMAIN's placeholder is replaced at synth with the distribution's domain
// (empty on staging, which turns rule 1 off). Tests: viewer_request.test.mjs.

var ATLAS_DOMAIN = '__ATLAS_DOMAIN__';

// Hosts under the domain that are not atlases.
var NON_ATLAS_HOSTS = { 'www': true, 'next': true };
// Old host names whose atlas has a different name.
var HOST_ALIASES = { 'westportvfd': 'westport' };

// First path segments that are not atlases (rule 5 must not catch them).
var RESERVED = {
    'local': true, 'api': true, 'usage': true, 'templates': true,
    'create': true, 'create-preview': true, 'create-starters': true,
    'create-status': true, 'create_atlas': true,
    'identify_as_scs': true, 'biochar_atlas_poc': true
};

var TEN_YEARS = 315360000;

function queryString(querystring) {
    var parts = [];
    for (var key in querystring) {
        var entry = querystring[key];
        var values = entry.multiValue ? entry.multiValue : [entry];
        for (var i = 0; i < values.length; i++) {
            var value = values[i].value;
            parts.push(value === '' ? key : key + '=' + value);
        }
    }
    return parts.length ? '?' + parts.join('&') : '';
}

function redirect(status, location) {
    return {
        statusCode: status,
        statusDescription: status === 301 ? 'Moved Permanently' : 'Found',
        headers: { 'location': { value: location } }
    };
}

function identifyAsScs(host) {
    // Domain=.fireatlas.org covers every *.fireatlas.org site; a browser
    // refuses it on any other host (staging's cloudfront.net), so it is only
    // set when this is the atlas domain.
    var attributes = 'Path=/; Max-Age=' + TEN_YEARS + '; Secure; SameSite=Lax';
    if (ATLAS_DOMAIN && host.endsWith(ATLAS_DOMAIN)) {
        attributes = 'Domain=.' + ATLAS_DOMAIN + '; ' + attributes;
    }
    return {
        statusCode: 200,
        statusDescription: 'OK',
        headers: { 'content-type': { value: 'text/html; charset=utf-8' },
                   'cache-control': { value: 'no-store' } },
        cookies: { 'fa_owner': { value: '1', attributes: attributes } },
        body: {
            encoding: 'text',
            data: '<!doctype html><meta name="viewport" content="width=device-width">' +
                  '<p style="font:16px sans-serif;margin:2em">This browser is now marked ' +
                  'as SCS and left out of the usage report.</p>'
        }
    };
}

function handler(event) {
    var request = event.request;
    var uri = request.uri;
    var host = (request.headers.host && request.headers.host.value) || '';
    var query = queryString(request.querystring);

    // 1. Old per-atlas hosts.
    if (ATLAS_DOMAIN && host !== ATLAS_DOMAIN && host.endsWith('.' + ATLAS_DOMAIN)) {
        var sub = host.slice(0, host.length - ATLAS_DOMAIN.length - 1);
        if (sub.indexOf('.') === -1 && !NON_ATLAS_HOSTS[sub]) {
            var atlas = HOST_ALIASES[sub] || sub;
            return redirect(301, 'https://' + ATLAS_DOMAIN + '/' + atlas + uri + query);
        }
        if (sub === 'www') {
            return redirect(301, 'https://' + ATLAS_DOMAIN + uri + query);
        }
    }

    // 2.
    if (uri === '/identify_as_scs') {
        return identifyAsScs(host);
    }

    // 3.
    if (uri === '/' || uri === '/index.html') {
        request.uri = '/local/index.html';
        return request;
    }
    if (uri === '/create' || uri === '/create/') {
        request.uri = '/local/create/index.html';
        return request;
    }
    if (uri === '/usage' || uri.indexOf('/usage/') === 0) {
        return redirect(302, '/api' + (uri === '/usage' ? '/usage/' : uri) + query);
    }

    // 4.
    if (uri === '/biochar_atlas_poc') {
        return redirect(302, '/abi_demo/staging/outlets/biochar_webedit/');
    }

    var segments = uri.split('/');   // ['', atlas, ...]

    // 5.
    var bareAtlas = /^\/[A-Za-z0-9_-]+\/?$/.test(uri);
    if (bareAtlas && !RESERVED[segments[1]]) {
        return redirect(302, '/' + segments[1] + '/current/outlets/console/public/');
    }

    // 6.
    if (segments.length > 2 && segments[2] === 'CURRENT' && !RESERVED[segments[1]]) {
        segments[2] = 'current';
        return redirect(301, segments.join('/') + query);
    }

    // 7. The same role and page of the console; a bare html/ goes to public.
    var html = uri.indexOf('/outlets/html');
    if (html !== -1) {
        var before = uri.slice(0, html);
        var rest = uri.slice(html + '/outlets/html'.length);
        if (rest === '' || rest === '/') {
            return redirect(301, before + '/outlets/console/public/');
        }
        if (rest.charAt(0) === '/') {
            return redirect(301, before + '/outlets/console' + rest + query);
        }
    }

    // 8.
    if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
    } else if (!segments[segments.length - 1].includes('.')) {
        request.uri = uri + '/index.html';
    }
    return request;
}
