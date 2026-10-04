<script lang="ts">
  import type { Doc } from './documents.svelte'
  import { t } from './i18n.svelte'

  let {
    docs,
    selectedId,
    onselect,
    onremove,
    ondiscardAll,
    disabled = false,
  }: {
    docs: Doc[]
    selectedId: number | null
    onselect: (id: number) => void
    onremove: (id: number) => void
    ondiscardAll: () => void
    disabled?: boolean
  } = $props()

  const m = $derived(t())
  const done = $derived(docs.filter((d) => d.status === 'ready' || d.status === 'error').length)
</script>

<aside class="doclist" aria-label={m.docs.list}>
  <div class="progress">
    <span>{m.docs.progress(done, docs.length)}</span>
    <div
      class="bar"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={docs.length}
      aria-valuenow={done}
    >
      <div class="fill" style:width={`${(done / docs.length) * 100}%`}></div>
    </div>
  </div>

  <ul>
    {#each docs as doc (doc.id)}
      <li class:selected={doc.id === selectedId}>
        <button class="item" onclick={() => onselect(doc.id)} title={doc.name} {disabled}>
          <span class="status status--{doc.status}" title={m.docs.status[doc.status]} aria-label={m.docs.status[doc.status]}>
            {#if doc.status === 'analyzing'}
              <span class="spinner" aria-hidden="true"></span>
            {:else if doc.status === 'ready'}✓{:else if doc.status === 'error'}!{:else}…{/if}
          </span>
          <span class="name">{doc.name}</span>
          {#if doc.boxesEdited}<span class="edited" title={m.docs.edited} aria-label={m.docs.edited}>●</span>{/if}
        </button>
        <button
          class="remove"
          onclick={() => onremove(doc.id)}
          title={m.docs.remove}
          aria-label={m.docs.removeNamed(doc.name)}
          disabled={disabled || doc.status === 'analyzing'}
        >×</button>
      </li>
    {/each}
  </ul>

  <button class="discard" onclick={ondiscardAll} {disabled}>{m.docs.discardAll}</button>
</aside>

<style>
  .doclist {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
    padding: 0.7rem;
    font-size: 0.85rem;
    min-width: 0;
  }
  .progress {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    color: var(--muted);
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--bg);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    max-height: 60vh;
    overflow-y: auto;
  }
  li {
    display: flex;
    align-items: center;
    border-radius: 8px;
  }
  li.selected {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
  }
  button {
    font: inherit;
    border: none;
    background: none;
    color: var(--fg);
    cursor: pointer;
    border-radius: 8px;
  }
  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .item {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.45rem;
    text-align: left;
  }
  .name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status {
    width: 1rem;
    display: inline-flex;
    justify-content: center;
    font-weight: 700;
    color: var(--muted);
  }
  .status--ready {
    color: var(--accent);
  }
  .status--error {
    color: var(--error);
  }
  .edited {
    color: var(--accent);
    font-size: 0.6rem;
  }
  .remove {
    padding: 0.2rem 0.5rem;
    color: var(--muted);
    font-size: 1.05rem;
    line-height: 1;
  }
  .remove:hover:not(:disabled) {
    color: var(--error);
  }
  .discard {
    align-self: flex-start;
    padding: 0.3rem 0.4rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .discard:hover:not(:disabled) {
    color: var(--error);
    text-decoration: underline;
  }
  .spinner {
    width: 11px;
    height: 11px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
