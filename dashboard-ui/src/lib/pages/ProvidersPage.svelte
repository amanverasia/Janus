<script lang="ts">
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, csv, firstList, idOf, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;

  const apiTypes = [
    ['openai_compat', 'OpenAI compatible'],
    ['anthropic', 'Anthropic Messages'],
    ['gemini', 'Google Gemini'],
    ['github_copilot', 'GitHub Copilot'],
    ['codex', 'OpenAI Codex'],
    ['kiro', 'AWS Kiro'],
    ['cursor', 'Cursor'],
    ['antigravity', 'Google Antigravity'],
    ['gemini_cli', 'Gemini CLI'],
    ['gemini-cli', 'Gemini CLI (legacy ID)'],
    ['claude_oauth', 'Claude OAuth'],
    ['claude', 'Claude OAuth (legacy ID)'],
    ['opencode_free', 'OpenCode Free'],
    ['mimo_free', 'Xiaomi MiMo Free']
  ] as const;

  let open = false;
  let editing: JsonObject | undefined;
  $: providers = firstList(data, 'providers', 'items');

  function show(item?: JsonObject) {
    editing = item;
    open = true;
  }

  async function submit(event: SubmitEvent) {
    const form = event.currentTarget as HTMLFormElement;
    const id = editing ? idOf(editing) : '';
    try {
      await action(
        id ? `/dashboard/api/providers/${encodeURIComponent(id)}` : '/dashboard/api/providers',
        {
          method: id ? 'PUT' : 'POST',
          body: new FormData(form),
          success: id ? 'Provider updated' : 'Provider connected'
        }
      );
    } catch {
      return;
    }
    open = false;
  }

  async function testProvider(provider: JsonObject) {
    try {
      await action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}/test`, {
        success: 'Connection test completed',
        refresh: false
      });
    } catch {
      return;
    }
  }
</script>

<PageHeader
  title="Providers"
  description="Connect upstream model providers and control which models Janus can route."
>
  <button class="button primary" on:click={() => show()}><Icon name="plus" />Add provider</button>
</PageHeader>
{#if providers.length}<div class="cards-grid">
    {#each providers as provider}<article class="item-card">
        <header>
          <div>
            <h3>{text(provider.name ?? provider.id)}</h3>
            <p class="mono">{text(provider.prefix)}/{text(provider.api_type)}</p>
          </div>
          <span class="status {bool(provider.is_enabled, true) ? 'active' : 'disabled'}">
            {bool(provider.is_enabled, true) ? 'Enabled' : 'Disabled'}
          </span>
        </header>
        <p>{text(provider.base_url, 'Default upstream endpoint')}</p>
        <p>
          <strong>{csv(provider.models_list ?? provider.models) || 'No models configured'}</strong>
        </p>
        <div class="card-actions">
          <button class="button" on:click={() => show(provider)}>
            <Icon name="edit" size={14} />Edit
          </button>
          <button class="button" on:click={() => testProvider(provider)}>Test</button>
          <button
            class="icon-button"
            title="Toggle"
            on:click={() =>
              action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}/toggle`, {
                method: 'PATCH',
                success: 'Provider status changed'
              })}
          >
            <Icon name="pulse" size={15} />
          </button>
          <button
            class="icon-button"
            title="Delete"
            on:click={() =>
              confirm('Delete this provider?') &&
              action(`/dashboard/api/providers/${encodeURIComponent(idOf(provider))}`, {
                method: 'DELETE',
                success: 'Provider deleted'
              })}
          >
            <Icon name="trash" size={15} />
          </button>
        </div>
      </article>{/each}
  </div>{:else}<section class="panel">
    <EmptyState
      icon="plug"
      title="Connect your first provider"
      message="Add an upstream API and Janus will make its models available for routing."
    >
      <button class="button primary" on:click={() => show()}>Add provider</button>
    </EmptyState>
  </section>{/if}
<Modal
  {open}
  title={editing ? 'Edit provider' : 'Connect provider'}
  description="Credentials are sent directly to Janus and are never returned in state payloads."
  wide
  on:close={() => (open = false)}
>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field">
        <span>Provider ID</span>
        <input name="id" required disabled={!!editing} value={text(editing?.id, '')} />
      </label>
      <label class="field">
        <span>Prefix</span>
        <input name="prefix" required value={text(editing?.prefix, '')} />
      </label>
      <label class="field">
        <span>API type</span>
        <select name="api_type" value={text(editing?.api_type, 'openai_compat')}>
          {#each apiTypes as apiType}<option value={apiType[0]}>{apiType[1]}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span>Base URL</span>
        <input
          name="base_url"
          type="url"
          value={text(editing?.base_url, '')}
          placeholder="https://api.example.com"
        />
      </label>
      <label class="field full">
        <span>API key</span>
        <input
          name="api_key"
          type="password"
          autocomplete="new-password"
          placeholder={editing ? 'Leave blank to preserve current key' : 'sk-…'}
        />
      </label>
      <label class="field full">
        <span>Models</span>
        <textarea
          name="models"
          required
          value={csv(editing?.models_list ?? editing?.models)}
          placeholder="model-one, model-two"></textarea>
      </label>
      <label class="field full">
        <span>Allowed models</span>
        <input
          name="allowed_models"
          value={csv(editing?.allowed_models)}
          placeholder="Optional allowlist"
        />
      </label>
      <label class="field">
        <span>Quota window</span>
        <select name="quota_window" value={text(editing?.quota_window, '')}>
          <option value="">No quota</option>
          <option value="5h">5 hours</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </label>
      <label class="field">
        <span>Quota limit</span>
        <input name="quota_limit" type="number" min="1" value={text(editing?.quota_limit, '')} />
      </label>
      <label class="field">
        <span>Quota metric</span>
        <select name="quota_metric" value={text(editing?.quota_metric, 'requests')}>
          <option value="requests">Requests</option>
          <option value="tokens">Tokens</option>
        </select>
      </label>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">{editing ? 'Save changes' : 'Connect provider'}</button>
    </div>
  </form>
</Modal>
