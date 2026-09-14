import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import Pagination from './Pagination.svelte';

describe('Pagination', () => {
  it('renders prev/next when the total exceeds a page', () => {
    const navigateQuery = vi.fn();
    const { getByRole, getByText } = render(Pagination, {
      props: {
        data: { offset: 0, limit: 25, total: 70 },
        navigateQuery,
        label: 'models'
      }
    });
    expect(getByRole('button', { name: 'Previous' })).toBeTruthy();
    expect(getByRole('button', { name: 'Next' })).toBeTruthy();
    expect(getByText(/page 1 of 3/)).toBeTruthy();
  });

  it('renders nothing when the total fits one page', () => {
    const navigateQuery = vi.fn();
    const { queryByRole } = render(Pagination, {
      props: {
        data: { offset: 0, limit: 25, total: 10 },
        navigateQuery,
        label: 'models'
      }
    });
    expect(queryByRole('button', { name: 'Previous' })).toBeNull();
    expect(queryByRole('button', { name: 'Next' })).toBeNull();
  });

  it('disables Next on the last page and keeps Previous enabled past page one', () => {
    const navigateQuery = vi.fn();
    const { getByRole } = render(Pagination, {
      props: {
        data: { offset: 50, limit: 25, total: 70 },
        navigateQuery,
        label: 'models'
      }
    });
    expect((getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(false);
    expect((getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('asks navigateQuery to advance when Next is clicked', async () => {
    const navigateQuery = vi.fn();
    const { getByRole } = render(Pagination, {
      props: {
        data: { offset: 0, limit: 25, total: 70 },
        navigateQuery,
        label: 'models'
      }
    });
    const { fireEvent } = await import('@testing-library/svelte');
    await fireEvent.click(getByRole('button', { name: 'Next' }));
    expect(navigateQuery).toHaveBeenCalledWith({
      offset: '25',
      limit: '25'
    });
  });
});
