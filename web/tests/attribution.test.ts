import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const appPath = resolve(import.meta.dirname, '../src/components/App.svelte');
const appSource = readFileSync(appPath, 'utf8');
const visibleCopy = appSource.replaceAll(/\s+/g, ' ');

describe('visible attribution and licensing', () => {
  it('credits the paper, every author, and the stated affiliations', () => {
    expect(visibleCopy).toContain('See-through: Single-image Layer Decomposition for Anime Characters');
    expect(appSource).toContain('https://arxiv.org/abs/2602.03749');

    for (const author of [
      'Jian Lin',
      'Chengze Li',
      'Haoyun Qin',
      'Kwun Wang Chan',
      'Yanghua Jin',
      'Hanyuan Liu',
      'Stephen Chun Wang Choy',
      'Xueting Liu',
    ]) {
      expect(visibleCopy, author).toContain(author);
    }

    for (const affiliation of [
      'Saint Francis University',
      'University of Pennsylvania',
      'Spellbrush',
      'Shitagaki Lab',
    ]) {
      expect(visibleCopy, affiliation).toContain(affiliation);
    }
    expect(visibleCopy).toContain('Chengze Li is the corresponding author');
    expect(visibleCopy).toContain('CC BY-NC-SA 4.0');
    expect(appSource).toContain('https://creativecommons.org/licenses/by-nc-sa/4.0/');
  });

  it('credits the upstream source and states its license without inventing a holder', () => {
    expect(appSource).toContain('https://github.com/shitagaki-lab/see-through');
    expect(appSource).toContain('https://github.com/shitagaki-lab/see-through/blob/main/LICENSE');
    expect(visibleCopy).toContain('published by Shitagaki Lab and contributors');
    expect(visibleCopy).toContain('Apache License 2.0');
    expect(visibleCopy).toContain(
      'The repository’s license does not name a separate copyright holder, so none is inferred here.',
    );
  });

  it('names every runtime model, its publisher, and the model licensing basis', () => {
    const models = [
      {
        name: 'LayerDiff 3D',
        publisher: 'layerdifforg',
        url: 'https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d',
      },
      {
        name: 'Marigold Depth',
        publisher: 'layerdifforg',
        url: 'https://huggingface.co/layerdifforg/seethroughv0.0.1_marigold',
      },
      {
        name: 'LayerDiff 3D NF4',
        publisher: '24yearsold',
        url: 'https://huggingface.co/24yearsold/seethroughv0.0.2_layerdiff3d_nf4',
      },
      {
        name: 'Marigold Depth NF4',
        publisher: '24yearsold',
        url: 'https://huggingface.co/24yearsold/seethroughv0.0.1_marigold_nf4',
      },
    ];

    for (const model of models) {
      expect(visibleCopy, model.name).toContain(model.name);
      expect(visibleCopy, model.publisher).toContain(model.publisher);
      expect(appSource, model.url).toContain(model.url);
    }

    expect(visibleCopy).toContain('LayerDiff 3D carries Apache-2.0 metadata');
    expect(visibleCopy).toContain('the other listed weights are Apache 2.0');
    expect(appSource).toContain(
      'https://huggingface.co/layerdifforg/seethroughv0.0.2_layerdiff3d/discussions/1',
    );
    expect(visibleCopy).toContain('per the publisher’s licensing statement');
  });

  it('keeps return moe ownership and independence in permanent credits linked from the header', () => {
    const workflowPosition = appSource.indexOf('aria-label="New inference run"');
    const creditsPosition = appSource.indexOf('id="credits"');

    expect(workflowPosition).toBeGreaterThan(0);
    expect(creditsPosition).toBeGreaterThan(workflowPosition);
    expect(appSource).toMatch(
      /<header[\s\S]*?<button[^>]+aria-label="Credits and licenses"[\s\S]*?<\/button>/i,
    );
    expect(visibleCopy).toContain('independent work by');
    expect(visibleCopy).toContain('return moe');
    expect(visibleCopy).toContain('general-purpose convenience interface');
    expect(appSource).not.toContain('github.com/returnmoe');
    expect(visibleCopy).toContain(
      'not approved by, endorsed by, sponsored by, or otherwise affiliated with the See-Through authors, Shitagaki Lab, their institutions, or the model publishers',
    );
    expect(appSource).toContain('aria-labelledby="credits-title"');
    expect(appSource).toContain('aria-modal="true"');
  });
});
