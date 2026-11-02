<script lang="ts">
  import Icon from '#lib/components/Icon.svelte';
  import PageHeader from '#lib/components/PageHeader.svelte';
  import { bool, number, object, text } from '#lib/data.ts';
  import type { JsonObject, MutationOptions } from '#lib/types.ts';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  $: settings = object(data.settings);
  $: stats = object(data.saver_stats ?? data.stats);
  $: health = object(data.saver_health);
  $: headroomHealth = object(health.HeadroomSaver);
  $: headroomDown = enabled('headroom') && headroomHealth.reachable === false;

  const savers = [
    {
      id: 'rtk',
      name: 'RTK',
      className: 'RTKSaver',
      description: 'Compresses conversation history while preserving the context models need.',
      badge: 'Recommended'
    },
    {
      id: 'caveman',
      name: 'Caveman',
      className: 'CavemanSaver',
      description: 'Encourages concise responses by removing filler and repeated phrasing.',
      badge: ''
    },
    {
      id: 'headroom',
      name: 'Headroom',
      className: 'HeadroomSaver',
      description: 'Compacts long context through a locally operated Headroom proxy.',
      badge: 'External'
    },
    {
      id: 'ponytail',
      name: 'Ponytail',
      className: 'PonytailSaver',
      description: 'Summarizes older turns into a focused, compact conversation memory.',
      badge: ''
    }
  ];

  function enabled(id: string) {
    return bool(settings[`saver_${id}_enabled`]);
  }

  let pending = new Set<string>();

  function setPending(key: string, isPending: boolean) {
    const next = new Set(pending);
    if (isPending) next.add(key);
    else next.delete(key);
    pending = next;
  }

  async function save(key: string, value: string, message: string) {
    if (pending.has(key)) return;
    setPending(key, true);
    const body = new FormData();
    body.set('key', key);
    body.set('value', value);
    try {
      await action('/dashboard/api/settings', { body, success: message });
    } catch {
    } finally {
      setPending(key, false);
    }
  }

  function toggle(id: string, name: string) {
    return save(`saver_${id}_enabled`, enabled(id) ? 'false' : 'true', `${name} updated`);
  }

  function unreachable(className: string): JsonObject | null {
    const status = object(health[className]);
    return status.reachable === false ? status : null;
  }

  function checkedAt(status: JsonObject): string {
    const value = text(status.checked_at, '');
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
  }

  function saverStats(className: string): JsonObject {
    return object(stats[className]);
  }
</script>

<PageHeader
  title="Token savers"
  description="Reduce repetitive context before it reaches an upstream model. Every saver fails safely."
