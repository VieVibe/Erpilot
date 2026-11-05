import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { buildSync } from 'esbuild';

const require = createRequire(import.meta.url);
require.extensions['.css'] = () => {};
buildSync({
  entryPoints: ['node_modules/@ant-design/x-markdown/plugins/Latex/index.js'],
  bundle: true, platform: 'node', format: 'cjs', packages: 'external',
  outfile: '.test-build/latex-plugin.cjs',
});
const Latex = require('../.test-build/latex-plugin.cjs').default;

test('the upstream Markdown plugin renders inline and block math with the patched KaTeX', () => {
  const [inline, block] = Latex();
  for (const [extension, source] of [[inline, '$x^2 + y^2$'], [block, '$$\nx^2 + y^2\n$$']]) {
    const token = extension.tokenizer(source);
    assert.ok(token);
    const html = extension.renderer(token);
    assert.match(html, /class="katex"/);
    assert.doesNotMatch(html, /katex-error/);
  }
});

test('the patched math renderer preserves the upstream default trust boundary', () => {
  const [inline] = Latex();
  const token = inline.tokenizer('$\\href{javascript:alert(1)}{click}$');
  const html = inline.renderer(token);
  assert.doesNotMatch(html, /href\s*=\s*["']javascript:/i);
  assert.doesNotMatch(html, /<script/i);
});
