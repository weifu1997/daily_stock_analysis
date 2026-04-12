import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { JsonViewer } from '../JsonViewer';

describe('JsonViewer', () => {
  it('escapes html in json values before highlighting', () => {
    const { container } = render(
      <JsonViewer
        data={{
          safe: '<img src=x onerror=alert(1)>',
          plain: 'hello',
        }}
      />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(container.innerHTML).not.toContain('<img src=x onerror=alert(1)>');
    expect(container.innerHTML).not.toContain('<img');
  });
});
