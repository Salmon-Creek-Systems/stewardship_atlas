// Run: node --test infrastructure/cdk/functions/*.test.mjs
// templates/js/auth_link.js: what the console's Log in / Log out link becomes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SOURCE = readFileSync(new URL('../../../templates/js/auth_link.js', import.meta.url), 'utf8');
const authLink = new Function(SOURCE + '\nreturn authLink;')();

test('logged in: log out', () => {
    assert.deepEqual(authLink(200, '/kennedy/staging/outlets/console/admin/'),
                     { text: 'Log out', href: '/api/auth/logout' });
});

test('not logged in: log in, coming back to this page', () => {
    assert.deepEqual(authLink(401, '/kennedy/current/outlets/console/public/?a=1'),
                     { text: 'Log in',
                       href: '/api/auth/login?next=%2Fkennedy%2Fcurrent%2Foutlets%2Fconsole%2Fpublic%2F%3Fa%3D1' });
});

test('no API here (the box, a file): no link', () => {
    for (const status of [0, 404, 500, 502]) {
        assert.equal(authLink(status, '/'), null);
    }
});
