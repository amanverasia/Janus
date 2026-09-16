<script lang="ts">
  import PageHeader from '$lib/components/PageHeader.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { csv, firstList, idOf, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';
  export let data: JsonObject;
  export let action: (url: string, o?: MutationOptions) => Promise<unknown>;
  let open = false;
  let editing: JsonObject | undefined;
  $: combos = firstList(data, 'combos', 'items');

  function comboModelsPreview(combo: JsonObject): string {
    const items = csv(combo.models_list ?? combo.models)
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    if (!items.length) return 'No models configured';
    if (items.length <= 2) return items.join(' → ');
    return `${items.slice(0, 2).join(' → ')} → +${items.length - 2} more`;
  }

  async function submit(e: SubmitEvent) {
    const f = e.currentTarget as HTMLFormElement;
    const id = editing ? idOf(editing) : '';
    try {
      await action(id ? `/dashboard/api/combos/${id}` : '/dashboard/api/combos', {
        method: id ? 'PUT' : 'POST',
        body: new FormData(f),
        success: id ? 'Combo updated' : 'Combo created'
      });
    } catch {
      return;
    }
    open = false;
  }
</script>

<PageHeader
  title="Combos"
  description="Create ordered model chains that fail over gracefully when an upstream is unavailable."
>
  <button
    class="button primary"
    on:click={() => {
      editing = undefined;
      open = true;
    }}
  >
    <Icon name="plus" />New combo
  </button>
</PageHeader>
{#if combos.length}<div class="cards-grid">
    {#each combos as combo}<article class="item-card">
        <header>
          <h3>{text(combo.name)}</h3>
          <span class="status active">
            {Array.isArray(combo.models_list) ? combo.models_list.length : 0} models
          </span>
        </header>
        <p class="mono combo-models" title={csv(combo.models_list ?? combo.models)}>
          {comboModelsPreview(combo)}
        </p>
        <div class="card-actions">
          <button
            class="button"
            on:click={() => {
              editing = combo;
              open = true;
            }}
          >
            <Icon name="edit" size={14} />Edit
          </button>
          <button
            class="button danger"
            on:click={() =>
              confirm('Delete this combo?') &&
              action(`/dashboard/api/combos/${idOf(combo)}`, {
                method: 'DELETE',
                success: 'Combo deleted'
              })}
          >
            <Icon name="trash" size={14} />Delete
          </button>
        </div>
      </article>{/each}
  </div>{:else}<section class="panel">
    <EmptyState
      icon="layers"
      title="No fallback combos yet"
      message="Group models into an ordered fallback chain."
    >
      <button
        class="button primary"
        on:click={() => {
          editing = undefined;
          open = true;
        }}
      >
        Create combo
      </button>
    </EmptyState>
  </section>{/if}
<Modal {open} title={editing ? 'Edit combo' : 'Create combo'} on:close={() => (open = false)}>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field full">
        <span>Name</span>
        <input
          name="name"
          required
          value={text(editing?.name, '')}
          placeholder="reliable-reasoning"
        />
      </label>
      <label class="field full">
        <span>Models in order</span>
        <textarea
          name="models"
          required
          value={csv(editing?.models_list ?? editing?.models)}
          placeholder="openai/gpt-5, anthropic/claude-sonnet"></textarea>
        <small>Comma-separated. Janus tries them from left to right.</small>
      </label>
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">Save combo</button>
    </div>
  </form>
</Modal>

<style>
  .combo-models {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }
</style>
