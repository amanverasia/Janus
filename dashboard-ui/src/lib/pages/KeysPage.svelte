<script lang="ts">
  import DataTable from '$lib/components/DataTable.svelte';
  import { copyText } from '$lib/clipboard';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import { bool, csv, dateTime, firstList, idOf, number, object, text } from '$lib/data';
  import type { JsonObject, MutationOptions } from '$lib/types';

  export let data: JsonObject;
  export let action: (url: string, options?: MutationOptions) => Promise<unknown>;
  export let navigateQuery: (params: Record<string, string>) => void;

  let open = false;
  let editing: JsonObject | undefined;
  let revealed = '';
  let copied = '';
  let copyError = '';
  $: keys = firstList(data, 'keys', 'api_keys', 'items');
  $: status = text(data.status, 'active');
  $: counts = object(data.counts);

  const cols = [
    { key: 'name', label: 'Name' },
    {
      key: 'key_prefix',
      label: 'Key',
      format: (value: unknown, row: JsonObject) =>
        text(value ?? row.prefix ?? row.masked_key, 'Hidden')
    },
    {
      key: 'allowed_models',
      label: 'Models',
      format: (value: unknown) => csv(value) || 'All models'
    },
    { key: 'created_at', label: 'Created', format: dateTime }
  ];

  async function submit(event: SubmitEvent) {
    const form = event.currentTarget as HTMLFormElement;
    const formData = new FormData(form);
    formData.set('login_field', '1');
    if (editing) formData.set('models_field', '1');
    try {
      const result = await action(
        editing ? `/dashboard/api/keys/${idOf(editing)}` : '/dashboard/api/v2/keys',
        {
          body: formData,
          success: editing ? 'API key updated' : 'API key created'
        }
      );
      if (!editing) {
        revealed = text(object(result).api_key, '');
        copied = '';
        copyError = '';
      }
    } catch {
      return;
    }
    open = false;
  }

  async function copy(value: string, target: string) {
    copied = '';
    copyError = '';
    try {
      await copyText(value);
      copied = target;
    } catch {
      copyError = target;
    }
    window.setTimeout(() => {
      if (copied === target) copied = '';
      if (copyError === target) copyError = '';
    }, 1800);
  }

  async function copyKey() {
    if (revealed) await copy(revealed, 'new-key');
  }

  async function copyPrefix(row: JsonObject) {
    const prefix = text(row.prefix ?? row.key_prefix, '');
    if (prefix) await copy(prefix, `prefix-${idOf(row)}`);
  }
</script>

<PageHeader
  title="API keys"
  description="Issue scoped client credentials. Plaintext keys are only shown once, at creation."
>
  <div class="tabs">
    {#each ['active', 'revoked', 'all'] as item}<button
        class:active={status === item}
        on:click={() => navigateQuery({ status: item })}
      >
        {item} · {number(counts[item])}
      </button>{/each}
  </div>
  <button
    class="button primary"
    on:click={() => {
      editing = undefined;
      open = true;
    }}
  >
    <Icon name="plus" />Create key
  </button>
</PageHeader>
{#if revealed}<div class="alert-strip info" style="margin-bottom:18px">
    <Icon name="key" />
    <div>
      <strong>Copy this key now.</strong>
      <div class="secret" style="margin-top:8px">{revealed}</div>
    </div>
    <button class="button" type="button" on:click={copyKey}>
      {copied === 'new-key' ? 'Copied!' : copyError === 'new-key' ? 'Copy failed' : 'Copy key'}
    </button>
  </div>{/if}
<section class="panel">
  <DataTable rows={keys} columns={cols} emptyTitle="No API keys">
    <svelte:fragment slot="actions" let:row>
      <button
        class="icon-button"
        title={copyError === `prefix-${idOf(row)}`
          ? 'Unable to copy key prefix'
          : copied === `prefix-${idOf(row)}`
            ? 'Key prefix copied'
            : 'Copy key prefix'}
        aria-label={copied === `prefix-${idOf(row)}` ? 'Key prefix copied' : 'Copy key prefix'}
        on:click={() => copyPrefix(row)}
      >
        <Icon name={copied === `prefix-${idOf(row)}` ? 'check' : 'copy'} size={15} />
      </button>
      <button
        class="icon-button"
        title="Edit"
        on:click={() => {
          editing = row;
          open = true;
        }}
      >
        <Icon name="edit" size={15} />
      </button>
      {#if bool(row.is_active, true)}<button
          class="icon-button"
          title="Revoke"
          on:click={() =>
            confirm('Revoke this key?') &&
            action(`/dashboard/api/keys/${idOf(row)}`, {
              method: 'DELETE',
              success: 'API key revoked'
            })}
        >
          <Icon name="trash" size={15} />
        </button>{/if}
    </svelte:fragment>
  </DataTable>
</section>
<Modal {open} title={editing ? 'Edit API key' : 'Create API key'} on:close={() => (open = false)}>
  <form on:submit|preventDefault={submit}>
    <div class="field-grid">
      <label class="field full">
        <span>Name</span>
        <input
          name="name"
          required={!editing}
          value={text(editing?.name, '')}
          placeholder="Production application"
        />
      </label>
      <label class="field full">
        <span>Allowed models</span>
        <input
          name="allowed_models"
          value={csv(editing?.allowed_models)}
          placeholder="All models (or comma-separated IDs)"
        />
      </label>
      <label class="field">
        <span>Daily budget</span>
        <input name="daily_budget" type="number" min="0" step="0.01" placeholder="Optional" />
      </label>
      <label class="check-field">
        <input
          name="can_login"
          type="checkbox"
          checked={editing ? bool(editing.can_login) : true}
        />
        <span>Allow dashboard login</span>
      </label>
      {#if editing}<label class="check-field full">
          <input name="clear_models" type="checkbox" />
          <span>Clear model restrictions</span>
        </label>{/if}
    </div>
    <div class="form-actions">
      <button type="button" class="button" on:click={() => (open = false)}>Cancel</button>
      <button class="button primary">{editing ? 'Save changes' : 'Create key'}</button>
    </div>
  </form>
</Modal>
