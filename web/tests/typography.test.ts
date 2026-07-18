import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const sourceRoot = resolve(import.meta.dirname, '../src');
const packagePath = resolve(import.meta.dirname, '../package.json');

function filesBelow(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry);
    return statSync(path).isDirectory() ? filesBelow(path) : [path];
  });
}

describe('local typography policy', () => {
  const sourceFiles = filesBelow(sourceRoot).filter((path) => ['.astro', '.css', '.svelte', '.ts'].includes(extname(path)));
  const presentationFiles = sourceFiles.filter((path) => ['.astro', '.css', '.svelte'].includes(extname(path)));
  const cssFiles = sourceFiles.filter((path) => extname(path) === '.css');

  it('never references Georgia or a generic serif family', () => {
    for (const path of sourceFiles) {
      const contents = readFileSync(path, 'utf8');
      expect(contents, path).not.toMatch(/\bGeorgia\b/i);
      expect(contents, path).not.toMatch(/(?<!sans-)\bserif\b/i);
    }
  });

  it('does not load fonts or assets from remote font services', () => {
    for (const path of sourceFiles) {
      const contents = readFileSync(path, 'utf8');
      expect(contents, path).not.toMatch(/fonts\.(googleapis|gstatic)\.com/i);
      expect(contents, path).not.toMatch(/https?:\/\/[^\s'\"]+\.(?:woff2?|ttf|otf)/i);
    }
  });

  it('uses Inter without the unrelated Manrope display face', () => {
    for (const path of presentationFiles) {
      expect(readFileSync(path, 'utf8'), path).not.toMatch(/\bManrope\b/i);
    }

    const packageJson = JSON.parse(readFileSync(packagePath, 'utf8')) as {
      dependencies?: Record<string, string>;
    };
    expect(packageJson.dependencies).not.toHaveProperty('@fontsource/manrope');
    expect(packageJson.dependencies).toHaveProperty('@fontsource/inter');
  });

  it('does not bring back eyebrow labels, forced uppercase, or positive display tracking', () => {
    for (const path of presentationFiles) {
      expect(readFileSync(path, 'utf8'), path).not.toMatch(/\beyebrow\b/i);
    }

    for (const path of cssFiles) {
      const contents = readFileSync(path, 'utf8').replaceAll(/\/\*[\s\S]*?\*\//g, '');
      expect(contents, path).not.toMatch(/text-transform\s*:\s*uppercase\b/i);

      for (const match of contents.matchAll(/letter-spacing\s*:\s*([^;}\n]+)/gi)) {
        const value = match[1].trim();
        const literal = value.match(/^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:px|r?em)?$/i);
        if (literal) {
          expect(Number(literal[1]), `${path}: letter-spacing: ${value}`).toBeLessThanOrEqual(0);
        }
      }
    }
  });

  it('keeps authored text at a readable 12px minimum', () => {
    const sizes: Array<{ path: string; declaration: string; pixels: number }> = [];

    for (const path of cssFiles) {
      const contents = readFileSync(path, 'utf8').replaceAll(/\/\*[\s\S]*?\*\//g, '');
      for (const declaration of contents.matchAll(/font-size\s*:\s*([^;}\n]+)/gi)) {
        for (const literal of declaration[1].matchAll(/(\d+(?:\.\d*)?|\.\d+)(px|r?em)\b/gi)) {
          const numeric = Number(literal[1]);
          const pixels = literal[2].toLowerCase() === 'px' ? numeric : numeric * 16;
          sizes.push({ path, declaration: declaration[1].trim(), pixels });
        }
      }
    }

    expect(sizes.length).toBeGreaterThan(0);
    for (const size of sizes) {
      expect(size.pixels, `${size.path}: font-size: ${size.declaration}`).toBeGreaterThanOrEqual(12);
    }
  });

  it('allows hairline dividers but rejects heavy left-edge outlines and inset rails', () => {
    for (const path of cssFiles) {
      const contents = readFileSync(path, 'utf8').replaceAll(/\/\*[\s\S]*?\*\//g, '');
      expect(contents, path).not.toMatch(
        /\bborder-left\s*:\s*(?:1\.[1-9]\d*|[2-9]\d*(?:\.\d+)?)px\b/i,
      );
      expect(contents, path).not.toMatch(
        /\bborder-inline-start\s*:\s*(?:1\.[1-9]\d*|[2-9]\d*(?:\.\d+)?)px\b/i,
      );
      expect(contents, path).not.toMatch(
        /box-shadow\s*:\s*inset\s+(?:[1-9]\d*|\.\d+)(?:px|r?em)\s+0(?:px|r?em)?\b/i,
      );
    }

    const appCssPath = resolve(sourceRoot, 'styles/app.css');
    const appCss = readFileSync(appCssPath, 'utf8');
    const artifactDockRule = appCss.match(/\.artifact-dock\s*\{([^}]*)\}/s);
    expect(artifactDockRule, 'The artifact dock must have a base style rule').not.toBeNull();
    expect(artifactDockRule?.[1], 'The artifact dock should be outlined on every side').toMatch(
      /(?:^|;)\s*border\s*:/i,
    );
  });
});
