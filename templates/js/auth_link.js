// Log in / Log out link (#243). Published once for the whole site at
// /local/js/auth_link.js (site_assets.py); a page opts in with
//     <a data-auth-link hidden></a>
// and this fills it. The session cookie is scoped to /api, so a page can't
// read it: /api/me answers instead. 200 is a login, 401 is none; anything
// else (the box, a page opened from disk) leaves the link hidden.

function authLink(status, here) {
    if (status === 200) {
        return { text: 'Log out', href: '/api/auth/logout' };
    }
    if (status === 401) {
        return { text: 'Log in', href: '/api/auth/login?next=' + encodeURIComponent(here) };
    }
    return null;
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', function () {
        var links = document.querySelectorAll('a[data-auth-link]');
        if (!links.length) return;
        fetch('/api/me', { cache: 'no-store' })
            .then(function (r) { return r.status; }, function () { return 0; })
            .then(function (status) {
                var link = authLink(status, window.location.pathname + window.location.search);
                if (!link) return;
                links.forEach(function (a) {
                    a.textContent = link.text;
                    a.href = link.href;
                    a.hidden = false;
                });
            });
    });
}
