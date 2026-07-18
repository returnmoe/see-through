import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const indexPath = resolve(import.meta.dirname, '../dist/index.html');
const html = readFileSync(indexPath, 'utf8');
const violations = [];

if (/<script\b(?![^>]*\bsrc=)[^>]*>/i.test(html)) {
  violations.push('inline script');
}
if (/<style\b/i.test(html) || /\sstyle\s*=/i.test(html)) {
  violations.push('inline style');
}

if (violations.length > 0) {
  throw new Error(`Production HTML violates the runtime CSP: ${violations.join(', ')}`);
}

console.log('CSP compatibility check passed: production HTML uses external scripts and styles.');