>
  {#if headroomDown}
    <span class="status error"><Icon name="spark" size={13} />Headroom unreachable</span>
  {:else}
    <span class="status active"><Icon name="spark" size={13} />Pipeline ready</span>
  {/if}
</PageHeader>

<div class="saver-grid">
  {#each savers as saver}
    {@const usage = saverStats(saver.className)}
    {@const down = enabled(saver.id) ? unreachable(saver.className) : null}
    <article class="saver-card" class:enabled={enabled(saver.id)} class:unhealthy={down !== null}>
      <header>
        <span class="saver-icon"><Icon name="spark" size={18} /></span>
        <div>
          <div class="saver-title">
            <h2>{saver.name}</h2>
            {#if saver.badge}<span>{saver.badge}</span>{/if}
          </div>
          <p>{saver.description}</p>
        </div>
        <button
          class="toggle-button"
          class:on={enabled(saver.id)}
          type="button"
          role="switch"
          aria-checked={enabled(saver.id)}
          aria-label={`${enabled(saver.id) ? 'Disable' : 'Enable'} ${saver.name}`}
          disabled={pending.has(`saver_${saver.id}_enabled`)}
          on:click={() => toggle(saver.id, saver.name)}
        >
          <span></span>
        </button>
      </header>

      {#if saver.id === 'caveman' || saver.id === 'ponytail'}
        <div class="saver-option">
          <span>Compression level</span>
          <div class="tabs">
            {#each ['lite', 'full', 'ultra'] as level}<button
                type="button"
                class:active={text(settings[`saver_${saver.id}_level`], 'full') === level}
                disabled={pending.has(`saver_${saver.id}_level`)}
                on:click={() =>
                  save(`saver_${saver.id}_level`, level, `${saver.name} level updated`)}
              >
                {level}
              </button>{/each}
          </div>
        </div>
      {:else if saver.id === 'headroom'}
        <label class="field saver-option">
          <span>Headroom proxy URL</span>
          <input
            value={text(settings.saver_headroom_url, 'http://localhost:8787')}
            placeholder="http://localhost:8787"
            disabled={pending.has('saver_headroom_url')}
            on:change={(event) =>
              save(
                'saver_headroom_url',
                (event.currentTarget as HTMLInputElement).value,
                'Headroom URL updated'
              )}
          />
        </label>
      {:else}
        <div class="saver-option">
          <span>Mode</span>
          <strong>Automatic conversation compression</strong>
        </div>
      {/if}

      {#if down}
        <div class="saver-alert" role="alert">
          <strong>{saver.name} is unreachable</strong>
          <p>
            {text(down.reason, 'No response')}{#if checkedAt(down)}
              (checked {checkedAt(down)}){/if}. Requests skip it and are sent uncompressed until it
            responds again. Correct the proxy URL above, or turn {saver.name} off.
          </p>
          <button
            class="button compact"
            type="button"
            disabled={pending.has(`saver_${saver.id}_enabled`)}
            on:click={() => toggle(saver.id, saver.name)}
          >
            Disable {saver.name}
          </button>
        </div>
      {/if}

      <footer>
        <div>
          <span>Requests processed</span>
          <strong>{number(usage.requests).toLocaleString()}</strong>
        </div>
        <div>
          <span>Average reduction</span>
          <strong>{number(usage.avg_pct).toFixed(1)}%</strong>
        </div>
        <div>
          <span>Data saved</span>
          <strong>{number(usage.saved_kb).toFixed(1)} KB</strong>
        </div>
      </footer>
    </article>
  {/each}
</div>

<style>
  .saver-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 15px;
  }
  .saver-card {
    padding: 19px;
    border: 1px solid var(--line);
    border-radius: 17px;
    background: var(--surface);
    box-shadow: 0 4px 18px rgba(44, 85, 92, 0.035);
    transition: 0.18s ease;
  }
  .saver-card.enabled.unhealthy {
    border-color: color-mix(in srgb, var(--danger) 45%, var(--line));
  }
  .saver-alert {
    margin-top: 12px;
    padding: 11px 12px;
    border: 1px solid color-mix(in srgb, var(--danger) 25%, transparent);
    border-radius: 11px;
    background: var(--danger-soft);
    color: var(--danger);
  }
  .saver-alert strong {
    display: block;
    font-size: 12px;
  }
  .saver-alert p {
    margin: 4px 0 10px;
    font-size: 11px;
    line-height: 1.5;
  }
  .saver-card.enabled {
    border-color: color-mix(in srgb, var(--accent) 25%, var(--line));
    box-shadow: 0 12px 30px color-mix(in srgb, var(--accent) 7%, transparent);
  }
  .saver-card > header {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .saver-icon {
    display: grid;
    place-items: center;
    width: 38px;
    height: 38px;
    flex: 0 0 auto;
    border-radius: 12px;
    color: var(--accent-strong);
    background: var(--accent-soft);
  }
  .saver-card header > div {
    flex: 1;
    min-width: 0;
  }
  .saver-title {
    display: flex;
    align-items: center;
    gap: 7px;
  }
  .saver-title h2 {
    margin: 1px 0 0;
    font-size: 14px;
  }
  .saver-title span {
    padding: 3px 6px;
    border-radius: 7px;
    color: var(--accent-strong);
    background: var(--accent-soft);
    font-size: 11px;
    font-weight: 750;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  .saver-card header p {
    margin: 5px 0 0;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
  }
  .toggle-button {
    position: relative;
    width: 39px;
    height: 23px;
    flex: 0 0 auto;
    padding: 2px;
    border: 0;
    border-radius: 99px;
    background: var(--line-strong);
    cursor: pointer;
    transition: 0.18s ease;
  }
  .toggle-button span {
    display: block;
    width: 19px;
    height: 19px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.18);
    transition: 0.18s ease;
  }
  .toggle-button.on {
    background: var(--accent);
  }
  .toggle-button.on span {
    transform: translateX(16px);
  }
  .saver-option {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 47px;
    padding: 11px 0;
    margin-top: 15px;
    border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }
  .saver-option > span {
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  .saver-option > strong {
    font-size: 11px;
  }
  .saver-option.field {
    align-items: stretch;
    display: flex;
    flex-direction: column;
  }
  .saver-option.field input {
    min-height: 36px;
  }
  .saver-card footer {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    padding-top: 13px;
  }
  .saver-card footer > div {
    padding: 9px;
    border-radius: 10px;
    background: var(--surface-soft);
  }
  .saver-card footer span,
  .saver-card footer strong {
    display: block;
  }
  .saver-card footer span {
    color: var(--muted);
    font-size: 11px;
  }
  .saver-card footer strong {
    margin-top: 4px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 900px) {
    .saver-grid {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 480px) {
    .saver-card footer {
      grid-template-columns: 1fr;
    }
    .saver-card footer > div {
      display: flex;
      justify-content: space-between;
    }
    .saver-card footer strong {
      margin: 0;
    }
  }
</style>
