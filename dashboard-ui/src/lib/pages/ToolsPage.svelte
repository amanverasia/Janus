<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { copyText } from '$lib/clipboard';
  import { bool, text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  let copied = '';
  $: base = text(data.base_url, `${location.origin}/v1`);
  $: requireKey = bool(data.require_api_key, true);
  $: curlCommand = requireKey
    ? `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer $JANUS_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "YOUR_MODEL", "messages": [{"role": "user", "content": "Hello"}]}'`
    : `curl ${base}/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "YOUR_MODEL", "messages": [{"role": "user", "content": "Hello"}]}'`;
  async function copy(value: string, label: string) {
    try {
      await copyText(value);
      copied = label;
    } catch {
      copied = 'error';
    }
    setTimeout(() => (copied = ''), 1800);
  }
</script>

<PageHeader
  title="Developer tools"
  description="Point familiar AI SDKs at Janus with its OpenAI-compatible base URL."
/>
<div class="cards-grid">
  <article class="item-card">
    <header>
      <h3>API base URL</h3>
      <span class="status {requireKey ? 'active' : 'pending'}">
        {requireKey ? 'API key required' : 'No API key required'}
      </span>
    </header>
    <p>
      Use this endpoint with OpenAI-compatible clients. {requireKey
        ? 'Requests must authenticate with a Janus key (create one on the Keys page).'
        : 'API-key enforcement is currently disabled on this server.'}
    </p>
    <div class="code-block">{base}</div>
    <div class="card-actions">
      <button class="button" on:click={() => copy(base, 'url')}>
        <Icon name="check" size={14} />{copied === 'url'
          ? 'Copied'
          : copied === 'error'
            ? 'Copy failed'
            : 'Copy URL'}
      </button>
    </div>
  </article>
  <article class="item-card">
    <header><h3>Quick test</h3></header>
    <p>
      A runnable request against this server. {requireKey
        ? 'Export your key first: JANUS_API_KEY=sk-janus-…'
        : 'No Authorization header needed while enforcement is off.'}
    </p>
    <div class="code-block"><pre>{curlCommand}</pre></div>
    <div class="card-actions">
      <button class="button" on:click={() => copy(curlCommand, 'curl')}>
        {copied === 'curl' ? 'Copied' : copied === 'error' ? 'Copy failed' : 'Copy command'}
      </button>
    </div>
  </article>
  <article class="item-card">
    <header><h3>Python</h3></header>
    <p>Configure the official OpenAI client.</p>
    <div class="code-block">
      from openai import OpenAI
      <br />
      client = OpenAI(base_url="{base}"{requireKey ? ', api_key="sk-janus-…"' : ''})
    </div>
    <div class="card-actions">
      <button
        class="button"
        on:click={() =>
          copy(
            `from openai import OpenAI\nclient = OpenAI(base_url="${base}"${requireKey ? ', api_key="sk-janus-…"' : ''})`,
            'python'
          )}
      >
        {copied === 'python' ? 'Copied' : 'Copy snippet'}
      </button>
    </div>
  </article>
  <article class="item-card">
    <header><h3>Environment</h3></header>
    <p>Connect tools that accept standard environment variables.</p>
    <div class="code-block">
      OPENAI_BASE_URL={base}
      <br />
      {#if requireKey}OPENAI_API_KEY=sk-janus-…{:else}# OPENAI_API_KEY not required while
        enforcement is off{/if}
    </div>
    <div class="card-actions">
      <button
        class="button"
        on:click={() =>
          copy(
            `OPENAI_BASE_URL=${base}\n${requireKey ? 'OPENAI_API_KEY=sk-janus-…' : '# OPENAI_API_KEY not required while enforcement is off'}`,
            'env'
          )}
      >
        {copied === 'env' ? 'Copied' : 'Copy variables'}
      </button>
    </div>
  </article>
</div>
