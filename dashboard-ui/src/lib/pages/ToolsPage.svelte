<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { text } from '$lib/data';
  import type { JsonObject } from '$lib/types';
  export let data: JsonObject;
  let copied = '';
  $: base = text(data.base_url, `${location.origin}/v1`);
  async function copy(value: string, label: string) {
    await navigator.clipboard.writeText(value);
    copied = label;
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
      <span class="status active">Ready</span>
    </header>
    <p>Use this endpoint with OpenAI-compatible clients.</p>
    <div class="code-block">{base}</div>
    <div class="card-actions">
      <button class="button" on:click={() => copy(base, 'url')}>
        <Icon name="check" size={14} />{copied === 'url' ? 'Copied' : 'Copy URL'}
      </button>
    </div>
  </article>
  <article class="item-card">
    <header><h3>Python</h3></header>
    <p>Configure the official OpenAI client.</p>
    <div class="code-block">
      from openai import OpenAI
      <br />
      client = OpenAI(base_url="{base}")
    </div>
    <div class="card-actions">
      <button
        class="button"
        on:click={() =>
          copy(`from openai import OpenAI\nclient = OpenAI(base_url="${base}")`, 'python')}
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
      OPENAI_API_KEY=sk-janus-…
    </div>
    <div class="card-actions">
      <button
        class="button"
        on:click={() => copy(`OPENAI_BASE_URL=${base}\nOPENAI_API_KEY=sk-janus-…`, 'env')}
      >
        {copied === 'env' ? 'Copied' : 'Copy variables'}
      </button>
    </div>
  </article>
</div>
